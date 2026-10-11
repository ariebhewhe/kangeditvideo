"""Application paths and constants."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    path = Path(base) / "KangEdit"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir(sub: str) -> Path:
    path = app_data_dir() / "cache" / sub
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_key(path: str) -> str:
    """Stable key for a media file: path + size + mtime, so caches survive across projects."""
    try:
        st = os.stat(path)
        raw = f"{os.path.abspath(path).lower()}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        raw = os.path.abspath(path).lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


VIDEO_EXTENSIONS = (
    ".mp4", ".mov", ".mxf", ".mkv", ".avi", ".m4v", ".mts", ".m2ts",
    ".webm", ".wmv", ".3gp", ".insv", ".lrv", ".ts", ".mpg", ".mpeg",
)

ASPECT_RATIOS = {
    "16:9 (Landscape)": (16, 9),
    "9:16 (Vertical)": (9, 16),
    "1:1 (Square)": (1, 1),
    "4:5 (Portrait)": (4, 5),
    "4:3 (Classic)": (4, 3),
    "2.39:1 (Cinema)": (2.39, 1),
}

# Short side of the frame in pixels.
RESOLUTIONS = {
    "720p": 720,
    "1080p": 1080,
    "1440p": 1440,
    "4K (2160p)": 2160,
}

FRAME_RATES = [23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0]


def frame_size(aspect: str, resolution: str) -> tuple[int, int]:
    """Output width/height (even numbers) for an aspect + short-side resolution."""
    aw, ah = ASPECT_RATIOS[aspect]
    short = RESOLUTIONS[resolution]
    if aw >= ah:
        h = short
        w = short * aw / ah
    else:
        w = short
        h = short * ah / aw
    return int(round(w / 2)) * 2, int(round(h / 2)) * 2
