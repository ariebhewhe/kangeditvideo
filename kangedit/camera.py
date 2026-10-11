"""Camera identification and automatic log-profile selection.

Detection runs in two stages:
  1. Metadata (at import): ffprobe tags plus the container's metadata boxes identify the camera make/model.
     Some files state the gamma outright -- Sony's embedded/sidecar XML ("CaptureGammaEquation") and the
     HLG transfer flag -- and that answer is used as-is.
  2. Picture (after analysis): when only the camera is known, its usual log profile is applied if the
     analysis thumbnails look like log footage (lifted blacks, compressed highlights, low saturation).
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .ffmpeg import ProbeInfo


@dataclass
class CameraInfo:
    make: str = ""
    model: str = ""
    log_profile: Optional[str] = None   # stated by the file itself ("none" = explicitly not log)
    guess: str = ""                     # the camera's usual log profile, applied only if the picture looks log
    source: str = ""                    # human-readable reason

    @property
    def label(self) -> str:
        if self.model and self.make.lower() in self.model.lower():
            return self.model
        return " ".join(p for p in (self.make, self.model) if p)


# ----------------------------------------------------------------------------- container metadata

_ISO_TYPES = {b"ftyp", b"moov", b"wide", b"free", b"skip", b"mdat", b"uuid", b"pnot", b"meta"}
_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"udta", b"edts", b"dinf"}
_SKIP = {b"mdat", b"stbl", b"free", b"skip", b"wide"}   # media data and sample tables: binary noise
_MAX_BOX = 4 << 20
_MAX_TOTAL = 16 << 20


def _iso_metadata(fh, start: int, end: int, out: list[bytes], budget: list[int], depth: int = 0) -> None:
    pos = start
    while pos + 8 <= end and budget[0] > 0:
        fh.seek(pos)
        head = fh.read(16)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head[:8])
        hdr = 8
        if size == 1 and len(head) >= 16:
            size, hdr = struct.unpack(">Q", head[8:16])[0], 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            return
        if kind in _CONTAINERS and depth < 6:
            _iso_metadata(fh, pos + hdr, min(end, pos + size), out, budget, depth + 1)
        elif kind not in _SKIP:
            n = min(size - hdr, _MAX_BOX, budget[0])
            fh.seek(pos + hdr)
            out.append(kind + b" " + fh.read(n))
            budget[0] -= n
        pos += size


def container_metadata(path: str) -> bytes:
    """Raw metadata bytes of a media file, skipping the media data itself."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
            if head[4:8] in _ISO_TYPES:
                out: list[bytes] = []
                _iso_metadata(fh, 0, os.fstat(fh.fileno()).st_size, out, [_MAX_TOTAL])
                return b"\n".join(out)
            fh.seek(0)
            return fh.read(2 << 20)   # MXF / MTS: header metadata sits at the start
    except OSError:
        return b""


def _sidecar_xml(path: str) -> bytes:
    """Sony writes C0001M01.XML next to C0001.MP4 (also in a separate folder on some cards)."""
    folder, name = os.path.split(path)
    stem = os.path.splitext(name)[0]
    for cand in (os.path.join(folder, stem + "M01.XML"), os.path.join(folder, stem + "M01.xml")):
        try:
            if os.path.getsize(cand) < (1 << 20):
                with open(cand, "rb") as fh:
                    return fh.read()
        except OSError:
            continue
    return b""


# ----------------------------------------------------------------------------- camera brands

# brand key -> (display name, byte patterns found in metadata, usual log profile)
_BRANDS = {
    "sony": ("Sony", (rb"\bSONY\b", rb"\bSony\b", rb"ILCE-\w+", rb"ILME-\w+"), "slog3_sg3c"),
    "panasonic": ("Panasonic", (rb"Panasonic", rb"LUMIX"), "vlog"),
    "canon": ("Canon", (rb"\bCanon\b", rb"CANON"), "clog3"),
    "fujifilm": ("Fujifilm", (rb"FUJIFILM",), "flog"),
    "nikon": ("Nikon", (rb"NIKON", rb"\bNikon\b"), "nlog"),
    "dji": ("DJI", (rb"\bDJI[. _]",), "dlog"),
    "blackmagic": ("Blackmagic", (rb"Blackmagic",), "bmdfilm5"),
    "arri": ("ARRI", (rb"\bARRI\b", rb"ALEXA"), "logc3"),
    "gopro": ("GoPro", (rb"GoPro",), ""),
    "apple": ("Apple", (), "applelog"),        # tags only: "Apple" appears in every QuickTime file
    "samsung": ("Samsung", (), ""),
    "google": ("Google", (), ""),
    "insta360": ("Insta360", (rb"Insta360",), ""),
}

_MODEL_PATTERNS = [
    rb"ILCE-\w+", rb"ILME-\w+", rb"PXW-\w+", rb"Canon EOS [\w ]{1,16}", rb"EOS C\d{2,3}[\w ]{0,8}",
    rb"NIKON Z ?\w{1,4}", rb"X-[A-Z]\d{1,3}[A-Z]{0,2}\b", rb"X100\w*", rb"GFX ?\d{2,3}\w*(?: II)?",
    rb"DC-\w+", rb"DMC-\w+", rb"ALEXA[ \w]{0,12}", rb"Pocket Cinema Camera[ \w]{0,8}", rb"URSA[ \w]{0,16}",
]

_FLOG2_MODELS = re.compile(r"X-H2|X-T5|X-T50|X-S20|X-M5|X-E5|X100VI|GFX ?100 ?S? ?II|GFX ETERNA", re.I)

_MAKE_TAGS = ("com.apple.quicktime.make", "make", "com.android.manufacturer", "manufacturer")
_MODEL_TAGS = ("com.apple.quicktime.model", "model", "com.android.model")


def _brand_of(text: str) -> Optional[str]:
    t = text.lower()
    for key, (name, _, _) in _BRANDS.items():
        if re.search(rf"\b({key}|{re.escape(name.lower())})\b", t):
            return key
    if re.search(r"\bfuji\b", t):
        return "fujifilm"
    if re.search(r"\bxavc", t):   # Sony's MP4 brand
        return "sony"
    return None


def _sony_gamma(xml: bytes) -> Optional[tuple[str, str]]:
    """(profile, reason) from Sony NonRealTimeMeta XML, or None."""
    m = re.search(rb'CaptureGammaEquation"?\s+value="([^"]+)"', xml, re.I)
    if not m:
        return None
    gamma = m.group(1).decode("ascii", "replace").lower()
    pm = re.search(rb'CaptureColorPrimaries"?\s+value="([^"]+)"', xml, re.I)
    prim = pm.group(1).decode("ascii", "replace").lower() if pm else ""
    if "s-log3" in gamma:
        prof = "slog3_sg3c" if ("cine" in gamma or "cine" in prim) else "slog3_sg3"
    elif "s-log2" in gamma:
        prof = "slog2"
    elif "hlg" in gamma:
        prof = "hlg"
    else:
        prof = "none"   # rec709, cine1-4, s-cinetone... -- recorded as standard video
    return prof, f"gamma '{gamma}'" + (f" / {prim}" if prim else "")


def detect(path: str, info: ProbeInfo) -> CameraInfo:
    cam = CameraInfo()
    tags = {k.lower(): str(v) for k, v in (info.tags or {}).items()}
    for k in _MAKE_TAGS:
        if tags.get(k):
            cam.make = tags[k].split(" | ")[0].strip()
            break
    for k in _MODEL_TAGS:
        if tags.get(k):
            cam.model = tags[k].split(" | ")[0].strip()
            break

    blob = container_metadata(path)
    xml = _sidecar_xml(path)
    sony = _sony_gamma(xml) or _sony_gamma(blob)
    m = re.search(rb'<Device[^>]*manufacturer="([^"]*)"[^>]*modelName="([^"]*)"', xml + b"\n" + blob)
    if m:
        cam.make = cam.make or m.group(1).decode("utf-8", "replace")
        cam.model = cam.model or m.group(2).decode("utf-8", "replace")

    # Brand: explicit make tag first, then other tags (encoder, handler names...), then raw metadata.
    brand = _brand_of(cam.make) if cam.make else None
    if brand is None:
        brand = _brand_of(" ".join(v for k, v in tags.items() if k not in ("creation_time", "language")))
        if brand == "apple":
            brand = None   # "Apple Video Media Handler" etc. say nothing about the camera
    if brand is None:
        for key, (_, pats, _) in _BRANDS.items():
            if any(re.search(p, blob) for p in pats):
                brand = key
                break
    if brand:
        cam.make = cam.make or _BRANDS[brand][0]
    if not cam.model:
        for pat in _MODEL_PATTERNS:
            mm = re.search(pat, blob)
            if mm:
                cam.model = mm.group(0).decode("ascii", "replace").strip()
                break

    if info.color_transfer == "arib-std-b67":
        cam.log_profile, cam.source = "hlg", "HLG flag in the file"
    elif sony:
        cam.log_profile, cam.source = sony[0], f"Sony metadata: {sony[1]}"
        cam.make = cam.make or "Sony"
    elif brand:
        guess = _BRANDS[brand][2]
        if brand == "fujifilm" and _FLOG2_MODELS.search(cam.model or ""):
            guess = "flog2"
        elif brand == "arri" and ("35" in (cam.model or "") or b"ALEXA 35" in blob):
            guess = "logc4"
        cam.guess = guess
        cam.source = f"{_BRANDS[brand][0]} camera"
    return cam


# ----------------------------------------------------------------------------- picture check

_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def looks_log(frames) -> Optional[bool]:
    """Whether RGB uint8 frames (legal range expanded to 0..255) look like flat log footage.

    Log footage keeps blacks well above zero and has low saturation; standard Rec.709 video almost
    always has some pixels near black and stronger color. None when there is too little to judge.
    """
    frames = [f for f in (frames if frames is not None else []) if f is not None and f.size]
    if not frames:
        return None
    px = np.concatenate([f.reshape(-1, 3) for f in frames]).astype(np.float32) / 255.0
    if len(px) < 500:
        return None
    luma = px @ _LUMA
    lo, hi = np.percentile(luma, [0.5, 99.5])
    if hi - lo < 0.05:
        return None   # blank / black frames
    mid = (luma > 0.15) & (luma < 0.85)
    chroma = float(np.mean(px[mid].max(axis=1) - px[mid].min(axis=1))) if mid.sum() > 100 else 0.0
    lifted_blacks = lo > 0.03
    soft_highlights = hi < 0.90
    desaturated = chroma < 0.13
    return bool(lifted_blacks and (soft_highlights or desaturated))


def decide_from_picture(guess: str, camera_label: str, frames) -> tuple[Optional[str], str]:
    """(profile, note) from the picture check; profile None means "leave the current choice"."""
    looks = looks_log(frames)
    if looks is None:
        return None, "Could not judge the picture"
    if not looks:
        return "none", "Picture looks like standard video (not log)"
    if guess:
        return guess, f"{camera_label or 'camera'} + flat log picture"
    if camera_label:
        return None, f"Picture looks like log, but there is no built-in profile for {camera_label} — import a LUT"
    return None, "Picture looks like log, but the camera is unknown — choose a profile"
