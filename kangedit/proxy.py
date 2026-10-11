"""Proxy generation: small, short-GOP H.264 copies for responsive editing."""

from __future__ import annotations

import os
import threading
from typing import Optional

from .config import cache_dir, file_key
from .ffmpeg import ProgressCb, ffmpeg_matrix, run_ffmpeg
from .models import Footage


def proxy_path_for(footage: Footage, height: int) -> str:
    return str(cache_dir("proxies") / f"{file_key(footage.path)}_{height}p.mp4")


def generate_proxy(footage: Footage, height: int = 540, progress: Optional[ProgressCb] = None,
                   cancel: Optional[threading.Event] = None) -> str:
    out = proxy_path_for(footage, height)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    w, h = footage.width, footage.height
    short = min(w, h)
    if short > height:
        s = height / short
        w, h = int(round(w * s / 2)) * 2, int(round(h * s / 2)) * 2
    matrix = ffmpeg_matrix(footage.color_space, min(footage.width, footage.height))
    vf = (f"scale={w}:{h}:flags=bicubic:in_color_matrix={matrix}:out_color_matrix=bt709"
          f":in_range=auto:out_range=tv,format=yuv420p")
    tmp = out + ".part.mp4"
    args = ["-i", footage.path, "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-g", "12", "-bf", "0",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart", tmp]
    try:
        run_ffmpeg(args, footage.duration, progress, cancel, label=f"Proxy: {footage.name}")
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return out
