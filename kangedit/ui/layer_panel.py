"""Layer tab: position, size, rotation, opacity, fades and sound of the overlay selected on a video layer."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..models import OverlayClip, Project
from .inspector import ParamSlider, wrap_angle

# Position presets: (x, y, scale) of the overlay's centre and size.
PLACEMENTS = {
    "full": (0.5, 0.5, 1.0),
    "center": (0.5, 0.5, 0.5),
    "tl": (0.22, 0.22, 0.35),
    "tr": (0.78, 0.22, 0.35),
    "bl": (0.22, 0.78, 0.35),
    "br": (0.78, 0.78, 0.35),
}


class LayerPanel(QScrollArea):
    before_change = Signal(str)     # key: one undo step per slider drag
    changed = Signal()
    request = Signal(str, str)      # action ("ov_delete", "ov_place:<key>"...), overlay id

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.clip: Optional[OverlayClip] = None
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 6, 14, 6)

        self.lbl_title = QLabel("")
        self.lbl_title.setStyleSheet("font-weight: 600; font-size: 13px;")
        self.lbl_title.setWordWrap(True)
        lay.addWidget(self.lbl_title)
        self.lbl_hint = QLabel("Drag footage or an image from Media onto a video layer (V2, V3…) above the main "
                               "track, then click it to place it here. Drag it in the viewer to move it.")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color: #8b909a;")
        lay.addWidget(self.lbl_hint)

        self.body = QWidget()
        v = QVBoxLayout(self.body)
        v.setContentsMargins(0, 0, 0, 0)

        g = QGroupBox("Position && size")
        f = QFormLayout(g)
        grid = QGridLayout()
        for k, (key, label) in enumerate((("full", "Full frame"), ("center", "Centre"), ("tl", "↖ Top left"),
                                          ("tr", "↗ Top right"), ("bl", "↙ Bottom left"), ("br", "↘ Bottom right"))):
            b = QPushButton(label)
            b.setToolTip("Picture in picture: a third of the frame in this corner" if len(key) == 2 else
                         "Fill the frame (letterboxed)" if key == "full" else "Half size in the centre")
            b.clicked.connect(lambda _=False, k=key: self._emit(f"ov_place:{k}"))
            grid.addWidget(b, k // 2, k % 2)
        f.addRow(grid)
        self.sl_x = ParamSlider(-50, 150, 50, 0.5, 1, " %")
        self.sl_y = ParamSlider(-50, 150, 50, 0.5, 1, " %")
        self.sl_scale = ParamSlider(2, 300, 100, 1, 0, " %")
        self.sl_scale.setToolTip("100 % fits the whole frame. Double-click to reset")
        self.sl_x.setToolTip("Horizontal position of the centre, % of the frame width")
        self.sl_y.setToolTip("Vertical position of the centre, % of the frame height")
        self.sl_rot = ParamSlider(-180, 180, 0, 0.5, 1, "°")
        self.sl_rot.setToolTip("Rotate clockwise. Double-click to reset")
        self.sl_x.changed.connect(lambda val: self._set("x", val / 100.0))
        self.sl_y.changed.connect(lambda val: self._set("y", val / 100.0))
        self.sl_scale.changed.connect(lambda val: self._set("scale", val / 100.0))
        self.sl_rot.changed.connect(lambda val: self._set("rotation", val))
        rot = QHBoxLayout()
        rot.setSpacing(4)
        rot.addWidget(self.sl_rot, 1)
        for text, step, tip in (("⟲", -90, "Rotate 90° left"), ("⟳", 90, "Rotate 90° right")):
            b = QToolButton(text=text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=step: self._rotate_by(k))
            rot.addWidget(b)
        f.addRow("X", self.sl_x)
        f.addRow("Y", self.sl_y)
        f.addRow("Size", self.sl_scale)
        f.addRow("Rotation", rot)
        v.addWidget(g)

        g = QGroupBox("Look && timing")
        f = QFormLayout(g)
        self.sl_opacity = ParamSlider(0, 100, 100, 1, 0, " %")
        self.sl_opacity.changed.connect(lambda val: self._set("opacity", val / 100.0))
        self.sl_fade_in = ParamSlider(0, 5, 0, 0.05, 2, " s")
        self.sl_fade_out = ParamSlider(0, 5, 0, 0.05, 2, " s")
        self.sl_fade_in.changed.connect(lambda val: self._set("fade_in", val))
        self.sl_fade_out.changed.connect(lambda val: self._set("fade_out", val))
        self.sp_start = QDoubleSpinBox(decimals=2, singleStep=0.1, suffix=" s")
        self.sp_start.setRange(0, 1e5)
        self.sp_start.valueChanged.connect(lambda val: self._set("start", val, "start"))
        self.sp_len = QDoubleSpinBox(decimals=2, singleStep=0.1, suffix=" s")
        self.sp_len.setRange(0.25, 1e5)
        self.sp_len.valueChanged.connect(self._set_length)
        self.sp_layer = QSpinBox()
        self.sp_layer.setRange(2, 9)
        self.sp_layer.setPrefix("V")
        self.sp_layer.setToolTip("Higher layers are in front of lower ones")
        self.sp_layer.valueChanged.connect(lambda val: self._set("track", int(val) - 2, "track"))
        f.addRow("Opacity", self.sl_opacity)
        f.addRow("Fade in", self.sl_fade_in)
        f.addRow("Fade out", self.sl_fade_out)
        f.addRow("Starts at", self.sp_start)
        f.addRow("Length", self.sp_len)
        f.addRow("Layer", self.sp_layer)
        v.addWidget(g)

        g = QGroupBox("Sound")
        f = QFormLayout(g)
        self.sl_volume = ParamSlider(0, 200, 100, 1, 0, " %")
        self.sl_volume.setToolTip("Volume of the overlay video's own sound (0 % mutes it)")
        self.sl_volume.changed.connect(lambda val: self._set("volume", val / 100.0, "volume"))
        f.addRow("Volume", self.sl_volume)
        self.grp_sound = g
        v.addWidget(g)

        b_del = QPushButton("Delete from the layer")
        b_del.clicked.connect(lambda: self._emit("ov_delete"))
        v.addWidget(b_del)
        lay.addWidget(self.body)
        lay.addStretch(1)
        self.set_clip(None)

    def set_project(self, project: Project):
        self.project = project
        self.set_clip(None)

    def _emit(self, action: str):
        if self.clip is not None:
            self.request.emit(action, self.clip.id)

    def set_clip(self, clip: Optional[OverlayClip]):
        self.clip = clip
        self.body.setVisible(clip is not None)
        self.lbl_hint.setVisible(clip is None)
        if clip is None:
            self.lbl_title.setText("No layer clip selected")
            return
        f = self.project.footage.get(clip.footage_id)
        self.lbl_title.setText(f"{f.name if f else '?'} on layer V{clip.track + 2}")
        for sl, val in ((self.sl_x, clip.x * 100), (self.sl_y, clip.y * 100), (self.sl_scale, clip.scale * 100),
                        (self.sl_rot, clip.rotation), (self.sl_opacity, clip.opacity * 100),
                        (self.sl_fade_in, clip.fade_in), (self.sl_fade_out, clip.fade_out),
                        (self.sl_volume, clip.volume * 100)):
            sl.set_value(val)
        for sp, val in ((self.sp_start, clip.start), (self.sp_len, clip.duration), (self.sp_layer, clip.track + 2)):
            sp.blockSignals(True)
            sp.setValue(val)
            sp.blockSignals(False)
        self.sp_len.setMaximum(max(0.25, (f.duration if f else clip.src_out) - clip.src_in))
        sound = f is not None and f.has_audio and not f.is_image
        self.grp_sound.setEnabled(sound)
        self.grp_sound.setToolTip("" if sound else "This footage has no sound")

    def _set(self, key: str, val, undo_key: str = ""):
        c = self.clip
        if c is None:
            return
        self.before_change.emit(f"layer:{c.id}:{undo_key or key}")
        setattr(c, key, val)
        if key == "track":
            self.lbl_title.setText(self.lbl_title.text().rsplit(" V", 1)[0] + f" V{c.track + 2}")
        self.changed.emit()

    def _set_length(self, val: float):
        c = self.clip
        if c is not None:
            self._set("src_out", c.src_in + max(0.25, val), "length")

    def _rotate_by(self, deg: float):
        if self.clip is not None:
            self._set("rotation", wrap_angle(self.clip.rotation + deg), "rotate")
            self.sl_rot.set_value(self.clip.rotation)
