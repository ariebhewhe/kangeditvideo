"""Inspector: trim points, log conversion, color correction, reframing and audio for one clip."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QScrollArea, QSlider, QToolButton, QVBoxLayout, QWidget)

from .. import lut as lutlib
from ..color import LOG_CHOICES, LOG_PROFILES, TONEMAPS, effective_profile, lut_profile, profile_label
from ..edits import TRANSITIONS
from ..models import Project, TimelineClip


class ParamSlider(QWidget):
    """Slider + spinbox pair working in float units."""

    changed = Signal(float)

    def __init__(self, lo: float, hi: float, default: float, step: float = 1.0, decimals: int = 0, suffix: str = ""):
        super().__init__()
        self.default = default
        self.scale = 1.0 / step
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(lo * self.scale), int(hi * self.scale))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(step)
        self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(74)
        self.btn_reset = QToolButton()
        self.btn_reset.setText("↺")
        self.btn_reset.setToolTip(f"Reset to {default:g}{suffix}")
        self.btn_reset.setAutoRaise(True)
        self.btn_reset.setFixedWidth(22)
        self.btn_reset.clicked.connect(self.reset)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        lay.addWidget(self.btn_reset)
        self.set_value(default)

    def reset(self):
        if abs(self.value() - self.default) > 1e-9:
            self.set_value(self.default, emit=True)

    def mouseDoubleClickEvent(self, _):
        self.reset()

    def _sync_reset(self):
        self.btn_reset.setEnabled(abs(self.spin.value() - self.default) > 1e-9)

    def _from_slider(self, v):
        val = v / self.scale
        self.spin.blockSignals(True)
        self.spin.setValue(val)
        self.spin.blockSignals(False)
        self._sync_reset()
        self.changed.emit(val)

    def _from_spin(self, val):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(val * self.scale)))
        self.slider.blockSignals(False)
        self._sync_reset()
        self.changed.emit(val)

    def set_value(self, val: float, emit: bool = False):
        for w in (self.slider, self.spin):
            w.blockSignals(True)
        self.slider.setValue(int(round(val * self.scale)))
        self.spin.setValue(val)
        for w in (self.slider, self.spin):
            w.blockSignals(False)
        self._sync_reset()
        if emit:
            self.changed.emit(val)

    def value(self) -> float:
        return self.spin.value()


GRADE_PARAMS = [
    ("exposure", "Exposure", -4, 4, 0, 0.05, 2, " EV"),
    ("contrast", "Contrast", -100, 100, 0, 1, 0, ""),
    ("highlights", "Highlights", -100, 100, 0, 1, 0, ""),
    ("shadows", "Shadows", -100, 100, 0, 1, 0, ""),
    ("temperature", "Temperature", -100, 100, 0, 1, 0, ""),
    ("tint", "Tint", -100, 100, 0, 1, 0, ""),
    ("saturation", "Saturation", 0, 200, 100, 1, 0, " %"),
    ("vibrance", "Vibrance", -100, 100, 0, 1, 0, ""),
]


IMPORT_LUT = "__import_lut__"


def fill_lut_combo(cb: QComboBox, base: list[tuple[str, str]], key_of=lambda path: path) -> None:
    """Fixed choices, then every library LUT, then an "Import LUT..." entry."""
    cb.blockSignals(True)
    cb.clear()
    for key, label in base:
        cb.addItem(label, key)
    luts = lutlib.library()
    if luts:
        cb.insertSeparator(cb.count())
        for name, path in luts:
            cb.addItem(f"LUT: {name}", key_of(path))
    cb.insertSeparator(cb.count())
    cb.addItem("Import LUT (.cube)…", IMPORT_LUT)
    cb.blockSignals(False)


def select_data(cb: QComboBox, key: str, label: str) -> None:
    """Select `key`, adding it first if the combo doesn't list it (e.g. a LUT from another machine)."""
    idx = cb.findData(key)
    if idx < 0:
        cb.insertItem(cb.count() - 2, label, key)
        idx = cb.findData(key)
    cb.setCurrentIndex(idx)


class Inspector(QScrollArea):
    # kind: "trim" | "grade" | "footage_color" | "reframe" | "audio"
    before_change = Signal(str)
    changed = Signal(str)
    # auto_color | auto_color_all | copy_grade_all | reset_color | detect_log | detect_log_all
    # | import_lut_log | import_lut_look
    request = Signal(str)

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.clip: Optional[TimelineClip] = None
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 6, 6, 6)

        self.lbl_title = QLabel("Select a clip on the timeline")
        self.lbl_title.setStyleSheet("font-weight: 600; font-size: 13px;")
        self.lbl_title.setWordWrap(True)
        lay.addWidget(self.lbl_title)

        # --- Clip timing
        g = QGroupBox("Clip")
        f = QFormLayout(g)
        self.sp_in = QDoubleSpinBox(decimals=3, singleStep=0.04, suffix=" s")
        self.sp_out = QDoubleSpinBox(decimals=3, singleStep=0.04, suffix=" s")
        self.lbl_dur = QLabel("")
        self.sp_in.setRange(0, 1e6)
        self.sp_out.setRange(0, 1e6)
        self.sp_in.valueChanged.connect(lambda v: self._set_trim("in", v))
        self.sp_out.valueChanged.connect(lambda v: self._set_trim("out", v))
        f.addRow("Source in", self.sp_in)
        f.addRow("Source out", self.sp_out)
        f.addRow("Duration", self.lbl_dur)
        lay.addWidget(g)

        # --- Edit into this clip (from the previous one)
        g = QGroupBox("Edit into this clip")
        f = QFormLayout(g)
        self.cb_trans = QComboBox()
        self.cb_trans.addItem("None (cut)", "")
        for key, (label, _) in TRANSITIONS.items():
            self.cb_trans.addItem(label, key)
        self.sp_tdur = QDoubleSpinBox(decimals=2, singleStep=0.1, suffix=" s")
        self.sp_tdur.setRange(0.1, 3.0)
        self.sp_tdur.setToolTip("Centered on the cut; shortened automatically when the footage has no "
                                "spare frames before/after the clips")
        self.sp_split = QDoubleSpinBox(decimals=2, singleStep=0.1, suffix=" s")
        self.sp_split.setRange(-3.0, 3.0)
        self.sp_split.setToolTip("Move the audio cut:\n  negative = J-cut (this clip's sound starts early, "
                                 "under the previous picture)\n  positive = L-cut (the previous clip's sound "
                                 "runs on under this picture)")
        self.cb_trans.currentIndexChanged.connect(self._set_edit)
        self.sp_tdur.valueChanged.connect(self._set_edit)
        self.sp_split.valueChanged.connect(self._set_edit)
        f.addRow("Transition", self.cb_trans)
        f.addRow("Length", self.sp_tdur)
        f.addRow("Audio J / L", self.sp_split)
        self.grp_edit = g
        lay.addWidget(g)

        # --- Log conversion (per footage)
        g = QGroupBox("Log → Rec.709 (applies to the whole footage)")
        f = QFormLayout(g)
        self.lbl_camera = QLabel("")
        self.lbl_camera.setWordWrap(True)
        self.lbl_camera.setStyleSheet("color: #9aa4b2;")
        b_detect = QPushButton("Detect")
        b_detect.setToolTip("Identify the camera from the file's metadata and picture, and choose its log profile")
        b_detect.clicked.connect(lambda: self.request.emit("detect_log"))
        b_detect_all = QPushButton("Detect all")
        b_detect_all.setToolTip("Identify the camera of every imported file and choose each one's log profile")
        b_detect_all.clicked.connect(lambda: self.request.emit("detect_log_all"))
        cam_row = QHBoxLayout()
        cam_row.addWidget(self.lbl_camera, 1)
        cam_row.addWidget(b_detect)
        cam_row.addWidget(b_detect_all)
        self.cb_log = QComboBox()
        self.cb_log.setToolTip("Built-in camera conversion, or an imported .cube LUT that converts log to Rec.709")
        self.cb_tone = QComboBox()
        for key, label in TONEMAPS:
            self.cb_tone.addItem(label, key)
        self.cb_log.currentIndexChanged.connect(self._footage_color)
        self.cb_tone.currentIndexChanged.connect(self._footage_color)
        f.addRow("Camera", cam_row)
        f.addRow("Camera profile", self.cb_log)
        f.addRow("Conversion", self.cb_tone)
        lay.addWidget(g)

        # --- Color correction
        g = QGroupBox("Exposure && color")
        v = QVBoxLayout(g)
        btns = QGridLayout()
        b_auto = QPushButton("✨ Auto")
        b_auto.setObjectName("primary")
        b_auto.setToolTip("Analyze this clip and set exposure, white balance and contrast automatically")
        b_auto.clicked.connect(lambda: self.request.emit("auto_color"))
        b_auto_all = QPushButton("✨ Auto all clips")
        b_auto_all.clicked.connect(lambda: self.request.emit("auto_color_all"))
        b_reset = QPushButton("Reset")
        b_reset.clicked.connect(lambda: self.request.emit("reset_color"))
        b_copy = QPushButton("Copy to all clips")
        b_copy.clicked.connect(lambda: self.request.emit("copy_grade_all"))
        btns.addWidget(b_auto, 0, 0)
        btns.addWidget(b_auto_all, 0, 1)
        btns.addWidget(b_reset, 1, 0)
        btns.addWidget(b_copy, 1, 1)
        v.addLayout(btns)
        form = QFormLayout()
        self.params: dict[str, ParamSlider] = {}
        for key, label, lo, hi, dflt, step, dec, suf in GRADE_PARAMS:
            ps = ParamSlider(lo, hi, dflt, step, dec, suf)
            ps.setToolTip("Double-click to reset")
            ps.changed.connect(lambda val, k=key: self._set_grade(k, val))
            self.params[key] = ps
            form.addRow(label, ps)
        self.cb_look = QComboBox()
        self.cb_look.setToolTip("Creative color grading LUT (.cube), applied after the adjustments above")
        self.cb_look.currentIndexChanged.connect(self._set_look)
        self.look_mix = ParamSlider(0, 100, 100, 1, 0, " %")
        self.look_mix.setToolTip("How strongly the look LUT is applied. Double-click to reset")
        self.look_mix.changed.connect(lambda val: self._set_grade("look_mix", val))
        form.addRow("Look (LUT)", self.cb_look)
        form.addRow("Look strength", self.look_mix)
        v.addLayout(form)
        lay.addWidget(g)

        # --- Reframe
        g = QGroupBox("Reframe (crop position for the export ratio)")
        f = QFormLayout(g)
        self.rf_zoom = ParamSlider(1.0, 4.0, 1.0, 0.01, 2, "×")
        self.rf_x = ParamSlider(-1.0, 1.0, 0.0, 0.01, 2)
        self.rf_y = ParamSlider(-1.0, 1.0, 0.0, 0.01, 2)
        for ps, key in ((self.rf_zoom, "zoom"), (self.rf_x, "offset_x"), (self.rf_y, "offset_y")):
            ps.changed.connect(lambda val, k=key: self._set_attr(k, val, "reframe"))
        f.addRow("Zoom", self.rf_zoom)
        f.addRow("Pan X", self.rf_x)
        f.addRow("Pan Y", self.rf_y)
        lay.addWidget(g)

        # --- Audio
        g = QGroupBox("Audio")
        f = QFormLayout(g)
        self.vol = ParamSlider(0, 200, 100, 1, 0, " %")
        self.vol.changed.connect(lambda val: self._set_attr("volume", val / 100.0, "audio"))
        f.addRow("Volume", self.vol)
        lay.addWidget(g)
        lay.addStretch(1)
        self.reload_luts()

    def reload_luts(self):
        """Refresh the LUT lists (after an import) and re-select the current clip's values."""
        fill_lut_combo(self.cb_log, LOG_CHOICES, lut_profile)
        fill_lut_combo(self.cb_look, [("", "None")])
        self.set_clip(self.clip)

    def set_project(self, project: Project):
        self.project = project
        self.set_clip(None)

    def set_clip(self, clip: Optional[TimelineClip]):
        self.clip = clip
        self.widget().setEnabled(clip is not None)
        if clip is None:
            self.lbl_title.setText("Select a clip on the timeline")
            return
        f = self.project.footage[clip.footage_id]
        score = f"  ·  score {clip.score * 100:.0f}" if clip.score is not None else ""
        self.lbl_title.setText(f"{f.name}{score}")
        for w in (self.sp_in, self.sp_out, self.cb_log, self.cb_tone, self.cb_look, self.cb_trans, self.sp_tdur,
                  self.sp_split):
            w.blockSignals(True)
        self.cb_trans.setCurrentIndex(max(0, self.cb_trans.findData(clip.transition)))
        self.sp_tdur.setValue(clip.transition_dur)
        self.sp_split.setValue(clip.audio_split)
        self.grp_edit.setEnabled(self.project.index_of(clip.id) > 0)
        self.sp_in.setMaximum(f.duration)
        self.sp_out.setMaximum(f.duration)
        self.sp_in.setValue(clip.src_in)
        self.sp_out.setValue(clip.src_out)
        select_data(self.cb_log, f.log_profile, profile_label(f.log_profile))
        self.cb_tone.setCurrentIndex(max(0, self.cb_tone.findData(f.tonemap)))
        self.cb_tone.setEnabled(effective_profile(f.log_profile) in LOG_PROFILES)
        select_data(self.cb_look, clip.grade.look, f"LUT: {lutlib.lut_name(clip.grade.look)}")
        self.look_mix.setEnabled(bool(clip.grade.look))
        self.look_mix.set_value(clip.grade.look_mix)
        for w in (self.sp_in, self.sp_out, self.cb_log, self.cb_tone, self.cb_look, self.cb_trans, self.sp_tdur,
                  self.sp_split):
            w.blockSignals(False)
        self._show_camera(f)
        self.lbl_dur.setText(f"{clip.duration:.2f} s")
        for key, ps in self.params.items():
            ps.set_value(getattr(clip.grade, key))
        self.rf_zoom.set_value(clip.zoom)
        self.rf_x.set_value(clip.offset_x)
        self.rf_y.set_value(clip.offset_y)
        self.vol.set_value(clip.volume * 100.0)

    def _set_trim(self, which: str, v: float):
        c = self.clip
        if c is None:
            return
        f = self.project.footage[c.footage_id]
        self.before_change.emit("trim")
        if which == "in":
            c.src_in = min(max(0.0, v), c.src_out - 0.25)
        else:
            c.src_out = max(min(f.duration, v), c.src_in + 0.25)
        self.lbl_dur.setText(f"{c.duration:.2f} s")
        self.changed.emit("trim")

    def _set_edit(self):
        c = self.clip
        if c is None:
            return
        self.before_change.emit("trim:edit")
        c.transition = self.cb_trans.currentData() or ""
        c.transition_dur = self.sp_tdur.value()
        c.audio_split = self.sp_split.value()
        self.changed.emit("trim")

    def _footage_color(self):
        c = self.clip
        if c is None:
            return
        f = self.project.footage[c.footage_id]
        if self.cb_log.currentData() == IMPORT_LUT:
            self.set_clip(c)   # put the previous choice back; the import selects the new LUT
            self.request.emit("import_lut_log")
            return
        self.before_change.emit("footage_color")
        if f.log_profile != self.cb_log.currentData():
            f.log_profile = self.cb_log.currentData()
            f.log_note, f.log_pending = "Chosen manually", False
        f.tonemap = self.cb_tone.currentData()
        self.cb_tone.setEnabled(effective_profile(f.log_profile) in LOG_PROFILES)
        self._show_camera(f)
        self.changed.emit("footage_color")

    def _show_camera(self, f):
        self.lbl_camera.setText((f.camera or "Not identified") + (f"\n{f.log_note}" if f.log_note else ""))

    def _set_look(self):
        c = self.clip
        if c is None:
            return
        if self.cb_look.currentData() == IMPORT_LUT:
            self.set_clip(c)
            self.request.emit("import_lut_look")
            return
        self.before_change.emit("grade:look")
        c.grade.look = self.cb_look.currentData() or ""
        self.look_mix.setEnabled(bool(c.grade.look))
        self.changed.emit("grade")

    def _set_grade(self, key: str, val: float):
        if self.clip is None:
            return
        self.before_change.emit("grade:" + key)
        setattr(self.clip.grade, key, float(val))
        self.changed.emit("grade")

    def _set_attr(self, key: str, val: float, kind: str):
        if self.clip is None:
            return
        self.before_change.emit(kind + ":" + key)
        setattr(self.clip, key, float(val))
        self.changed.emit(kind)
