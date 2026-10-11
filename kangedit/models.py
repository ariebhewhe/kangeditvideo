"""Project data model: footage, timeline clips, settings, undo history and persistence."""

from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

from .color import Grade


def new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class Footage:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    codec: str = ""
    pix_fmt: str = ""
    bit_depth: int = 8
    color_space: str = ""
    color_range: str = ""
    color_transfer: str = ""
    rotation: int = 0
    creation_time: str = ""
    log_profile: str = "none"      # built-in camera profile key, "lut:<path>" or "none"
    tonemap: str = "filmic"
    camera: str = ""               # identified camera make/model
    log_note: str = ""             # how log_profile was chosen (shown in tooltips)
    log_pending: bool = False      # waiting for the picture check after analysis (see camera.py)
    log_guess: str = ""            # the camera's usual log profile, applied if the picture looks log
    proxy_path: Optional[str] = None
    analysis_path: Optional[str] = None
    # Word-level transcript in source time: [{"s": start, "e": end, "w": text}, ...]
    transcript: Optional[list] = None
    transcript_lang: str = ""
    id: str = field(default_factory=new_id)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def has_proxy(self) -> bool:
        return bool(self.proxy_path) and os.path.exists(self.proxy_path)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Footage":
        known = {k: v for k, v in d.items() if k in Footage.__dataclass_fields__}
        return Footage(**known)


@dataclass
class TimelineClip:
    footage_id: str
    src_in: float
    src_out: float
    grade: Grade = field(default_factory=Grade)
    volume: float = 1.0
    zoom: float = 1.0
    offset_x: float = 0.0   # -1..1 reframe pan
    offset_y: float = 0.0
    score: Optional[float] = None
    # Edit from the previous clip into this one (see edits.py). transition: "" (cut) or an edits.TRANSITIONS
    # key, centered on the cut. audio_split: where the audio cut sits relative to the picture cut
    # (< 0: J-cut, this clip's sound starts early; > 0: L-cut, the previous clip's sound runs on).
    transition: str = ""
    transition_dur: float = 0.5
    audio_split: float = 0.0
    label: str = ""                # "opening" | "closing" bookend, or "hook", when auto edit placed it
    id: str = field(default_factory=new_id)

    @property
    def duration(self) -> float:
        return max(0.0, self.src_out - self.src_in)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["grade"] = self.grade.to_dict()
        return d

    @staticmethod
    def from_dict(d: dict) -> "TimelineClip":
        d = dict(d)
        g = Grade.from_dict(d.pop("grade", None))
        known = {k: v for k, v in d.items() if k in TimelineClip.__dataclass_fields__}
        return TimelineClip(grade=g, **known)


@dataclass
class SoundClip:
    """A sound effect on the SFX layer: placed at a timeline time, independent of the video clips."""
    path: str
    start: float                   # timeline position, seconds
    src_in: float = 0.0
    src_out: float = 0.0
    volume: float = 1.0
    name: str = ""
    id: str = field(default_factory=new_id)

    @property
    def duration(self) -> float:
        return max(0.0, self.src_out - self.src_in)

    @property
    def end(self) -> float:
        return self.start + self.duration

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "SoundClip":
        return SoundClip(**{k: v for k, v in d.items() if k in SoundClip.__dataclass_fields__})


@dataclass
class TextClip:
    """An animated title / text overlay on the text track (see titles.py)."""
    text: str
    start: float                   # timeline position, seconds
    duration: float = 3.0
    preset: str = ""               # titles.TEXT_PRESETS key it was made from
    font: str = "Arial"
    size_pct: float = 8.0          # font height, % of frame height
    color: str = "#FFFFFF"
    bold: bool = True
    italic: bool = False
    outline: bool = True           # dark outline around the letters (ignored with a box)
    box: bool = False              # background box behind each line
    box_color: str = "#000000"
    box_opacity: float = 60.0      # %
    align: str = "center"          # left | center | right (also which side of the block sits at x)
    x: float = 0.5                 # anchor position, 0..1 of the frame (vertical: block centre)
    y: float = 0.5
    anim_in: str = "fade"          # titles.ANIM_IN key
    anim_out: str = "fade"         # titles.ANIM_OUT key
    anim_dur: float = 0.5          # seconds of each animation
    id: str = field(default_factory=new_id)

    @property
    def end(self) -> float:
        return self.start + self.duration

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TextClip":
        return TextClip(**{k: v for k, v in d.items() if k in TextClip.__dataclass_fields__})


@dataclass
class CaptionStyle:
    enabled: bool = True
    font: str = "Arial"
    size_pct: float = 5.5          # font height as % of frame height
    position: str = "bottom"       # bottom | middle | top
    uppercase: bool = False
    max_chars: int = 32            # characters per line
    max_lines: int = 2
    max_duration: float = 4.0      # seconds per caption
    color: str = "#FFFFFF"
    outline: bool = True
    box: bool = False
    animation: str = "none"        # see captions.ANIMATIONS
    highlight: str = "#FFD400"     # active-word color for the animations
    auto_layout: bool = True       # size / position / line length chosen from the frame ratio and format


@dataclass
class ProjectSettings:
    aspect: str = "16:9 (Landscape)"
    resolution: str = "1080p"
    fps: float = 30.0
    fit_mode: str = "fill"         # fill | fit | blur
    use_proxies: bool = True
    proxy_height: int = 540
    whisper_model: str = "large-v3"
    whisper_language: str = ""     # "" = auto detect
    captions: CaptionStyle = field(default_factory=CaptionStyle)
    video_format: str = "short"    # short | long | custom (auto-edit preset)
    # Fade from / to black and silence at the start / end of the timeline (seconds, 0 = off).
    fade_in: float = 0.0
    fade_out: float = 0.0
    fade_picture: bool = True
    fade_sound: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    def fades(self, duration: float, sound: bool = False) -> tuple[float, float]:
        """(fade in, fade out) lengths for the picture (or the sound), shortened to fit the timeline."""
        if not (self.fade_sound if sound else self.fade_picture) or duration <= 0:
            return 0.0, 0.0
        fi, fo = max(0.0, self.fade_in), max(0.0, self.fade_out)
        if fi + fo > duration:
            k = duration / (fi + fo)
            fi, fo = fi * k, fo * k
        return fi, fo

    @staticmethod
    def from_dict(d: dict) -> "ProjectSettings":
        d = dict(d or {})
        cap = CaptionStyle(**{k: v for k, v in (d.pop("captions", None) or {}).items()
                              if k in CaptionStyle.__dataclass_fields__})
        known = {k: v for k, v in d.items() if k in ProjectSettings.__dataclass_fields__}
        return ProjectSettings(captions=cap, **known)


class Project:
    FILE_EXT = ".kgproj"

    def __init__(self):
        self.footage: dict[str, Footage] = {}
        self.clips: list[TimelineClip] = []
        self.sounds: list[SoundClip] = []
        self.texts: list[TextClip] = []
        self.settings = ProjectSettings()
        self.path: Optional[str] = None
        self.dirty = False
        self._undo: list[dict] = []
        self._redo: list[dict] = []

    # ------------------------------------------------------------------ timeline helpers
    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)

    def clip_starts(self) -> list[float]:
        starts, t = [], 0.0
        for c in self.clips:
            starts.append(t)
            t += c.duration
        return starts

    def clip_at(self, t: float) -> tuple[Optional[int], float]:
        """Index of the clip under timeline time t and the offset into it."""
        acc = 0.0
        for i, c in enumerate(self.clips):
            if t < acc + c.duration or i == len(self.clips) - 1:
                return i, min(max(0.0, t - acc), c.duration)
            acc += c.duration
        return None, 0.0

    def index_of(self, clip_id: str) -> int:
        for i, c in enumerate(self.clips):
            if c.id == clip_id:
                return i
        return -1

    def conflicts(self, min_gap: float = 0.0) -> set[str]:
        """Clip ids that overlap (or sit closer than min_gap to) another clip of the same footage."""
        bad: set[str] = set()
        by_src: dict[str, list[TimelineClip]] = {}
        for c in self.clips:
            by_src.setdefault(c.footage_id, []).append(c)
        for items in by_src.values():
            items = sorted(items, key=lambda c: c.src_in)
            for a, b in zip(items, items[1:]):
                if abs(b.src_in - a.src_out) < 0.02:
                    continue  # contiguous pieces (e.g. after a split) are not a repeat
                if b.src_in < a.src_out + min_gap - 1e-3:
                    bad.add(a.id)
                    bad.add(b.id)
        return bad

    # ------------------------------------------------------------------ undo
    def _snapshot(self) -> dict:
        return {
            "clips": [c.to_dict() for c in self.clips],
            "sounds": [x.to_dict() for x in self.sounds],
            "texts": [x.to_dict() for x in self.texts],
            "footage": {fid: {"log_profile": f.log_profile, "tonemap": f.tonemap,
                              "transcript": copy.deepcopy(f.transcript)}
                        for fid, f in self.footage.items()},
        }

    def _restore(self, snap: dict) -> None:
        self.clips = [TimelineClip.from_dict(d) for d in snap["clips"]]
        self.sounds = [SoundClip.from_dict(d) for d in snap.get("sounds", [])]
        self.texts = [TextClip.from_dict(d) for d in snap.get("texts", [])]
        for fid, d in snap["footage"].items():
            f = self.footage.get(fid)
            if f:
                f.log_profile = d["log_profile"]
                f.tonemap = d["tonemap"]
                f.transcript = d["transcript"]

    def set_detected_profile(self, footage_id: str, profile: str, note: str) -> None:
        """Apply an automatically detected log profile.

        Detection finishes in the background after the user may already have edited, so the result is
        written into the undo/redo snapshots too -- otherwise undoing an unrelated edit would revert it.
        """
        f = self.footage.get(footage_id)
        if f is None:
            return
        old = f.log_profile
        f.log_profile, f.log_note, f.log_pending = profile, note, False
        for snap in self._undo + self._redo:
            d = snap["footage"].get(footage_id)
            if d is not None and d["log_profile"] == old:
                d["log_profile"] = profile
        self.dirty = True

    def checkpoint(self) -> None:
        """Call before a modification to make it undoable."""
        self._undo.append(self._snapshot())
        if len(self._undo) > 200:
            self._undo.pop(0)
        self._redo.clear()
        self.dirty = True

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        self.dirty = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        self.dirty = True
        return True

    # ------------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return {
            "app": "KangEdit",
            "version": 1,
            "settings": self.settings.to_dict(),
            "footage": [f.to_dict() for f in self.footage.values()],
            "clips": [c.to_dict() for c in self.clips],
            "sounds": [x.to_dict() for x in self.sounds],
            "texts": [x.to_dict() for x in self.texts],
        }

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=1)
        os.replace(tmp, path)
        self.path = path
        self.dirty = False

    @staticmethod
    def load(path: str) -> "Project":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        p = Project()
        p.settings = ProjectSettings.from_dict(data.get("settings"))
        for fd in data.get("footage", []):
            f = Footage.from_dict(fd)
            p.footage[f.id] = f
        p.clips = [TimelineClip.from_dict(c) for c in data.get("clips", []) if c.get("footage_id") in p.footage]
        p.sounds = [SoundClip.from_dict(x) for x in data.get("sounds", [])]
        p.texts = [TextClip.from_dict(x) for x in data.get("texts", [])]
        p.path = path
        return p
