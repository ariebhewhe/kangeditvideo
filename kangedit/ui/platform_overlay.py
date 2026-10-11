"""Instagram Reels / TikTok screen layouts drawn over the viewer, to keep titles and captions clear of the
app's buttons and text. Positions follow the apps' 1080 x 1920 layout (approximate, guidance only)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF

PLATFORM_OVERLAYS = {
    "": "None",
    "reels": "Instagram Reels",
    "tiktok": "TikTok",
}

# Area the app's interface leaves free, in 1080 x 1920 pixels: left, top, right, bottom.
SAFE_AREAS = {
    "reels": (60, 220, 960, 1500),
    "tiktok": (60, 150, 940, 1540),
}

_W, _H = 1080.0, 1920.0
_WHITE = QColor(255, 255, 255, 215)
_DIM = QColor(255, 255, 255, 150)


class _Ref:
    """Maps the 1080 x 1920 reference layout onto the frame rectangle."""

    def __init__(self, r: QRectF):
        self.r = r
        self.sx, self.sy = r.width() / _W, r.height() / _H
        self.s = min(self.sx, self.sy)

    def pt(self, x: float, y: float) -> QPointF:
        return QPointF(self.r.left() + x * self.sx, self.r.top() + y * self.sy)

    def rect(self, x: float, y: float, w: float, h: float) -> QRectF:
        return QRectF(self.pt(x, y), self.pt(x + w, y + h))

    def font(self, px: float, bold: bool = False) -> QFont:
        f = QFont()
        f.setPixelSize(max(6, int(px * self.s)))
        f.setBold(bold)
        return f


def draw_platform_overlay(p: QPainter, r: QRectF, kind: str) -> None:
    if kind not in SAFE_AREAS or r.width() < 10:
        return
    ref = _Ref(r)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setClipRect(r)
    # The apps darken the bottom of the picture behind the caption text.
    g = QLinearGradient(ref.pt(0, 1350), ref.pt(0, _H))
    g.setColorAt(0, QColor(0, 0, 0, 0))
    g.setColorAt(1, QColor(0, 0, 0, 120))
    p.fillRect(ref.rect(0, 1350, _W, _H - 1350), g)
    if kind == "reels":
        _reels(p, ref)
    else:
        _tiktok(p, ref)
    l, t, rr, b = SAFE_AREAS[kind]
    p.setPen(QPen(QColor(255, 214, 10, 200), 1, Qt.DashLine))
    p.setBrush(Qt.NoBrush)
    p.drawRect(ref.rect(l, t, rr - l, b - t))
    p.setFont(ref.font(24))
    p.setPen(QColor(255, 214, 10, 220))
    p.drawText(ref.rect(l + 10, t + 6, 400, 40), Qt.AlignLeft | Qt.AlignTop, "Safe area")
    p.restore()


# ---------------------------------------------------------------- icons (centred on x, y; size in ref px)
def _heart(p: QPainter, ref: _Ref, x: float, y: float, size: float):
    s = size / 2
    path = QPainterPath(ref.pt(x, y + s * 0.9))
    path.cubicTo(ref.pt(x - s * 1.6, y - s * 0.1), ref.pt(x - s * 0.7, y - s * 1.3), ref.pt(x, y - s * 0.45))
    path.cubicTo(ref.pt(x + s * 0.7, y - s * 1.3), ref.pt(x + s * 1.6, y - s * 0.1), ref.pt(x, y + s * 0.9))
    p.drawPath(path)


def _bubble(p: QPainter, ref: _Ref, x: float, y: float, size: float):
    s = size / 2
    p.drawEllipse(ref.rect(x - s, y - s * 0.85, size, s * 1.7))
    p.drawLine(ref.pt(x - s * 0.55, y + s * 0.7), ref.pt(x - s * 0.95, y + s * 1.05))


def _plane(p: QPainter, ref: _Ref, x: float, y: float, size: float):
    s = size / 2
    p.drawPolygon(QPolygonF([ref.pt(x - s, y - s * 0.2), ref.pt(x + s, y - s), ref.pt(x + s * 0.2, y + s),
                             ref.pt(x - s * 0.05, y + s * 0.1)]))


def _arrow(p: QPainter, ref: _Ref, x: float, y: float, size: float):
    s = size / 2
    p.drawPolygon(QPolygonF([ref.pt(x + s, y - s * 0.1), ref.pt(x + s * 0.05, y - s * 0.9),
                             ref.pt(x + s * 0.05, y - s * 0.45), ref.pt(x - s, y + s * 0.1),
                             ref.pt(x - s * 0.6, y + s * 0.8), ref.pt(x + s * 0.05, y + s * 0.35),
                             ref.pt(x + s * 0.05, y + s * 0.75)]))


def _bookmark(p: QPainter, ref: _Ref, x: float, y: float, size: float):
    s = size / 2
    p.drawPolygon(QPolygonF([ref.pt(x - s * 0.7, y - s), ref.pt(x + s * 0.7, y - s), ref.pt(x + s * 0.7, y + s),
                             ref.pt(x, y + s * 0.45), ref.pt(x - s * 0.7, y + s)]))


def _dots(p: QPainter, ref: _Ref, x: float, y: float, size: float):
    p.save()
    p.setBrush(_WHITE)
    for dx in (-size * 0.38, 0, size * 0.38):
        p.drawEllipse(ref.pt(x + dx, y), 4 * ref.s, 4 * ref.s)
    p.restore()


def _label(p: QPainter, ref: _Ref, x: float, y: float, text: str, px: float = 26, bold: bool = True,
           align=Qt.AlignHCenter | Qt.AlignTop, w: float = 220):
    p.setFont(ref.font(px, bold))
    p.setPen(_WHITE)
    left = x - w / 2 if align & Qt.AlignHCenter else x
    p.drawText(ref.rect(left, y, w, px * 1.6), align, text)


def _icon_pen(p: QPainter, ref: _Ref, width: float = 5.0):
    p.setPen(QPen(_WHITE, max(1.0, width * ref.s), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)


def _nav_bar(p: QPainter, ref: _Ref, dark: bool, plus_box: bool):
    p.fillRect(ref.rect(0, 1790, _W, 130), QColor(0, 0, 0, 200 if dark else 140))
    _icon_pen(p, ref, 4)
    xs = [108, 324, 540, 756, 972]
    for k, x in enumerate(xs):
        if k == 2 and plus_box:
            p.drawRoundedRect(ref.rect(x - 52, 1822, 104, 64), 14 * ref.s, 14 * ref.s)
            p.drawLine(ref.pt(x - 18, 1854), ref.pt(x + 18, 1854))
            p.drawLine(ref.pt(x, 1836), ref.pt(x, 1872))
        else:
            p.drawRoundedRect(ref.rect(x - 24, 1830, 48, 48), 10 * ref.s, 10 * ref.s)


def _reels(p: QPainter, ref: _Ref):
    _label(p, ref, 48, 90, "Reels", 52, True, Qt.AlignLeft | Qt.AlignTop, 400)
    _icon_pen(p, ref, 4)
    p.drawRoundedRect(ref.rect(970, 100, 64, 52), 12 * ref.s, 12 * ref.s)       # camera
    p.drawEllipse(ref.pt(1002, 126), 13 * ref.s, 13 * ref.s)
    # Right rail
    x = 1000
    _icon_pen(p, ref)
    _heart(p, ref, x, 1170, 64)
    _label(p, ref, x, 1212, "12.3K")
    _bubble(p, ref, x, 1320, 62)
    _label(p, ref, x, 1362, "248")
    _plane(p, ref, x, 1462, 60)
    _label(p, ref, x, 1502, "1,024")
    _dots(p, ref, x, 1600, 60)
    _icon_pen(p, ref, 4)
    p.drawRoundedRect(ref.rect(x - 30, 1660, 60, 60), 10 * ref.s, 10 * ref.s)    # audio cover
    # Bottom left: account, caption, audio
    p.setBrush(QColor(255, 255, 255, 90))
    p.drawEllipse(ref.pt(80, 1580), 32 * ref.s, 32 * ref.s)
    _label(p, ref, 128, 1560, "username", 30, True, Qt.AlignLeft | Qt.AlignTop, 260)
    p.setPen(QPen(_WHITE, max(1.0, 2.5 * ref.s)))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(ref.rect(400, 1556, 120, 50), 10 * ref.s, 10 * ref.s)
    _label(p, ref, 460, 1564, "Follow", 26, True, Qt.AlignHCenter | Qt.AlignTop, 120)
    _label(p, ref, 48, 1636, "Caption text appears here… more", 28, False, Qt.AlignLeft | Qt.AlignTop, 840)
    _label(p, ref, 48, 1696, "♫ username · Original audio", 26, False, Qt.AlignLeft | Qt.AlignTop, 840)
    _nav_bar(p, ref, dark=True, plus_box=False)


def _tiktok(p: QPainter, ref: _Ref):
    p.setFont(ref.font(34, True))
    p.setPen(_DIM)
    p.drawText(ref.rect(240, 80, 300, 60), Qt.AlignRight | Qt.AlignVCenter, "Following")
    p.setPen(_WHITE)
    p.drawText(ref.rect(580, 80, 300, 60), Qt.AlignLeft | Qt.AlignVCenter, "For You")
    p.fillRect(ref.rect(610, 140, 70, 5), _WHITE)
    _icon_pen(p, ref, 4)
    p.drawEllipse(ref.pt(1000, 110), 22 * ref.s, 22 * ref.s)                    # search
    p.drawLine(ref.pt(1016, 126), ref.pt(1032, 142))
    _label(p, ref, 70, 92, "LIVE", 28, True, Qt.AlignLeft | Qt.AlignTop, 120)
    # Right rail
    x = 1000
    p.setBrush(QColor(255, 255, 255, 90))
    p.setPen(QPen(_WHITE, max(1.0, 3 * ref.s)))
    p.drawEllipse(ref.pt(x, 960), 46 * ref.s, 46 * ref.s)
    p.setBrush(QColor(254, 44, 85, 230))
    p.setPen(Qt.NoPen)
    p.drawEllipse(ref.pt(x, 1008), 18 * ref.s, 18 * ref.s)
    _icon_pen(p, ref)
    p.setBrush(_WHITE)
    _heart(p, ref, x, 1100, 70)
    _label(p, ref, x, 1146, "245K")
    p.setBrush(_WHITE)
    _bubble(p, ref, x, 1250, 66)
    _label(p, ref, x, 1294, "1,832")
    _bookmark(p, ref, x, 1390, 56)
    _label(p, ref, x, 1430, "12K")
    _arrow(p, ref, x, 1520, 64)
    _label(p, ref, x, 1560, "Share")
    p.setBrush(QColor(40, 40, 40, 220))
    p.setPen(QPen(QColor(90, 90, 90), max(1.0, 10 * ref.s)))
    p.drawEllipse(ref.pt(x, 1700), 42 * ref.s, 42 * ref.s)                       # sound disc
    # Bottom left
    _label(p, ref, 40, 1590, "@username", 32, True, Qt.AlignLeft | Qt.AlignTop, 600)
    _label(p, ref, 40, 1640, "Caption text with #hashtags appears here…", 28, False,
           Qt.AlignLeft | Qt.AlignTop, 840)
    _label(p, ref, 40, 1700, "♫ original sound - username", 26, False, Qt.AlignLeft | Qt.AlignTop, 840)
    _nav_bar(p, ref, dark=True, plus_box=True)
