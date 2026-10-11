"""Guided tour: a dimmed overlay that spotlights one part of the window at a time with a short explanation.

Shown automatically the first time the app starts; Help → "Guided tour" (or the ? button) shows it again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Union

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

Target = Callable[[], Union[QWidget, tuple[QWidget, QRect], None]]


@dataclass
class TourStep:
    title: str
    text: str
    target: Optional[Target] = None     # None: a centred message without a spotlight


class TourOverlay(QWidget):
    finished = Signal(bool)              # True when the last step was reached, False when skipped

    def __init__(self, window: QWidget, steps: list[TourStep]):
        super().__init__(window)
        self.window_ = window
        self.steps = steps
        self.index = 0
        self.hole = QRect()
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.StrongFocus)

        self.bubble = QFrame(self)
        self.bubble.setObjectName("tourBubble")
        self.bubble.setStyleSheet(
            "#tourBubble { background: #23252b; border: 1px solid #4c8dff; border-radius: 8px; }"
            "#tourBubble QLabel { background: transparent; }")
        self.bubble.setFixedWidth(340)
        self.lbl_step = QLabel()
        self.lbl_step.setStyleSheet("color: #8b909a; font-size: 11px;")
        self.lbl_title = QLabel()
        self.lbl_title.setStyleSheet("font-size: 15px; font-weight: 600; color: #ffffff;")
        self.lbl_title.setWordWrap(True)
        self.lbl_text = QLabel()
        self.lbl_text.setWordWrap(True)
        self.lbl_text.setTextFormat(Qt.RichText)
        self.lbl_text.setStyleSheet("color: #d8dae0; font-size: 12px;")
        self.b_skip = QPushButton("Skip tour")
        self.b_back = QPushButton("Back")
        self.b_next = QPushButton("Next")
        self.b_next.setObjectName("primary")
        self.b_skip.clicked.connect(lambda: self.close_tour(False))
        self.b_back.clicked.connect(self.back)
        self.b_next.clicked.connect(self.next)
        btns = QHBoxLayout()
        btns.addWidget(self.b_skip)
        btns.addStretch(1)
        btns.addWidget(self.b_back)
        btns.addWidget(self.b_next)
        lay = QVBoxLayout(self.bubble)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.addWidget(self.lbl_step)
        lay.addWidget(self.lbl_title)
        lay.addWidget(self.lbl_text)
        lay.addSpacing(6)
        lay.addLayout(btns)
        window.installEventFilter(self)

    # ------------------------------------------------------------------ control
    def start(self, index: int = 0):
        self.setGeometry(self.window_.rect())
        self.show()
        self.raise_()
        self.setFocus()
        self.show_step(index)

    def show_step(self, index: int):
        self.index = max(0, min(index, len(self.steps) - 1))
        step = self.steps[self.index]
        self.lbl_step.setText(f"Step {self.index + 1} of {len(self.steps)}")
        self.lbl_title.setText(step.title)
        self.lbl_text.setText(step.text)
        self.b_back.setEnabled(self.index > 0)
        self.b_next.setText("Done" if self.index == len(self.steps) - 1 else "Next")
        self._place()

    def next(self):
        if self.index >= len(self.steps) - 1:
            self.close_tour(True)
        else:
            self.show_step(self.index + 1)

    def back(self):
        self.show_step(self.index - 1)

    def close_tour(self, completed: bool):
        self.window_.removeEventFilter(self)
        self.hide()
        self.finished.emit(completed)
        self.deleteLater()

    # ------------------------------------------------------------------ layout
    def _target_rect(self) -> QRect:
        step = self.steps[self.index]
        got = step.target() if step.target else None
        if got is None:
            return QRect()
        w, local = (got if isinstance(got, tuple) else (got, None))
        if w is None or not w.isVisible():
            return QRect()
        local = local if local is not None else w.rect()
        top_left = w.mapTo(self.window_, local.topLeft())
        return QRect(top_left, local.size()).adjusted(-6, -6, 6, 6).intersected(self.rect().adjusted(4, 4, -4, -4))

    def _place(self):
        self.hole = self._target_rect()
        self.bubble.adjustSize()
        bw, bh = self.bubble.width(), self.bubble.sizeHint().height()
        W, H, gap = self.width(), self.height(), 14
        if self.hole.isNull():
            pos = QPoint((W - bw) // 2, (H - bh) // 2)
        else:
            h = self.hole
            candidates = [QPoint(h.left(), h.bottom() + gap),            # below
                          QPoint(h.left(), h.top() - gap - bh),          # above
                          QPoint(h.right() + gap, h.top()),              # right
                          QPoint(h.left() - gap - bw, h.top())]          # left
            pos = next((p for p in candidates if 0 <= p.x() <= W - bw and 0 <= p.y() <= H - bh), None)
            if pos is None:   # the target fills the window: put the bubble inside it, near the top
                pos = QPoint(h.left() + gap, h.top() + gap)
            pos.setX(max(8, min(pos.x(), W - bw - 8)))
            pos.setY(max(8, min(pos.y(), H - bh - 8)))
        self.bubble.move(pos)
        self.bubble.raise_()
        self.update()

    # ------------------------------------------------------------------ events
    def eventFilter(self, obj, e):
        if obj is self.window_ and e.type() == QEvent.Resize:
            self.setGeometry(self.window_.rect())
            self._place()
        return False

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        if not self.hole.isNull():
            hole = QPainterPath()
            hole.addRoundedRect(QRectF(self.hole), 8, 8)
            path = path.subtracted(hole)
        p.fillPath(path, QColor(0, 0, 0, 165))
        if not self.hole.isNull():
            p.setPen(QPen(QColor("#4c8dff"), 2))
            p.drawRoundedRect(QRectF(self.hole), 8, 8)

    def mousePressEvent(self, e):
        e.accept()    # the tour is modal: clicks outside the bubble do nothing

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_tour(False)
        elif e.key() in (Qt.Key_Right, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.next()
        elif e.key() == Qt.Key_Left:
            self.back()
        else:
            super().keyPressEvent(e)
