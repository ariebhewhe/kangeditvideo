"""Choosing the hook: the strongest moments of the whole video as thumbnails (its start, middle and end), the
strongest ticked, as many as the hook has clips. Tick others, or ask for more suggestions."""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget)

from ..autoedit import MAX_HOOK_CLIPS
from ..models import TimelineClip
from .theme import fmt_time

FRAME_W = 112          # each of a card's three frames


def strip(analysis, a: float, b: float, width: int = FRAME_W) -> Optional[QPixmap]:
    """Three frames of a moment a..b side by side (start, middle, end), from the analysis's thumbnails."""
    if analysis is None or not len(analysis.thumbs):
        return None
    tt = np.asarray(analysis.thumb_times, np.float32)
    frames = []
    for t in (a + 0.15, (a + b) / 2, b - 0.15):
        k = int(np.argmin(np.abs(tt - t))) if len(tt) else 0
        frames.append(np.asarray(analysis.thumbs[min(k, len(analysis.thumbs) - 1)]))
    h = min(f.shape[0] for f in frames)
    arr = np.ascontiguousarray(np.hstack([f[:h] for f in frames]))
    img = QImage(arr.data, arr.shape[1], arr.shape[0], arr.strides[0], QImage.Format_RGB888).copy()
    return QPixmap.fromImage(img).scaledToWidth(3 * width, Qt.SmoothTransformation)


class HookPicker(QDialog):
    """fetch(count, avoid) -> candidate moments for a `count`-clip hook not overlapping `avoid` (moments
    already offered), strongest first. names(clip) -> a card's text; thumbs(clip) -> its picture;
    length_for(count) -> how long each moment of a `count`-clip hook is."""

    def __init__(self, parent, count: int, fetch: Callable[[int, list], list],
                 thumbs: Callable[[TimelineClip], Optional[QPixmap]], names: Callable[[TimelineClip], str],
                 length_for: Callable[[int], float]):
        super().__init__(parent)
        self.setWindowTitle("🎣 Choose the hook")
        self.fetch, self.thumbs, self.names, self.length_for = fetch, thumbs, names, length_for
        self.shown: list[TimelineClip] = []      # every moment offered (More suggestions offers others)
        self.cards: list[tuple[QToolButton, TimelineClip]] = []
        lay = QVBoxLayout(self)
        intro = QLabel("The strongest moments of the whole video (movement, faces, people laughing or cheering). "
                       "The ticked ones open the video, in this order. Tick the ones you like.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(QLabel("Hook clips"))
        self.sp_count = QSpinBox()
        self.sp_count.setRange(1, MAX_HOOK_CLIPS)
        self.sp_count.setValue(max(1, min(MAX_HOOK_CLIPS, count)))
        self.sp_count.setToolTip("How many moments open the video: 1 quick hook, or a teaser of 2 s moments")
        self.sp_count.valueChanged.connect(self._count_changed)
        row.addWidget(self.sp_count)
        row.addStretch(1)
        self.btn_more = QPushButton("↻ More suggestions")
        self.btn_more.setToolTip("Other strong moments, not shown yet")
        self.btn_more.clicked.connect(self.more)
        row.addWidget(self.btn_more)
        lay.addLayout(row)
        self.grid_box = QWidget()
        self.grid = QGridLayout(self.grid_box)
        self.grid.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.grid_box, 1)
        self.lbl = QLabel("")
        self.lbl.setStyleSheet("color: #9aa0ab;")
        lay.addWidget(self.lbl)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.btn_ok = bb.button(QDialogButtonBox.Ok)
        self.btn_ok.setText("Use as the hook")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._length = None
        self._load(fresh=True)

    @property
    def count(self) -> int:
        return self.sp_count.value()

    def _load(self, fresh: bool = False) -> None:
        avoid = [] if fresh else list(self.shown)
        got = self.fetch(self.count, avoid)
        if not got and not fresh:                # every strong moment was offered: start the round again
            self.shown = []
            got = self.fetch(self.count, [])
        self.shown += [TimelineClip.from_dict(c.to_dict()) for c in got]
        self._length = self.length_for(self.count)
        self._show(got)

    def _show(self, clips: list) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.cards = []
        cols = 3
        for k, c in enumerate(clips):
            b = QToolButton()
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            pix = self.thumbs(c)
            if pix is not None:
                b.setIcon(pix)
                b.setIconSize(QSize(pix.width(), pix.height()))
            b.setText(self.names(c))
            b.setToolTip("Start · middle · end of the moment. Click to tick or untick it.")
            b.toggled.connect(self._update)
            self.grid.addWidget(b, k // cols, k % cols)
            self.cards.append((b, c))
        self._tick_best()
        if not clips:
            self.lbl.setText("No strong moment left that the edit doesn't already show.")

    def _tick_best(self) -> None:
        for k, (b, _) in enumerate(self.cards):
            b.blockSignals(True)
            b.setChecked(k < self.count)
            b.blockSignals(False)
        self._update()

    def _count_changed(self) -> None:
        # One hook clip is longer than a teaser's moments: other moments then fit better.
        if self._length is not None and abs(self.length_for(self.count) - self._length) > 0.05:
            self.shown = []
            self._load(fresh=True)
        else:
            self._tick_best()

    def more(self) -> None:
        self._load()

    def _update(self) -> None:
        n = len(self.chosen())
        for k, (b, c) in enumerate(self.cards):
            order = [x for x, _ in self.cards if x.isChecked()]
            b.setText(("✔ " + str(order.index(b) + 1) + "  " if b.isChecked() else "") + self.names(c))
        self.btn_ok.setEnabled(n > 0)
        if self.cards:
            self.lbl.setText(f"{n} ticked: the hook is {n} clip(s), "
                             f"{sum(c.duration for c in self.chosen()):.1f} s" if n else "Tick at least one moment.")

    def chosen(self) -> list[TimelineClip]:
        """The ticked moments, in the order shown (strongest first)."""
        return [c for b, c in self.cards if b.isChecked()]


def card_name(c: TimelineClip, footage_name: str) -> str:
    score = f"  ★{max(0.0, c.score) * 100:.0f}" if c.score is not None else ""
    return f"{footage_name}\n{fmt_time(c.src_in, frames=False)}, {c.duration:.1f} s{score}"
