"""Magnetic single-track timeline: reorder, trim and slip clips."""

from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QMenu, QScrollBar, QSlider, QToolButton,
                               QVBoxLayout, QWidget)

from ..analysis import WAVE_RATE, Analysis
from ..captions import Caption
from ..models import Project
from .theme import CLIP_COLORS, fmt_time

RULER = 24
TRACK_Y = RULER + 6
TRACK_H = 84
WAVE_H = 18
CAP_Y = TRACK_Y + TRACK_H + 8
CAP_H = 24
EDGE = 7
MIN_DUR = 0.25


class TimelineView(QWidget):
    seek = Signal(float)
    selection_changed = Signal(str)
    edit_started = Signal()
    edited = Signal()
    edit_finished = Signal()
    slip_preview = Signal(str, float)
    slip_end = Signal()
    caption_clicked = Signal(int)
    context_action = Signal(str, str)
    view_changed = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.analyses: dict[str, Analysis] = {}
        self.captions: list[Caption] = []
        self.playhead = 0.0
        self.pps = 40.0          # pixels per second
        self.scroll_x = 0.0
        self.selected: str = ""
        self.mode = "move"       # move | slip
        self.min_gap = 0.0
        self._conflicts: set[str] = set()
        self._drag: Optional[dict] = None
        self._thumb_cache: dict[tuple, QImage] = {}
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(CAP_Y + CAP_H + 10)

    # ------------------------------------------------------------------ helpers
    def set_project(self, project: Project):
        self.project = project
        self.selected = ""
        self._thumb_cache.clear()
        self.refresh()

    def refresh(self):
        self._conflicts = self.project.conflicts(self.min_gap)
        self.update()
        self.view_changed.emit()

    def t_to_x(self, t: float) -> float:
        return t * self.pps - self.scroll_x

    def x_to_t(self, x: float) -> float:
        return max(0.0, (x + self.scroll_x) / self.pps)

    def content_width(self) -> float:
        return self.project.duration * self.pps + 200

    def fit(self):
        dur = self.project.duration
        if dur > 0:
            self.pps = max(2.0, min(400.0, (self.width() - 40) / dur))
        self.scroll_x = 0
        self.refresh()

    def set_zoom(self, pps: float, anchor_x: Optional[float] = None):
        anchor_x = self.width() / 2 if anchor_x is None else anchor_x
        t = self.x_to_t(anchor_x)
        self.pps = max(2.0, min(400.0, pps))
        self.scroll_x = max(0.0, t * self.pps - anchor_x)
        self.refresh()

    def ensure_visible(self, t: float):
        x = self.t_to_x(t)
        if x < 0 or x > self.width() - 20:
            self.scroll_x = max(0.0, t * self.pps - self.width() * 0.3)
            self.view_changed.emit()

    def set_playhead(self, t: float, follow: bool = False):
        self.playhead = t
        if follow:
            self.ensure_visible(t)
        self.update()

    def _clip_rects(self):
        out, t = [], 0.0
        for c in self.project.clips:
            x0, x1 = self.t_to_x(t), self.t_to_x(t + c.duration)
            out.append((c, QRectF(x0, TRACK_Y, max(1.0, x1 - x0), TRACK_H), t))
            t += c.duration
        return out

    def _hit(self, pos: QPointF):
        if not (TRACK_Y <= pos.y() <= TRACK_Y + TRACK_H):
            return None, None
        for c, r, t in self._clip_rects():
            if r.left() - 2 <= pos.x() <= r.right() + 2:
                if pos.x() - r.left() <= EDGE and r.width() > EDGE * 3:
                    return c, "trim_l"
                if r.right() - pos.x() <= EDGE and r.width() > EDGE * 3:
                    return c, "trim_r"
                return c, "body"
        return None, None

    def _footage_color(self, fid: str) -> QColor:
        ids = list(self.project.footage)
        k = ids.index(fid) if fid in ids else 0
        return QColor(CLIP_COLORS[k % len(CLIP_COLORS)])

    def _thumb(self, fid: str, idx: int) -> Optional[QImage]:
        key = (fid, idx)
        img = self._thumb_cache.get(key)
        if img is None:
            a = self.analyses.get(fid)
            if a is None or not len(a.thumbs):
                return None
            arr = np.ascontiguousarray(a.thumbs[min(idx, len(a.thumbs) - 1)])
            img = QImage(arr.data, arr.shape[1], arr.shape[0], arr.strides[0], QImage.Format_RGB888).copy()
            self._thumb_cache[key] = img
        return img

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#16171a"))
        self._paint_ruler(p)
        p.fillRect(QRectF(0, TRACK_Y, self.width(), TRACK_H), QColor("#1d1f23"))
        p.fillRect(QRectF(0, CAP_Y, self.width(), CAP_H), QColor("#1a1b1f"))

        if not self.project.clips:
            p.setPen(QColor("#5d6068"))
            p.drawText(QRectF(0, TRACK_Y, self.width(), TRACK_H), Qt.AlignCenter,
                       "Timeline is empty — use Auto Edit, or right-click footage → Append")
        for c, r, t in self._clip_rects():
            if r.right() < 0 or r.left() > self.width():
                continue
            self._paint_clip(p, c, r)

        for i, cap in enumerate(self.captions):
            x0, x1 = self.t_to_x(cap.start), self.t_to_x(cap.end)
            if x1 < 0 or x0 > self.width():
                continue
            r = QRectF(x0 + 1, CAP_Y + 2, max(2.0, x1 - x0 - 2), CAP_H - 4)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#5b4a8a"))
            p.drawRoundedRect(r, 3, 3)
            if r.width() > 24:
                p.setPen(QColor("#ece8f7"))
                f = QFont()
                f.setPixelSize(11)
                p.setFont(f)
                p.drawText(r.adjusted(4, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           p.fontMetrics().elidedText(cap.text, Qt.ElideRight, int(r.width() - 7)))

        d = self._drag
        if d and d.get("kind") == "move" and d.get("active") and d.get("drop") is not None:
            starts = self.project.clip_starts() + [self.project.duration]
            x = self.t_to_x(starts[d["drop"]])
            p.setPen(QPen(QColor("#ffd34d"), 3))
            p.drawLine(QPointF(x, TRACK_Y - 4), QPointF(x, TRACK_Y + TRACK_H + 4))

        # Playhead
        x = self.t_to_x(self.playhead)
        p.setPen(QPen(QColor("#ff4d4d"), 1.5))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.setBrush(QColor("#ff4d4d"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 9)]))

    def _paint_ruler(self, p: QPainter):
        p.fillRect(QRectF(0, 0, self.width(), RULER), QColor("#202227"))
        steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
        major = next((s for s in steps if s * self.pps >= 70), 600)
        minor = major / 5
        t0 = self.x_to_t(0)
        t1 = self.x_to_t(self.width())
        f = QFont()
        f.setPixelSize(10)
        p.setFont(f)
        k = int(t0 / minor)
        while k * minor <= t1:
            t = k * minor
            x = self.t_to_x(t)
            is_major = abs(t / major - round(t / major)) < 1e-6
            p.setPen(QColor("#6c707a") if is_major else QColor("#3d4048"))
            p.drawLine(QPointF(x, RULER - (10 if is_major else 4)), QPointF(x, RULER))
            if is_major:
                p.setPen(QColor("#9aa0ab"))
                label = fmt_time(t, frames=False) if major >= 1 else f"{t:.2f}s"
                p.drawText(QPointF(x + 3, 11), label)
            k += 1

    def _paint_clip(self, p: QPainter, c, r: QRectF):
        f = self.project.footage.get(c.footage_id)
        col = self._footage_color(c.footage_id)
        path = QPainterPath()
        path.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        p.save()
        p.setClipPath(path)
        p.fillRect(r, col.darker(160))

        # Filmstrip
        a = self.analyses.get(c.footage_id)
        strip = QRectF(r.left(), r.top() + 16, r.width(), TRACK_H - 16 - WAVE_H)
        if a is not None and len(a.thumbs):
            th_h = strip.height()
            th_w = th_h * a.thumbs.shape[2] / a.thumbs.shape[1]
            x = r.left()
            if x < 0:  # skip tiles scrolled out on the left
                x += int(-x // th_w) * th_w
            while x < min(r.right(), self.width()):
                src_t = c.src_in + (x - r.left()) / self.pps
                idx = int(np.searchsorted(a.thumb_times, src_t, side="right") - 1)
                img = self._thumb(c.footage_id, max(0, idx))
                if img is not None:
                    p.drawImage(QRectF(x, strip.top(), th_w, th_h), img)
                x += th_w

        # Waveform
        wr = QRectF(r.left(), r.bottom() - WAVE_H, r.width(), WAVE_H)
        p.fillRect(wr, QColor(0, 0, 0, 110))
        if a is not None and a.wave.size > 1 and f is not None and f.has_audio:
            x0 = int(max(r.left(), 0))
            x1 = int(min(r.right(), self.width()))
            if x1 > x0:
                xs = np.arange(x0, x1, 2)
                ts = c.src_in + (xs - r.left()) / self.pps
                vals = a.wave[np.clip((ts * WAVE_RATE).astype(int), 0, a.wave.size - 1)] * c.volume
                mid = wr.center().y()
                amp = (WAVE_H / 2 - 1) * np.clip(vals, 0, 1)
                poly = QPolygonF([QPointF(x, mid - h) for x, h in zip(xs, amp)] +
                                 [QPointF(x, mid + h) for x, h in zip(xs[::-1], amp[::-1])])
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#7fd1a8"))
                p.drawPolygon(poly)

        # Header
        p.fillRect(QRectF(r.left(), r.top(), r.width(), 16), col)
        if r.width() > 30 and f is not None:
            fnt = QFont()
            fnt.setPixelSize(10)
            p.setFont(fnt)
            p.setPen(QColor("white"))
            label = f"{f.name}  {c.duration:.1f}s"
            if c.score is not None:
                label += f"  ★{max(0.0, c.score) * 100:.0f}"
            p.drawText(QRectF(r.left() + 4, r.top(), r.width() - 8, 16), Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(label, Qt.ElideRight, int(r.width() - 8)))
        p.restore()

        if c.id in self._conflicts:
            p.setPen(QPen(QColor("#ff5252"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
        if c.id == self.selected:
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 211, 77, 220))
            p.drawRect(QRectF(r.left(), r.top() + 16, 4, TRACK_H - 16))
            p.drawRect(QRectF(r.right() - 4, r.top() + 16, 4, TRACK_H - 16))

    # ------------------------------------------------------------------ interaction
    def _snap(self, t: float) -> float:
        fps = max(1.0, self.project.settings.fps)
        return round(t * fps) / fps

    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() == Qt.RightButton:
            c, _ = self._hit(pos)
            if c is not None:
                self.selected = c.id
                self.selection_changed.emit(c.id)
                self.update()
                self._context_menu(e.globalPosition().toPoint(), c.id)
            return
        if e.button() != Qt.LeftButton:
            return
        if pos.y() < RULER:
            self._drag = {"kind": "scrub"}
            self.seek.emit(self.x_to_t(pos.x()))
            return
        if CAP_Y <= pos.y() <= CAP_Y + CAP_H:
            t = self.x_to_t(pos.x())
            for i, cap in enumerate(self.captions):
                if cap.start <= t < cap.end:
                    self.caption_clicked.emit(i)
                    break
            self.seek.emit(t)
            return
        c, part = self._hit(pos)
        if c is None:
            self.selected = ""
            self.selection_changed.emit("")
            self.seek.emit(self.x_to_t(pos.x()))
            self._drag = {"kind": "scrub"}
            self.update()
            return
        if self.selected != c.id:
            self.selected = c.id
            self.selection_changed.emit(c.id)
        kind = part
        if part == "body":
            slip = self.mode == "slip" or (e.modifiers() & (Qt.AltModifier | Qt.ShiftModifier))
            kind = "slip" if slip else "move"
        self._drag = {"kind": kind, "clip": c.id, "x0": pos.x(), "in0": c.src_in, "out0": c.src_out,
                      "active": False, "drop": None}
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        d = self._drag
        if d is None:
            c, part = self._hit(pos)
            if part in ("trim_l", "trim_r"):
                self.setCursor(Qt.SizeHorCursor)
            elif part == "body":
                slip = self.mode == "slip" or (e.modifiers() & (Qt.AltModifier | Qt.ShiftModifier))
                self.setCursor(Qt.OpenHandCursor if slip else Qt.PointingHandCursor)
                f = self.project.footage.get(c.footage_id)
                self.setToolTip(f"{f.name if f else ''}\nSource {c.src_in:.2f}s → {c.src_out:.2f}s\n"
                                "Drag: reorder · Alt/Shift+drag: slip · Edges: trim")
            else:
                self.setCursor(Qt.ArrowCursor)
            return
        if d["kind"] == "scrub":
            self.seek.emit(self.x_to_t(pos.x()))
            return
        idx = self.project.index_of(d["clip"])
        if idx < 0:
            return
        c = self.project.clips[idx]
        f = self.project.footage[c.footage_id]
        dx = pos.x() - d["x0"]
        if not d["active"]:
            if abs(dx) < 3:
                return
            d["active"] = True
            if d["kind"] != "move":
                self.edit_started.emit()
        dt = dx / self.pps
        if d["kind"] == "trim_l":
            c.src_in = self._snap(min(max(0.0, d["in0"] + dt), d["out0"] - MIN_DUR))
            self.slip_preview.emit(c.id, c.src_in)
        elif d["kind"] == "trim_r":
            c.src_out = self._snap(max(min(f.duration, d["out0"] + dt), d["in0"] + MIN_DUR))
            self.slip_preview.emit(c.id, max(c.src_in, c.src_out - 1.0 / max(1.0, f.fps)))
        elif d["kind"] == "slip":
            dur = d["out0"] - d["in0"]
            new_in = self._snap(min(max(0.0, d["in0"] - dt), max(0.0, f.duration - dur)))
            c.src_in, c.src_out = new_in, new_in + dur
            self.slip_preview.emit(c.id, c.src_in)
            self.setCursor(Qt.ClosedHandCursor)
        elif d["kind"] == "move":
            t = self.x_to_t(pos.x())
            starts = self.project.clip_starts()
            drop = len(starts)
            for k, (s, cl) in enumerate(zip(starts, self.project.clips)):
                if t < s + cl.duration / 2:
                    drop = k
                    break
            d["drop"] = drop
            self.update()
            return
        self._conflicts = self.project.conflicts(self.min_gap)
        self.update()
        self.edited.emit()

    def mouseReleaseEvent(self, e):
        d = self._drag
        self._drag = None
        if d is None:
            return
        if d["kind"] == "move" and d.get("active") and d.get("drop") is not None:
            i = self.project.index_of(d["clip"])
            drop = d["drop"]
            if i >= 0 and drop not in (i, i + 1):
                self.edit_started.emit()
                clip = self.project.clips.pop(i)
                self.project.clips.insert(drop - 1 if drop > i else drop, clip)
                self.edited.emit()
                self.edit_finished.emit()
        elif d["kind"] in ("trim_l", "trim_r", "slip") and d.get("active"):
            self.slip_end.emit()
            self.edit_finished.emit()
        elif d["kind"] in ("move",) and not d.get("active"):
            # Simple click on a clip: move the playhead there.
            self.seek.emit(self.x_to_t(e.position().x()))
        self.setCursor(Qt.ArrowCursor)
        self.refresh()

    def mouseDoubleClickEvent(self, e):
        c, _ = self._hit(e.position())
        if c is not None:
            i = self.project.index_of(c.id)
            self.seek.emit(self.project.clip_starts()[i])

    def wheelEvent(self, e):
        delta = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ControlModifier:
            self.set_zoom(self.pps * (1.2 if delta > 0 else 1 / 1.2), e.position().x())
        else:
            self.scroll_x = max(0.0, min(self.scroll_x - delta, max(0.0, self.content_width() - self.width())))
            self.refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.view_changed.emit()

    def _context_menu(self, gpos, clip_id: str):
        m = QMenu(self)
        for key, label in (("split", "Split at playhead\tS"), ("duplicate", "Duplicate"),
                           ("auto_color", "Auto color"), ("reset_color", "Reset color"),
                           ("copy_grade", "Copy grade to all clips"),
                           ("alt_moment", "Replace with another best moment"),
                           (None, None), ("delete", "Delete\tDel")):
            if key is None:
                m.addSeparator()
                continue
            act = QAction(label, m)
            act.triggered.connect(lambda _=False, k=key: self.context_action.emit(k, clip_id))
            m.addAction(act)
        m.exec(gpos)


class TimelinePanel(QWidget):
    """Timeline view + mode buttons, zoom controls and scrollbar."""

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.view = TimelineView(project)
        self.scroll = QScrollBar(Qt.Horizontal)
        self.scroll.valueChanged.connect(self._on_scroll)
        self.view.view_changed.connect(self._sync_scroll)

        self.btn_move = QToolButton(text="↔ Move", checkable=True, checked=True)
        self.btn_slip = QToolButton(text="⇋ Slip", checkable=True)
        self.btn_move.setToolTip("Drag clips to reorder them. Drag clip edges to trim.")
        self.btn_slip.setToolTip("Drag inside a clip to slide which moment it shows (keeps its length).\n"
                                 "Tip: hold Alt or Shift in Move mode to slip temporarily.")
        grp = QButtonGroup(self)
        grp.addButton(self.btn_move)
        grp.addButton(self.btn_slip)
        self.btn_move.toggled.connect(lambda on: setattr(self.view, "mode", "move" if on else "slip"))
        self.btn_split = QToolButton(text="✂ Split")
        self.btn_delete = QToolButton(text="🗑 Delete")
        self.btn_fit = QToolButton(text="Fit")
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(2, 400)
        self.zoom.setValue(40)
        self.zoom.setFixedWidth(140)
        self.zoom.valueChanged.connect(lambda v: self.view.set_zoom(float(v)) if abs(v - self.view.pps) > 0.5 else None)
        self.btn_fit.clicked.connect(self.view.fit)
        self.lbl = QLabel("")
        self.lbl.setStyleSheet("color: #8b909a;")

        top = QHBoxLayout()
        top.setContentsMargins(4, 2, 4, 2)
        for w in (self.btn_move, self.btn_slip, self.btn_split, self.btn_delete):
            top.addWidget(w)
        top.addSpacing(12)
        top.addWidget(self.lbl, 1)
        top.addWidget(QLabel("Zoom"))
        top.addWidget(self.zoom)
        top.addWidget(self.btn_fit)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.scroll)

    def _on_scroll(self, v):
        if abs(v - self.view.scroll_x) >= 1:
            self.view.scroll_x = float(v)
            self.view.update()

    def _sync_scroll(self):
        v = self.view
        maxv = int(max(0.0, v.content_width() - v.width()))
        self.scroll.blockSignals(True)
        self.scroll.setRange(0, maxv)
        self.scroll.setPageStep(max(1, v.width()))
        self.scroll.setValue(int(v.scroll_x))
        self.scroll.blockSignals(False)
        self.zoom.blockSignals(True)
        self.zoom.setValue(int(v.pps))
        self.zoom.blockSignals(False)
        n = len(v.project.clips)
        bad = len(v._conflicts)
        txt = f"{n} clips · {fmt_time(v.project.duration, frames=False)}"
        if bad:
            txt += f"  ·  ⚠ {bad} clips overlap / too close to another clip from the same footage"
        self.lbl.setText(txt)
