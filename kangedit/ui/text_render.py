"""Draws titles (titles.TextClip) with QPainter for the viewer, laid out like the libass export."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

from ..fonts import ensure_qt_font
from ..models import TextClip
from ..titles import TextState, ass_metrics, visible_lines

_ready_fonts: set[str] = set()


def _font(tc: TextClip, px: float) -> QFont:
    if tc.font not in _ready_fonts:
        ensure_qt_font(tc.font)
        _ready_fonts.add(tc.font)
    f = QFont(tc.font)
    f.setPixelSize(max(1, int(round(px))))
    f.setBold(tc.bold)
    f.setItalic(tc.italic)
    return f


def text_rect(tc: TextClip, frame: QRectF, st: TextState | None = None) -> QRectF:
    """Where the (settled, or `st`) text block sits on the frame rect, for hit tests and selection."""
    st = st or TextState()
    size, pad, _ = ass_metrics(tc, max(1, int(round(frame.height()))))
    fm = QFontMetricsF(_font(tc, size))
    lines = tc.text.split("\n")
    w = max((fm.horizontalAdvance(line) for line in lines), default=0.0) * st.scale
    h = fm.height() * len(lines) * st.scale
    ax = frame.left() + tc.x * frame.width() + st.dx * frame.height()
    ay = frame.top() + tc.y * frame.height() + st.dy * frame.height()
    x0 = {"left": ax, "right": ax - w}.get(tc.align, ax - w / 2)
    return QRectF(x0, ay - h / 2, w, h).adjusted(-pad, -pad / 2, pad, pad / 2)


def draw_text(p: QPainter, frame: QRectF, tc: TextClip, st: TextState, opacity: float = 1.0) -> None:
    """Draw one text clip in state `st` onto the frame rect (the picture's area on screen)."""
    size, pad, _ = ass_metrics(tc, max(1, int(round(frame.height()))))
    font = _font(tc, size)
    fm = QFontMetricsF(font)
    line_h = fm.height()
    full = tc.text.split("\n")
    shown = visible_lines(tc, st.chars)
    block_h = line_h * len(full)
    ax = frame.left() + tc.x * frame.width() + st.dx * frame.height()
    ay = frame.top() + tc.y * frame.height() + st.dy * frame.height()

    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setOpacity(max(0.0, min(1.0, st.alpha * opacity)))
    # Scale around the anchor point, as libass scales around \pos.
    p.translate(ax, ay)
    p.scale(max(0.0, st.scale), max(0.0, st.scale))
    p.translate(-ax, -ay)
    fill = QColor(tc.color)
    box = QColor(tc.box_color)
    box.setAlphaF(max(0.0, min(1.0, tc.box_opacity / 100.0)))
    for i, (line, (typed, _rest)) in enumerate(zip(full, shown)):
        lw = fm.horizontalAdvance(line)          # the whole line: typed letters never shift
        x = {"left": ax, "right": ax - lw}.get(tc.align, ax - lw / 2)
        top = ay - block_h / 2 + i * line_h
        if tc.box and typed:
            tw = fm.horizontalAdvance(typed)
            p.fillRect(QRectF(x - pad, top - pad / 2, tw + 2 * pad, line_h + pad), box)
        if not typed:
            continue
        path = QPainterPath()
        path.addText(QPointF(x, top + fm.ascent()), font, typed)
        if pad and not tc.box:
            p.strokePath(path, QPen(QColor(0, 0, 0, 230), pad * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.fillPath(path, fill)
    p.restore()
