"""Background frame rendering for the viewer.

Decoding (a long-GOP seek can take a second), grading and reframing all run on a worker thread so
the UI never blocks. Still requests (scrubbing, slipping, edits) are latest-wins; during playback
the worker reads ahead into a small buffer that the UI drains in sync with the playback clock.
"""

from __future__ import annotations

import bisect
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import numpy as np

from . import reframe
from .color import Grade, LutCache, apply_grade, needs_color
from .decoder import DecoderPool
from .edits import blend
from .models import Footage

READ_AHEAD = 30          # frames buffered during playback
START_FRAMES = 6         # playback's clock starts once this many frames are ready (or the buffer stalls)
STILL_PATIENCE = 0.12    # s a still keeps decoding toward its exact frame once a newer one is waiting
# Clip i decodes on decoder slot i % SLOTS, so while it plays the next clips' decoders can be
# positioned at their first frames (pre-roll): a cut into long-GOP footage costs up to a second
# of decoding from the previous keyframe, far more than the read-ahead buffer covers.
SLOTS = 3
PREROLL = SLOTS - 1      # clips positioned ahead of the one playing


@dataclass(frozen=True)
class Shot:
    """Everything needed to render one timeline clip (a private snapshot, safe to read off-thread)."""
    index: int                   # position on the timeline
    start: float                 # timeline start
    dur: float
    src_in: float
    footage_id: str
    grade: Grade
    log_profile: str
    tonemap: str
    zoom: float
    ox: float
    oy: float
    footage: Footage = field(compare=False, hash=False)
    trans_in: str = ""           # ffmpeg xfade name of the transition into this clip
    half_in: float = 0.0         # its half-length (seconds), centered on the clip's start
    half_out: float = 0.0        # half-length of the transition out of it (the next shot's trans_in)


@dataclass(frozen=True)
class Look:
    """Project-wide render settings."""
    use_proxies: bool
    fit_mode: str
    width: int
    height: int
    decode_h: int


@dataclass
class Rendered:
    image: Optional[np.ndarray]  # BGRA (QImage.Format_RGB32); None: decoding failed, `info` holds the error
    t: float                     # timeline time (playback) or the requested time (stills)
    info: str
    serial: int = 0


class PreviewEngine:
    def __init__(self, on_still: Callable[[Rendered], None]):
        self.decoders = DecoderPool()
        self.luts = LutCache()
        self._on_still = on_still
        self._cv = threading.Condition()
        self._still: Optional[tuple] = None      # (serial, shot, src_t, t, look)
        self._still_serial = 0
        self._play: Optional[dict] = None
        self._buf: deque[Rendered] = deque()
        self._lut_wanted: list[tuple] = []        # (grade, log_profile, tonemap), most urgent first
        self._quit = False
        threading.Thread(target=self._run, name="preview", daemon=True).start()
        threading.Thread(target=self._run_luts, name="preview-lut", daemon=True).start()
        threading.Thread(target=self._run_preroll, name="preview-preroll", daemon=True).start()

    # ------------------------------------------------------------------ requests (UI thread)
    def request_still(self, shot: Shot, src_t: float, t: float, look: Look,
                      shots: Optional[list[Shot]] = None) -> int:
        """Render one frame: the timeline at t when `shots` is given (transitions blended), else `shot`'s
        source frame at src_t (slipping / trimming)."""
        with self._cv:
            self._still_serial += 1
            self._still = (self._still_serial, shot, src_t, t, look, shots)
            self._cv.notify_all()
            return self._still_serial

    def start_playback(self, shots: list[Shot], look: Look, t0: float, clock0: Optional[float], fps: float) -> None:
        """Read ahead from t0. With clock0 None the clock waits for set_clock (see ready_to_start)."""
        fps = max(1.0, fps)
        with self._cv:
            self._still = None
            self._buf.clear()
            self._play = {"shots": shots, "starts": [s.start for s in shots], "look": look,
                          "t0": t0, "clock0": clock0, "fps": fps, "k": int(np.ceil(t0 * fps - 1e-6)),
                          "end": shots[-1].start + shots[-1].dur if shots else 0.0, "prerolled": set()}
            # Build the grading LUTs of the clips ahead in play order, starting at the playhead.
            first = max(0, bisect.bisect_right(self._play["starts"], t0) - 1)
            seen, wanted = set(), []
            for s in shots[first:] + shots[:first]:
                key = (s.grade.key(), s.log_profile, s.tonemap)
                if (key not in seen and needs_color(s.grade, s.log_profile)
                        and self.luts.peek(s.grade, s.log_profile, s.tonemap) is None):
                    seen.add(key)
                    wanted.append((s.grade, s.log_profile, s.tonemap))
            self._lut_wanted = wanted
            self._cv.notify_all()

    def set_clock(self, clock0: float) -> None:
        with self._cv:
            if self._play is not None:
                self._play["clock0"] = clock0

    def ready_to_start(self) -> bool:
        """Enough frames are buffered to start the clock without an immediate stall."""
        with self._cv:
            play = self._play
            return play is None or len(self._buf) >= START_FRAMES or len(self._buf) >= READ_AHEAD \
                or (self._buf and play["k"] / play["fps"] >= play["end"])

    def stop_playback(self) -> None:
        with self._cv:
            self._play = None
            self._buf.clear()
            self._lut_wanted.clear()
            self._cv.notify_all()

    def take_frame(self, now_t: float) -> Optional[Rendered]:
        """Newest buffered playback frame due at timeline time now_t (older ones are discarded)."""
        out = None
        with self._cv:
            while self._buf and self._buf[0].t <= now_t:
                out = self._buf.popleft()
            if out is not None:
                self._cv.notify_all()
        return out

    def drop_decoders(self) -> None:
        self.decoders.drop()

    def close(self) -> None:
        with self._cv:
            self._quit = True
            self._play = None
            self._cv.notify_all()
        self.decoders.close()

    # ------------------------------------------------------------------ worker
    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._quit and self._still is None and not (
                        self._play is not None and len(self._buf) < READ_AHEAD):
                    self._cv.wait()
                if self._quit:
                    return
                still, self._still = self._still, None
                play = self._play if still is None else None
            try:
                if still is not None:
                    self._render_still(*still)
                elif play is not None:
                    self._render_next(play)
            except Exception:  # noqa: BLE001 — a bad file must not kill the preview thread
                if play is not None:
                    play["k"] += 1
                time.sleep(0.01)

    def _render_still(self, serial: int, shot: Shot, src_t: float, t: float, look: Look,
                      shots: Optional[list[Shot]]) -> None:
        started = time.perf_counter()

        def newer_waiting() -> bool:
            return self._still is not None and time.perf_counter() - started > STILL_PATIENCE

        try:
            if shots:
                out = self._compose(shots, shot.index, t, look, False, newer_waiting)
            else:
                img = self._decode(shot, src_t, look, newer_waiting)
                out = None if img is None else self._finish(img, shot, look, playing=False)
            if out is None:
                return
        except Exception as e:  # noqa: BLE001
            self._on_still(Rendered(None, t, f"Cannot decode {shot.footage.name}: {e}", serial))
            return
        self._on_still(Rendered(out, t, self._info(shot, look), serial))

    def _render_next(self, play: dict) -> None:
        fps = play["fps"]
        k = play["k"]
        t = k / fps
        if t >= play["end"]:
            with self._cv:
                if self._play is play:
                    self._play = None   # nothing left to read ahead
            return
        # Fell behind the clock (slow decode): jump ahead instead of rendering frames already late.
        clock0 = play["clock0"]
        now_t = play["t0"] + time.perf_counter() - clock0 if clock0 is not None else play["t0"]
        if t < now_t - 1.0 / fps:
            k = int(np.ceil(now_t * fps)) + 2
            t = k / fps
            if t >= play["end"]:
                return
        i = max(0, bisect.bisect_right(play["starts"], t) - 1)
        shot = play["shots"][i]
        look = play["look"]
        out = self._compose(play["shots"], i, t, look, True, None)
        with self._cv:
            if self._play is not play:
                return          # stopped or restarted meanwhile
            play["k"] = k + 1
            play["shot"] = i
            if out is not None:
                self._buf.append(Rendered(out, t, self._info(shot, look)))
            self._cv.notify_all()

    def _run_preroll(self) -> None:
        while True:
            with self._cv:
                job = None
                while not self._quit and job is None:
                    play = self._play
                    # Pre-roll competes for the same CPU cores: never while playback is starving.
                    if play is not None and len(self._buf) >= min(READ_AHEAD // 3, START_FRAMES):
                        cur = play.get("shot", max(0, bisect.bisect_right(play["starts"], play["t0"]) - 1))
                        for j in range(cur + 1, min(cur + 1 + PREROLL, len(play["shots"]))):
                            if j not in play["prerolled"]:
                                play["prerolled"].add(j)
                                job = (play["shots"][j], play["look"], play["fps"])
                                break
                    if job is None:
                        self._cv.wait()
                if self._quit:
                    return
            shot, look, fps = job
            try:   # decode the clip's first frame now; playback then continues from there sequentially
                first_t = np.ceil((shot.start - shot.half_in) * fps - 1e-6) / fps
                self._decode(shot, shot.src_in + max(-shot.half_in, first_t - shot.start), look, None)
            except Exception:  # noqa: BLE001
                pass

    def _compose(self, shots: list[Shot], i: int, t: float, look: Look, playing: bool, interrupt) -> Optional[np.ndarray]:
        """The timeline frame at t: shot i, or two shots blended inside a transition."""
        shot = shots[i]
        pair = None
        if shot.half_in > 0 and i > 0 and t < shot.start + shot.half_in:
            pair = (shots[i - 1], shot, shot.trans_in, shot.start, shot.half_in)
        elif shot.half_out > 0 and i + 1 < len(shots) and t >= shot.start + shot.dur - shot.half_out:
            nxt = shots[i + 1]
            pair = (shot, nxt, nxt.trans_in, nxt.start, shot.half_out)
        if pair is not None:
            a, b, kind, cut, half = pair
            # Both sides run into their handles: a past its out point, b before its in point.
            ia = self._decode(a, a.src_in + (t - a.start), look, interrupt)
            ib = self._decode(b, b.src_in + (t - b.start), look, interrupt)
            if ia is not None and ib is not None:
                return blend(kind, self._finish(ia, a, look, playing), self._finish(ib, b, look, playing),
                             (t - (cut - half)) / (2 * half))
        img = self._decode(shot, shot.src_in + min(max(0.0, t - shot.start), shot.dur), look, interrupt)
        return None if img is None else self._finish(img, shot, look, playing)

    def _decode(self, shot: Shot, src_t: float, look: Look, interrupt) -> Optional[np.ndarray]:
        dec = self.decoders.get(shot.footage, look.use_proxies, look.decode_h, shot.index % SLOTS)
        return dec.frame_at(src_t, interrupt)

    def _finish(self, img: np.ndarray, shot: Shot, look: Look, playing: bool) -> np.ndarray:
        fn = self._grader(shot, playing)
        out = reframe.render(img, look.width, look.height, look.fit_mode, shot.zoom, shot.ox, shot.oy, fn)
        return cv2.cvtColor(out, cv2.COLOR_RGB2BGRA)   # Qt's native 32-bit layout: cheapest to paint

    def _grader(self, shot: Shot, playing: bool):
        g, lp, tm = shot.grade, shot.log_profile, shot.tonemap
        if not needs_color(g, lp):
            return None
        lut = self.luts.peek(g, lp, tm)
        if lut is not None:
            return lut.apply
        if playing:
            return self.luts.get(g, lp, tm).apply
        # Adjusting sliders: grade this frame exactly and build its LUT in the background
        # (only the latest grade is worth building while a slider moves).
        with self._cv:
            self._lut_wanted = [(g, lp, tm)]
            self._cv.notify_all()
        return lambda a: (apply_grade(a.astype(np.float32) / 255.0, g, lp, tm) * 255.0 + 0.5).astype(np.uint8)

    def _run_luts(self) -> None:
        while True:
            with self._cv:
                while not self._quit and not self._lut_wanted:
                    self._cv.wait()
                if self._quit:
                    return
                g, lp, tm = self._lut_wanted.pop(0)
            try:
                if self.luts.peek(g, lp, tm) is None:
                    self.luts.get(g, lp, tm)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _info(shot: Shot, look: Look) -> str:
        f = shot.footage
        using_proxy = look.use_proxies and f.has_proxy()
        return (f"{f.name}  {'PROXY' if using_proxy else 'ORIGINAL'}"
                f"{'  ' + f.log_profile.upper() if f.log_profile != 'none' else ''}")
