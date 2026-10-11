"""Main window: wires media bin, viewer, timeline, inspector, captions and background tasks."""

from __future__ import annotations

import copy
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog, QInputDialog, QLabel,
                               QMainWindow, QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSplitter,
                               QTabWidget, QWidget)

from .. import APP_NAME, camera, version_string
from .. import lut as lutlib
from ..analysis import Analysis, analysis_path_for, analyze_many, load_or_analyze
from ..autoedit import AutoEditSettings, apply_edit_style, find_hook, select
from .. import sfx
from ..bookends import find_bookends, story_order
from ..captions import Caption, build_captions, edit_caption, listen, project_layout, transcribe, write_srt
from ..color import Grade, auto_correct, lut_profile, missing_lut, profile_label
from ..decoder import DecoderPool
from ..config import (ASPECT_RATIOS, CACHE_KINDS, FRAME_RATES, RESOLUTIONS, VIDEO_EXTENSIONS, cache_dir, cache_root,
                      clear_cache, default_cache_root, fmt_bytes, folder_size, legacy_cache_root, lut_dir,
                      open_path, set_cache_root, settings, sfx_dir)
from ..export import export, render_audio
from ..ffmpeg import Cancelled, probe
from ..models import Footage, Project, SoundClip, TimelineClip, new_id
from ..proxy import generate_proxy, proxy_path_for
from ..tasks import TaskQueue
from .captions_panel import CaptionsPanel
from .dialogs import (FIT_MODES, AutoEditDialog, CacheDialog, CaptionStyleDialog, ExportDialog, OfficialLutDialog,
                      ProxyDialog)
from .inspector import Inspector
from .media_bin import MediaBin
from .sfx_panel import SoundPanel
from .theme import fmt_time
from .timeline import TimelinePanel
from .viewer import ADJUST_MODES, Viewer

PROJECT_FILTER = f"{APP_NAME} project (*{Project.FILE_EXT})"


class MainWindow(QMainWindow):
    audio_ready = Signal(int, str)
    analysis_ready = Signal(str, object)   # footage id, Analysis (from worker threads)

    def __init__(self):
        super().__init__()
        self.project = Project()
        self.analyses: dict[str, Analysis] = {}
        self.captions: list[Caption] = []
        self.qs = settings()
        lutlib.set_official_choices(self._official_luts())
        self._init_cache_root()
        self.tasks = TaskQueue()
        self.last_auto = self._load_auto_settings()
        self._auto_ids: list[str] = []     # footage the last auto edit used (for regenerate)
        self._tried_hooks: list[TimelineClip] = []   # hooks "New hook" already showed (not offered again)
        self._progress_text = ""
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
        self.sfx_panel = SoundPanel()

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.viewer)
        split.addWidget(self.timeline)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([560, 280])
        self.setCentralWidget(split)

        d_bin = QDockWidget("Media", self)
        d_bin.setObjectName("media")
        d_bin.setWidget(self.bin)
        d_bin.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.LeftDockWidgetArea, d_bin)
        tabs = QTabWidget()
        tabs.addTab(self.inspector, "Inspector")
        tabs.addTab(self.caps_panel, "Captions")
        tabs.addTab(self.sfx_panel, "Sounds")
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
        QTimer.singleShot(800, self._offer_legacy_cache)

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
        self.recent_menu = m.addMenu("Open recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        self.recent_menu.setEnabled(bool(self._recent_projects()))
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
        m.addAction(self._act("Select all clips", self.tl.select_all, "Ctrl+A"))
        m.addAction(self._act("Delete selected clips / sound", self._delete_selected, "Del"))
        m.addAction(self._act("Duplicate clip", lambda: self.clip_action("duplicate", self.tl.selected), "Ctrl+D"))
        m.addAction(self._act("Replace with another best moment",
                              lambda: self.clip_action("alt_moment", self.tl.selected), "R"))

        m = mb.addMenu("&Tools")
        self.a_auto = self._act("Auto Edit…", self.auto_edit_dialog, "Ctrl+Shift+A",
                                "Pick the best moments from your footage automatically")
        m.addAction(self.a_auto)
        m.addAction(self._act("Regenerate auto edit shorter (−25%)", lambda: self.regenerate_auto_edit(0.75),
                              "Ctrl+Shift+-", "Rebuild the automatic edit about a quarter shorter than the timeline"))
        m.addAction(self._act("Regenerate auto edit longer (+25%)", lambda: self.regenerate_auto_edit(1.25),
                              "Ctrl+Shift+=", "Rebuild the automatic edit about a quarter longer than the timeline"))
        m.addAction(self._act("Regenerate auto edit at length…", lambda: self.regenerate_auto_edit(None), None,
                              "Rebuild the automatic edit with the same settings at a length you choose"))
        m.addAction(self._act("Find a new hook clip", self.new_hook, "H",
                              "Replace the opening hook with another strong moment not used in the edit"))
        self.a_proxies = self._act("Generate proxies…", self.proxy_dialog, None,
                                   "Create small copies of the footage for faster editing")
        m.addAction(self.a_proxies)
        self.a_auto_proxy = QAction("Generate proxies automatically on import", self, checkable=True)
        self.a_auto_proxy.setChecked(self.qs.value("auto_proxy", False, type=bool))
        self.a_auto_proxy.toggled.connect(lambda v: self.qs.setValue("auto_proxy", v))
        m.addAction(self.a_auto_proxy)
        self.a_gpu_analysis = QAction("Use GPU to speed up analysis", self, checkable=True)
        self.a_gpu_analysis.setToolTip("Decode footage on the graphics card (NVIDIA, AMD or Intel) while the CPU "
                                       "analyzes other clips")
        self.a_gpu_analysis.setChecked(self.qs.value("gpu_analysis", True, type=bool))
        self.a_gpu_analysis.toggled.connect(lambda v: self.qs.setValue("gpu_analysis", v))
        m.addAction(self.a_gpu_analysis)
        self.a_auto_log = QAction("Detect camera log profile on import", self, checkable=True)
        self.a_auto_log.setToolTip("Identify the camera of imported footage and choose its log → Rec.709 "
                                   "conversion automatically")
        self.a_auto_log.setChecked(self.qs.value("auto_log", True, type=bool))
        self.a_auto_log.toggled.connect(lambda v: self.qs.setValue("auto_log", v))
        m.addAction(self.a_auto_log)
        m.addAction(self._act("Detect log profile for all footage", lambda: self.detect_log(list(self.project.footage)),
                              None, "Identify the camera of every imported file and choose its log → Rec.709 conversion"))
        m.addSeparator()
        self.a_color_all = self._act("Auto color all clips", lambda: self.inspector_request("auto_color_all"))
        m.addAction(self.a_color_all)
        m.addAction(self._act("Import LUT (.cube)…", lambda: self.import_lut("library"), None,
                              "Add log-conversion or color-grading LUTs to the LUT library"))
        m.addAction(self._act("Official camera LUTs…", self.official_luts_dialog, None,
                              "Use the camera makers' own log → Rec.709 LUTs (DJI D-Log M, Sony, Panasonic…)"))
        m.addAction(self._act("Open LUT folder", lambda: open_path(str(lut_dir()))))
        self.a_captions = self._act("Generate captions", self.generate_captions, None,
                                    "Transcribe speech and create captions")
        m.addAction(self.a_captions)
        m.addAction(self._act("Caption style…", self.caption_style_dialog))
        m.addSeparator()
        m.addAction(self._act("Re-analyze all footage", lambda: self.queue_analysis(list(self.project.footage), True)))
        m.addAction(self._act("Cache folder && clearing…", self.cache_dialog, None,
                              "Choose where proxies and analysis are stored, and clear them"))

        m = mb.addMenu("&View")
        m.addAction(self._act("Fit timeline", self.tl.fit, "Shift+Z"))
        sub = m.addMenu("Color in viewer")
        sub.setToolTipsVisible(True)
        group = QActionGroup(self)
        self.a_adjust: dict[str, QAction] = {}
        for key, label in ADJUST_MODES:
            a = QAction(label.split(": ", 1)[1].capitalize(), self, checkable=True)
            a.setToolTip("Log conversion, LUTs and grading in the viewer (export always includes them)")
            a.triggered.connect(lambda _=False, k=key: self.viewer.set_adjust_mode(k))
            group.addAction(a)
            sub.addAction(a)
            self.a_adjust[key] = a
        sub.addSeparator()
        sub.addAction(self._act("Toggle color on / off", self._toggle_adjustments, "Ctrl+Shift+B",
                                "Show the picture as recorded, without log conversion, LUTs or grading"))
        self.viewer.set_adjust_mode(self.qs.value("viewer_color", "on", type=str))
        self.a_adjust[self.viewer.adjust_mode].setChecked(True)
        self.viewer.adjust_mode_changed.connect(self._adjust_mode_changed)
        a = QAction("Rule-of-thirds guides", self, checkable=True)
        a.toggled.connect(lambda v: (setattr(self.viewer.canvas, "show_guides", v), self.viewer.canvas.update()))
        m.addAction(a)

        m = mb.addMenu("&Help")
        m.addAction(self._act("Keyboard shortcuts", self.show_shortcuts, "F1"))
        m.addAction(self._act(f"About {APP_NAME}", lambda: QMessageBox.about(
            self, APP_NAME, f"<b>{APP_NAME} {version_string()}</b><br>Automatic best-moment video editor.<br>"
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
        for text, factor, tip in (("− Shorter", 0.75, "Regenerate the auto edit about 25% shorter"),
                                  ("+ Longer", 1.25, "Regenerate the auto edit about 25% longer")):
            rb = QPushButton(text)
            rb.setToolTip(tip + " (same settings and footage)")
            rb.clicked.connect(lambda _=False, k=factor: self.regenerate_auto_edit(k))
            tb.addWidget(rb)
        hb = QPushButton("🎣 New hook")
        hb.setToolTip("Replace the opening hook with another strong moment not used in the edit (H). "
                      "Click again for the next one; Undo brings the previous back.")
        hb.clicked.connect(self.new_hook)
        tb.addWidget(hb)
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
        t.started.connect(self._status_progress)
        t.pending_changed.connect(self._on_pending)
        # "Ready" replaces progress text only, not the result message a finished task just showed.
        t.idle.connect(lambda: self.lbl_status.text() == self._progress_text and self.lbl_status.setText("Ready"))

        self.viewer.time_changed.connect(lambda tt: self.tl.set_playhead(tt, follow=self.viewer.playing))
        self.viewer.play_state.connect(self._on_play_state)

        self.tl.seek.connect(self.viewer.seek)
        self.tl.selection_changed.connect(self._on_select)
        self.tl.edit_started.connect(self.project_checkpoint)
        self.tl.edited.connect(self._on_timeline_edited)
        self.tl.edit_finished.connect(self._on_timeline_finished)
        self.tl.slip_preview.connect(self.viewer.show_source)
        self.tl.slip_end.connect(lambda: self.viewer.show_source(None))
        self.tl.sounds_changed.connect(self._sounds_changed)
        self.tl.sound_dropped.connect(lambda paths, t: self.add_sounds(paths, t))
        self.sfx_panel.add_sound.connect(lambda path, name: self.add_sounds([path], None, [name]))
        self.tl.context_action.connect(self.clip_action)
        self.tl.caption_clicked.connect(lambda i: (self.right_tabs.setCurrentWidget(self.caps_panel),
                                                   self.caps_panel.select(i)))
        self.timeline.btn_split.clicked.connect(self.split_at_playhead)
        self.timeline.fades_changed.connect(self._fades_changed)
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
        self.analysis_ready.connect(self._on_analysis_ready)

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
        self.timeline.sync_fades()

    def _fades_changed(self):
        self.project.dirty = True
        self.viewer.refresh()
        self._schedule_audio()
        self._update_title()

    def _project_controls_changed(self):
        s = self.project.settings
        s.aspect = self.cb_aspect.currentData()
        s.resolution = self.cb_res.currentData()
        s.fps = float(self.cb_fps.currentData())
        s.fit_mode = self.cb_fit.currentData()
        self.project.dirty = True
        self.viewer.refresh()
        self._update_title()

    def _toggle_adjustments(self):
        self.viewer.set_adjust_mode("off" if self.viewer.adjust_mode == "on" else "on")

    def _adjust_mode_changed(self, mode: str):
        self.qs.setValue("viewer_color", mode)
        self.a_adjust[mode].setChecked(True)
        self.lbl_status.setText({"on": "Viewer shows color adjustments",
                                 "playback": "Color adjustments are hidden while playing (export unaffected)",
                                 "off": "Viewer shows the picture as recorded (export unaffected)"}[mode])

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
                if f.log_pending:
                    self._log_from_picture(f, list(a.thumbs))
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
        self._add_recent(path)

    # ------------------------------------------------------------------ recent projects
    MAX_RECENT = 10

    def _recent_projects(self) -> list[str]:
        v = self.qs.value("recent_projects", [])
        if isinstance(v, str):   # QSettings returns a lone entry as a plain string on some platforms
            v = [v]
        return [p for p in (v or []) if p]

    def _set_recent(self, paths: list[str]):
        self.qs.setValue("recent_projects", paths[:self.MAX_RECENT])
        self.recent_menu.setEnabled(bool(paths))

    def _add_recent(self, path: str):
        path = os.path.abspath(path)
        key = os.path.normcase(path)
        self._set_recent([path] + [p for p in self._recent_projects() if os.path.normcase(p) != key])

    def _fill_recent_menu(self):
        m = self.recent_menu
        m.clear()
        paths = self._recent_projects()
        for k, path in enumerate(paths, 1):
            name, folder = (s.replace("&", "&&") for s in (os.path.basename(path), os.path.dirname(path)))
            a = m.addAction(f"&{k % 10}  {name}    —  {folder}")
            a.setToolTip(path)
            a.setStatusTip(path)
            a.triggered.connect(lambda _=False, p=path: self.open_recent(p))
            if not os.path.exists(path):
                a.setText(a.text() + "  (missing)")
        if paths:
            m.addSeparator()
            m.addAction(self._act("Clear recent projects", lambda: self._set_recent([])))

    def open_recent(self, path: str):
        if not os.path.exists(path):
            r = QMessageBox.question(self, APP_NAME, f"The project file no longer exists:\n{path}\n\n"
                                     "Remove it from the recent projects?")
            if r == QMessageBox.Yes:
                key = os.path.normcase(path)
                self._set_recent([p for p in self._recent_projects() if os.path.normcase(p) != key])
            return
        if self._confirm_discard():
            self.load_project_file(path)

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
        self._add_recent(path)
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
                self._identify_camera(f, info)
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
    def _status_progress(self, text: str):
        self._progress_text = text
        self.lbl_status.setText(text)

    def _on_progress(self, p: float, msg: str):
        self.progress.setValue(int(p * 1000))
        pending = self.tasks._pending
        self._status_progress(msg + (f"   (+{pending - 1} queued)" if pending > 1 else ""))

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
        """Analyze footage as one background task; clips run in parallel and appear as each one finishes."""
        todo = [self.project.footage[fid] for fid in ids
                if fid in self.project.footage and (force or fid not in self.analyses)]
        if not todo:
            return
        use_gpu = self.a_gpu_analysis.isChecked()

        def job(progress, cancel):
            pending = []
            for f in todo:
                path = analysis_path_for(f)
                if force:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                a = Analysis.load(path) if os.path.exists(path) else None
                if a is not None:
                    f.analysis_path = path
                    self.analysis_ready.emit(f.id, a)
                else:
                    pending.append(f)
            failed: list[str] = []
            analyze_many(pending, progress, cancel,
                         on_result=lambda f, a: self.analysis_ready.emit(f.id, a),
                         on_error=lambda f, msg: failed.append(f"{f.name}: {msg}"), use_gpu=use_gpu)
            return failed

        def done(failed):
            if failed:
                QMessageBox.warning(self, APP_NAME, "Analysis failed:\n\n" + "\n".join(failed)[-1500:])

        name = f"Analyzing {todo[0].name}" if len(todo) == 1 else f"Analyzing {len(todo)} clips"
        self.tasks.submit(name, job, done, self._task_error("Analysis"))

    def _on_analysis_ready(self, fid: str, a: Analysis):
        if fid in self.project.footage:
            self.analyses[fid] = a
            self.tl._thumb_cache = {k: v for k, v in self.tl._thumb_cache.items() if k[0] != fid}
            f = self.project.footage[fid]
            if f.log_pending:
                self._log_from_picture(f, list(a.thumbs))
            self.refresh_all(captions=False)

    def queue_proxies(self, ids: list[str]):
        height = self.project.settings.proxy_height
        use_gpu = self.qs.value("gpu_proxies", True, type=bool)
        for fid in ids:
            f = self.project.footage.get(fid)
            if f is None:
                continue

            def done(path, fid=fid):
                ff = self.project.footage.get(fid)
                if ff:
                    ff.proxy_path = path
                    self.viewer.drop_decoders()
                    self.project.dirty = True
                    self.refresh_all(captions=False)
                    self._schedule_audio()

            self.tasks.submit(f"Proxy {f.name}", lambda p, c, f=f: generate_proxy(f, height, p, c, use_gpu), done,
                              self._task_error(f"Proxy for {f.name}"))

    def proxy_dialog(self):
        if not self.project.footage:
            QMessageBox.information(self, APP_NAME, "Import some footage first.")
            return
        dlg = ProxyDialog(self.project, self.qs.value("gpu_proxies", True, type=bool), self)
        if dlg.exec() != ProxyDialog.Accepted:
            return
        self.qs.setValue("gpu_proxies", dlg.chk_gpu.isChecked())
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
        self._run_auto_edit(s, ids, dlg.aspect())

    def regenerate_auto_edit(self, factor: Optional[float]):
        """Rebuild the automatic edit with the last settings and footage, shorter / longer than the timeline
        (factor), or at a length the user enters (None)."""
        ids = [fid for fid in self._auto_ids if fid in self.project.footage] \
            or list(dict.fromkeys(c.footage_id for c in self.project.clips)) or list(self.project.footage)
        if not ids:
            self.auto_edit_dialog()
            return
        current = self.project.duration or self.last_auto.target_duration
        if factor is None:
            v, ok = QInputDialog.getDouble(self, "Regenerate auto edit",
                                           f"New length in seconds (the timeline is {current:.0f} s):",
                                           round(current), 5, 3600, 0)
            if not ok:
                return
            target = v
        else:
            target = current * factor
        total = sum(self.project.footage[fid].duration for fid in ids)
        target = float(round(max(5.0, min(target, total * 0.9))))
        s = copy.copy(self.last_auto)
        s.target_duration = target
        s.replace_timeline = True
        s.clip_min = min(s.clip_min, target)
        self.last_auto.target_duration = target
        self.qs.setValue("auto/target_duration", target)
        self._run_auto_edit(s, ids, None, f"Regenerating the edit at {target:.0f} s")

    def _run_auto_edit(self, s: AutoEditSettings, ids: list[str], aspect: Optional[str], name: str = "Auto edit"):
        self._auto_ids = list(ids)
        self.queue_analysis(ids)
        footage = {fid: self.project.footage[fid] for fid in ids}
        existing = [] if s.replace_timeline else list(self.project.clips)
        use_gpu = self.a_gpu_analysis.isChecked()
        lang = self.project.settings.whisper_language

        def job(progress, cancel):
            progress(0.3, "Analyzing footage…")
            analyses = {fid: load_or_analyze(f, cancel=cancel, use_gpu=use_gpu) for fid, f in footage.items()}
            order = story_order(footage, ids)
            intro = outro = None
            notes: list[str] = []
            sel = s
            if s.bookends:
                progress(0.5, "Finding the opening and closing…")
                max_len = 20.0 if s.video_format == "long" else max(4.0, s.clip_max * 2)
                intro, outro, notes = find_bookends(
                    footage, order, max_len, s.clip_min,
                    listen=lambda f, a, d: listen(f, a, d, "small", lang, cancel),
                    progress=lambda msg: progress(0.5, msg), cancel=cancel)
                if cancel.is_set():
                    raise Cancelled()
                ends = [c for c in (intro, outro) if c is not None]
                sel = copy.copy(s)
                sel.target_duration = max(s.clip_min, s.target_duration - sum(c.duration for c in ends))
            progress(0.8, "Choosing the best moments…")
            body = select(footage, analyses, sel, order, existing=existing + [c for c in (intro, outro) if c])
            clips = ([intro] if intro else []) + body + ([outro] if outro else [])
            apply_edit_style(clips, footage, analyses, s)
            return analyses, clips, notes

        def done(res):
            analyses, clips, notes = res
            self.analyses.update(analyses)
            if not clips:
                QMessageBox.information(self, APP_NAME, "No suitable moments were found. Try shorter clip lengths "
                                                        "or a smaller gap.")
                return
            self.project_checkpoint()
            self._tried_hooks = []
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
            msg = f"Auto edit: {len(clips)} clips, {self.project.duration:.1f} s"
            if s.replace_timeline and self.project.duration < 0.85 * s.target_duration:
                msg += (f" (only this much distinct material for the {s.target_duration:.0f} s target — "
                        "lower 'Gap between reuses' or allow more uses per footage)")
            self.lbl_status.setText(msg + (" · " + " · ".join(notes) if notes else ""))

        self.tasks.submit(name, job, done, self._task_error("Auto edit"))

    def new_hook(self):
        """Put a different strong moment at the start as the hook: replaces the clip labelled "hook" (or the
        first clip of a hook-first edit), otherwise inserts one before everything."""
        clips = self.project.clips
        ids = [fid for fid in (self._auto_ids or list(self.project.footage))
               if fid in self.project.footage and fid in self.analyses]
        if not ids:
            QMessageBox.information(self, APP_NAME, "Analyze some footage first (or run ✨ Auto Edit).")
            return
        idx = next((k for k, c in enumerate(clips) if c.label == "hook"), None)
        if idx is None and clips and self.last_auto.order == "hook" and clips[0].label != "opening":
            idx = 0
        old = clips[idx] if idx is not None else None
        s = self.last_auto
        length = old.duration if old else (s.clip_max if s.video_format != "long" else min(s.clip_max, 6.0))
        footage = {fid: self.project.footage[fid] for fid in ids}
        analyses = {fid: self.analyses[fid] for fid in ids}
        if old is not None and not any(_same_moment(old, t) for t in self._tried_hooks):
            self._tried_hooks.append(TimelineClip.from_dict(old.to_dict()))
        hook = find_hook(footage, analyses, s, length, list(clips) + self._tried_hooks)
        if hook is None and self._tried_hooks:
            # Every strong moment has been shown once: start the round again (still avoiding the timeline).
            self._tried_hooks = [TimelineClip.from_dict(old.to_dict())] if old else []
            hook = find_hook(footage, analyses, s, length, list(clips) + self._tried_hooks)
        if hook is None:
            self.lbl_status.setText("No other hook moment found (every strong moment is already in the edit).")
            return
        self._tried_hooks.append(TimelineClip.from_dict(hook.to_dict()))
        self.project_checkpoint()
        if old is not None:
            # Keep the clip's look and audio settings; only the moment changes.
            old.footage_id, old.src_in, old.src_out, old.score = hook.footage_id, hook.src_in, hook.src_out, hook.score
            old.label = "hook"
        else:
            clips.insert(0, hook)
            if len(clips) > 1:
                clips[1].transition, clips[1].audio_split = "", 0.0
        self._after_structure_change()
        self.tl.selected = (old or hook).id
        self.tl.update()
        self.inspector.set_clip(self.selected_clip())
        self.viewer.seek(0)
        f = self.project.footage[hook.footage_id]
        self.lbl_status.setText(f"New hook: {f.name} at {hook.src_in:.1f} s (score {hook.score * 100:.0f})")

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
                self.viewer.drop_decoders()
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
        elif action == "detect_log":
            self.detect_log(ids)
        elif action == "import_lut_log":
            self.import_lut("log", ids)

    def set_log_profile(self, ids: list[str], key: str):
        self.project_checkpoint()
        for fid in ids:
            f = self.project.footage.get(fid)
            if f is not None:
                f.log_profile, f.log_note, f.log_pending = key, "Chosen manually", False
        self.refresh_all(captions=False)
        entry = lutlib.LUT_ONLY.get(key)
        if entry is not None and missing_lut(key):
            r = QMessageBox.question(
                self, APP_NAME, f"{entry.menu_label} converts with {entry.maker}'s own LUT, which isn't in the "
                f"LUT library yet.\n\nUntil it is, the footage is shown and exported with the built-in "
                f"{profile_label(entry.base)} curve as an approximation.\n\nGet the LUT now?",
                QMessageBox.Yes | QMessageBox.No)
            if r == QMessageBox.Yes:
                self.official_luts_dialog()

    # ================================================================== camera / log detection
    def _identify_camera(self, f: Footage, info) -> None:
        """At import: name the camera and apply a log profile the file states outright. When only the camera
        is known, the profile is decided from the picture once analysis has thumbnails."""
        try:
            cam = camera.detect(f.path, info)
        except Exception:  # noqa: BLE001 - identification is best effort
            return
        f.camera = cam.label
        if not self.a_auto_log.isChecked():
            return
        if cam.log_profile is not None:
            f.log_profile, f.log_note = self._official_profile(cam.log_profile), f"Detected from {cam.source}"
        else:
            f.log_guess, f.log_pending = cam.guess, True

    def _log_from_picture(self, f: Footage, frames) -> None:
        profile, note = camera.decide_from_picture(f.log_guess, f.camera, frames)
        if profile is not None and profile != f.log_profile and f.log_profile == "none":
            self.project.set_detected_profile(f.id, self._official_profile(profile), f"Detected: {note}")
            self.lbl_status.setText(f"{f.name}: {profile_label(profile)} ({note})")
        else:
            f.log_note, f.log_pending = note, False

    def detect_log(self, ids: list[str]):
        """Re-run camera identification and choose the log profile for the given footage (in the background)."""
        ids = [fid for fid in ids if fid in self.project.footage]
        if not ids:
            return
        footage = {fid: self.project.footage[fid] for fid in ids}
        thumbs = {fid: list(self.analyses[fid].thumbs) for fid in ids
                  if fid in self.analyses and len(self.analyses[fid].thumbs)}
        use_proxies = self.project.settings.use_proxies
        name = "Detect log profile" + (f" ({len(ids)} files)" if len(ids) > 1 else "")

        def job(progress, cancel):
            pool = DecoderPool(limit=2)
            results = []
            try:
                for k, (fid, f) in enumerate(footage.items()):
                    if cancel.is_set():
                        raise Cancelled()
                    progress(k / len(footage), f"{name}: {f.name}")
                    try:
                        cam = camera.detect(f.path, probe(f.path))
                        if cam.log_profile is not None:
                            profile, note = cam.log_profile, f"from {cam.source}"
                        else:
                            frames = thumbs.get(fid)
                            if frames is None:   # not analyzed yet: sample the picture
                                dec = pool.get(f, use_proxies, 360)
                                frames = [img for img in (dec.frame_at(f.duration * (i + 0.5) / 5) for i in range(5))
                                          if img is not None]
                            profile, note = camera.decide_from_picture(cam.guess, cam.label, frames)
                        results.append((fid, cam.label, profile, note, None))
                    except Exception as e:  # noqa: BLE001
                        results.append((fid, None, None, None, str(e)))
                return results
            finally:
                pool.close()

        def done(results):
            self.project_checkpoint()
            lines = []
            for fid, label, profile, note, err in results:
                f = self.project.footage.get(fid)
                if f is None:
                    continue
                if err:
                    lines.append(f"{f.name}: {err}")
                    continue
                f.camera = label
                if profile is not None:
                    f.log_profile = self._official_profile(profile)
                f.log_note, f.log_pending = (f"Detected {note}" if profile else note), False
                lines.append(f"{f.name}: {f.camera or 'unknown camera'} → {profile_label(f.log_profile)}"
                             + ("" if profile else f"  ({note})"))
            self.project.dirty = True
            self.refresh_all(captions=False)
            self.inspector.set_clip(self.selected_clip())
            self.viewer.refresh()
            if len(lines) == 1:
                QTimer.singleShot(0, lambda: self.lbl_status.setText(lines[0]))
            elif lines:
                QMessageBox.information(self, APP_NAME, "Camera detection:\n\n" + "\n".join(lines[:25])
                                        + (f"\n… and {len(lines) - 25} more" if len(lines) > 25 else ""))

        self.tasks.submit(name, job, done, self._task_error(name))

    # ================================================================== LUTs
    def import_lut(self, target: str, ids: Optional[list[str]] = None):
        """Import .cube files into the LUT library. target: "library", "log" (conversion for footage `ids` or
        the selected clip's footage) or "look" (creative LUT on the selected clip)."""
        paths, _ = QFileDialog.getOpenFileNames(self, "Import LUT", self.qs.value("lut_import_dir", ""),
                                                "Cube LUT or .zip (*.cube *.zip);;All files (*)")
        if not paths:
            return
        imported = self._import_lut_files(paths)
        if not imported:
            return
        lut = imported[0]
        clip = self.selected_clip()
        if target == "log":
            ids = ids or ([clip.footage_id] if clip else [])
            if ids:
                self.set_log_profile(ids, lut_profile(lut))
        elif target == "look" and clip is not None:
            self.project_checkpoint()
            clip.grade.look = lut
            self.inspector.set_clip(clip)
            self.viewer.refresh()
        self._update_title()
        self.lbl_status.setText(f"Imported {len(imported)} LUT(s)" if len(imported) > 1
                                else f"Imported LUT {lutlib.lut_name(lut)}")

    def _import_lut_files(self, paths: list[str]) -> list[str]:
        """Copy .cube files (or those inside .zip archives / folders) into the LUT library."""
        if os.path.isfile(paths[0]):
            self.qs.setValue("lut_import_dir", os.path.dirname(paths[0]))
        imported, errors = [], []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for path in paths:
                try:
                    imported += lutlib.import_any(path)
                except (lutlib.LutError, OSError, zipfile.BadZipFile) as e:
                    errors.append(f"{os.path.basename(path)}: {e}")
        finally:
            QApplication.restoreOverrideCursor()
        if errors:
            QMessageBox.warning(self, APP_NAME, "Some LUTs could not be imported:\n\n" + "\n".join(errors[:12]))
        self.inspector.reload_luts()
        if imported:   # LUT-only profiles (DJI D-Log M...) may have just got their official LUT
            self.bin.refresh()
            self.viewer.refresh()
        return imported

    def _official_luts(self) -> dict:
        """{built-in log profile: official LUT path} chosen in the official-LUT dialog (existing files only)."""
        out = {}
        for e in lutlib.OFFICIAL_LUTS:
            path = self.qs.value(f"official_lut/{e.profile}", "", type=str)
            if path and os.path.exists(path):
                out[e.profile] = path
        return out

    def _official_profile(self, profile: str) -> str:
        """The official LUT to use instead of a detected built-in profile, when the user set one up."""
        if not self.qs.value("official_lut_auto", True, type=bool) or profile in lutlib.LUT_ONLY:
            return profile   # LUT-only profiles find their official LUT by themselves
        path = self._official_luts().get(profile)
        return lut_profile(path) if path else profile

    def official_luts_dialog(self):
        dlg = OfficialLutDialog(self._official_luts(), self.qs.value("official_lut_auto", True, type=bool),
                                self._import_lut_files, self)
        accepted = dlg.exec() == OfficialLutDialog.Accepted
        self.inspector.reload_luts()   # the dialog may have downloaded LUTs either way
        self.bin.refresh()
        self.viewer.refresh()
        if not accepted:
            return
        mapping = dlg.mapping()
        for e in lutlib.OFFICIAL_LUTS:
            self.qs.setValue(f"official_lut/{e.profile}", mapping.get(e.profile, ""))
        self.qs.setValue("official_lut_auto", dlg.chk_auto.isChecked())
        lutlib.set_official_choices(self._official_luts())
        self.bin.refresh()
        self.viewer.refresh()
        if not dlg.chk_apply.isChecked():
            return
        # Footage converted with a built-in profile that now has an official LUT.
        changed = 0
        self.project_checkpoint()
        for f in self.project.footage.values():
            if f.log_profile in mapping and f.log_profile not in lutlib.LUT_ONLY:
                f.log_profile = lut_profile(mapping[f.log_profile])
                changed += 1
        self.project.dirty = changed > 0 or self.project.dirty
        self.refresh_all(captions=False)
        self.inspector.set_clip(self.selected_clip())
        self.viewer.refresh()
        self._update_title()
        self.lbl_status.setText(f"Official LUTs: {changed} footage file(s) switched" if changed
                                else "Official LUTs saved")

    # ================================================================== cache
    def _init_cache_root(self):
        path = self.qs.value("cache_dir", "", type=str)
        if not path:
            return
        set_cache_root(path)
        try:
            cache_root()
        except OSError:
            set_cache_root(None)   # keep the setting: the drive may just be disconnected
            QTimer.singleShot(0, lambda: QMessageBox.warning(
                self, APP_NAME, f"The cache folder is not available:\n{path}\n\n"
                                "The default cache folder is used for this session."))

    def cache_dialog(self):
        CacheDialog(self._change_cache_folder, self._clear_cache, self).exec()

    def _change_cache_folder(self, path: str) -> bool:
        if self.tasks.busy:
            QMessageBox.information(self, APP_NAME, "Wait for the background tasks to finish (or cancel them) "
                                                    "before changing the cache folder.")
            return False
        old = cache_root()
        new = Path(path) if path else default_cache_root()
        if os.path.normcase(str(new.resolve())) == os.path.normcase(str(old.resolve())):
            return False
        if any(new.resolve().is_relative_to((old / kind).resolve()) for kind in CACHE_KINDS):
            QMessageBox.warning(self, APP_NAME, "Choose a folder outside the current cache's sub-folders.")
            return False
        try:
            new.mkdir(parents=True, exist_ok=True)
            test = new / ".kangedit_write_test"
            test.write_bytes(b"")
            test.unlink()
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"This folder cannot be used for the cache:\n{e}")
            return False
        copyable = folder_size(old / "proxies") + folder_size(old / "analysis")
        copy_files = False
        if copyable:
            r = QMessageBox.question(
                self, APP_NAME, f"Copy the files from the current cache folder to the new one?\n\n"
                                f"From: {old}\nTo: {new}\n\nProxies and analysis, {fmt_bytes(copyable)}. The old folder "
                                f"is left as it is (delete it yourself when you no longer need it). If you don't copy, "
                                f"they are recreated in the new folder when needed.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return False
            copy_files = r == QMessageBox.Yes
        set_cache_root(path or None)
        if path:
            self.qs.setValue("cache_dir", path)
        else:
            self.qs.remove("cache_dir")
        self._schedule_audio()
        if copy_files:
            self._copy_cache(old, cache_root())
        self.lbl_status.setText(f"Cache folder: {cache_root()}")
        return True

    def _copy_cache(self, old: Path, new: Path):
        """Copy proxies and analysis from `old` into the cache folder `new` (in the background) and point the
        project's footage at the copies. The old folder is not touched."""

        def job(progress, cancel):
            files = [(kind, e) for kind in ("proxies", "analysis") if (old / kind).is_dir()
                     for e in os.scandir(old / kind) if e.is_file()]
            total = sum(e.stat().st_size for _, e in files) or 1
            done_bytes, copied = 0, 0
            for kind, e in files:
                if cancel.is_set():
                    break
                size = e.stat().st_size
                dst = new / kind / e.name
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if not (dst.exists() and dst.stat().st_size == size):   # same cache key -> same content
                        part = dst.with_name(dst.name + ".part")
                        shutil.copy2(e.path, part)
                        os.replace(part, dst)
                    copied += 1
                except OSError:
                    pass   # unreadable: recreated when needed
                done_bytes += size
                progress(done_bytes / total, f"Copying cache {copied}/{len(files)}")
            return copied

        def finish(_=None):
            self.viewer.drop_decoders()
            self._relink_cache_paths(old)
            self.refresh_all(captions=False)
            self.viewer.refresh()

        def failed(msg: str):
            finish()
            self._task_error("Copying the cache")(msg)

        self.tasks.submit("Copying cache", job, finish, failed)

    def _offer_legacy_cache(self):
        """Caches used to live in the per-user data folder; offer once to copy them next to the app."""
        if self.qs.value("cache_dir", "", type=str) or self.qs.value("legacy_cache_offered", False, type=bool):
            return
        old, new = legacy_cache_root(), cache_root()
        if not old.is_dir() or os.path.normcase(str(old.resolve())) == os.path.normcase(str(new.resolve())):
            return
        size = folder_size(old / "proxies") + folder_size(old / "analysis")
        self.qs.setValue("legacy_cache_offered", True)
        if not size:
            return
        r = QMessageBox.question(
            self, APP_NAME, f"The cache folder is now next to the app:\n{new}\n\nCopy the proxies and analysis from "
                            f"the previous cache folder ({fmt_bytes(size)})?\n{old}\n\nThe old folder is left as it is.")
        if r == QMessageBox.Yes:
            self._copy_cache(old, new)

    def _relink_cache_paths(self, old: Path):
        """Point footage at its cache files in the current cache folder after they were moved from `old`."""
        old_key = os.path.normcase(str(old.resolve()))
        for f in self.project.footage.values():
            for attr in ("proxy_path", "analysis_path"):
                p = getattr(f, attr)
                if not p:
                    continue
                pp = Path(p)
                if os.path.normcase(str(pp.parent.parent.resolve())) != old_key:
                    continue
                cand = cache_root() / pp.parent.name / pp.name
                if cand.exists():
                    setattr(f, attr, str(cand))
                    self.project.dirty = True

    def _clear_cache(self, kinds: list[str]):
        if self.tasks.busy:
            QMessageBox.information(self, APP_NAME, "Wait for the background tasks to finish (or cancel them) "
                                                    "before clearing the cache.")
            return
        if "proxies" in kinds:
            self.viewer.drop_decoders()
        if "tmp" in kinds:
            self.viewer.stop()
            self.viewer.set_audio(None)   # releases the preview audio file
        freed, locked = clear_cache(kinds)
        for f in self.project.footage.values():
            if f.proxy_path and not os.path.exists(f.proxy_path):
                f.proxy_path = None
            if f.analysis_path and not os.path.exists(f.analysis_path):
                f.analysis_path = None
        self.refresh_all(captions=False)
        if "tmp" in kinds:
            self._schedule_audio()
        self.lbl_status.setText(f"Cleared {fmt_bytes(freed)} from the cache"
                                + (f" ({locked} item(s) in use were kept)" if locked else ""))

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
        if action == "select_all":
            self.tl.select_all()
            return
        if action.startswith("sound_"):
            self._sound_action(action, clip_id)
            return
        if action.startswith(("transition:", "split:")):
            key, val = action.split(":", 1)
            ids = set(self.tl.selected_ids()) if clip_id in self.tl.selected_ids() else {clip_id}
            self.project_checkpoint()
            for x in self.project.clips:
                if x.id in ids:
                    if key == "transition":
                        x.transition = val
                        if val:
                            x.audio_split = 0.0
                    else:
                        x.audio_split = float(val)
                        if x.audio_split:
                            x.transition = ""
            self._on_timeline_edited()
            self.tl.refresh()
            self.inspector.set_clip(self.selected_clip())
            self._schedule_audio()
            self._update_title()
            return
        i = self.project.index_of(clip_id) if clip_id else -1
        if i < 0:
            return
        c = self.project.clips[i]
        # Act on the whole selection when the clip is part of it.
        sel = self.tl.selected_ids()
        targets = [x for x in self.project.clips if x.id in sel] if clip_id in sel else [c]
        if action == "delete":
            self.project_checkpoint()
            gone = {x.id for x in targets}
            self.project.clips = [x for x in self.project.clips if x.id not in gone]
            self.tl.selected = ""
            self._after_structure_change()
        elif action == "duplicate":
            self.project_checkpoint()
            for x in reversed(targets):
                d = TimelineClip.from_dict(x.to_dict())
                d.id = new_id()
                self.project.clips.insert(self.project.index_of(x.id) + 1, d)
            self._after_structure_change()
        elif action == "split":
            starts = self.project.clip_starts()
            if not (starts[i] < self.viewer.t < starts[i] + c.duration):
                self.viewer.seek(starts[i] + c.duration / 2)
            self.split_at_playhead()
        elif action == "alt_moment":
            replaced = 0
            self.project_checkpoint()
            for x in targets:
                if x.footage_id not in self.analyses:
                    continue
                alt = self._best_for(x.footage_id, x.duration, existing=list(self.project.clips))
                if alt is not None:
                    x.src_in, x.src_out, x.score = alt.src_in, alt.src_out, alt.score
                    replaced += 1
            if not replaced:
                self.lbl_status.setText("No other moment available in this footage (gap / repeat rules).")
                return
            self._after_structure_change()
        elif action == "auto_color":
            self._auto_color(targets)
        elif action == "reset_color":
            self.project_checkpoint()
            for x in targets:
                x.grade = Grade()
            self.inspector.set_clip(self.selected_clip())
            self.viewer.refresh()
            self._update_title()
        elif action == "copy_grade":
            if len(targets) > 1:
                self.project_checkpoint()
                for x in targets:
                    x.grade = Grade.from_dict(c.grade.to_dict())
                self.viewer.refresh()
                self._update_title()
            else:
                self.tl.selected = clip_id
                self.inspector.set_clip(c)
                self.inspector_request("copy_grade_all")

    # ================================================================== sound effects
    def add_sounds(self, paths: list[str], t: Optional[float] = None, names: Optional[list[str]] = None):
        """Put audio files on the SFX layer at timeline time t (default: the playhead), one after another."""
        t = self.viewer.t if t is None else t
        added, bad = [], []
        for k, path in enumerate(paths):
            if os.path.normcase(os.path.dirname(os.path.abspath(path))) != os.path.normcase(str(sfx_dir())):
                try:
                    path = sfx.import_file(path)     # keep projects working if the original moves
                except OSError as e:
                    bad.append(f"{os.path.basename(path)}: {e}")
                    continue
            dur = sfx.duration(path)
            if dur <= 0:
                bad.append(f"{os.path.basename(path)}: not a readable audio file")
                continue
            name = names[k] if names and k < len(names) else os.path.splitext(os.path.basename(path))[0]
            added.append(SoundClip(path, round(t, 3), 0.0, round(dur, 3), 1.0, name))
            t += dur
        if bad:
            QMessageBox.warning(self, APP_NAME, "Some sounds could not be added:\n\n" + "\n".join(bad[:10]))
        if not added:
            return
        self.project_checkpoint()
        self.project.sounds += added
        self.sfx_panel.refresh_library()
        self.tl.selected_sound = added[-1].id
        self.tl.refresh()
        self._sounds_changed()
        self.lbl_status.setText(f"Added sound “{added[0].name}” at {fmt_time(added[0].start, frames=False)}"
                                if len(added) == 1 else f"Added {len(added)} sounds")

    def _sounds_changed(self):
        self.project.dirty = True
        self._schedule_audio()
        self._update_title()

    def _sound_action(self, action: str, sound_id: str):
        x = next((s for s in self.project.sounds if s.id == sound_id), None)
        if x is None:
            return
        if action == "sound_preview":
            self.sfx_panel.preview_file(x.path)
            return
        self.project_checkpoint()
        if action == "sound_delete":
            self.project.sounds.remove(x)
            self.tl.selected_sound = ""
        elif action == "sound_to_playhead":
            x.start = round(self.viewer.t, 3)
        elif action.startswith("sound_volume:"):
            x.volume = float(action.split(":", 1)[1])
        self.tl.refresh()
        self._sounds_changed()

    def _delete_selected(self):
        if self.tl.selected_sound:
            self._sound_action("sound_delete", self.tl.selected_sound)
        else:
            self.clip_action("delete", self.tl.selected)

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
        if req in ("import_lut_log", "import_lut_look"):
            self.import_lut(req.rsplit("_", 1)[1])
            return
        if req == "detect_log":
            if clip is not None:
                self.detect_log([clip.footage_id])
            return
        if req == "detect_log_all":
            self.detect_log(list(self.project.footage))
            return
        if req == "auto_color_all":
            self._auto_color(list(self.project.clips))
            return
        elif clip is None:
            return
        elif req == "auto_color":
            self._auto_color([clip])
            return
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

    def _auto_color(self, clips: list[TimelineClip]):
        """Auto-grade clips in the background (progress + Cancel in the status bar); applied in one undo step."""
        jobs = []
        for c in clips:
            f = self.project.footage.get(c.footage_id)
            if f is not None:
                probe_clip = TimelineClip(c.footage_id, c.src_in, c.src_out, id=c.id)
                jobs.append((probe_clip, f.log_profile, f.tonemap, Grade.from_dict(c.grade.to_dict())))
        if not jobs:
            return
        use_proxies = self.project.settings.use_proxies
        footage = dict(self.project.footage)
        name = "Auto color" if len(jobs) == 1 else f"Auto color {len(jobs)} clips"

        def job(progress, cancel):
            pool = DecoderPool(limit=4)   # own decoders: don't disturb the viewer's
            try:
                grades = {}
                for k, (c, lp, tm, base) in enumerate(jobs):
                    if cancel.is_set():
                        raise Cancelled()
                    progress(k / len(jobs), f"{name}: {k + 1}/{len(jobs)}")
                    dec = pool.get(footage[c.footage_id], use_proxies, 360)
                    frames = []
                    for i in range(5):
                        img = dec.frame_at(c.src_in + c.duration * (i + 0.5) / 5)
                        if img is not None:
                            frames.append(img.copy())
                    grades[c.id] = auto_correct(frames, lp, tm, base=base)
                progress(1.0, name)
                return grades
            finally:
                pool.close()

        def done(grades: dict):
            targets = [(c, grades[c.id]) for c in self.project.clips if c.id in grades]
            if not targets:
                return
            self.project_checkpoint()
            for c, g in targets:
                c.grade = g
            self.project.dirty = True
            self.inspector.set_clip(self.selected_clip())
            self.viewer.refresh()
            self._update_title()

        self.tasks.submit(name, job, done, self._task_error(name))

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
            write_srt(path, self.captions, self.project.settings.captions, project_layout(self.project))
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
        dlg = ExportDialog(self.project, bool(self.captions), self, self.qs.value("gpu_export", True, type=bool))
        if dlg.exec() != ExportDialog.Accepted:
            return
        es = dlg.settings()
        self.qs.setValue("gpu_export", es.use_gpu)
        proj = self._clone_project()

        def done(path):
            r = QMessageBox.information(self, APP_NAME, f"Export finished:\n{path}",
                                        QMessageBox.Open | QMessageBox.Ok)
            if r == QMessageBox.Open:
                open_path(path)

        self.tasks.submit(f"Exporting {os.path.basename(es.output)}",
                          lambda p, c: export(proj, es, p, c), done, self._task_error("Export"))

    def _clone_project(self) -> Project:
        """Frozen copy so edits during a long export don't change what is rendered."""
        p = Project()
        p.settings = copy.deepcopy(self.project.settings)
        p.footage = {fid: copy.deepcopy(f) for fid, f in self.project.footage.items()}
        p.clips = [TimelineClip.from_dict(c.to_dict()) for c in self.project.clips]
        p.sounds = [SoundClip.from_dict(x.to_dict()) for x in self.project.sounds]
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
            "S — split at playhead\nDel — delete the selected clips (or sound)\nCtrl+D — duplicate clip\n"
            "Ctrl+A — select all clips\nR — replace clip with another best moment\n"
            "Ctrl+Shift+− / Ctrl+Shift+= — regenerate the auto edit shorter / longer\n"
            "Ctrl+Z / Ctrl+Shift+Z — undo / redo\nShift+Z — fit timeline\n"
            "Ctrl+Shift+B — color adjustments in the viewer on / off\n"
            "Ctrl+wheel on timeline — zoom · on the Media thumbnails — thumbnail size\n\n"
            "Timeline: drag a clip to reorder · drag its edges to trim ·\n"
            "Alt/Shift+drag (or Slip mode) to slide the moment inside the clip ·\n"
            "Ctrl+click adds a clip to the selection · Shift+click selects a range ·\n"
            "SFX layer: drag sounds to move, edges to trim, right-click for volume"))

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        self.viewer.stop()
        self.tasks.shutdown()
        self._audio_cancel.set()
        self.viewer.shutdown()
        e.accept()


def _same_moment(a: TimelineClip, b: TimelineClip) -> bool:
    return a.footage_id == b.footage_id and a.src_in < b.src_out and b.src_in < a.src_out
