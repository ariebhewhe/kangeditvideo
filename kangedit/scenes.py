"""When footage was recorded, scenes (time groups of footage), and scenic openers for them.

The recording time of a file is the oldest of the dates it carries: the camera's creation time tag, the file's
creation date and its modification date. Copying a file renews its creation date but keeps its modification
date, and a camera with its clock unset writes no tag or a wrong one, so the oldest believable date is the
closest to when it was really shot. Bins by date, story order (and so where the opening and closing are looked
for) and scenes all use it.

A scene is a time group: files recorded close together. A new scene starts where the next file begins more
than SCENE_GAP after the previous one ended (a new day, a new place). In a long-form edit each scene after the
first opens with a scenic / travel shot from that scene (a wide establishing shot: no faces, no speech, sharp,
colourful, steady or gently moving), and "transitions between scenes" means between these time groups.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import numpy as np

from .models import Footage, TimelineClip

SCENE_GAP = 30 * 60.0          # seconds between two recordings that start a new scene
EARLIEST = datetime(1995, 1, 1).timestamp()   # dates before this are a camera clock that was never set
SAMPLE_RATE = 4.0              # analysis samples per second (analysis.py)


def _tag_time(f: Footage) -> Optional[float]:
    if not f.creation_time:
        return None
    try:
        return datetime.fromisoformat(f.creation_time.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _file_times(path: str) -> list[float]:
    try:
        st = os.stat(path)
    except OSError:
        return []
    out = [st.st_mtime]
    birth = getattr(st, "st_birthtime", None)       # macOS, BSD, and Windows (Python 3.12+)
    if birth is None and os.name == "nt":
        birth = st.st_ctime                          # Windows: the creation date
    if birth:
        out.append(birth)
    return out


def recorded_time(f: Footage) -> Optional[float]:
    """When footage was recorded (a timestamp): the oldest believable of its creation tag, its file's creation
    date and its modification date; None when it has none of them."""
    times = [t for t in [_tag_time(f)] + _file_times(f.path) if t is not None and t >= EARLIEST]
    return min(times) if times else None


def time_groups(footage: dict[str, Footage], order: list[str], gap: float = SCENE_GAP) -> dict[str, int]:
    """Scene number of each file in `order` (story order): a new scene starts where a file begins more than
    `gap` seconds after the previous one ended. A file without a recording time stays in the scene before it."""
    groups: dict[str, int] = {}
    g, end = 0, None
    for fid in order:
        f = footage.get(fid)
        if f is None:
            continue
        t = recorded_time(f)
        if t is not None:
            if end is not None and t - end > gap:
                g += 1
            end = max(end or t, t + f.duration)
        groups[fid] = g
    return groups


def _rank(x: np.ndarray) -> np.ndarray:
    """0..1 rank of each value (equal values share their rank)."""
    if x.size == 0:
        return x.astype(np.float32)
    vals, inv = np.unique(x, return_inverse=True)
    if len(vals) == 1:
        return np.full(x.size, 0.5, np.float32)
    return (np.arange(len(vals), dtype=np.float32) / (len(vals) - 1))[inv]


def scenic_scores(analyses: dict, footage: dict[str, Footage]) -> dict[str, np.ndarray]:
    """How much each analysis sample looks like a scenic / travel shot, roughly 0..1, ranked across the given
    footage: nobody's face filling it, no one speaking, sharp, colourful, well exposed, and either steady or
    moving smoothly (a pan, a drive, a walk), never shaky."""
    ids = [i for i in analyses if i in footage]
    if not ids:
        return {}
    cat = lambda name: np.concatenate([np.asarray(getattr(analyses[i], name), np.float32) for i in ids])  # noqa: E731
    sizes = [len(analyses[i].times) for i in ids]
    sharp, color, jerk = _rank(cat("sharp")), _rank(cat("colorful")), _rank(cat("jerk"))
    no_face = 1.0 - np.clip(cat("faces") * 40.0, 0.0, 1.0)
    quiet = np.clip((-22.0 - cat("audio")) / 16.0, 0.0, 1.0)          # -38 dBFS and below: no one talking
    has_audio = np.concatenate([np.full(n, footage[i].has_audio, bool) for i, n in zip(ids, sizes)])
    quiet = np.where(has_audio, quiet, 0.7)
    bright, clipped, motion = cat("brightness"), cat("clipped"), cat("motion")
    expo = np.clip(1.0 - np.maximum(0.0, np.abs(bright - 0.5) - 0.3) * 5.0, 0, 1) - np.clip(clipped * 2.0, 0, 1)
    glide = np.clip(motion / 6.0, 0.0, 1.0) * (1.0 - jerk)             # a smooth pan / drive
    shake = np.clip((jerk - 0.75) / 0.25, 0.0, 1.0) + np.clip((motion - 30.0) / 30.0, 0.0, 1.0)
    total = (0.30 * no_face + 0.20 * sharp + 0.20 * color + 0.15 * expo + 0.10 * quiet + 0.10 * glide
             - 0.40 * shake) / 1.05
    total = total * (0.4 + 0.6 * no_face) * (0.6 + 0.4 * quiet)     # a face or a voice makes it no view
    out, k = {}, 0
    for i, n in zip(ids, sizes):
        sc = total[k:k + n].copy()
        k += n
        t = np.asarray(analyses[i].times)
        sc[(t < 0.6) | (t > footage[i].duration - 0.6)] -= 0.5           # record start / stop bumps
        for c in np.asarray(analyses[i].cuts):
            sc[np.abs(t - c) < 0.3] -= 1.0
        out[i] = sc
    return out


def _overlaps(fid: str, a: float, b: float, used: list[TimelineClip], gap: float) -> bool:
    return any(c.footage_id == fid and a < c.src_out + gap and b > c.src_in - gap for c in used)


def find_opener(fids: list[str], analyses: dict, footage: dict[str, Footage], length: float,
                used: list[TimelineClip], gap: float = 0.0, scores: Optional[dict] = None) -> Optional[TimelineClip]:
    """The best scenic shot `length` seconds long in these files (one scene's), clear of the `used` clips by
    `gap` seconds, a little earlier in the scene preferred (arriving somewhere). None when there is none worth
    showing (too short footage, or no moment scenic enough)."""
    scores = scores if scores is not None else scenic_scores({i: analyses[i] for i in fids if i in analyses},
                                                             footage)
    best: Optional[tuple[float, str, float]] = None
    for rank, fid in enumerate(fids):
        if fid not in analyses or fid not in scores:
            continue
        f, sc = footage[fid], scores[fid]
        t = np.asarray(analyses[fid].times)
        L = min(length, f.duration - 1.2)
        n = int(round(L * SAMPLE_RATE))
        if n < 2 or len(sc) < n:
            continue
        csum = np.r_[0.0, np.cumsum(sc)]
        mean = (csum[n:] - csum[:-n]) / n                                  # window starting at each sample
        starts = t[:len(mean)]
        mean = mean - 0.02 * rank                                          # earlier files of the scene first
        for k in np.argsort(-mean)[:50]:
            a = float(starts[k])
            b = a + L
            if b > f.duration - 0.3 or _overlaps(fid, a, b, used, gap):
                continue
            if best is None or mean[k] > best[0]:
                best = (float(mean[k]), fid, a)
            break
    if best is None or best[0] < 0.35:
        return None
    _, fid, a = best
    L = min(length, footage[fid].duration - 1.2)
    return TimelineClip(fid, round(a, 3), round(a + L, 3), label="scene")


def add_scene_openers(clips: list[TimelineClip], groups: dict[str, int], analyses: dict,
                      footage: dict[str, Footage], length: float, gap: float = 0.0,
                      used: Optional[list[TimelineClip]] = None) -> int:
    """Open every scene of an edit after the first with a scenic shot from that scene (in place): before the
    first clip of each new time group, past any hook. Returns how many were added."""
    used = list(used or []) + list(clips)
    start = next((k for k, c in enumerate(clips) if c.label != "hook"), len(clips))
    added, seen = 0, set()
    prev = None
    k = start
    while k < len(clips):
        g = groups.get(clips[k].footage_id)
        if g is not None and prev is not None and g != prev and g not in seen and clips[k].label != "closing":
            fids = [f for f, gg in groups.items() if gg == g]
            opener = find_opener(fids, analyses, footage, length, used, gap)
            if opener is not None:
                clips.insert(k, opener)
                used.append(opener)
                added += 1
                k += 1
        if g is not None:
            if prev is not None and g != prev:
                seen.add(prev)
            prev = g
        k += 1
    return added
