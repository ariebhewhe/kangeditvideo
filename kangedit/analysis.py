"""Per-footage content analysis: the raw signals used to find the best moments."""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from .config import cache_dir, file_key
from .ffmpeg import Cancelled, ProgressCb, ffmpeg_bin, ffmpeg_matrix, popen
from .models import Footage

SAMPLE_RATE = 4.0       # analysis samples per second
THUMB_EVERY = 4         # keep one thumbnail per second
THUMB_W = 128
ANALYSIS_W = 320
WAVE_RATE = 20          # waveform envelope points per second
CACHE_VERSION = 2


@dataclass
class Analysis:
    times: np.ndarray        # (N,) sample times
    sharp: np.ndarray        # log Laplacian variance
    activity: np.ndarray     # mean abs frame difference
    motion: np.ndarray       # global camera translation per sample (px @ 320w)
    jerk: np.ndarray         # change of translation (shake)
    brightness: np.ndarray   # mean luma 0..1
    clipped: np.ndarray      # fraction of crushed/clipped pixels
    colorful: np.ndarray     # Hasler-Suesstrunk colorfulness
    faces: np.ndarray        # face area fraction
    audio: np.ndarray        # RMS loudness (dBFS) per sample
    cuts: np.ndarray         # times of detected scene changes
    embed: np.ndarray        # (N, 144) tiny grey frames for similarity
    thumbs: np.ndarray       # (M, h, w, 3) RGB thumbnails, 1 per second
    thumb_times: np.ndarray  # (M,)
    wave: np.ndarray         # (K,) waveform envelope 0..1 at WAVE_RATE

    def save(self, path: str) -> None:
        np.savez_compressed(path, version=CACHE_VERSION, **{k: getattr(self, k) for k in self.__dataclass_fields__})

    @staticmethod
    def load(path: str) -> Optional["Analysis"]:
        try:
            with np.load(path) as d:
                if int(d.get("version", 0)) != CACHE_VERSION:
                    return None
                return Analysis(**{k: d[k] for k in Analysis.__dataclass_fields__})
        except Exception:
            return None


def analysis_path_for(footage: Footage) -> str:
    return str(cache_dir("analysis") / f"{file_key(footage.path)}_v{CACHE_VERSION}.npz")


_face_cascade = None


def _faces():
    global _face_cascade
    if _face_cascade is None:
        try:
            path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            c = cv2.CascadeClassifier(path)
            _face_cascade = c if not c.empty() else False
        except Exception:
            _face_cascade = False
    return _face_cascade


def _colorfulness(img: np.ndarray) -> float:
    r, g, b = (img[..., i].astype(np.float32) for i in range(3))
    rg = r - g
    yb = 0.5 * (r + g) - b
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def _read_audio(path: str, duration: float, cancel: Optional[threading.Event]) -> np.ndarray:
    exe = ffmpeg_bin()
    proc = popen([exe, "-v", "error", "-nostdin", "-i", path, "-vn", "-ac", "1", "-ar", "8000",
                  "-f", "s16le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunks = []
    while True:
        if cancel is not None and cancel.is_set():
            proc.kill()
            raise Cancelled()
        buf = proc.stdout.read(8000 * 2 * 4)
        if not buf:
            break
        chunks.append(buf)
    proc.wait()
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(b"".join(chunks), dtype=np.int16).astype(np.float32) / 32768.0


def analyze(footage: Footage, progress: Optional[ProgressCb] = None,
            cancel: Optional[threading.Event] = None) -> Analysis:
    src = footage.proxy_path if footage.has_proxy() else footage.path
    w = ANALYSIS_W
    h = max(2, int(round(ANALYSIS_W * footage.height / max(1, footage.width) / 2)) * 2)
    matrix = "bt709" if src != footage.path else ffmpeg_matrix(footage.color_space, min(footage.width, footage.height))
    vf = f"fps={SAMPLE_RATE},scale={w}:{h}:flags=area:in_color_matrix={matrix}:in_range=auto:out_range=pc"
    exe = ffmpeg_bin()
    proc = popen([exe, "-v", "error", "-nostdin", "-i", src, "-an", "-sn", "-vf", vf,
                  "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_bytes = w * h * 3
    expected = max(1, int(footage.duration * SAMPLE_RATE))
    th = int(round(THUMB_W * h / w / 2)) * 2

    sharp, activity, motion, jerk, bright, clipped, colorful, faces = ([] for _ in range(8))
    hists, embeds, thumbs, thumb_t = [], [], [], []
    prev_gray = None
    prev_shift = np.zeros(2)
    hann = None
    cascade = _faces()
    i = 0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            sharp.append(float(np.log1p(cv2.Laplacian(gray, cv2.CV_32F).var())))
            bright.append(float(gray.mean()) / 255.0)
            clipped.append(float(np.mean((gray > 250) | (gray < 5))))
            colorful.append(_colorfulness(img[::2, ::2]))
            g32 = gray.astype(np.float32)
            if prev_gray is not None:
                activity.append(float(np.mean(np.abs(g32 - prev_gray))))
                if hann is None:
                    hann = cv2.createHanningWindow((w, h), cv2.CV_32F)
                (dx, dy), _ = cv2.phaseCorrelate(prev_gray, g32, hann)
                shift = np.array([dx, dy])
                motion.append(float(np.hypot(dx, dy)))
                jerk.append(float(np.linalg.norm(shift - prev_shift)))
                prev_shift = shift
            else:
                activity.append(0.0)
                motion.append(0.0)
                jerk.append(0.0)
            prev_gray = g32
            hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256]).flatten()
            hists.append(hist / max(1.0, hist.sum()))
            embeds.append(cv2.resize(gray, (16, 9), interpolation=cv2.INTER_AREA).flatten().astype(np.float32))
            area = 0.0
            if cascade is not False and i % 2 == 0:
                found = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(18, 18))
                area = float(sum(fw * fh for (_, _, fw, fh) in found)) / (w * h) if len(found) else 0.0
            elif faces:
                area = faces[-1]
            faces.append(area)
            if i % THUMB_EVERY == 0:
                thumbs.append(cv2.resize(img, (THUMB_W, th), interpolation=cv2.INTER_AREA))
                thumb_t.append(i / SAMPLE_RATE)
            i += 1
            if progress and i % 8 == 0:
                progress(min(0.9, 0.9 * i / expected), f"Analyzing {footage.name}")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()

    n = len(sharp)
    if n == 0:
        raise RuntimeError(f"Could not decode frames from {footage.name}")
    times = np.arange(n, dtype=np.float32) / SAMPLE_RATE

    # Scene cuts: big jumps in colour histogram.
    hists_a = np.array(hists)
    cuts = []
    if n > 2:
        d = np.r_[0.0, 0.5 * np.abs(np.diff(hists_a, axis=0)).sum(axis=1)]
        thr = max(0.45, float(np.median(d) + 6 * np.std(d)))
        cuts = [float(times[k]) for k in range(1, n) if d[k] > thr]

    # Audio loudness per analysis sample + waveform envelope.
    audio_db = np.full(n, -60.0, dtype=np.float32)
    wave = np.zeros(max(1, int(footage.duration * WAVE_RATE)), dtype=np.float32)
    if footage.has_audio:
        if progress:
            progress(0.92, f"Analyzing audio {footage.name}")
        pcm = _read_audio(src, footage.duration, cancel)
        if pcm.size:
            win = int(8000 / SAMPLE_RATE)
            m = min(n, pcm.size // win)
            if m:
                rms = np.sqrt(np.mean(pcm[: m * win].reshape(m, win) ** 2, axis=1) + 1e-10)
                audio_db[:m] = 20 * np.log10(rms)
            ww = int(8000 / WAVE_RATE)
            k = pcm.size // ww
            if k:
                env = np.abs(pcm[: k * ww]).reshape(k, ww).max(axis=1)
                wave = np.clip(env / max(1e-3, float(np.percentile(env, 99))), 0, 1).astype(np.float32)

    a = Analysis(
        times=times,
        sharp=np.array(sharp, np.float32),
        activity=np.array(activity, np.float32),
        motion=np.array(motion, np.float32),
        jerk=np.array(jerk, np.float32),
        brightness=np.array(bright, np.float32),
        clipped=np.array(clipped, np.float32),
        colorful=np.array(colorful, np.float32),
        faces=np.array(faces, np.float32),
        audio=audio_db,
        cuts=np.array(cuts, np.float32),
        embed=np.array(embeds, np.float32),
        thumbs=np.array(thumbs, np.uint8),
        thumb_times=np.array(thumb_t, np.float32),
        wave=wave,
    )
    if progress:
        progress(1.0, f"Analyzed {footage.name}")
    return a


def load_or_analyze(footage: Footage, progress: Optional[ProgressCb] = None,
                    cancel: Optional[threading.Event] = None) -> Analysis:
    path = analysis_path_for(footage)
    if os.path.exists(path):
        a = Analysis.load(path)
        if a is not None:
            footage.analysis_path = path
            return a
    a = analyze(footage, progress, cancel)
    a.save(path)
    footage.analysis_path = path
    return a
