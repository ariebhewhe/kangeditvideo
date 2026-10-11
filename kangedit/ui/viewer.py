"""Timeline viewer: decodes, grades, reframes and overlays captions in real time."""

from __future__ import annotations

import os
import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, Signal, QPointF
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QFontMetricsF
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget, QStyle

from .. import reframe
from ..captions import Caption, wrap
from ..color import Grade, LutCache, apply_grade, needs_color
from ..config import frame_size
from ..decoder import DecoderPool
from ..models import Project
from .theme import fmt_time


class Canvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image: Optional[QImage] = None
        self.caption: Optional[str] = None
        self.caption_style = None
        self.out_h = 1080
        self.message = "Import footage and press Auto Edit"
        self.show_guides = False
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 180)

    def sizeHint(self):
        return QSize(800, 450)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0d0e10"))
        if self.image is None:
            p.setPen(QColor("#6b6e76"))
            p.drawText(self.rect(), Qt.AlignCenter, self.message)
            return
        iw, ih = self.image.width(), self.image.height()
        s = min(self.width() / iw, self.height() / ih)
        w, h = iw * s, ih * s
        r = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(r, self.image)
        if self.show_guides:
            p.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.DashLine))
            for k in (1, 2):
                p.drawLine(QPointF(r.left() + r.width() * k / 3, r.top()), QPointF(r.left() + r.width() * k / 3, r.bottom()))
                p.drawLine(QPointF(r.left(), r.top() + r.height() * k / 3), QPointF(r.right(), r.top() + r.height() * k / 3))
        if self.caption and self.caption_style is not None:
            self._draw_caption(p, r)

    def _draw_caption(self, p: QPainter, r: QRectF):
        st = self.caption_style
        lines = wrap(self.caption, st.max_chars, st.max_lines)
        if not lines:
            return
        px = max(8.0, r.height() * st.size_pct / 100.0)
        font = QFont(st.font)
        font.setPixelSize(int(px))
        font.setBold(True)
        fm = QFontMetricsF(font)
        line_h = fm.height()
        block_h = line_h * len(lines)
        if st.position == "top":
            y0 = r.top() + r.height() * 0.08
        elif st.position == "middle":
            y0 = r.center().y() - block_h / 2
        else:
            y0 = r.bottom() - r.height() * 0.08 - block_h
        p.setRenderHint(QPainter.Antialiasing)
        for i, line in enumerate(lines):
            tw = fm.horizontalAdvance(line)
            x = r.center().x() - tw / 2
            base = y0 + i * line_h + fm.ascent()
            if st.box:
                pad = px * 0.2
                p.fillRect(QRectF(x - pad, y0 + i * line_h - pad / 2, tw + 2 * pad, line_h + pad / 2), QColor(0, 0, 0, 140))
            path = QPainterPath()
            path.addText(QPointF(x, base), font, line)
            if st.outline and not st.box:
                p.strokePath(path, QPen(QColor(0, 0, 0, 230), max(2.0, px / 7), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.fillPath(path, QColor(st.color))


class Viewer(QWidget):
    time_changed = Signal(float)
    play_state = Signal(bool)

    PREVIEW_SHORT = 540

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.decoders = DecoderPool()
        self.luts = LutCache()
        self.captions: list[Caption] = []
        self.t = 0.0
        self.playing = False
        self._play_t0 = 0.0
        self._play_clock = 0.0
        self._override: Optional[tuple[str, float]] = None   # (clip_id, source time) during slip
        self._audio_path: Optional[str] = None
        self.audio_valid = False

        self.canvas = Canvas()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self._lut_timer = QTimer(self, singleShot=True, interval=250)
        self._lut_timer.timeout.connect(self._build_pending_lut)
        self._pending_lut = None

        self.audio_out = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_out)

        st = self.style()
        self.btn_start = QPushButton(st.standardIcon(QStyle.SP_MediaSkipBackward), "")
        self.btn_prev = QPushButton(st.standardIcon(QStyle.SP_MediaSeekBackward), "")
        self.btn_play = QPushButton(st.standardIcon(QStyle.SP_MediaPlay), "")
        self.btn_next = QPushButton(st.standardIcon(QStyle.SP_MediaSeekForward), "")
        self.btn_end = QPushButton(st.standardIcon(QStyle.SP_MediaSkipForward), "")
        for b, tip in ((self.btn_start, "Go to start (Home)"), (self.btn_prev, "Previous clip (Up)"),
                       (self.btn_play, "Play / Pause (Space)"), (self.btn_next, "Next clip (Down)"),
                       (self.btn_end, "Go to end (End)")):
            b.setToolTip(tip)
            b.setFixedWidth(40)
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_start.clicked.connect(lambda: self.seek(0))
        self.btn_end.clicked.connect(lambda: self.seek(max(0.0, self.project.duration - 1e-3)))
        self.btn_prev.clicked.connect(self.prev_clip)
        self.btn_next.clicked.connect(self.next_clip)
        self.lbl_time = QLabel("00:00:00 / 00:00:00")
        self.lbl_time.setStyleSheet("font-family: Consolas, monospace; font-size: 13px;")
        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet("color: #8b909a;")

        bar = QHBoxLayout()
        bar.addWidget(self.lbl_time)
        bar.addStretch(1)
        for b in (self.btn_start, self.btn_prev, self.btn_play, self.btn_next, self.btn_end):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.lbl_info)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.canvas, 1)
        lay.addLayout(bar)

    # ------------------------------------------------------------------ public api
    def set_project(self, project: Project):
        self.stop()
        self.project = project
        self.decoders.close()
        self.t = 0.0
        self.audio_valid = False
        self.refresh()

    def set_captions(self, caps: list[Caption]):
        self.captions = caps
        self.refresh()

    def set_audio(self, path: Optional[str]):
        if self.playing:
            return
        self._audio_path = path
        self.audio_valid = bool(path) and os.path.exists(path)
        self.player.setSource(QUrl.fromLocalFile(path) if self.audio_valid else QUrl())

    def invalidate_audio(self):
        self.audio_valid = False

    def seek(self, t: float):
        dur = self.project.duration
        self.t = max(0.0, min(t, max(0.0, dur - 1e-3)))
        if self.playing:
            self._play_t0 = self.t
            self._play_clock = time.perf_counter()
            if self.audio_valid:
                self.player.setPosition(int(self.t * 1000))
        self.refresh()
        self.time_changed.emit(self.t)

    def show_source(self, clip_id: Optional[str], src_t: float = 0.0):
        """Temporarily show a clip's source frame (used while slipping/trimming)."""
        self._override = (clip_id, src_t) if clip_id else None
        self.refresh()

    def toggle_play(self):
        if self.playing:
            self.stop()
        else:
            self.play()

    def play(self):
        if not self.project.clips:
            return
        if self.t >= self.project.duration - 0.05:
            self.t = 0.0
        self.playing = True
        self._override = None
        self._play_t0 = self.t
        self._play_clock = time.perf_counter()
        if self.audio_valid:
            self.player.setPosition(int(self.t * 1000))
            self.player.play()
        self.timer.start(max(5, int(1000 / max(1.0, self.project.settings.fps) / 2)))
        self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self.play_state.emit(True)

    def stop(self):
        if not self.playing:
            return
        self.playing = False
        self.timer.stop()
        self.player.pause()
        self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_state.emit(False)

    def step(self, frames: int):
        self.stop()
        self.seek(self.t + frames / max(1.0, self.project.settings.fps))

    def prev_clip(self):
        starts = self.project.clip_starts()
        prev = [s for s in starts if s < self.t - 0.05]
        self.seek(prev[-1] if prev else 0.0)

    def next_clip(self):
        starts = self.project.clip_starts()
        nxt = [s for s in starts if s > self.t + 0.01]
        if nxt:
            self.seek(nxt[0])

    # ------------------------------------------------------------------ rendering
    def _tick(self):
        if not self.playing:
            return
        t = self._play_t0 + (time.perf_counter() - self._play_clock)
        if t >= self.project.duration:
            self.t = max(0.0, self.project.duration - 1e-3)
            self.stop()
            self.refresh()
            self.time_changed.emit(self.t)
            return
        frame_dur = 1.0 / max(1.0, self.project.settings.fps)
        if abs(t - self.t) < frame_dur * 0.5:
            return
        self.t = t
        self.refresh()
        self.time_changed.emit(self.t)

    def preview_size(self) -> tuple[int, int]:
        s = self.project.settings
        tw, th = frame_size(s.aspect, s.resolution)
        k = self.PREVIEW_SHORT / min(tw, th)
        return max(2, int(tw * k) // 2 * 2), max(2, int(th * k) // 2 * 2)

    def refresh(self):
        proj = self.project
        dur = proj.duration
        fps = proj.settings.fps
        self.lbl_time.setText(f"{fmt_time(self.t, fps)} / {fmt_time(dur, fps)}")
        self.canvas.caption_style = proj.settings.captions
        if not proj.clips:
            self.canvas.image = None
            self.canvas.caption = None
            self.lbl_info.setText("")
            self.canvas.update()
            return
        idx = proj.index_of(self._override[0]) if self._override else -1
        if idx >= 0:
            clip = proj.clips[idx]
            footage = proj.footage.get(clip.footage_id)
            src_t = self._override[1]
            grade, zoom, ox, oy = clip.grade, clip.zoom, clip.offset_x, clip.offset_y
        else:
            self._override = None
            idx, off = proj.clip_at(self.t)
            if idx is None:
                return
            clip = proj.clips[idx]
            footage = proj.footage.get(clip.footage_id)
            src_t = clip.src_in + off
            grade, zoom, ox, oy = clip.grade, clip.zoom, clip.offset_x, clip.offset_y
        if footage is None:
            return
        try:
            dec = self.decoders.get(footage, proj.settings.use_proxies, self.PREVIEW_SHORT)
            img = dec.frame_at(src_t)
        except Exception as e:  # noqa: BLE001
            self.canvas.image = None
            self.canvas.message = f"Cannot decode {footage.name}: {e}"
            self.canvas.update()
            return
        if img is None:
            return
        using_proxy = proj.settings.use_proxies and footage.has_proxy()
        self.lbl_info.setText(f"{footage.name}  {'PROXY' if using_proxy else 'ORIGINAL'}"
                              f"{'  ' + footage.log_profile.upper() if footage.log_profile != 'none' else ''}")
        grade = grade or Grade()
        img = self._grade(img, grade, footage.log_profile, footage.tonemap)
        tw, th = self.preview_size()
        out = reframe.render(img, tw, th, proj.settings.fit_mode, zoom, ox, oy)
        out = np.ascontiguousarray(out)
        self._frame = out  # keep buffer alive for QImage
        self.canvas.image = QImage(out.data, out.shape[1], out.shape[0], out.strides[0], QImage.Format_RGB888)
        self.canvas.caption = None
        if self._override is None and proj.settings.captions.enabled:
            for c in self.captions:
                if c.start <= self.t < c.end:
                    self.canvas.caption = c.text
                    break
        self.canvas.update()

    def _grade(self, img: np.ndarray, grade, log_profile: str, tonemap: str) -> np.ndarray:
        if not needs_color(grade, log_profile):
            return img
        lut = self.luts.peek(grade, log_profile, tonemap)
        if lut is not None:
            return lut.apply(img)
        if self.playing:
            return self.luts.get(grade, log_profile, tonemap).apply(img)
        # While adjusting sliders: grade this frame directly, build the LUT once idle.
        small = img
        out = apply_grade(small.astype(np.float32) / 255.0, grade, log_profile, tonemap)
        self._pending_lut = (grade, log_profile, tonemap)
        self._lut_timer.start()
        return (out * 255.0 + 0.5).astype(np.uint8)

    def _build_pending_lut(self):
        if self._pending_lut:
            g, lp, tm = self._pending_lut
            self.luts.get(Grade.from_dict(g.to_dict()), lp, tm)
            self._pending_lut = None

    def grab_frames(self, clip, n: int = 5) -> list[np.ndarray]:
        """Ungraded sample frames from a clip (for auto color)."""
        f = self.project.footage[clip.footage_id]
        dec = self.decoders.get(f, self.project.settings.use_proxies, 360)
        frames = []
        for k in range(n):
            t = clip.src_in + clip.duration * (k + 0.5) / n
            img = dec.frame_at(t)
            if img is not None:
                frames.append(img.copy())
        return frames
