"""Dialogs: auto edit, export, caption style and proxy settings."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFontComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QRadioButton,
                               QSpinBox, QVBoxLayout, QWidget)

from ..autoedit import FORMAT_PRESETS, ORDERS, STYLE_LABELS, AutoEditSettings
from ..captions import LANGUAGES, WHISPER_MODELS
from ..config import ASPECT_RATIOS, FRAME_RATES, RESOLUTIONS, frame_size
from ..export import CAPTION_MODES, CODECS, QUALITIES, ExportSettings
from ..models import CaptionStyle, Project

FIT_MODES = {"fill": "Fill (crop to frame)", "fit": "Fit (black bars)", "blur": "Fit with blurred background"}


def _combo(items: dict, current) -> QComboBox:
    cb = QComboBox()
    for k, v in items.items():
        cb.addItem(v, k)
    idx = cb.findData(current)
    cb.setCurrentIndex(max(0, idx))
    return cb


class AutoEditDialog(QDialog):
    def __init__(self, project: Project, selected_ids: list[str], last: AutoEditSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Auto Edit — find the best moments")
        self.setMinimumWidth(520)
        self.project = project
        lay = QVBoxLayout(self)

        # --- Format
        g = QGroupBox("Video format")
        v = QVBoxLayout(g)
        self.fmt_group = QButtonGroup(self)
        row = QHBoxLayout()
        self.fmt_buttons = {}
        for key, preset in FORMAT_PRESETS.items():
            rb = QRadioButton(preset["label"])
            self.fmt_group.addButton(rb)
            self.fmt_buttons[key] = rb
            rb.toggled.connect(lambda on, k=key: on and self._apply_format(k))
            row.addWidget(rb)
        v.addLayout(row)
        self.lbl_hint = QLabel("")
        self.lbl_hint.setStyleSheet("color: #8b909a;")
        self.lbl_hint.setWordWrap(True)
        v.addWidget(self.lbl_hint)
        self.chk_aspect = QCheckBox("Also switch the project frame to the format's aspect ratio")
        self.chk_aspect.setChecked(True)
        v.addWidget(self.chk_aspect)
        lay.addWidget(g)

        # --- Timing
        g = QGroupBox("Timing")
        f = QFormLayout(g)
        self.sp_target = QDoubleSpinBox(decimals=0, suffix=" s")
        self.sp_target.setRange(5, 3600)
        self.sp_min = QDoubleSpinBox(decimals=1, singleStep=0.5, suffix=" s")
        self.sp_min.setRange(0.5, 60)
        self.sp_max = QDoubleSpinBox(decimals=1, singleStep=0.5, suffix=" s")
        self.sp_max.setRange(0.5, 120)
        self.sp_gap = QDoubleSpinBox(decimals=1, singleStep=0.5, suffix=" s")
        self.sp_gap.setRange(0, 600)
        self.sp_gap.setToolTip("When the same footage is used more than once, its segments are at least this far "
                               "apart in the source and never overlap.")
        self.sp_uses = QSpinBox()
        self.sp_uses.setRange(0, 100)
        self.sp_uses.setSpecialValueText("Unlimited")
        clip_row = QHBoxLayout()
        clip_row.addWidget(self.sp_min)
        clip_row.addWidget(QLabel("to"))
        clip_row.addWidget(self.sp_max)
        f.addRow("Target length", self.sp_target)
        f.addRow("Clip length", clip_row)
        f.addRow("Gap between reuses", self.sp_gap)
        f.addRow("Max uses per footage", self.sp_uses)
        lay.addWidget(g)

        # --- Selection
        g = QGroupBox("Selection")
        f = QFormLayout(g)
        self.cb_style = _combo(STYLE_LABELS, last.style)
        self.cb_order = _combo(ORDERS, last.order)
        self.chk_every = QCheckBox("Use every footage at least once (if it fits)")
        self.chk_every.setChecked(last.use_every_footage)
        self.chk_replace = QCheckBox("Replace current timeline (otherwise append)")
        self.chk_replace.setChecked(last.replace_timeline)
        f.addRow("Prioritize", self.cb_style)
        f.addRow("Clip order", self.cb_order)
        f.addRow(self.chk_every)
        f.addRow(self.chk_replace)
        lay.addWidget(g)

        # --- Footage
        g = QGroupBox("Footage to use")
        v = QVBoxLayout(g)
        self.list = QListWidget()
        self.list.setMaximumHeight(130)
        use_ids = set(selected_ids) if len(selected_ids) > 1 else set(project.footage)
        for f_ in project.footage.values():
            it = QListWidgetItem(f"{f_.name}  ({f_.duration:.0f}s)")
            it.setData(Qt.UserRole, f_.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if f_.id in use_ids else Qt.Unchecked)
            self.list.addItem(it)
        v.addWidget(self.list)
        lay.addWidget(g)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Create edit")
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self.fmt_buttons.get(last.video_format, self.fmt_buttons["short"]).setChecked(True)
        # Restore the user's last values (they may have fine-tuned the preset).
        self.sp_target.setValue(last.target_duration)
        self.sp_min.setValue(last.clip_min)
        self.sp_max.setValue(last.clip_max)
        self.sp_gap.setValue(last.min_gap)
        self.sp_uses.setValue(last.max_uses)
        self.cb_order.setCurrentIndex(max(0, self.cb_order.findData(last.order)))

    def _apply_format(self, key: str):
        preset = FORMAT_PRESETS[key]
        self.lbl_hint.setText(preset["hint"])
        self.chk_aspect.setEnabled(preset["aspect"] is not None)
        s = preset["settings"]
        if not s:
            return
        self.sp_target.setValue(s["target_duration"])
        self.sp_min.setValue(s["clip_min"])
        self.sp_max.setValue(s["clip_max"])
        self.sp_gap.setValue(s["min_gap"])
        self.cb_order.setCurrentIndex(max(0, self.cb_order.findData(s["order"])))

    def format_key(self) -> str:
        for k, b in self.fmt_buttons.items():
            if b.isChecked():
                return k
        return "custom"

    def footage_ids(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def aspect(self):
        a = FORMAT_PRESETS[self.format_key()]["aspect"]
        return a if (a and self.chk_aspect.isChecked()) else None

    def settings(self) -> AutoEditSettings:
        lo, hi = sorted((self.sp_min.value(), self.sp_max.value()))
        return AutoEditSettings(
            video_format=self.format_key(),
            target_duration=self.sp_target.value(),
            clip_min=lo,
            clip_max=hi,
            min_gap=self.sp_gap.value(),
            max_uses=self.sp_uses.value(),
            use_every_footage=self.chk_every.isChecked(),
            order=self.cb_order.currentData(),
            style=self.cb_style.currentData(),
            replace_timeline=self.chk_replace.isChecked(),
        )


class ExportDialog(QDialog):
    def __init__(self, project: Project, has_captions: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export video")
        self.setMinimumWidth(500)
        self.project = project
        s = project.settings
        lay = QVBoxLayout(self)
        form = QFormLayout()

        self.ed_out = QLineEdit()
        base = os.path.splitext(project.path)[0] if project.path else os.path.join(os.path.expanduser("~"), "Videos", "kangedit_export")
        self.ed_out.setText(base + ".mp4")
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.ed_out, 1)
        row.addWidget(b)
        form.addRow("Save to", row)

        self.cb_aspect = _combo({k: k for k in ASPECT_RATIOS}, s.aspect)
        self.cb_res = _combo({k: k for k in RESOLUTIONS}, s.resolution)
        self.cb_fps = QComboBox()
        for r in FRAME_RATES:
            self.cb_fps.addItem(f"{r:g} fps", r)
        self.cb_fps.setCurrentIndex(min(range(len(FRAME_RATES)), key=lambda i: abs(FRAME_RATES[i] - s.fps)))
        self.cb_fit = _combo(FIT_MODES, s.fit_mode)
        self.cb_codec = _combo({k: v[0] for k, v in CODECS.items()}, "h264")
        self.cb_quality = _combo({k: k for k in QUALITIES}, "High")
        self.cb_caps = _combo(CAPTION_MODES, "burn" if has_captions and s.captions.enabled else "none")
        self.cb_caps.setEnabled(has_captions)
        self.chk_proxy = QCheckBox("Draft export from proxies (fast, lower quality)")
        self.lbl_size = QLabel("")
        self.lbl_size.setStyleSheet("color: #8b909a;")
        for cb in (self.cb_aspect, self.cb_res):
            cb.currentIndexChanged.connect(self._update)
        self.cb_codec.currentIndexChanged.connect(self._codec_changed)
        form.addRow("Aspect ratio", self.cb_aspect)
        form.addRow("Resolution", self.cb_res)
        form.addRow("", self.lbl_size)
        form.addRow("Frame rate", self.cb_fps)
        form.addRow("Reframe", self.cb_fit)
        form.addRow("Format", self.cb_codec)
        form.addRow("Quality", self.cb_quality)
        form.addRow("Captions", self.cb_caps)
        form.addRow(self.chk_proxy)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Export")
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._update()

    def _update(self):
        w, h = frame_size(self.cb_aspect.currentData(), self.cb_res.currentData())
        self.lbl_size.setText(f"Output {w} × {h}, duration {self.project.duration:.1f} s")

    def _codec_changed(self):
        ext = CODECS[self.cb_codec.currentData()][1]
        self.ed_out.setText(os.path.splitext(self.ed_out.text())[0] + ext)
        self.cb_quality.setEnabled(self.cb_codec.currentData() != "prores")

    def _browse(self):
        ext = CODECS[self.cb_codec.currentData()][1]
        path, _ = QFileDialog.getSaveFileName(self, "Export video", self.ed_out.text(), f"Video (*{ext})")
        if path:
            self.ed_out.setText(path)

    def _accept(self):
        if not self.ed_out.text().strip():
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.ed_out.text())), exist_ok=True)
        self.accept()

    def settings(self) -> ExportSettings:
        return ExportSettings(
            output=self.ed_out.text().strip(),
            aspect=self.cb_aspect.currentData(),
            resolution=self.cb_res.currentData(),
            fps=float(self.cb_fps.currentData()),
            fit_mode=self.cb_fit.currentData(),
            codec=self.cb_codec.currentData(),
            quality=self.cb_quality.currentData(),
            captions=self.cb_caps.currentData() if self.cb_caps.isEnabled() else "none",
            use_proxies=self.chk_proxy.isChecked(),
        )


class CaptionStyleDialog(QDialog):
    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Captions")
        self.project = project
        st = project.settings.captions
        lay = QVBoxLayout(self)

        g = QGroupBox("Speech recognition (Whisper, offline)")
        f = QFormLayout(g)
        self.cb_model = QComboBox()
        for m in WHISPER_MODELS:
            self.cb_model.addItem(m, m)
        self.cb_model.setCurrentIndex(max(0, self.cb_model.findData(project.settings.whisper_model)))
        self.cb_model.setToolTip("tiny/base: fastest · small: good balance · medium/large: most accurate, slower.\n"
                                 "The model is downloaded once on first use.")
        self.cb_lang = QComboBox()
        for code, name in LANGUAGES:
            self.cb_lang.addItem(name, code)
        self.cb_lang.setCurrentIndex(max(0, self.cb_lang.findData(project.settings.whisper_language)))
        f.addRow("Model", self.cb_model)
        f.addRow("Language", self.cb_lang)
        lay.addWidget(g)

        g = QGroupBox("Style")
        f = QFormLayout(g)
        self.cb_font = QFontComboBox()
        self.cb_font.setCurrentText(st.font)
        self.sp_size = QDoubleSpinBox(decimals=1, singleStep=0.5, suffix=" % of height")
        self.sp_size.setRange(2, 15)
        self.sp_size.setValue(st.size_pct)
        self.cb_pos = _combo({"bottom": "Bottom", "middle": "Middle", "top": "Top"}, st.position)
        self.sp_chars = QSpinBox()
        self.sp_chars.setRange(8, 80)
        self.sp_chars.setValue(st.max_chars)
        self.sp_lines = QSpinBox()
        self.sp_lines.setRange(1, 3)
        self.sp_lines.setValue(st.max_lines)
        self.sp_dur = QDoubleSpinBox(decimals=1, suffix=" s")
        self.sp_dur.setRange(0.5, 10)
        self.sp_dur.setValue(st.max_duration)
        self.chk_upper = QCheckBox("UPPERCASE")
        self.chk_upper.setChecked(st.uppercase)
        self.chk_outline = QCheckBox("Outline")
        self.chk_outline.setChecked(st.outline)
        self.chk_box = QCheckBox("Background box")
        self.chk_box.setChecked(st.box)
        self.color = st.color
        self.b_color = QPushButton()
        self.b_color.clicked.connect(self._pick_color)
        self._paint_color()
        presets = QHBoxLayout()
        for name, cfg in (("Social (big, 1 line)", dict(size=8.0, chars=18, lines=1, dur=1.6, upper=True, pos="middle")),
                          ("Classic subtitles", dict(size=5.0, chars=42, lines=2, dur=5.0, upper=False, pos="bottom"))):
            b = QPushButton(name)
            b.clicked.connect(lambda _=False, c=cfg: self._preset(c))
            presets.addWidget(b)
        f.addRow("Presets", presets)
        f.addRow("Font", self.cb_font)
        f.addRow("Size", self.sp_size)
        f.addRow("Position", self.cb_pos)
        f.addRow("Max chars / line", self.sp_chars)
        f.addRow("Max lines", self.sp_lines)
        f.addRow("Max duration", self.sp_dur)
        f.addRow("Text color", self.b_color)
        row = QHBoxLayout()
        row.addWidget(self.chk_upper)
        row.addWidget(self.chk_outline)
        row.addWidget(self.chk_box)
        f.addRow(row)
        lay.addWidget(g)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _preset(self, c):
        self.sp_size.setValue(c["size"])
        self.sp_chars.setValue(c["chars"])
        self.sp_lines.setValue(c["lines"])
        self.sp_dur.setValue(c["dur"])
        self.chk_upper.setChecked(c["upper"])
        self.cb_pos.setCurrentIndex(self.cb_pos.findData(c["pos"]))

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Caption color")
        if c.isValid():
            self.color = c.name()
            self._paint_color()

    def _paint_color(self):
        self.b_color.setText(self.color)
        self.b_color.setStyleSheet(f"background: {self.color}; color: black;")

    def apply(self):
        s = self.project.settings
        s.whisper_model = self.cb_model.currentData()
        s.whisper_language = self.cb_lang.currentData()
        st = s.captions
        st.font = self.cb_font.currentText()
        st.size_pct = self.sp_size.value()
        st.position = self.cb_pos.currentData()
        st.max_chars = self.sp_chars.value()
        st.max_lines = self.sp_lines.value()
        st.max_duration = self.sp_dur.value()
        st.uppercase = self.chk_upper.isChecked()
        st.outline = self.chk_outline.isChecked()
        st.box = self.chk_box.isChecked()
        st.color = self.color


class ProxyDialog(QDialog):
    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Proxies")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Proxies are small H.264 copies used for smooth playback and faster\n"
                             "analysis. Export always uses the original files unless you pick a draft export."))
        f = QFormLayout()
        self.cb_res = QComboBox()
        for h in (360, 540, 720, 1080):
            self.cb_res.addItem(f"{h}p", h)
        self.cb_res.setCurrentIndex(max(0, self.cb_res.findData(project.settings.proxy_height)))
        self.chk_all = QCheckBox("Generate for all footage without a proxy now")
        self.chk_all.setChecked(True)
        f.addRow("Proxy resolution", self.cb_res)
        f.addRow(self.chk_all)
        lay.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
