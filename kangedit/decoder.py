"""Frame-accurate preview decoding with PyAV."""

from __future__ import annotations

import threading
from typing import Optional

import av
import numpy as np

from .models import Footage


def _colorspace(cs: str, height: int) -> str:
    cs = (cs or "").lower()
    if cs.startswith("bt2020"):
        return "bt2020"
    if cs in ("smpte170m", "bt470bg"):
        return "itu601"
    if cs == "bt709":
        return "itu709"
    return "itu709" if height >= 576 else "itu601"


class FrameDecoder:
    """Sequential-friendly decoder: reads forward when possible, seeks otherwise."""

    def __init__(self, path: str, rotation: int = 0, color_space: str = "", max_height: int = 720):
        self.path = path
        self.rotation = rotation % 360
        self.container = av.open(path)
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"
        self.tb = float(self.stream.time_base) if self.stream.time_base else 1 / 90000
        self.start = float(self.stream.start_time * self.stream.time_base) if self.stream.start_time is not None else 0.0
        rate = self.stream.average_rate or self.stream.guessed_rate or 30
        self.frame_dur = 1.0 / float(rate)
        cc = self.stream.codec_context
        self.src_w, self.src_h = cc.width, cc.height
        self.colorspace = _colorspace(color_space, self.src_h)
        scale = min(1.0, max_height / float(min(self.src_w, self.src_h) if self.rotation in (90, 270) else self.src_h))
        self.out_w = max(2, int(self.src_w * scale) // 2 * 2)
        self.out_h = max(2, int(self.src_h * scale) // 2 * 2)
        self._iter = None
        self._last_t: Optional[float] = None
        self._last_img: Optional[np.ndarray] = None
        self._next_frame = None
        self.lock = threading.Lock()

    def close(self):
        try:
            self.container.close()
        except Exception:
            pass

    def _frames(self):
        for packet in self.container.demux(self.stream):
            for frame in packet.decode():
                yield frame

    def _to_rgb(self, frame) -> np.ndarray:
        rng = frame.color_range
        src_range = "JPEG" if rng == 2 else "MPEG"
        f = frame.reformat(width=self.out_w, height=self.out_h, format="rgb24",
                           src_colorspace=self.colorspace, src_color_range=src_range,
                           dst_color_range="JPEG", interpolation="BILINEAR")
        img = f.to_ndarray()
        if self.rotation:
            img = np.ascontiguousarray(np.rot90(img, self.rotation // 90))
        return img

    def _seek(self, t: float):
        target = int((t + self.start) / self.tb)
        self.container.seek(max(0, target), stream=self.stream, backward=True, any_frame=False)
        self._iter = self._frames()
        self._next_frame = None

    def frame_at(self, t: float) -> Optional[np.ndarray]:
        """RGB uint8 frame shown at source time t (seconds from file start)."""
        with self.lock:
            if self._last_t is not None and abs(t - self._last_t) < self.frame_dur * 0.5 and self._last_img is not None:
                return self._last_img
            forward_ok = (self._iter is not None and self._last_t is not None
                          and self._last_t - self.frame_dur * 0.5 <= t <= self._last_t + 2.0)
            if not forward_ok:
                self._seek(t)
            best = None
            try:
                while True:
                    frame = self._next_frame if self._next_frame is not None else next(self._iter)
                    self._next_frame = None
                    ft = (frame.time if frame.time is not None else 0.0) - self.start
                    if ft + self.frame_dur * 0.5 < t:
                        best = frame
                        continue
                    if ft > t + self.frame_dur * 0.5 and best is not None:
                        self._next_frame = frame
                        break
                    best = frame
                    break
            except StopIteration:
                self._iter = None
            except av.error.FFmpegError:
                self._iter = None
            if best is None:
                return self._last_img
            bt = (best.time or 0.0) - self.start
            self._last_t = bt if abs(bt - t) < 1.0 else t
            self._last_img = self._to_rgb(best)
            return self._last_img


class DecoderPool:
    """Keeps one open decoder per media file (proxy or original)."""

    def __init__(self, limit: int = 8):
        self._pool: dict[str, FrameDecoder] = {}
        self._limit = limit

    def get(self, footage: Footage, use_proxy: bool, max_height: int = 720) -> FrameDecoder:
        proxy = use_proxy and footage.has_proxy()
        path = footage.proxy_path if proxy else footage.path
        key = f"{path}|{max_height}"
        dec = self._pool.get(key)
        if dec is None:
            if len(self._pool) >= self._limit:
                old_key = next(iter(self._pool))
                self._pool.pop(old_key).close()
            dec = FrameDecoder(path, 0 if proxy else footage.rotation,
                               "bt709" if proxy else footage.color_space, max_height)
            self._pool[key] = dec
        else:
            self._pool[key] = self._pool.pop(key)  # LRU bump
        return dec

    def drop(self, path_prefix: str = "") -> None:
        for k in list(self._pool):
            if k.startswith(path_prefix):
                self._pool.pop(k).close()

    def close(self):
        self.drop("")
