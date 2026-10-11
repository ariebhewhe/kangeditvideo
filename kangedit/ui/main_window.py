"""Main window: wires media bin, viewer, timeline, inspector, captions and background tasks."""

from __future__ import annotations

import copy
import os
import threading
import time
from typing import Optional

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog, QLabel, QMainWindow,
                               QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSplitter, QTabWidget,
                               QWidget)

from .. import APP_NAME, __version__
from ..analysis import Analysis, analysis_path_for, load_or_analyze
from ..autoedit import AutoEditSettings, select
from ..captions import Caption, build_captions, edit_caption, transcribe, write_srt
from ..color import Grade, auto_correct
from ..config import ASPECT_RATIOS, FRAME_RATES, RESOLUTIONS, VIDEO_EXTENSIONS, cache_dir
from ..export import export, render_audio
from ..ffmpeg import probe
from ..models import Footage, Project, TimelineClip, new_id
from ..proxy import generate_proxy, proxy_path_for
from ..tasks import TaskQueue
from .captions_panel import CaptionsPanel
from .dialogs import FIT_MODES, AutoEditDialog, CaptionStyleDialog, ExportDialog, ProxyDialog
from .inspector import Inspector
from .media_bin import MediaBin
from .timeline import TimelinePanel
from .viewer import Viewer

PROJECT_FILTER = f"{APP_NAME} project (*{Project.FILE_EXT})"


class MainWindow(QMainWindow):
    audio_ready = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.project = Project()
        self.analyses: dict[str, Analysis] = {}
        self.captions: list[Caption] = []
        self.qs = QSettings(APP_NAME, APP_NAME)
        self.tasks = TaskQueue()
        self.last_auto = self._load_auto_settings()
        self._last_ckpt: tuple = ("", 0.0)
        self._audio_version = 0
        self._audio_pending: Optional[str] = None
        self._audio_cancel = threading.Event()

        self.setWindowTitle(APP_NAME)
        self.resize(1500, 920)

        # --- widgets
        self.viewer = Viewer(self.project)
        self.timeline = TimelinePanel(self.project)
        self.tl = self.timeline.view
        self.bin = MediaBin(self.project)
        self.inspector = Inspector(self.project)
        self.caps_panel = CaptionsPanel()

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.viewer)
        split.addWidget(self.timeline)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([600, 230])
        self.setCentralWidget(split)

        d_bin = QDockWidget("Media", self)
        d_bin.setObjectName("media")
        d_bin.setWidget(self.bin)
        d_bin.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.LeftDockWidgetArea, d_bin)
        tabs = QTabWidget()
        tabs.addTab(self.inspector, "Inspector")
        tabs.addTab(self.caps_panel, "Captions")
        self.right_tabs = tabs
        d_right = QDockWidget("Clip", self)
        d_right.setObjectName("clip")
        d_right.setWidget(tabs)
        d_right.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.RightDockWidgetArea, d_right)
        self.resizeDocks([d_bin, d_right], [430, 370], Qt.Horizontal)

        self._build_actions()
        self._build_toolbar()
        self._build_statusbar()
        self._connect()
        self._sync_project_controls()

        self._audio_timer = QTimer(self, singleShot=True, interval=700)
        self._audio_timer.timeout.connect(self._render_audio)

    # ================================================================== construction
    def _act(self, text, slot, shortcut=None, tip=None) -> QAction:
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        return a

    def _build_actions(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(self._act("New project", self.new_project, "Ctrl+N"))
        m.addAction(self._act("Open project…", self.open_project, "Ctrl+O"))
        m.addAction(self._act("Save project", self.save_project, "Ctrl+S"))
        m.addAction(self._act("Save project as…", lambda: self.save_project(True), "Ctrl+Shift+S"))
        m.addSeparator()
        self.a_import = self._act("Import footage…", self.import_dialog, "Ctrl+I")
        m.addAction(self.a_import)
        self.a_export = self._act("Export video…", self.export_dialog, "Ctrl+E")
        m.addAction(self.a_export)
        m.addSeparator()
        m.addAction(self._act("Quit", self.close, "Ctrl+Q"))

        m = mb.addMenu("&Edit")
        m.addAction(self._act("Undo", self.undo, "Ctrl+Z"))
        m.addAction(self._act("Redo", self.redo, "Ctrl+Shift+Z"))
        m.addSeparator()
        m.addAction(self._act("Split at playhead", self.split_at_playhead, "S"))
        m.addAction(self._act("Delete clip", lambda: self.clip_action("delete", self.tl.selected), "Del"))
        m.addAction(self._act("Duplicate clip", lambda: self.clip_action("duplicate", self.tl.selected), "Ctrl+D"))
        m.addAction(self._act("Replace with another best moment",
                              lambda: self.clip_action("alt_moment", self.tl.selected), "R"))

        m = mb.addMenu("&Tools")
        self.a_auto = self._act("Auto Edit…", self.auto_edit_dialog, "Ctrl+Shift+A",
                                "Pick the best moments from your footage automatically")
        m.addAction(self.a_auto)
        self.a_proxies = self._act("Generate proxies…", self.proxy_dialog, None,
                                   "Create small copies of the footage for faster editing")
        m.addAction(self.a_proxies)
        self.a_auto_proxy = QAction("Generate proxies automatically on import", self, checkable=True)
        self.a_auto_proxy.setChecked(self.qs.value("auto_proxy", False, type=bool))
        self.a_auto_proxy.toggled.connect(lambda v: self.qs.setValue("auto_proxy", v))
        m.addAction(self.a_auto_proxy)
        m.addSeparator()
        self.a_color_all = self._act("Auto color all clips", lambda: self.inspector_request("auto_color_all"))
        m.addAction(self.a_color_all)
        self.a_captions = self._act("Generate captions", self.generate_captions, None,
                                    "Transcribe speech and create captions")
        m.addAction(self.a_captions)
        m.addAction(self._act("Caption style…", self.caption_style_dialog))
        m.addSeparator()
        m.addAction(self._act("Re-analyze all footage", lambda: self.queue_analysis(list(self.project.footage), True)))
        m.addAction(self._act("Open cache folder", lambda: os.startfile(str(cache_dir("")))))

        m = mb.addMenu("&View")
        m.addAction(self._act("Fit timeline", self.tl.fit, "Shift+Z"))
        a = QAction("Rule-of-thirds guides", self, checkable=True)
        a.toggled.connect(lambda v: (setattr(self.viewer.canvas, "show_guides", v), self.viewer.canvas.update()))
        m.addAction(a)

        m = mb.addMenu("&Help")
        m.addAction(self._act("Keyboard shortcuts", self.show_shortcuts, "F1"))
        m.addAction(self._act(f"About {APP_NAME}", lambda: QMessageBox.about(
            self, APP_NAME, f"<b>{APP_NAME} {__version__}</b><br>Automatic best-moment video editor.<br>"
                            "Python · PySide6 · FFmpeg · OpenCV · faster-whisper")))

        for key, slot in (("Space", lambda: self.viewer.toggle_play()),
                          ("Left", lambda: self.viewer.step(-1)), ("Right", lambda: self.viewer.step(1)),
                          ("Shift+Left", lambda: self.viewer.step(-10)), ("Shift+Right", lambda: self.viewer.step(10)),
                          ("Up", self.viewer.prev_clip), ("Down", self.viewer.next_clip),
                          ("Home", lambda: self.viewer.seek(0)),
                          ("End", lambda: self.viewer.seek(self.project.duration)),
                          ("Ctrl+Y", self.redo)):
            QShortcut(QKeySequence(key), self, activated=slot)

    def _build_toolbar(self):
        tb = self.addToolBar("Main")
        tb.setObjectName("main")
        tb.setMovable(False)
        tb.addAction(self.a_import)
        b = QPushButton("✨ Auto Edit")
        b.setObjectName("primary")
        b.setToolTip("Find the best moments and build the edit (Short or Long format)")
        b.clicked.connect(self.auto_edit_dialog)
        tb.addWidget(b)
        tb.addSeparator()
        tb.addAction(self.a_proxies)
        self.chk_proxy = QCheckBox("Use proxies")
        self.chk_proxy.setToolTip("Play back from proxies when available")
        self.chk_proxy.toggled.connect(self._toggle_proxies)
        tb.addWidget(self.chk_proxy)
        tb.addSeparator()
        tb.addAction(self.a_color_all)
        tb.addAction(self.a_captions)
        tb.addSeparator()
        tb.addWidget(QLabel(" Frame "))
        self.cb_aspect = QComboBox()
        for k in ASPECT_RATIOS:
            self.cb_aspect.addItem(k, k)
        tb.addWidget(self.cb_aspect)
        self.cb_res = QComboBox()
        for k in RESOLUTIONS:
            self.cb_res.addItem(k, k)
        tb.addWidget(self.cb_res)
        self.cb_fps = QComboBox()
        for r in FRAME_RATES:
            self.cb_fps.addItem(f"{r:g} fps", r)
        tb.addWidget(self.cb_fps)
        self.cb_fit = QComboBox()
        for k, v in FIT_MODES.items():
            self.cb_fit.addItem(v, k)
        tb.addWidget(self.cb_fit)
        for cb in (self.cb_aspect, self.cb_res, self.cb_fps, self.cb_fit):
            cb.currentIndexChanged.connect(self._project_controls_changed)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        b = QPushButton("⬆ Export")
        b.setObjectName("primary")
        b.clicked.connect(self.export_dialog)
        tb.addWidget(b)

    def _build_statusbar(self):
        sb = self.statusBar()
        self.lbl_status = QLabel("Ready")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFixedWidth(260)
        self.progress.setVisible(False)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self.tasks.cancel_all)
        sb.addWidget(self.lbl_status, 1)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.btn_cancel)

    def _connect(self):
        t = self.tasks
        t.progress.connect(self._on_progress)
        t.started.connect(lambda name: self.lbl_status.setText(name))
        t.pending_changed.connect(self._on_pending)
        t.idle.connect(lambda: self.lbl_status.setText("Ready"))

        self.viewer.time_changed.connect(lambda tt: self.tl.set_playhead(tt, follow=self.viewer.playing))
        self.viewer.play_state.connect(self._on_play_state)

        self.tl.seek.connect(self.viewer.seek)
        self.tl.selection_changed.connect(self._on_select)
        self.tl.edit_started.connect(self.project_checkpoint)
        self.tl.edited.connect(self._on_timeline_edited)
        self.tl.edit_finished.connect(self._on_timeline_finished)
        self.tl.slip_preview.connect(self.viewer.show_source)
        self.tl.slip_end.connect(lambda: self.viewer.show_source(None))
        self.tl.context_action.connect(self.clip_action)
        self.tl.caption_clicked.connect(lambda i: (self.right_tabs.setCurrentWidget(self.caps_panel),
                                                   self.caps_panel.select(i)))
        self.timeline.btn_split.clicked.connect(self.split_at_playhead)
        self.timeline.btn_delete.clicked.connect(lambda: self.clip_action("delete", self.tl.selected))

        self.bin.import_requested.connect(self.import_dialog)
        self.bin.files_dropped.connect(self.import_files)
        self.bin.action.connect(self.bin_action)
        self.bin.log_profile.connect(self.set_log_profile)

        self.inspector.before_change.connect(self._inspector_before)
        self.inspector.changed.connect(self._inspector_changed)
        self.inspector.request.connect(self.inspector_request)

        self.caps_panel.generate.connect(self.generate_captions)
        self.caps_panel.style_requested.connect(self.caption_style_dialog)
        self.caps_panel.export_srt.connect(self.save_srt)
        self.caps_panel.text_edited.connect(self._caption_edited)
        self.caps_panel.seek.connect(self.viewer.seek)
        self.caps_panel.toggled.connect(self._toggle_captions)

        self.audio_ready.connect(self._on_audio_ready)

    # ================================================================== project state
    def _sync_project_controls(self):
        s = self.project.settings
        for cb, val in ((self.cb_aspect, s.aspect), (self.cb_res, s.resolution), (self.cb_fit, s.fit_mode)):
            cb.blockSignals(True)
            cb.setCurrentIndex(max(0, cb.findData(val)))
            cb.blockSignals(False)
        self.cb_fps.blockSignals(True)
        self.cb_fps.setCurrentIndex(min(range(len(FRAME_RATES)), key=lambda i: abs(FRAME_RATES[i] - s.fps)))
        self.cb_fps.blockSignals(False)
        self.chk_proxy.blockSignals(True)
        self.chk_proxy.setChecked(s.use_proxies)
        self.chk_proxy.blockSignals(False)
        self.caps_panel.chk_show.blockSignals(True)
        self.caps_panel.chk_show.setChecked(s.captions.enabled)
        self.caps_panel.chk_show.blockSignals(False)

    def _project_controls_changed(self):
        s = self.project.settings
        s.aspect = self.cb_aspect.currentData()
        s.resolution = self.cb_res.currentData()
        s.fps = float(self.cb_fps.currentData())
        s.fit_mode = self.cb_fit.currentData()
        self.project.dirty = True
        self.viewer.refresh()
        self._update_title()

    def _toggle_proxies(self, on: bool):
        self.project.settings.use_proxies = on
        self.viewer.refresh()
        self._schedule_audio()

    def _update_title(self):
        name = os.path.basename(self.project.path) if self.project.path else "Untitled"
        self.setWindowTitle(f"{name}{' *' if self.project.dirty else ''} — {APP_NAME}")

    def project_checkpoint(self, key: str = ""):
        now = time.monotonic()
        if key and self._last_ckpt[0] == key and now - self._last_ckpt[1] < 2.0:
            self._last_ckpt = (key, now)
            return
        self._last_ckpt = (key, now)
        self.project.checkpoint()
        self._update_title()

    def refresh_all(self, captions: bool = True):
        self.bin.analyses = self.analyses
        self.tl.analyses = self.analyses
        self.tl.min_gap = self.last_auto.min_gap
        self.bin.refresh()
        self.tl.refresh()
        if captions:
            self.update_captions()
        self.viewer.refresh()
        clip = self.selected_clip()
        self.inspector.set_clip(clip)
        self._update_title()

    def selected_clip(self) -> Optional[TimelineClip]:
        i = self.project.index_of(self.tl.selected) if self.tl.selected else -1
        return self.project.clips[i] if i >= 0 else None

    def set_project(self, project: Project):
        self.tasks.cancel_all()
        self.project = project
        self.analyses = {}
        for w in (self.viewer, self.tl, self.bin, self.inspector):
            w.set_project(project)
        self._sync_project_controls()
        missing = []
        for fid, f in project.footage.items():
            path = analysis_path_for(f)
            a = Analysis.load(path) if os.path.exists(path) else None
            if a is not None:
                self.analyses[fid] = a
            elif os.path.exists(f.path):
                missing.append(fid)
        if missing:
            self.queue_analysis(missing)
        self.refresh_all()
        self.tl.fit()
        self._schedule_audio()

    # ================================================================== file ops
    def _confirm_discard(self) -> bool:
        if not self.project.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, "Save changes to the current project?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_project()
        return r == QMessageBox.Discard

    def new_project(self):
        if self._confirm_discard():
            self.set_project(Project())

    def open_project(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", self.qs.value("last_dir", ""), PROJECT_FILTER)
        if not path:
            return
        self.load_project_file(path)

    def load_project_file(self, path: str):
        try:
            p = Project.load(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, APP_NAME, f"Could not open project:\n{e}")
            return
        missing = [f.name for f in p.footage.values() if not os.path.exists(f.path)]
        if missing:
            QMessageBox.warning(self, APP_NAME, "Some footage files are missing:\n" + "\n".join(missing[:15]))
        self.set_project(p)

    def save_project(self, save_as: bool = False) -> bool:
        path = self.project.path
        if save_as or not path:
            start = path or os.path.join(self.qs.value("last_dir", ""), "Untitled" + Project.FILE_EXT)
            path, _ = QFileDialog.getSaveFileName(self, "Save project", start, PROJECT_FILTER)
            if not path:
                return False
            if not path.endswith(Project.FILE_EXT):
                path += Project.FILE_EXT
        try:
            self.project.save(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, APP_NAME, f"Could not save project:\n{e}")
            return False
        self._update_title()
        self.lbl_status.setText(f"Saved {path}")
        return True

    def import_dialog(self):
        exts = " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)
        paths, _ = QFileDialog.getOpenFileNames(self, "Import footage", self.qs.value("last_dir", ""),
                                                f"Video files ({exts});;All files (*)")
        if paths:
            self.import_files(paths)

    def import_files(self, paths: list[str]):
        self.qs.setValue("last_dir", os.path.dirname(paths[0]))
        existing = {os.path.normcase(os.path.abspath(f.path)) for f in self.project.footage.values()}
        added, errors = [], []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for path in sorted(paths):
                if os.path.normcase(os.path.abspath(path)) in existing:
                    continue
                try:
                    info = probe(path)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{os.path.basename(path)}: {e}")
                    continue
                f = Footage(path=os.path.abspath(path), duration=info.duration, width=info.width, height=info.height,
                            fps=info.fps, has_audio=info.has_audio, codec=info.codec, pix_fmt=info.pix_fmt,
                            bit_depth=info.bit_depth, color_space=info.color_space, color_range=info.color_range,
                            color_transfer=info.color_transfer, rotation=info.rotation,
                            creation_time=info.creation_time)
                if info.color_transfer == "arib-std-b67":
                    f.log_profile = "hlg"
                for h in (self.project.settings.proxy_height, 360, 540, 720, 1080):
                    pp = proxy_path_for(f, h)
                    if os.path.exists(pp):
                        f.proxy_path = pp
                        break
                self.project.footage[f.id] = f
                added.append(f.id)
        finally:
            QApplication.restoreOverrideCursor()
        if errors:
            QMessageBox.warning(self, APP_NAME, "Some files could not be imported:\n\n" + "\n".join(errors[:12]))
        if not added:
            return
        if not self.project.clips:
            first = self.project.footage[added[0]]
            self.project.settings.fps = min(FRAME_RATES, key=lambda r: abs(r - first.fps))
            self._sync_project_controls()
        self.project.dirty = True
        if self.a_auto_proxy.isChecked():
            self.queue_proxies(added)
        self.queue_analysis(added)
        self.refresh_all(captions=False)
        self.lbl_status.setText(f"Imported {len(added)} file(s). Analyzing in the background…")

    # ================================================================== background jobs
    def _on_progress(self, p: float, msg: str):
        self.progress.setValue(int(p * 1000))
        pending = self.tasks._pending
        self.lbl_status.setText(msg + (f"   (+{pending - 1} queued)" if pending > 1 else ""))

    def _on_pending(self, n: int):
        self.progress.setVisible(n > 0)
        self.btn_cancel.setVisible(n > 0)
        if n == 0:
            self.progress.setValue(0)

    def _task_error(self, name: str):
        def handler(msg: str):
            if msg:
                QMessageBox.warning(self, APP_NAME, f"{name} failed:\n\n{msg[-1500:]}")
            else:
                self.lbl_status.setText(f"{name}: cancelled")
        return handler

    def queue_analysis(self, ids: list[str], force: bool = False):
        for fid in ids:
            f = self.project.footage.get(fid)
            if f is None or (fid in self.analyses and not force):
                continue

            def job(progress, cancel, f=f):
                if force:
                    try:
                        os.remove(analysis_path_for(f))
                    except OSError:
                        pass
                return load_or_analyze(f, progress, cancel)

            def done(a, fid=fid):
                if fid in self.project.footage:
                    self.analyses[fid] = a
                    self.tl._thumb_cache = {k: v for k, v in self.tl._thumb_cache.items() if k[0] != fid}
                    self.refresh_all(captions=False)

            self.tasks.submit(f"Analyzing {f.name}", job, done, self._task_error(f"Analysis of {f.name}"))

    def queue_proxies(self, ids: list[str]):
        height = self.project.settings.proxy_height
        for fid in ids:
            f = self.project.footage.get(fid)
            if f is None:
                continue

            def done(path, fid=fid):
                ff = self.project.footage.get(fid)
                if ff:
                    ff.proxy_path = path
                    self.viewer.decoders.drop()
                    self.project.dirty = True
                    self.refresh_all(captions=False)
                    self._schedule_audio()

            self.tasks.submit(f"Proxy {f.name}", lambda p, c, f=f: generate_proxy(f, height, p, c), done,
                              self._task_error(f"Proxy for {f.name}"))

    def proxy_dialog(self):
        if not self.project.footage:
            QMessageBox.information(self, APP_NAME, "Import some footage first.")
            return
        dlg = ProxyDialog(self.project, self)
        if dlg.exec() != ProxyDialog.Accepted:
            return
        self.project.settings.proxy_height = dlg.cb_res.currentData()
        if dlg.chk_all.isChecked():
            ids = [fid for fid, f in self.project.footage.items() if not f.has_proxy()]
            self.queue_proxies(ids)
            if not self.chk_proxy.isChecked():
                self.chk_proxy.setChecked(True)

    # ================================================================== auto edit
    def _load_auto_settings(self) -> AutoEditSettings:
        s = AutoEditSettings()
        for k in s.to_dict():
            v = self.qs.value(f"auto/{k}")
            if v is not None:
                cur = getattr(s, k)
                try:
                    setattr(s, k, (v in (True, "true", "1")) if isinstance(cur, bool) else type(cur)(v))
                except (TypeError, ValueError):
                    pass
        return s

    def auto_edit_dialog(self):
        if not self.project.footage:
            QMessageBox.information(self, APP_NAME, "Import some footage first (File → Import, or drag files "
                                                    "onto the Media panel).")
            return
        dlg = AutoEditDialog(self.project, self.bin.selected_ids(), self.last_auto, self)
        if dlg.exec() != AutoEditDialog.Accepted:
            return
        s = dlg.settings()
        ids = dlg.footage_ids()
        if not ids:
            return
        self.last_auto = s
        for k, v in s.to_dict().items():
            self.qs.setValue(f"auto/{k}", v)
        aspect = dlg.aspect()
        self.queue_analysis(ids)
        footage = {fid: self.project.footage[fid] for fid in ids}
        existing = [] if s.replace_timeline else list(self.project.clips)

        def job(progress, cancel):
            progress(0.5, "Choosing the best moments…")
            analyses = {fid: load_or_analyze(f) for fid, f in footage.items()}
            return analyses, select(footage, analyses, s, ids, existing=existing)

        def done(res):
            analyses, clips = res
            self.analyses.update(analyses)
            if not clips:
                QMessageBox.information(self, APP_NAME, "No suitable moments were found. Try shorter clip lengths "
                                                        "or a smaller gap.")
                return
            self.project_checkpoint()
            if s.replace_timeline:
                self.project.clips = clips
            else:
                self.project.clips.extend(clips)
            self.project.settings.video_format = s.video_format
            if aspect:
                self.project.settings.aspect = aspect
                self._sync_project_controls()
            self.tl.selected = ""
            self.refresh_all()
            self.tl.fit()
            self.viewer.seek(0)
            self._schedule_audio()
            self.lbl_status.setText(f"Auto edit: {len(clips)} clips, {self.project.duration:.1f} s")

        self.tasks.submit("Auto edit", job, done, self._task_error("Auto edit"))

    def _best_for(self, fid: str, duration: Optional[float] = None, existing=None) -> Optional[TimelineClip]:
        a = self.analyses.get(fid)
        if a is None:
            return None
        s = copy.copy(self.last_auto)
        d = duration or s.clip_max
        s.target_duration = d
        s.clip_min = min(s.clip_min, d)
        s.clip_max = max(d, s.clip_min)
        s.use_every_footage = True
        s.max_uses = 0
        clips = select({fid: self.project.footage[fid]}, {fid: a}, s, [fid],
                       existing=self.project.clips if existing is None else existing)
        return clips[0] if clips else None

    # ================================================================== bin actions
    def bin_action(self, action: str, ids: list[str]):
        if not ids:
            return
        if action == "remove":
            used = [fid for fid in ids if any(c.footage_id == fid for c in self.project.clips)]
            if used and QMessageBox.question(self, APP_NAME, "Some of this footage is used on the timeline. "
                                                             "Remove it and its clips?") != QMessageBox.Yes:
                return
            self.project_checkpoint()
            self.project.clips = [c for c in self.project.clips if c.footage_id not in ids]
            for fid in ids:
                self.project.footage.pop(fid, None)
                self.analyses.pop(fid, None)
            self.refresh_all()
            self._schedule_audio()
        elif action == "append_whole":
            self.project_checkpoint()
            for fid in ids:
                f = self.project.footage[fid]
                self.project.clips.append(TimelineClip(fid, 0.0, f.duration))
            self._after_structure_change()
        elif action == "append_best":
            missing = [fid for fid in ids if fid not in self.analyses]
            if missing:
                self.queue_analysis(missing)
                self.lbl_status.setText("Analyzing first — try again when analysis is finished.")
                return
            self.project_checkpoint()
            added = 0
            for fid in ids:
                c = self._best_for(fid)
                if c:
                    self.project.clips.append(c)
                    added += 1
            if not added:
                self.lbl_status.setText("No unused moment left in that footage (gap rule).")
            self._after_structure_change()
        elif action == "proxy":
            self.queue_proxies(ids)
        elif action == "delete_proxy":
            for fid in ids:
                f = self.project.footage[fid]
                self.viewer.decoders.drop()
                if f.proxy_path and os.path.exists(f.proxy_path):
                    try:
                        os.remove(f.proxy_path)
                    except OSError as e:
                        QMessageBox.warning(self, APP_NAME, str(e))
                f.proxy_path = None
            self.refresh_all(captions=False)
        elif action == "analyze":
            self.queue_analysis(ids, force=True)
        elif action == "transcribe":
            self.queue_transcription(ids, force=True)

    def set_log_profile(self, ids: list[str], key: str):
        self.project_checkpoint()
        for fid in ids:
            if fid in self.project.footage:
                self.project.footage[fid].log_profile = key
        self.refresh_all(captions=False)

    # ================================================================== timeline editing
    def _after_structure_change(self):
        self.refresh_all()
        self._schedule_audio()

    def _on_select(self, clip_id: str):
        self.inspector.set_clip(self.selected_clip())
        if clip_id:
            self.right_tabs.setCurrentWidget(self.inspector)

    def _on_timeline_edited(self):
        self.viewer.invalidate_audio()
        self.captions = build_captions(self.project)
        self.tl.captions = self.captions
        self.viewer.captions = self.captions
        self.viewer.refresh()
        self.inspector.set_clip(self.selected_clip())

    def _on_timeline_finished(self):
        self.refresh_all()
        self._schedule_audio()

    def split_at_playhead(self):
        idx, off = self.project.clip_at(self.viewer.t)
        if idx is None:
            return
        c = self.project.clips[idx]
        if off < 0.1 or off > c.duration - 0.1:
            return
        self.project_checkpoint()
        cut = c.src_in + off
        new = TimelineClip.from_dict(c.to_dict())
        new.id = new_id()
        new.src_in = cut
        c.src_out = cut
        self.project.clips.insert(idx + 1, new)
        self._after_structure_change()

    def clip_action(self, action: str, clip_id: str):
        i = self.project.index_of(clip_id) if clip_id else -1
        if i < 0:
            return
        c = self.project.clips[i]
        if action == "delete":
            self.project_checkpoint()
            self.project.clips.pop(i)
            self.tl.selected = ""
            self._after_structure_change()
        elif action == "duplicate":
            self.project_checkpoint()
            d = TimelineClip.from_dict(c.to_dict())
            d.id = new_id()
            self.project.clips.insert(i + 1, d)
            self._after_structure_change()
        elif action == "split":
            starts = self.project.clip_starts()
            if not (starts[i] < self.viewer.t < starts[i] + c.duration):
                self.viewer.seek(starts[i] + c.duration / 2)
            self.split_at_playhead()
        elif action == "alt_moment":
            if c.footage_id not in self.analyses:
                return
            alt = self._best_for(c.footage_id, c.duration, existing=list(self.project.clips))
            if alt is None:
                self.lbl_status.setText("No other moment available in this footage (gap / repeat rules).")
                return
            self.project_checkpoint()
            c.src_in, c.src_out, c.score = alt.src_in, alt.src_out, alt.score
            self._after_structure_change()
        elif action in ("auto_color", "reset_color", "copy_grade"):
            self.tl.selected = clip_id
            self.inspector.set_clip(c)
            self.inspector_request({"copy_grade": "copy_grade_all"}.get(action, action))

    def undo(self):
        if self.project.undo():
            self.refresh_all()
            self._schedule_audio()

    def redo(self):
        if self.project.redo():
            self.refresh_all()
            self._schedule_audio()

    # ================================================================== inspector
    def _inspector_before(self, kind: str):
        c = self.selected_clip()
        self.project_checkpoint(f"{kind}:{c.id if c else ''}")

    def _inspector_changed(self, kind: str):
        self.project.dirty = True
        if kind in ("trim",):
            self._on_timeline_edited()
            self.tl.refresh()
            self._schedule_audio()
        elif kind == "footage_color":
            self.bin.refresh()
            self.viewer.refresh()
        elif kind == "audio":
            self.tl.update()
            self._schedule_audio()
        else:
            self.viewer.refresh()
        self._update_title()

    def inspector_request(self, req: str):
        clip = self.selected_clip()
        if req == "auto_color_all":
            if not self.project.clips:
                return
            self.project_checkpoint()
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                for k, c in enumerate(self.project.clips):
                    self.lbl_status.setText(f"Auto color {k + 1}/{len(self.project.clips)}")
                    QApplication.processEvents()
                    self._auto_color(c)
            finally:
                QApplication.restoreOverrideCursor()
            self.lbl_status.setText("Auto color applied to all clips")
        elif clip is None:
            return
        elif req == "auto_color":
            self.project_checkpoint()
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self._auto_color(clip)
            finally:
                QApplication.restoreOverrideCursor()
        elif req == "reset_color":
            self.project_checkpoint()
            clip.grade = Grade()
        elif req == "copy_grade_all":
            self.project_checkpoint()
            for c in self.project.clips:
                c.grade = Grade.from_dict(clip.grade.to_dict())
        self.inspector.set_clip(self.selected_clip())
        self.viewer.refresh()
        self._update_title()

    def _auto_color(self, clip: TimelineClip):
        f = self.project.footage[clip.footage_id]
        frames = self.viewer.grab_frames(clip)
        clip.grade = auto_correct(frames, f.log_profile, f.tonemap, base=clip.grade)

    # ================================================================== captions
    def update_captions(self):
        self.captions = build_captions(self.project)
        self.tl.captions = self.captions
        self.tl.update()
        self.viewer.set_captions(self.captions)
        self.caps_panel.set_captions(self.captions)

    def queue_transcription(self, ids: list[str], force: bool = False):
        s = self.project.settings
        for fid in ids:
            f = self.project.footage.get(fid)
            if f is None or (f.transcript is not None and not force):
                continue

            def done(words, fid=fid):
                ff = self.project.footage.get(fid)
                if ff is not None:
                    ff.transcript = words
                    self.project.dirty = True
                    self.update_captions()
                    self.bin.refresh()

            self.tasks.submit(f"Transcribing {f.name}",
                              lambda p, c, f=f: transcribe(f, s.whisper_model, s.whisper_language, p, c),
                              done, self._task_error(f"Transcription of {f.name}"))

    def generate_captions(self):
        used = list(dict.fromkeys(c.footage_id for c in self.project.clips))
        if not used:
            QMessageBox.information(self, APP_NAME, "Put some clips on the timeline first.")
            return
        todo = [fid for fid in used if self.project.footage[fid].transcript is None]
        force = False
        if not todo:
            r = QMessageBox.question(self, APP_NAME, "All footage on the timeline is already transcribed.\n"
                                                     "Transcribe again (manual caption edits will be lost)?")
            if r != QMessageBox.Yes:
                return
            todo, force = used, True
        if not any(self.project.footage[fid].has_audio for fid in todo):
            QMessageBox.information(self, APP_NAME, "The footage on the timeline has no audio.")
            return
        self.project.settings.captions.enabled = True
        self._sync_project_controls()
        self.right_tabs.setCurrentWidget(self.caps_panel)
        self.queue_transcription(todo, force)

    def caption_style_dialog(self):
        dlg = CaptionStyleDialog(self.project, self)
        if dlg.exec() == CaptionStyleDialog.Accepted:
            dlg.apply()
            self.project.dirty = True
            self.update_captions()
            self._update_title()

    def _caption_edited(self, row: int, text: str):
        if 0 <= row < len(self.captions):
            self.project_checkpoint()
            edit_caption(self.project, self.captions[row], text)
            QTimer.singleShot(0, self.update_captions)

    def _toggle_captions(self, on: bool):
        self.project.settings.captions.enabled = on
        self.viewer.refresh()

    def save_srt(self):
        if not self.captions:
            QMessageBox.information(self, APP_NAME, "No captions yet — click Generate captions first.")
            return
        start = os.path.splitext(self.project.path)[0] + ".srt" if self.project.path else "captions.srt"
        path, _ = QFileDialog.getSaveFileName(self, "Save subtitles", start, "SubRip (*.srt)")
        if path:
            write_srt(path, self.captions, self.project.settings.captions)
            self.lbl_status.setText(f"Saved {path}")

    # ================================================================== export
    def export_dialog(self):
        if not self.project.clips:
            QMessageBox.information(self, APP_NAME, "The timeline is empty.")
            return
        missing = {self.project.footage[c.footage_id].name for c in self.project.clips
                   if not os.path.exists(self.project.footage[c.footage_id].path)}
        if missing:
            QMessageBox.warning(self, APP_NAME, "Missing source files:\n" + "\n".join(sorted(missing)))
            return
        dlg = ExportDialog(self.project, bool(self.captions), self)
        if dlg.exec() != ExportDialog.Accepted:
            return
        es = dlg.settings()
        proj = self._clone_project()

        def done(path):
            r = QMessageBox.information(self, APP_NAME, f"Export finished:\n{path}",
                                        QMessageBox.Open | QMessageBox.Ok)
            if r == QMessageBox.Open:
                os.startfile(path)

        self.tasks.submit(f"Exporting {os.path.basename(es.output)}",
                          lambda p, c: export(proj, es, p, c), done, self._task_error("Export"))

    def _clone_project(self) -> Project:
        """Frozen copy so edits during a long export don't change what is rendered."""
        p = Project()
        p.settings = copy.deepcopy(self.project.settings)
        p.footage = {fid: copy.deepcopy(f) for fid, f in self.project.footage.items()}
        p.clips = [TimelineClip.from_dict(c.to_dict()) for c in self.project.clips]
        return p

    # ================================================================== preview audio
    def _schedule_audio(self):
        self.viewer.invalidate_audio()
        self._audio_timer.start()

    def _render_audio(self):
        if not self.project.clips:
            self.viewer.set_audio(None)
            return
        self._audio_cancel.set()
        self._audio_cancel = threading.Event()
        cancel = self._audio_cancel
        self._audio_version += 1
        ver = self._audio_version
        proj = self._clone_project()
        use_proxies = self.project.settings.use_proxies
        path = str(cache_dir("tmp") / f"preview_audio_{ver % 4}.wav")

        def work():
            try:
                render_audio(proj, use_proxies, path, cancel)
                if not cancel.is_set():
                    self.audio_ready.emit(ver, path)
            except Exception:  # noqa: BLE001 - preview audio is best effort
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_audio_ready(self, ver: int, path: str):
        if ver != self._audio_version:
            return
        if self.viewer.playing:
            self._audio_pending = path
        else:
            self.viewer.set_audio(path)

    def _on_play_state(self, playing: bool):
        if not playing and self._audio_pending:
            self.viewer.set_audio(self._audio_pending)
            self._audio_pending = None

    # ================================================================== misc
    def show_shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts", (
            "Space — play / pause\n←/→ — previous / next frame (Shift: 10 frames)\n"
            "↑/↓ — previous / next clip\nHome / End — start / end\n"
            "S — split at playhead\nDel — delete clip\nCtrl+D — duplicate clip\n"
            "R — replace clip with another best moment\n"
            "Ctrl+Z / Ctrl+Shift+Z — undo / redo\nShift+Z — fit timeline\n"
            "Ctrl+wheel on timeline — zoom\n\n"
            "Timeline: drag a clip to reorder · drag its edges to trim ·\n"
            "Alt/Shift+drag (or Slip mode) to slide the moment inside the clip"))

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        self.viewer.stop()
        self.tasks.shutdown()
        self._audio_cancel.set()
        self.viewer.decoders.close()
        e.accept()
