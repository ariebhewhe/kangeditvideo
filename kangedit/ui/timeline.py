"""Magnetic single-track timeline: reorder, trim and slip clips."""

from __future__ import annotations

import os
from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFormLayout, QWidgetAction, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollBar, QSizePolicy, QSlider, QToolBar,
                               QToolButton, QVBoxLayout, QWidget)

from .. import sfx
from ..analysis import WAVE_RATE, Analysis
from ..captions import Caption
from ..edits import TRANSITIONS, edits
from ..models import Project, SoundClip
from ..titles import ANIM_IN, ANIM_OUT, TEXT_PRESETS
from .theme import CLIP_COLORS, fmt_time

RULER = 24
TEXT_Y = RULER + 4                 # text / title track: two rows so overlapping titles stay visible
TEXT_ROW = 16
TEXT_H = TEXT_ROW * 2
TRACK_Y = TEXT_Y + TEXT_H + 4
TRACK_H = 84
WAVE_H = 18
SFX_Y = TRACK_Y + TRACK_H + 6     # sound-effect layer: two rows so overlapping sounds stay visible
SFX_ROW = 17
SFX_H = SFX_ROW * 2
CAP_Y = SFX_Y + SFX_H + 6
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
    sound_selected = Signal(str)
    sounds_changed = Signal()            # a sound was moved / trimmed (after edit_started)
    sound_dropped = Signal(list, float)  # audio file paths dropped on the timeline, at timeline time
    text_selected = Signal(str)
    texts_changed = Signal()             # a title was moved / trimmed (after edit_started)

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.analyses: dict[str, Analysis] = {}
        self.captions: list[Caption] = []
        self.playhead = 0.0
        self.pps = 40.0          # pixels per second
        self.scroll_x = 0.0
        self._selected: str = ""          # primary selection (shown in the inspector)
        self._selection: set[str] = set()  # every selected clip, including the primary one
        self.mode = "move"       # move | slip
        self.min_gap = 0.0
        self._conflicts: set[str] = set()
        self._drag: Optional[dict] = None
        self._thumb_cache: dict[tuple, QImage] = {}
        self._static: Optional[QPixmap] = None   # everything but the playhead
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(CAP_Y + CAP_H + 10)
        self.setAcceptDrops(True)
        self.selected_sound = ""
        self.selected_text = ""
        self._sound_max: dict[str, float] = {}   # file length per sound file (trim limit)
        self._scrub_x = 0.0                      # last mouse x while scrubbing (for edge auto-scroll)
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(30)
        self._edge_timer.timeout.connect(self._edge_scroll)

    # ------------------------------------------------------------------ selection
    @property
    def selected(self) -> str:
        return self._selected

    @selected.setter
    def selected(self, clip_id: str):
        """Select one clip only (or none with "")."""
        self._selected = clip_id
        self._selection = {clip_id} if clip_id else set()

    def selected_ids(self) -> list[str]:
        """Selected clips in timeline order."""
        return [c.id for c in self.project.clips if c.id in self._selection]

    def select_all(self):
        self._selection = {c.id for c in self.project.clips}
        if self._selected not in self._selection:
            self._selected = self.project.clips[0].id if self.project.clips else ""
        self.selection_changed.emit(self._selected)
        self.update()

    def _click_select(self, clip_id: str, mods) -> None:
        """Ctrl: toggle the clip. Shift: select the range from the primary clip. Plain: only this clip."""
        if mods & Qt.ControlModifier:
            if clip_id in self._selection and len(self._selection) > 1:
                self._selection.discard(clip_id)
                if self._selected == clip_id:
                    self._selected = self.selected_ids()[0]
            else:
                self._selection.add(clip_id)
                self._selected = clip_id
        elif mods & Qt.ShiftModifier and self._selected:
            ids = [c.id for c in self.project.clips]
            a, b = sorted((ids.index(self._selected), ids.index(clip_id))) if self._selected in ids else (0, 0)
            self._selection = set(ids[a:b + 1]) | {clip_id}
        else:
            self.selected = clip_id
        self.selection_changed.emit(self._selected)
        self.update()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_A and e.modifiers() & Qt.ControlModifier:
            self.select_all()
            return
        super().keyPressEvent(e)

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
        end = max([self.project.duration] + [x.end for x in self.project.sounds] + [x.end for x in self.project.texts])
        return end * self.pps + 200

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

    def _edge_speed(self, x: float) -> float:
        """Pixels to scroll per tick when scrubbing near / past the left or right edge."""
        margin = 30.0
        if x < margin:
            return -min(60.0, 4.0 + (margin - x) * 0.6)
        if x > self.width() - margin:
            return min(60.0, 4.0 + (x - self.width() + margin) * 0.6)
        return 0.0

    def _edge_scroll(self):
        d = self._drag
        speed = self._edge_speed(self._scrub_x)
        if d is None or d.get("kind") != "scrub" or speed == 0.0:
            self._edge_timer.stop()
            return
        max_scroll = max(0.0, self.content_width() - self.width())
        new = max(0.0, min(self.scroll_x + speed, max_scroll))
        if new != self.scroll_x:
            self.scroll_x = new
            self.update()
            self.view_changed.emit()
        self.seek.emit(self.x_to_t(min(max(0.0, self._scrub_x), self.width())))

    def ensure_visible(self, t: float):
        x = self.t_to_x(t)
        if x < 0 or x > self.width() - 20:
            self.scroll_x = max(0.0, t * self.pps - self.width() * 0.3)
            self.view_changed.emit()

    def update(self, *args):
        """A full update means the content changed; a partial one (playhead moves) reuses the cache."""
        if not args:
            self._static = None
        super().update(*args)

    def set_playhead(self, t: float, follow: bool = False):
        old_x, old_scroll = self.t_to_x(self.playhead), self.scroll_x
        self.playhead = t
        if follow:
            self.ensure_visible(t)
        if self.scroll_x != old_scroll:
            self.update()
            return
        for x in (old_x, self.t_to_x(t)):
            self.update(QRect(int(x) - 8, 0, 17, self.height()))

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
        dpr = self.devicePixelRatioF()
        size = QSize(max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr)))
        if self._static is None or self._static.size() != size:
            self._static = QPixmap(size)
            self._static.setDevicePixelRatio(dpr)
            sp = QPainter(self._static)
            self._paint_static(sp)
            sp.end()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._static)
        self._paint_playhead(p)

    def _paint_static(self, p: QPainter):
        p.fillRect(self.rect(), QColor("#16171a"))
        self._paint_ruler(p)
        p.fillRect(QRectF(0, TRACK_Y, self.width(), TRACK_H), QColor("#1d1f23"))
        p.fillRect(QRectF(0, CAP_Y, self.width(), CAP_H), QColor("#1a1b1f"))
        self._paint_sounds(p)
        self._paint_texts(p)

        if not self.project.clips:
            p.setPen(QColor("#5d6068"))
            p.drawText(QRectF(0, TRACK_Y, self.width(), TRACK_H), Qt.AlignCenter,
                       "Timeline is empty — use Auto Edit, or right-click footage → Append")
        for c, r, t in self._clip_rects():
            if r.right() < 0 or r.left() > self.width():
                continue
            self._paint_clip(p, c, r)
        self._paint_edits(p)
        self._paint_fades(p)

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

    def _paint_fades(self, p: QPainter):
        """Fade in / out at the ends of the timeline: a shaded ramp over the picture and / or the sound."""
        st = self.project.settings
        dur = self.project.duration
        if not self.project.clips:
            return
        wy = TRACK_Y + TRACK_H - WAVE_H
        bands = []
        if st.fades(dur) != (0.0, 0.0):
            bands.append((TRACK_Y, wy, st.fades(dur)))
        if st.fades(dur, sound=True) != (0.0, 0.0):
            bands.append((wy, TRACK_Y + TRACK_H, st.fades(dur, sound=True)))
        p.setPen(QPen(QColor(255, 255, 255, 200), 1))
        p.setBrush(QColor(0, 0, 0, 140))
        for top, bot, (fi, fo) in bands:
            if fi > 0:
                x0, x1 = self.t_to_x(0.0), self.t_to_x(fi)
                p.drawPolygon(QPolygonF([QPointF(x0, top), QPointF(x1, top), QPointF(x0, bot)]))
            if fo > 0:
                x0, x1 = self.t_to_x(dur - fo), self.t_to_x(dur)
                p.drawPolygon(QPolygonF([QPointF(x0, top), QPointF(x1, top), QPointF(x1, bot)]))

    def _paint_edits(self, p: QPainter):
        """Transitions (bow tie over the cut) and J/L split edits (badge where the sound cuts)."""
        if len(self.project.clips) < 2:
            return
        f = QFont()
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        starts = self.project.clip_starts()
        for k, e in enumerate(edits(self.project)):
            if k == 0:
                continue
            x = self.t_to_x(starts[k])
            if x < -40 or x > self.width() + 40:
                continue
            if e.half:
                w = max(8.0, e.half * self.pps)
                top, bot = TRACK_Y + 17, TRACK_Y + TRACK_H - WAVE_H - 1
                poly = QPolygonF([QPointF(x - w, top), QPointF(x + w, bot), QPointF(x + w, top), QPointF(x - w, bot)])
                p.setPen(QPen(QColor(255, 255, 255, 220), 1))
                p.setBrush(QColor(20, 20, 24, 150))
                p.drawPolygon(poly)
            elif e.split:
                xa = self.t_to_x(starts[k] + e.split)
                wy = TRACK_Y + TRACK_H - WAVE_H
                p.setPen(QPen(QColor("#ffd34d"), 2))
                p.drawLine(QPointF(x, wy - 4), QPointF(xa, wy - 4))
                p.drawLine(QPointF(xa, wy - 4), QPointF(xa, TRACK_Y + TRACK_H))
                r = QRectF(xa - 7, wy - 13, 14, 12)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#ffd34d"))
                p.drawRoundedRect(r, 2, 2)
                p.setPen(QColor("#1a1a1a"))
                p.drawText(r, Qt.AlignCenter, "J" if e.split < 0 else "L")

    def _paint_playhead(self, p: QPainter):
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
        if c.id in self._selection and c.id != self.selected:
            p.setPen(QPen(QColor(255, 255, 255, 170), 2))
            p.setBrush(QColor(255, 255, 255, 28))
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

    # ------------------------------------------------------------------ sound-effect layer
    def _sound_rows(self) -> list[tuple[SoundClip, int]]:
        """Each sound with its display row (0/1): overlapping sounds alternate rows."""
        return self._rows(self.project.sounds)

    def _sound_rects(self) -> list[tuple[SoundClip, QRectF]]:
        out = []
        for x, row in self._sound_rows():
            x0, x1 = self.t_to_x(x.start), self.t_to_x(x.end)
            out.append((x, QRectF(x0, SFX_Y + row * SFX_ROW + 1, max(3.0, x1 - x0), SFX_ROW - 2)))
        return out

    def _sound_hit(self, pos: QPointF):
        if not (SFX_Y <= pos.y() <= SFX_Y + SFX_H):
            return None, None
        for x, r in reversed(self._sound_rects()):
            if r.left() - 2 <= pos.x() <= r.right() + 2 and r.top() <= pos.y() <= r.bottom():
                if pos.x() - r.left() <= 5 and r.width() > 15:
                    return x, "s_trim_l"
                if r.right() - pos.x() <= 5 and r.width() > 15:
                    return x, "s_trim_r"
                return x, "s_move"
        return None, "lane"

    def _paint_sounds(self, p: QPainter):
        p.fillRect(QRectF(0, SFX_Y, self.width(), SFX_H), QColor("#1b1d21"))
        f = QFont()
        f.setPixelSize(10)
        p.setFont(f)
        if not self.project.sounds:
            p.setPen(QColor("#4d5058"))
            p.drawText(QRectF(8, SFX_Y, self.width() - 16, SFX_H), Qt.AlignVCenter | Qt.AlignLeft,
                       "Sound effects — drop audio files here, or add them from the Sounds tab")
            return
        for x, r in self._sound_rects():
            if r.right() < 0 or r.left() > self.width():
                continue
            sel = x.id == self.selected_sound
            p.setPen(QPen(QColor("#ffffff"), 1.5) if sel else Qt.NoPen)
            p.setBrush(QColor("#b9672c") if sel else QColor("#8f5428"))
            p.drawRoundedRect(r, 3, 3)
            env = sfx.envelope(x.path) if os.path.exists(x.path) else None
            if env is not None and r.width() > 6:
                xs = np.arange(max(r.left(), 0), min(r.right(), self.width()), 2.0)
                if xs.size > 1:
                    ts = x.src_in + (xs - r.left()) / self.pps
                    amp = env[np.clip((ts * sfx.ENVELOPE_RATE).astype(int), 0, env.size - 1)] * min(1.0, x.volume)
                    mid, hh = r.center().y(), (r.height() / 2 - 1) * amp
                    poly = QPolygonF([QPointF(a, mid - h) for a, h in zip(xs, hh)] +
                                     [QPointF(a, mid + h) for a, h in zip(xs[::-1], hh[::-1])])
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(255, 220, 180, 110))
                    p.drawPolygon(poly)
            self._paint_sound_fades(p, x, r)
            if r.width() > 24:
                p.setPen(QColor("#fff4ea"))
                label = x.name or os.path.splitext(os.path.basename(x.path))[0]
                if abs(x.volume - 1.0) > 0.01:
                    label += f"  {x.volume * 100:.0f}%"
                p.drawText(r.adjusted(4, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           p.fontMetrics().elidedText(label, Qt.ElideRight, int(r.width() - 7)))

    def _paint_sound_fades(self, p: QPainter, x: SoundClip, r: QRectF):
        """Fade in / out as shaded corners with a ramp line."""
        fi, fo = x.fades()
        p.setPen(QPen(QColor(255, 244, 234, 200), 1))
        p.setBrush(QColor(0, 0, 0, 90))
        for w, left in ((fi * self.pps, True), (fo * self.pps, False)):
            if w < 2:
                continue
            if left:
                p.drawPolygon(QPolygonF([r.topLeft(), QPointF(r.left() + w, r.top()), r.bottomLeft()]))
            else:
                p.drawPolygon(QPolygonF([QPointF(r.right() - w, r.top()), r.topRight(), r.bottomRight()]))

    # ------------------------------------------------------------------ text track
    @staticmethod
    def _rows(items) -> list[tuple[object, int]]:
        """Each item (with .start / .end) and its display row (0/1): overlapping items alternate rows."""
        out, row_end = [], [-1e9, -1e9]
        for x in sorted(items, key=lambda x: x.start):
            row = 0 if row_end[0] <= x.start + 1e-6 else 1 if row_end[1] <= x.start + 1e-6 else \
                (0 if row_end[0] <= row_end[1] else 1)
            row_end[row] = max(row_end[row], x.end)
            out.append((x, row))
        return out

    def _text_rects(self) -> list[tuple[object, QRectF]]:
        out = []
        for x, row in self._rows(self.project.texts):
            x0, x1 = self.t_to_x(x.start), self.t_to_x(x.end)
            out.append((x, QRectF(x0, TEXT_Y + row * TEXT_ROW + 1, max(3.0, x1 - x0), TEXT_ROW - 2)))
        return out

    def _text_hit(self, pos: QPointF):
        if not (TEXT_Y <= pos.y() <= TEXT_Y + TEXT_H):
            return None, None
        for x, r in reversed(self._text_rects()):
            if r.left() - 2 <= pos.x() <= r.right() + 2 and r.top() <= pos.y() <= r.bottom():
                if pos.x() - r.left() <= 5 and r.width() > 15:
                    return x, "t_trim_l"
                if r.right() - pos.x() <= 5 and r.width() > 15:
                    return x, "t_trim_r"
                return x, "t_move"
        return None, "lane"

    def _paint_texts(self, p: QPainter):
        p.fillRect(QRectF(0, TEXT_Y, self.width(), TEXT_H), QColor("#1b1d21"))
        f = QFont()
        f.setPixelSize(10)
        p.setFont(f)
        if not self.project.texts:
            p.setPen(QColor("#4d5058"))
            p.drawText(QRectF(8, TEXT_Y, self.width() - 16, TEXT_H), Qt.AlignVCenter | Qt.AlignLeft,
                       "Titles & text — right-click here to add one, or use the Text tab")
            return
        for x, r in self._text_rects():
            if r.right() < 0 or r.left() > self.width():
                continue
            sel = x.id == self.selected_text
            p.setPen(QPen(QColor("#ffffff"), 1.5) if sel else Qt.NoPen)
            p.setBrush(QColor("#2f9e8f") if sel else QColor("#237a6f"))
            p.drawRoundedRect(r, 3, 3)
            if r.width() > 18:
                p.setPen(QColor("#eafffb"))
                label = "T  " + x.text.replace("\n", " / ")
                p.drawText(r.adjusted(4, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           p.fontMetrics().elidedText(label, Qt.ElideRight, int(r.width() - 7)))

    def select_text(self, text_id: str):
        self.selected_text = text_id
        if text_id:
            if self._selected:
                self.selected = ""
                self.selection_changed.emit("")
            self.selected_sound = ""
        self.text_selected.emit(text_id)
        self.update()

    def _text_press(self, e, pos) -> bool:
        x, part = self._text_hit(pos)
        if part is None:
            return False
        t = self.x_to_t(pos.x())
        if x is None:     # empty part of the lane
            if e.button() == Qt.RightButton:
                self._lane_menu(e.globalPosition().toPoint(), t)
                return True
            self.select_text("")
            self.seek.emit(t)
            self._drag = {"kind": "scrub"}
            return True
        self.select_text(x.id)
        if e.button() == Qt.RightButton:
            self._text_menu(e.globalPosition().toPoint(), x.id)
            return True
        self._drag = {"kind": part, "text": x.id, "x0": pos.x(), "start0": x.start, "dur0": x.duration,
                      "active": False}
        return True

    def _text_move(self, pos, d: dict):
        x = next((s for s in self.project.texts if s.id == d["text"]), None)
        if x is None:
            return
        dx = pos.x() - d["x0"]
        if not d["active"]:
            if abs(dx) < 3:
                return
            d["active"] = True
            self.edit_started.emit()
        dt = dx / self.pps
        snaps = [self.playhead] + self.project.clip_starts() + [self.project.duration]
        if d["kind"] == "t_move":
            start = max(0.0, d["start0"] + dt)
            for t in snaps:   # snap either edge to the playhead and the cuts
                if abs((start - t) * self.pps) < 6:
                    start = t
                    break
                if abs((start + x.duration - t) * self.pps) < 6:
                    start = t - x.duration
                    break
            x.start = max(0.0, self._snap(start))
        elif d["kind"] == "t_trim_l":
            end = d["start0"] + d["dur0"]
            start = min(max(0.0, d["start0"] + dt), end - MIN_DUR)
            start = next((t for t in snaps if abs((start - t) * self.pps) < 6 and t < end - MIN_DUR), start)
            x.start, x.duration = start, end - start
        else:
            end = max(d["start0"] + MIN_DUR, d["start0"] + d["dur0"] + dt)
            end = next((t for t in snaps if abs((end - t) * self.pps) < 6 and t > d["start0"] + MIN_DUR), end)
            x.duration = end - d["start0"]
        self.update()
        self.texts_changed.emit()

    def _menu_action(self, m: QMenu, label: str, key: str, payload: str):
        a = QAction(label, m)
        a.triggered.connect(lambda _=False: self.context_action.emit(key, payload))
        m.addAction(a)
        return a

    def _add_text_menu(self, m: QMenu, t: float, title: str = "Add text here"):
        sub = m.addMenu(title)
        for key, cfg in TEXT_PRESETS.items():
            self._menu_action(sub, cfg["label"], f"text_add:{key}", f"{t:.4f}")

    def _text_menu(self, gpos, text_id: str):
        m = QMenu(self)
        self._menu_action(m, "Edit text…\tDouble-click", "text_edit", text_id)
        self._menu_action(m, "Move to playhead", "text_to_playhead", text_id)
        self._menu_action(m, "Duplicate", "text_duplicate", text_id)
        for title, table, prefix in (("Animation in", ANIM_IN, "text_anim_in"), ("Animation out", ANIM_OUT, "text_anim_out")):
            sub = m.addMenu(title)
            for k, label in table.items():
                self._menu_action(sub, label, f"{prefix}:{k}", text_id)
        sub = m.addMenu("Position")
        for k, label in (("top", "Top"), ("middle", "Middle"), ("bottom", "Bottom"), ("lower_left", "Lower left")):
            self._menu_action(sub, label, f"text_pos:{k}", text_id)
        m.addSeparator()
        self._menu_action(m, "Delete text\tDel", "text_delete", text_id)
        m.exec(gpos)

    def _lane_menu(self, gpos, t: float):
        """Right-click on an empty part of the timeline."""
        m = QMenu(self)
        self._add_text_menu(m, t)
        self._menu_action(m, "Add sound effect…", "open_sounds", "")
        m.addSeparator()
        self._menu_action(m, "Move playhead here", "seek", f"{t:.4f}")
        if self.project.clips:
            self._menu_action(m, "Split at playhead\tS", "split_playhead", "")
            self._menu_action(m, "Select all clips\tCtrl+A", "select_all", "")
        self._menu_action(m, "Fit timeline\tShift+Z", "fit", "")
        m.addSeparator()
        self._menu_action(m, "Generate captions", "captions_generate", "")
        m.exec(gpos)

    def _caption_menu(self, gpos, index: int, t: float):
        m = QMenu(self)
        if index >= 0:
            self._menu_action(m, "Edit caption…", "caption_edit", str(index))
            self._menu_action(m, "Delete caption", "caption_delete", str(index))
            m.addSeparator()
        if self.captions:
            self._menu_action(m, "Delete all captions…", "caption_delete_all", "")
        self._menu_action(m, "Generate captions", "captions_generate", "")
        self._menu_action(m, "Caption style…", "caption_style", "")
        m.addSeparator()
        self._add_text_menu(m, t)
        m.exec(gpos)

    def _select_sound(self, sound_id: str):
        self.selected_sound = sound_id
        if sound_id and self._selected:
            self.selected = ""
            self.selection_changed.emit("")
        if sound_id and self.selected_text:
            self.select_text("")
        self.sound_selected.emit(sound_id)
        self.update()

    def _sound_press(self, e, pos) -> bool:
        x, part = self._sound_hit(pos)
        if part is None:
            return False
        if x is None:     # empty part of the lane: deselect and move the playhead
            self._select_sound("")
            self.seek.emit(self.x_to_t(pos.x()))
            self._drag = {"kind": "scrub"}
            return True
        self._select_sound(x.id)
        if e.button() == Qt.RightButton:
            self._sound_menu(e.globalPosition().toPoint(), x.id)
            return True
        if x.path not in self._sound_max:
            self._sound_max[x.path] = sfx.duration(x.path) or x.src_out
        self._drag = {"kind": part, "sound": x.id, "x0": pos.x(), "start0": x.start, "in0": x.src_in,
                      "out0": x.src_out, "active": False}
        return True

    def _sound_move(self, pos, d: dict):
        x = next((s for s in self.project.sounds if s.id == d["sound"]), None)
        if x is None:
            return
        dx = pos.x() - d["x0"]
        if not d["active"]:
            if abs(dx) < 3:
                return
            d["active"] = True
            self.edit_started.emit()
        dt = dx / self.pps
        if d["kind"] == "s_move":
            start = max(0.0, d["start0"] + dt)
            # Snap the start to the playhead and to video cuts within a few pixels.
            for t in [self.playhead] + self.project.clip_starts():
                if abs((start - t) * self.pps) < 6:
                    start = t
                    break
            x.start = self._snap(start)
        elif d["kind"] == "s_trim_l":
            new_in = min(max(0.0, d["in0"] + dt), d["out0"] - 0.05)
            new_in = max(new_in, d["in0"] - d["start0"])          # the sound can't start before 0
            x.src_in = new_in
            x.start = d["start0"] + (new_in - d["in0"])
        else:
            x.src_out = max(min(self._sound_max.get(x.path, d["out0"]), d["out0"] + dt), d["in0"] + 0.05)
        self.update()

    def _sound_menu(self, gpos, sound_id: str):
        m = QMenu(self)
        for key, label, tip in (("sound_to_playhead", "Move to playhead", ""), ("sound_preview", "Preview", ""),
                                ("sound_fit", "Fit to timeline",
                                 "Cover the whole timeline: repeat the sound with crossfades if it is shorter, "
                                 "cut it with a fade out if it is longer")):
            a = QAction(label, m)
            a.setToolTip(tip)
            a.triggered.connect(lambda _=False, k=key: self.context_action.emit(k, sound_id))
            m.addAction(a)
        m.setToolTipsVisible(True)
        x = next((s for s in self.project.sounds if s.id == sound_id), None)
        if x is not None:
            for key, label, cur in (("sound_fade_in", "Fade in", x.fade_in), ("sound_fade_out", "Fade out", x.fade_out)):
                sub = m.addMenu(f"{label} ({cur:g} s)" if cur > 0 else label)
                for v in (0.0, 0.5, 1.0, 2.0, 3.0, 5.0):
                    a = sub.addAction("Off" if v == 0 else f"{v:g} s")
                    a.setCheckable(True)
                    a.setChecked(abs(cur - v) < 1e-3)
                    a.triggered.connect(lambda _=False, k=key, v=v: self.context_action.emit(f"{k}:{v}", sound_id))
            m.addSeparator()
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(12, 4, 12, 4)
            lbl = QLabel(f"Volume {x.volume * 100:.0f}%")
            lbl.setMinimumWidth(80)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, 200)
            sl.setValue(round(x.volume * 100))
            sl.setMinimumWidth(140)
            sl.valueChanged.connect(lambda v: (lbl.setText(f"Volume {v}%"),
                                               self.context_action.emit(f"sound_volume:{v / 100}", sound_id)))
            row.addWidget(lbl)
            row.addWidget(sl, 1)
            act = QWidgetAction(m)
            act.setDefaultWidget(box)
            m.addAction(act)
        m.addSeparator()
        a = QAction("Delete sound\tDel", m)
        a.triggered.connect(lambda: self.context_action.emit("sound_delete", sound_id))
        m.addAction(a)
        m.exec(gpos)

    def _drop_paths(self, e) -> list[str]:
        md = e.mimeData()
        if not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile()
                and u.toLocalFile().lower().endswith(sfx.AUDIO_EXTENSIONS)]

    def dragEnterEvent(self, e):
        if self._drop_paths(e):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if self._drop_paths(e):
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = self._drop_paths(e)
        if paths:
            e.acceptProposedAction()
            self.sound_dropped.emit(paths, self._snap(self.x_to_t(e.position().x())))

    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() in (Qt.LeftButton, Qt.RightButton) and self._text_press(e, pos):
            return
        if e.button() == Qt.RightButton and SFX_Y <= pos.y() <= SFX_Y + SFX_H and self._sound_hit(pos)[0] is None:
            self._lane_menu(e.globalPosition().toPoint(), self.x_to_t(pos.x()))
            return
        if e.button() in (Qt.LeftButton, Qt.RightButton) and self._sound_press(e, pos):
            return
        if self.selected_sound and e.button() == Qt.LeftButton:
            self._select_sound("")
        if self.selected_text and e.button() == Qt.LeftButton:
            self.select_text("")
        if e.button() == Qt.RightButton:
            t = self.x_to_t(pos.x())
            if CAP_Y <= pos.y() <= CAP_Y + CAP_H:
                idx = next((i for i, cap in enumerate(self.captions) if cap.start <= t < cap.end), -1)
                self._caption_menu(e.globalPosition().toPoint(), idx, t)
                return
            c, _ = self._hit(pos)
            if c is None:
                self._lane_menu(e.globalPosition().toPoint(), t)
                return
            if c is not None:
                if c.id not in self._selection:   # keep a multi-selection the clip belongs to
                    self.selected = c.id
                self._selected = c.id
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
        mods = e.modifiers()
        if mods & Qt.ControlModifier:
            self._click_select(c.id, mods)
            return
        # Shift+click selects a range, Shift+drag slips: decided on release (see mouseReleaseEvent).
        if not (mods & Qt.ShiftModifier) and (c.id not in self._selection or len(self._selection) == 1):
            if self.selected != c.id:
                self.selected = c.id
                self.selection_changed.emit(c.id)
        kind = part
        if part == "body":
            slip = self.mode == "slip" or (mods & (Qt.AltModifier | Qt.ShiftModifier))
            kind = "slip" if slip else "move"
        self._drag = {"kind": kind, "clip": c.id, "x0": pos.x(), "in0": c.src_in, "out0": c.src_out,
                      "active": False, "drop": None, "mods": mods}
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        d = self._drag
        if d is None:
            tx, tpart = self._text_hit(pos)
            if tx is not None:
                self.setCursor(Qt.SizeHorCursor if tpart in ("t_trim_l", "t_trim_r") else Qt.OpenHandCursor)
                self.setToolTip(f"{tx.text}\n{tx.start:.2f}s, {tx.duration:.2f}s long\n"
                                "Drag: move · Edges: change length · Double-click: edit · Right-click: more")
                return
            x, spart = self._sound_hit(pos)
            if x is not None:
                self.setCursor(Qt.SizeHorCursor if spart in ("s_trim_l", "s_trim_r") else Qt.OpenHandCursor)
                self.setToolTip(f"{x.name}\n{x.start:.2f}s, {x.duration:.2f}s long, volume {x.volume * 100:.0f}%\n"
                                "Drag: move · Edges: trim · Right-click: volume / delete")
                return
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
            self._scrub_x = pos.x()
            self.seek.emit(self.x_to_t(min(max(0.0, pos.x()), self.width())))
            if self._edge_speed(pos.x()) and not self._edge_timer.isActive():
                self._edge_timer.start()
            return
        if "sound" in d:
            self._sound_move(pos, d)
            return
        if "text" in d:
            self._text_move(pos, d)
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
        self._edge_timer.stop()
        if d is None:
            return
        if "sound" in d:
            if d["active"]:
                self.sounds_changed.emit()
                self.edit_finished.emit()
            self.setCursor(Qt.ArrowCursor)
            self.refresh()
            return
        if "text" in d:
            if d["active"]:
                self.texts_changed.emit()
                self.edit_finished.emit()
            else:
                self.seek.emit(self.x_to_t(e.position().x()))
            self.setCursor(Qt.ArrowCursor)
            self.refresh()
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
        elif not d.get("active") and d.get("mods", Qt.NoModifier) & Qt.ShiftModifier:
            self._click_select(d["clip"], d["mods"])
        elif not d.get("active") and d["kind"] != "scrub":
            # Simple click on a clip: select only it, and move the playhead there.
            if len(self._selection) > 1:
                self.selected = d["clip"]
                self.selection_changed.emit(d["clip"])
            if d["kind"] == "move":
                self.seek.emit(self.x_to_t(e.position().x()))
        self.setCursor(Qt.ArrowCursor)
        self.refresh()

    def mouseDoubleClickEvent(self, e):
        tx, _ = self._text_hit(e.position())
        if tx is not None:
            self.context_action.emit("text_edit", tx.id)
            return
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
        n = len(self._selection) if clip_id in self._selection else 1
        many = f" {n} clips" if n > 1 else ""
        items = [("split", "Split at playhead\tS")] if n == 1 else []
        items += [("transition", None), ("split", None),
                  ("duplicate", f"Duplicate{many}"), ("auto_color", f"Auto color{many}"),
                  ("reset_color", f"Reset color{many}"),
                  ("copy_grade", "Copy this clip's grade to the selected clips" if n > 1 else "Copy grade to all clips"),
                  ("alt_moment", f"Other moment{many}	R"),
                  ("face_zoom", f"Zoom{many} to face"),
                  (None, None), ("select_all", "Select all clips\tCtrl+A"), ("delete", f"Delete{many}\tDel")]
        for key, label in items:
            if key is None:
                m.addSeparator()
                continue
            if key == "transition":
                sub = m.addMenu(f"Transition into {'these clips' if n > 1 else 'this clip'}")
                for tk, tl in [("", "None (cut)")] + [(k, v[0]) for k, v in TRANSITIONS.items()]:
                    a = QAction(tl, sub)
                    a.triggered.connect(lambda _=False, tk=tk: self.context_action.emit(f"transition:{tk}", clip_id))
                    sub.addAction(a)
                continue
            if key == "split":
                sub = m.addMenu("Audio edit (J / L cut)")
                for sv, sl in ((0.0, "None (straight cut)"), (-0.5, "J-cut: sound starts 0.5 s early"),
                               (-1.0, "J-cut: sound starts 1 s early"), (0.5, "L-cut: sound runs on 0.5 s"),
                               (1.0, "L-cut: sound runs on 1 s")):
                    a = QAction(sl, sub)
                    a.triggered.connect(lambda _=False, sv=sv: self.context_action.emit(f"split:{sv}", clip_id))
                    sub.addAction(a)
                m.addSeparator()
                continue
            act = QAction(label, m)
            act.triggered.connect(lambda _=False, k=key: self.context_action.emit(k, clip_id))
            m.addAction(act)
        m.exec(gpos)


class TimelinePanel(QWidget):
    """Timeline view + mode buttons, zoom controls and scrollbar."""

    fades_changed = Signal()   # the fade controls edited project.settings

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.view = TimelineView(project)
        self.scroll = QScrollBar(Qt.Horizontal)
        self.scroll.valueChanged.connect(self._on_scroll)
        self.view.view_changed.connect(self._sync_scroll)

        self.btn_undo = QToolButton(text="↶ Undo")
        self.btn_undo.setToolTip("Undo the last change (Ctrl+Z)")
        self.btn_redo = QToolButton(text="↷ Redo")
        self.btn_redo.setToolTip("Redo the change you undid (Ctrl+Shift+Z or Ctrl+Y)")
        for b in (self.btn_undo, self.btn_redo):
            b.setEnabled(False)
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
        self.btn_fade = QToolButton(text="◐ Fade")
        self.btn_fade.setToolTip("Fade the picture and / or sound in from black at the start of the timeline "
                                 "and out at its end")
        self.btn_fade.setPopupMode(QToolButton.InstantPopup)
        self.btn_fade.setMenu(self._fade_menu())
        self.btn_hook = QToolButton(text="🎣 Hook")
        self.btn_hook.setToolTip("Replace the first clip (the hook) with another strong moment not used in the "
                                 "edit (H). Click again for the next one; Undo brings the previous back.")
        self.btn_alt = QToolButton(text="🔀 Other moment")
        self.btn_alt.setToolTip("Swap the selected clip for the best moment close by in its footage (R). Its length "
                                "follows the new moment. Click again for the next one; Undo brings the previous back.")
        self.btn_fit = QToolButton(text="Fit")
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(2, 400)
        self.zoom.setValue(40)
        self.zoom.setFixedWidth(140)
        self.zoom.valueChanged.connect(lambda v: self.view.set_zoom(float(v)) if abs(v - self.view.pps) > 0.5 else None)
        self.btn_fit.clicked.connect(self.view.fit)
        self.lbl = QLabel("")
        self.lbl.setStyleSheet("color: #8b909a;")

        # A toolbar rather than a row of buttons: when the window is narrow, the buttons that don't fit go into
        # its » menu instead of forcing the whole window (and the docks beside it) wider.
        top = QToolBar()
        top.setObjectName("timelineBar")
        top.setStyleSheet("QToolBar#timelineBar { background: transparent; padding: 2px 4px; spacing: 6px; }"
                          "QToolButton#qt_toolbar_ext_button { padding: 0px; min-width: 18px; }")
        top.addWidget(self.btn_undo)
        top.addWidget(self.btn_redo)
        sep = QLabel("|")
        sep.setStyleSheet("color: #45484f;")
        top.addWidget(sep)
        for w in (self.btn_move, self.btn_slip, self.btn_split, self.btn_delete, self.btn_fade):
            top.addWidget(w)
        sep = QLabel("|")
        sep.setStyleSheet("color: #45484f;")
        top.addWidget(sep)
        for w in (self.btn_hook, self.btn_alt):
            top.addWidget(w)
        self.lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # Always room for the clip count and length; the overlap warning after it may be clipped (tooltip).
        self.lbl.setMinimumWidth(self.lbl.fontMetrics().horizontalAdvance("000 clips · 00:00:00") + 8)
        top.addWidget(self.lbl)
        top.addWidget(QLabel("Zoom"))
        top.addWidget(self.zoom)
        top.addWidget(self.btn_fit)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(top)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.scroll)

    FADE_LENGTHS = (0.5, 1.0, 2.0, 3.0)

    def _fade_menu(self) -> QMenu:
        m = QMenu(self)
        box = QWidget()
        f = QFormLayout(box)
        f.setContentsMargins(10, 8, 10, 8)
        self.btn_fade_in, self.btn_fade_out = QPushButton("Fade in at start"), QPushButton("Fade out at end")
        toggles = QHBoxLayout()
        for b in (self.btn_fade_in, self.btn_fade_out):
            b.setCheckable(True)
            b.setObjectName("toggle")
            b.toggled.connect(self._fades_edited)
            toggles.addWidget(b)
        self._fade_len = 1.0
        self.fade_len_group = QButtonGroup(box)
        self.fade_len_group.setExclusive(True)
        lengths = QHBoxLayout()
        for k, v in enumerate(self.FADE_LENGTHS):
            b = QPushButton(f"{v:g} s", checkable=True)
            b.setObjectName("toggle")
            self.fade_len_group.addButton(b, k)
            lengths.addWidget(b)
        self.fade_len_group.idClicked.connect(self._fade_length_picked)
        self.chk_fade_picture = QCheckBox("Picture (from / to black)")
        self.chk_fade_sound = QCheckBox("Sound (from / to silence)")
        for chk in (self.chk_fade_picture, self.chk_fade_sound):
            chk.toggled.connect(self._fades_edited)
        f.addRow(toggles)
        f.addRow("Length", lengths)
        f.addRow(self.chk_fade_picture)
        f.addRow(self.chk_fade_sound)
        act = QWidgetAction(m)
        act.setDefaultWidget(box)
        m.addAction(act)
        return m

    def sync_fades(self):
        """Show the project's fade settings."""
        s = self.view.project.settings
        ws = (self.btn_fade_in, self.btn_fade_out, self.chk_fade_picture, self.chk_fade_sound)
        for w in ws:
            w.blockSignals(True)
        self._fade_len = s.fade_in if s.fade_in > 0 else (s.fade_out if s.fade_out > 0 else self._fade_len)
        self.btn_fade_in.setChecked(s.fade_in > 0)
        self.btn_fade_out.setChecked(s.fade_out > 0)
        self.chk_fade_picture.setChecked(s.fade_picture)
        self.chk_fade_sound.setChecked(s.fade_sound)
        for w in ws:
            w.blockSignals(False)
        self._show_fade_length()
        self._fade_label()

    def _show_fade_length(self):
        k = min(range(len(self.FADE_LENGTHS)), key=lambda i: abs(self.FADE_LENGTHS[i] - self._fade_len))
        self.fade_len_group.button(k).setChecked(True)

    def _fade_length_picked(self, k: int):
        self._fade_len = self.FADE_LENGTHS[k]
        self._fades_edited()

    def _fades_edited(self):
        s = self.view.project.settings
        s.fade_in = self._fade_len if self.btn_fade_in.isChecked() else 0.0
        s.fade_out = self._fade_len if self.btn_fade_out.isChecked() else 0.0
        s.fade_picture, s.fade_sound = self.chk_fade_picture.isChecked(), self.chk_fade_sound.isChecked()
        self._fade_label()
        self.view.update()
        self.fades_changed.emit()

    def _fade_label(self):
        s = self.view.project.settings
        on = (s.fade_in > 0 or s.fade_out > 0) and (s.fade_picture or s.fade_sound)
        ends = "/".join(n for n, v in (("in", s.fade_in), ("out", s.fade_out)) if v > 0)
        self.btn_fade.setText("◐ Fade" + (f" {ends} {max(s.fade_in, s.fade_out):g} s" if on else ""))

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
        self.lbl.setToolTip(txt)   # clipped when the timeline is narrow
