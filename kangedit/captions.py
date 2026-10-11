"""Automatic captions.

Each footage is transcribed once (word-level timestamps in *source* time). Timeline
captions are then derived from the clips' in/out points, so they follow every trim,
slip and reorder without re-transcribing. Editing a caption rewrites the underlying
words, so manual fixes survive later edits too.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .ffmpeg import Cancelled, ProgressCb, ffmpeg_bin, run
from .models import CaptionStyle, Footage, Project

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]

LANGUAGES = [
    ("", "Auto detect"), ("en", "English"), ("id", "Indonesian"), ("es", "Spanish"), ("fr", "French"),
    ("de", "German"), ("it", "Italian"), ("pt", "Portuguese"), ("nl", "Dutch"), ("ja", "Japanese"),
    ("ko", "Korean"), ("zh", "Chinese"), ("hi", "Hindi"), ("ar", "Arabic"), ("ru", "Russian"),
    ("tr", "Turkish"), ("vi", "Vietnamese"), ("th", "Thai"), ("ms", "Malay"), ("tl", "Tagalog"),
]

_model_lock = threading.Lock()
_model = None
_model_name = None


def _get_model(name: str):
    global _model, _model_name
    with _model_lock:
        if _model is None or _model_name != name:
            from faster_whisper import WhisperModel
            device, compute = "cpu", "int8"
            try:
                import ctranslate2
                if ctranslate2.get_cuda_device_count() > 0:
                    device, compute = "cuda", "float16"
            except Exception:
                pass
            _model = WhisperModel(name, device=device, compute_type=compute)
            _model_name = name
        return _model


def _load_audio_16k(path: str) -> np.ndarray:
    """Decode audio to 16 kHz mono float32 with ffmpeg (independent of PyAV versions)."""
    res = run([ffmpeg_bin(), "-v", "error", "-nostdin", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
               "-f", "f32le", "-"])
    if res.returncode != 0:
        raise RuntimeError(res.stderr.decode("utf-8", "replace").strip() or "audio decode failed")
    return np.frombuffer(res.stdout, dtype=np.float32).copy()


def transcribe(footage: Footage, model_name: str = "small", language: str = "",
               progress: Optional[ProgressCb] = None, cancel: Optional[threading.Event] = None) -> list[dict]:
    if not footage.has_audio:
        footage.transcript = []
        return []
    if progress:
        progress(0.0, f"Loading speech model '{model_name}' (first run downloads it)…")
    model = _get_model(model_name)
    src = footage.proxy_path if footage.has_proxy() else footage.path
    audio = _load_audio_16k(src)
    if audio.size == 0:
        footage.transcript = []
        return []
    segments, info = model.transcribe(
        audio,
        language=language or None,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=400),
        beam_size=5,
        condition_on_previous_text=False,
    )
    words: list[dict] = []
    dur = info.duration or footage.duration or 1.0
    for seg in segments:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"s": round(float(w.start), 3), "e": round(float(w.end), 3), "w": text})
        if progress:
            progress(min(0.99, seg.end / dur), f"Transcribing {footage.name}")
    footage.transcript = words
    footage.transcript_lang = info.language or language
    return words


@dataclass
class Caption:
    start: float
    end: float
    text: str
    footage_id: str
    word_idx: list[int] = field(default_factory=list)
    clip_id: str = ""


_SENT_END = re.compile(r"[.!?…。！？]$")


def build_captions(project: Project, style: Optional[CaptionStyle] = None) -> list[Caption]:
    style = style or project.settings.captions
    max_chars_total = style.max_chars * max(1, style.max_lines)
    out: list[Caption] = []
    t0 = 0.0
    for clip in project.clips:
        f = project.footage.get(clip.footage_id)
        words = f.transcript if f else None
        if words:
            cur: Optional[Caption] = None
            last_end = None
            for idx, w in enumerate(words):
                mid = (w["s"] + w["e"]) / 2
                if mid < clip.src_in or mid >= clip.src_out:
                    continue
                ts = t0 + max(w["s"], clip.src_in) - clip.src_in
                te = t0 + min(w["e"], clip.src_out) - clip.src_in
                text = w["w"]
                if cur is not None:
                    new_len = len(cur.text) + 1 + len(text)
                    gap = ts - last_end if last_end is not None else 0
                    if (new_len > max_chars_total or te - cur.start > style.max_duration or gap > 0.7
                            or (_SENT_END.search(cur.text) and len(cur.word_idx) >= 3)):
                        out.append(cur)
                        cur = None
                if cur is None:
                    cur = Caption(ts, te, text, clip.footage_id, [idx], clip.id)
                else:
                    cur.text += " " + text
                    cur.end = te
                    cur.word_idx.append(idx)
                last_end = te
            if cur is not None:
                out.append(cur)
        t0 += clip.duration
    # Hold each caption a little after the last word, without overlapping the next one.
    for a, b in zip(out, out[1:]):
        if b.start > a.end:
            a.end = min(a.end + 0.25, b.start)
    if out:
        out[-1].end = min(out[-1].end + 0.25, project.duration)
    for c in out:
        c.text = c.text.upper() if style.uppercase else c.text
    return out


def wrap(text: str, max_chars: int, max_lines: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    if len(text) <= max_chars or max_lines <= 1:
        return [text]
    # Balanced split into at most max_lines lines.
    lines, cur = [], ""
    target = max(max_chars // 2, len(text) // max_lines + 1)
    for w in words:
        if cur and len(cur) + 1 + len(w) > target and len(lines) < max_lines - 1:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    lines.append(cur)
    return lines


def edit_caption(project: Project, cap: Caption, new_text: str) -> None:
    f = project.footage.get(cap.footage_id)
    if not f or not f.transcript or not cap.word_idx:
        return
    idx = sorted(cap.word_idx)
    first, last = f.transcript[idx[0]], f.transcript[idx[-1]]
    new_text = new_text.strip()
    keep = {"s": first["s"], "e": last["e"], "w": new_text}
    for k in reversed(idx):
        del f.transcript[k]
    if new_text:
        f.transcript.insert(idx[0], keep)


def _ts(t: float, sep: str = ",") -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_srt(path: str, caps: list[Caption], style: CaptionStyle) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for i, c in enumerate(caps, 1):
            lines = wrap(c.text, style.max_chars, style.max_lines)
            fh.write(f"{i}\n{_ts(c.start)} --> {_ts(c.end)}\n" + "\n".join(lines) + "\n\n")


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def write_ass(path: str, caps: list[Caption], style: CaptionStyle, width: int, height: int) -> None:
    size = max(10, int(round(height * style.size_pct / 100.0)))
    align = {"bottom": 2, "middle": 5, "top": 8}.get(style.position, 2)
    margin_v = int(height * 0.08) if style.position != "middle" else 0
    border = 3 if style.box else 1
    outline = max(1, size // 14) if style.outline or style.box else 0
    if style.box:
        outline = max(4, size // 5)
    back = "&H80000000"
    out_col = "&H90000000" if style.box else "&H00000000"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n")
        fh.write(f"PlayResX: {width}\nPlayResY: {height}\n\n")
        fh.write("[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                 "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
                 "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
        fh.write(f"Style: Default,{style.font},{size},{_ass_color(style.color)},&H000000FF,{out_col},{back},"
                 f"-1,0,0,0,100,100,0,0,{border},{outline},{0 if style.box else 1},{align},"
                 f"{int(width * 0.06)},{int(width * 0.06)},{margin_v},1\n\n")
        fh.write("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        for c in caps:
            lines = wrap(c.text, style.max_chars, style.max_lines)
            text = r"\N".join(l.replace("{", "(").replace("}", ")") for l in lines)
            fh.write(f"Dialogue: 0,{_ts(c.start, '.')[:-1]},{_ts(c.end, '.')[:-1]},Default,,0,0,0,,{text}\n")
