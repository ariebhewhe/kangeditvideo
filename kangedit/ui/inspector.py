"""Inspector: trim points, log conversion, color correction, reframing and audio for one clip."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget)

from ..color import LOG_CHOICES, TONEMAPS
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
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self.set_value(default)

    def mouseDoubleClickEvent(self, _):
        self.set_value(self.default, emit=True)

    def _from_slider(self, v):
        val = v / self.scale
        self.spin.blockSignals(True)
        self.spin.setValue(val)
        self.spin.blockSignals(False)
        self.changed.emit(val)

    def _from_spin(self, val):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(val * self.scale)))
        self.slider.blockSignals(False)
        self.changed.emit(val)

    def set_value(self, val: float, emit: bool = False):
        for w in (self.slider, self.spin):
            w.blockSignals(True)
        self.slider.setValue(int(round(val * self.scale)))
        self.spin.setValue(val)
        for w in (self.slider, self.spin):
            w.blockSignals(False)
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


class Inspector(QScrollArea):
    # kind: "trim" | "grade" | "footage_color" | "reframe" | "audio"
    before_change = Signal(str)
    changed = Signal(str)
    request = Signal(str)   # auto_color | auto_color_all | copy_grade_all | copy_grade_footage | reset_color

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

        # --- Log conversion (per footage)
        g = QGroupBox("Log → Rec.709 (applies to the whole footage)")
        f = QFormLayout(g)
        self.cb_log = QComboBox()
        for key, label in LOG_CHOICES:
            self.cb_log.addItem(label, key)
        self.cb_tone = QComboBox()
        for key, label in TONEMAPS:
            self.cb_tone.addItem(label, key)
        self.cb_log.currentIndexChanged.connect(self._footage_color)
        self.cb_tone.currentIndexChanged.connect(self._footage_color)
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
        self.set_clip(None)

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
        for w in (self.sp_in, self.sp_out, self.cb_log, self.cb_tone):
            w.blockSignals(True)
        self.sp_in.setMaximum(f.duration)
        self.sp_out.setMaximum(f.duration)
        self.sp_in.setValue(clip.src_in)
        self.sp_out.setValue(clip.src_out)
        self.cb_log.setCurrentIndex(max(0, self.cb_log.findData(f.log_profile)))
        self.cb_tone.setCurrentIndex(max(0, self.cb_tone.findData(f.tonemap)))
        self.cb_tone.setEnabled(f.log_profile != "none")
        for w in (self.sp_in, self.sp_out, self.cb_log, self.cb_tone):
            w.blockSignals(False)
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

    def _footage_color(self):
        c = self.clip
        if c is None:
            return
        f = self.project.footage[c.footage_id]
        self.before_change.emit("footage_color")
        f.log_profile = self.cb_log.currentData()
        f.tonemap = self.cb_tone.currentData()
        self.cb_tone.setEnabled(f.log_profile != "none")
        self.changed.emit("footage_color")

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
