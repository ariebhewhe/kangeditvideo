"""Best-moment selection.

Scores every analysis sample, slides candidate windows over each footage and greedily
picks the best ones under these rules:
  * a footage may be used several times, but its segments never overlap and keep at
    least `min_gap` seconds of source time between them;
  * visually near-identical segments of the same footage are rejected (no repeats);
  * repeated use of a footage is progressively penalised so all footage gets a chance.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional

import numpy as np

from .analysis import SAMPLE_RATE, Analysis
from .models import Footage, TimelineClip


STYLES = {
    "balanced": dict(sharp=.25, color=.15, act=.15, audio=.15, face=.15, expo=.15, shake=.35),
    "action":   dict(sharp=.15, color=.10, act=.35, audio=.20, face=.05, expo=.15, shake=.20),
    "people":   dict(sharp=.15, color=.05, act=.05, audio=.25, face=.35, expo=.15, shake=.35),
    "scenic":   dict(sharp=.30, color=.30, act=.00, audio=.00, face=.05, expo=.20, shake=.50),
}
STYLE_LABELS = {
    "balanced": "Balanced",
    "action": "Action / sports (motion + loud moments)",
    "people": "People / talking (faces + speech)",
    "scenic": "Scenic / travel (colour + stable shots)",
}

ORDERS = {
    "hook": "Hook first (best clip, then chronological)",
    "chronological": "Chronological",
    "best_first": "Best moments first",
}


@dataclass
class AutoEditSettings:
    video_format: str = "short"     # short | long | custom
    target_duration: float = 30.0
    clip_min: float = 1.2
    clip_max: float = 3.0
    min_gap: float = 3.0            # min source-time gap between segments of the same footage
    max_uses: int = 0               # per footage, 0 = unlimited
    use_every_footage: bool = True
    order: str = "hook"
    style: str = "balanced"
    similarity: float = 0.93        # cosine similarity above which segments count as repeats
    replace_timeline: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


# Video format presets: (settings overrides, project aspect)
FORMAT_PRESETS = {
    "short": dict(
        label="Short-form (Reels / TikTok / Shorts)",
        aspect="9:16 (Vertical)",
        settings=dict(target_duration=30.0, clip_min=1.2, clip_max=3.0, min_gap=3.0, order="hook"),
        hint="Vertical 9:16, 15-60 s, fast 1-3 s cuts, strongest moment first as the hook.",
    ),
    "long": dict(
        label="Long-form (YouTube / film)",
        aspect="16:9 (Landscape)",
        settings=dict(target_duration=180.0, clip_min=4.0, clip_max=10.0, min_gap=8.0, order="chronological"),
        hint="Landscape 16:9, several minutes, longer 4-10 s shots in story order.",
    ),
    "custom": dict(label="Custom", aspect=None, settings={}, hint="Set every option yourself."),
}


def _rank(x: np.ndarray) -> np.ndarray:
    if x.size == 0:
        return x
    order = np.argsort(x, kind="mergesort")
    r = np.empty(x.size, dtype=np.float32)
    r[order] = np.linspace(0.0, 1.0, x.size, dtype=np.float32)
    return r


def score_samples(analyses: dict[str, Analysis], footage: dict[str, Footage], style: str) -> dict[str, np.ndarray]:
    """Per-sample quality score in roughly 0..1, ranked globally across all footage."""
    w = STYLES.get(style, STYLES["balanced"])
    ids = list(analyses)
    if not ids:
        return {}
    cat = lambda name: np.concatenate([getattr(analyses[i], name) for i in ids])  # noqa: E731
    sizes = [len(analyses[i].times) for i in ids]
    sharp, color, act, audio, jerk = (_rank(cat(n)) for n in ("sharp", "colorful", "activity", "audio", "jerk"))
    faces = np.clip(cat("faces") * 25.0, 0, 1)
    bright = cat("brightness")
    clipped = cat("clipped")
    motion = cat("motion")
    has_audio = np.concatenate([np.full(s, footage[i].has_audio, bool) for i, s in zip(ids, sizes)])

    act_s = np.where(act < 0.85, act, np.maximum(0.0, 0.85 - (act - 0.85) * 5.0))
    expo = np.clip(1.0 - np.maximum(0.0, np.abs(bright - 0.5) - 0.35) * 6.0, 0, 1) - np.clip(clipped * 2.0, 0, 1)
    shake = np.clip((jerk - 0.7) / 0.3, 0, 1) + np.clip((motion - 25.0) / 25.0, 0, 1)
    audio_s = np.where(has_audio, audio, 0.5)

    total = (w["sharp"] * sharp + w["color"] * color + w["act"] * act_s + w["audio"] * audio_s
             + w["face"] * faces + w["expo"] * expo - w["shake"] * shake)
    total = total / sum(v for k, v in w.items() if k != "shake")

    out, k = {}, 0
    for i, s in zip(ids, sizes):
        sc = total[k:k + s].astype(np.float32).copy()
        k += s
        t = analyses[i].times
        dur = footage[i].duration
        sc[t < 0.6] -= 0.5                 # camera bump at record start
        sc[t > dur - 0.6] -= 0.5           # and at record stop
        for c in analyses[i].cuts:
            sc[np.abs(t - c) < 0.3] -= 1.0
        out[i] = sc
    return out


@dataclass
class _Cands:
    fid: str
    score: np.ndarray      # best window score per start sample
    length: np.ndarray     # chosen window length (seconds) per start sample
    embed: np.ndarray      # normalised embedding per start sample (window middle)
    valid: np.ndarray      # bool mask


def _candidates(fid: str, a: Analysis, sc: np.ndarray, dur: float, s: AutoEditSettings) -> _Cands:
    n = len(sc)
    starts = a.times
    csum = np.r_[0.0, np.cumsum(sc)]
    csq = np.r_[0.0, np.cumsum(sc.astype(np.float64) ** 2)]
    lengths = sorted({round(s.clip_min, 2), round((s.clip_min + s.clip_max) / 2, 2), round(s.clip_max, 2)})
    best = np.full(n, -np.inf, dtype=np.float32)
    best_len = np.full(n, lengths[0], dtype=np.float32)
    cut_times = np.asarray(a.cuts, dtype=np.float32)
    span = max(1e-6, s.clip_max - s.clip_min)
    for L in lengths:
        l = max(1, int(round(L * SAMPLE_RATE)))
        if l > n:
            continue
        m = n - l + 1
        mean = (csum[l:l + m] - csum[:m]) / l
        var = np.maximum(0.0, (csq[l:l + m] - csq[:m]) / l - mean ** 2)
        val = mean - 0.25 * np.sqrt(var) + 0.04 * (L - s.clip_min) / span
        ok = starts[:m] + L <= dur - 0.05
        if cut_times.size:
            st = starts[:m, None]
            ok &= ~np.any((cut_times[None, :] > st + 0.05) & (cut_times[None, :] < st + L - 0.05), axis=1)
        val = np.where(ok, val, -np.inf)
        better = val > best[:m]
        best[:m][better] = val[better]
        best_len[:m][better] = L
    # Very short footage: allow one window covering everything usable.
    if not np.isfinite(best).any() and dur >= min(1.0, s.clip_min):
        best[0] = float(np.mean(sc))
        best_len[0] = min(dur, s.clip_max)
    mid = np.clip((np.arange(n) + (best_len * SAMPLE_RATE / 2).astype(int)), 0, n - 1)
    e = a.embed[mid].astype(np.float32)
    e = e - e.mean(axis=1, keepdims=True)
    e /= np.linalg.norm(e, axis=1, keepdims=True) + 1e-6
    return _Cands(fid, best, best_len, e, np.isfinite(best))


def select(project_footage: dict[str, Footage], analyses: dict[str, Analysis],
           s: AutoEditSettings, order_ids: Optional[list[str]] = None,
           existing: Optional[list[TimelineClip]] = None) -> list[TimelineClip]:
    """Pick clips. `existing` clips are treated as already used (no overlap / repeats)."""
    ids = [i for i in (order_ids or list(project_footage)) if i in analyses]
    if not ids:
        return []
    scores = score_samples({i: analyses[i] for i in ids}, project_footage, s.style)
    cands = {i: _candidates(i, analyses[i], scores[i], project_footage[i].duration, s) for i in ids}
    picked: list[tuple[str, float, float, float]] = []   # fid, in, out, score
    picked_embed: dict[str, list[np.ndarray]] = {i: [] for i in ids}
    uses = {i: 0 for i in ids}
    total = 0.0

    def block(fid: str, a_in: float, a_out: float, emb: np.ndarray):
        c = cands[fid]
        t = analyses[fid].times
        c.valid &= ~((t < a_out + s.min_gap) & (t + c.length > a_in - s.min_gap))
        picked_embed[fid].append(emb)

    def effective(fid: str) -> np.ndarray:
        c = cands[fid]
        sc = np.where(c.valid, c.score, -np.inf)
        if picked_embed[fid]:
            sims = c.embed @ np.stack(picked_embed[fid]).T
            sc = np.where(sims.max(axis=1) > s.similarity, -np.inf, sc)
        return sc - 0.12 * uses[fid]

    def take(fid: str, eff: np.ndarray) -> bool:
        nonlocal total
        k = int(np.argmax(eff))
        if not np.isfinite(eff[k]):
            return False
        c = cands[fid]
        remaining = s.target_duration - total
        length = float(min(c.length[k], remaining))
        if length < min(s.clip_min, 1.0) - 1e-6:
            return False
        a_in = float(analyses[fid].times[k])
        a_out = min(project_footage[fid].duration, a_in + length)
        picked.append((fid, a_in, a_out, float(c.score[k])))
        block(fid, a_in, a_out, c.embed[k])
        uses[fid] += 1
        total += a_out - a_in
        return True

    for ex in existing or []:
        if ex.footage_id in cands:
            t = analyses[ex.footage_id].times
            k = int(np.clip(np.searchsorted(t, (ex.src_in + ex.src_out) / 2), 0, len(t) - 1))
            block(ex.footage_id, ex.src_in, ex.src_out, cands[ex.footage_id].embed[k])

    if s.use_every_footage:
        firsts = []
        for fid in ids:
            eff = effective(fid)
            if np.isfinite(eff).any():
                firsts.append((float(np.max(eff)), fid))
        for _, fid in sorted(firsts, reverse=True):
            if total >= s.target_duration - 0.05:
                break
            take(fid, effective(fid))

    while total < s.target_duration - 0.05:
        best_fid, best_val, best_eff = None, -np.inf, None
        for fid in ids:
            if s.max_uses and uses[fid] >= s.max_uses:
                continue
            eff = effective(fid)
            v = float(np.max(eff)) if eff.size else -np.inf
            if v > best_val:
                best_fid, best_val, best_eff = fid, v, eff
        if best_fid is None or not np.isfinite(best_val):
            break
        if not take(best_fid, best_eff):
            break

    # Ordering
    pos = {fid: k for k, fid in enumerate(ids)}
    chrono = sorted(picked, key=lambda p: (pos[p[0]], p[1]))
    if s.order == "best_first":
        ordered = sorted(picked, key=lambda p: -p[3])
    elif s.order == "hook" and picked:
        hook = max(picked, key=lambda p: p[3])
        ordered = [hook] + [p for p in chrono if p is not hook]
    else:
        ordered = chrono
    return [TimelineClip(footage_id=f, src_in=a, src_out=b, score=sc) for f, a, b, sc in ordered]
