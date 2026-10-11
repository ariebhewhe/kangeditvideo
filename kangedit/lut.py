"""Imported .cube LUTs: parsing, interpolation and the LUT library.

A LUT is used in one of two places (see color.apply_grade):
  * as the footage's log conversion ("lut:<path>" instead of a built-in camera profile), applied to the
    decoded signal before any grading, e.g. a manufacturer's "S-Log3 to Rec.709" LUT;
  * as a clip's creative look, applied to the graded Rec.709 image and mixed in by a strength amount.
"""

from __future__ import annotations

import filecmp
import os
import re
import shutil
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .config import lut_dir

LUT_PREFIX = "lut:"
LUT_EXTENSIONS = (".cube",)


class LutError(ValueError):
    pass


class CubeLut:
    """A 1D or 3D LUT from a .cube file (Adobe/Resolve format)."""

    def __init__(self, path: str):
        self.path = path
        self.title = ""
        size_1d = size_3d = 0
        dmin = np.zeros(3, np.float32)
        dmax = np.ones(3, np.float32)
        rows: list[list[float]] = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    line = raw.split("#", 1)[0].strip()
                    if not line:
                        continue
                    head = line.split(None, 1)[0].upper()
                    if head[0].isdigit() or head[0] in "-+.":
                        vals = line.split()
                        if len(vals) != 3:
                            raise LutError(f"bad data line: {line!r}")
                        rows.append([float(v) for v in vals])
                    elif head == "TITLE":
                        self.title = line[5:].strip().strip('"')
                    elif head == "LUT_3D_SIZE":
                        size_3d = int(line.split()[1])
                    elif head == "LUT_1D_SIZE":
                        size_1d = int(line.split()[1])
                    elif head in ("DOMAIN_MIN", "LUT_3D_INPUT_RANGE", "LUT_1D_INPUT_RANGE"):
                        vals = [float(v) for v in line.split()[1:]]
                        if len(vals) == 2:  # *_INPUT_RANGE lo hi
                            dmin[:] = vals[0]
                            dmax[:] = vals[1]
                        else:
                            dmin[:] = vals[:3]
                    elif head == "DOMAIN_MAX":
                        dmax[:] = [float(v) for v in line.split()[1:4]]
                    # Other keywords (LUT_IN_VIDEO_RANGE, vendor tags...) are ignored.
        except OSError as e:
            raise LutError(str(e)) from e
        except (ValueError, IndexError) as e:
            raise LutError(f"not a valid .cube file ({e})") from e

        data = np.asarray(rows, np.float32)
        if size_3d:
            if len(data) != size_3d ** 3:
                raise LutError(f"expected {size_3d ** 3} entries for a {size_3d}³ LUT, found {len(data)}")
            # .cube order: red changes fastest -> index as [b, g, r].
            self.table = data.reshape(size_3d, size_3d, size_3d, 3)
            self.kind = "3d"
            self.size = size_3d
        elif size_1d:
            if len(data) != size_1d:
                raise LutError(f"expected {size_1d} entries for a 1D LUT, found {len(data)}")
            self.table = data
            self.kind = "1d"
            self.size = size_1d
        else:
            raise LutError("missing LUT_3D_SIZE / LUT_1D_SIZE")
        if self.size < 2:
            raise LutError("LUT size must be at least 2")
        self.dmin = dmin
        self.span = np.maximum(dmax - dmin, 1e-6)

    @property
    def name(self) -> str:
        return lut_name(self.path)

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        """rgb: (N, 3) float -> (N, 3) float32, trilinear (3D) or linear (1D) interpolation."""
        n = self.size - 1
        x = np.clip((np.asarray(rgb, np.float32) - self.dmin) / self.span, 0.0, 1.0) * n
        i0 = np.minimum(x.astype(np.int32), n - 1)
        f = x - i0
        if self.kind == "1d":
            out = np.empty_like(x)
            for c in range(3):
                col = self.table[:, c]
                out[:, c] = col[i0[:, c]] * (1.0 - f[:, c]) + col[i0[:, c] + 1] * f[:, c]
            return out
        t = self.table
        r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
        fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]
        c00 = t[b0, g0, r0] * (1 - fr) + t[b0, g0, r0 + 1] * fr
        c01 = t[b0, g0 + 1, r0] * (1 - fr) + t[b0, g0 + 1, r0 + 1] * fr
        c10 = t[b0 + 1, g0, r0] * (1 - fr) + t[b0 + 1, g0, r0 + 1] * fr
        c11 = t[b0 + 1, g0 + 1, r0] * (1 - fr) + t[b0 + 1, g0 + 1, r0 + 1] * fr
        c0 = c00 * (1 - fg) + c01 * fg
        c1 = c10 * (1 - fg) + c11 * fg
        return (c0 * (1 - fb) + c1 * fb).astype(np.float32)


_cache: dict[str, tuple[float, CubeLut]] = {}
_cache_lock = threading.Lock()


def load(path: str) -> CubeLut:
    """Parsed LUT, cached until the file changes."""
    mtime = os.path.getmtime(path)
    with _cache_lock:
        hit = _cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
    lut = CubeLut(path)
    with _cache_lock:
        _cache[path] = (mtime, lut)
    return lut


def try_load(path: str) -> Optional[CubeLut]:
    """LUT or None when the file is missing/broken (the image is then left unchanged)."""
    try:
        return load(path) if path else None
    except (OSError, LutError):
        return None


def lut_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def library() -> list[tuple[str, str]]:
    """(name, path) for every LUT in the library folder, sorted by name."""
    items = []
    for entry in os.scandir(lut_dir()):
        if entry.is_file() and entry.name.lower().endswith(LUT_EXTENSIONS):
            items.append((lut_name(entry.path), entry.path))
    return sorted(items, key=lambda it: it[0].lower())


def import_lut(src: str) -> str:
    """Validate a .cube file and copy it into the library. Returns the library path."""
    CubeLut(src)  # raises LutError if unusable
    dst_dir = lut_dir()
    base, ext = os.path.splitext(os.path.basename(src))
    dst = str(dst_dir / f"{base}{ext.lower()}")
    if os.path.abspath(src).lower() == os.path.abspath(dst).lower():
        return dst
    k = 2
    while os.path.exists(dst):
        if filecmp.cmp(src, dst, shallow=False):
            return dst  # already imported
        dst = str(dst_dir / f"{base} ({k}){ext.lower()}")
        k += 1
    shutil.copy2(src, dst)
    return dst


# ----------------------------------------------------------------------------- official camera LUTs
# Manufacturers publish their own log -> Rec.709 conversion LUTs. They sit behind download pages and
# license terms, so the app doesn't fetch them: the LUT dialog links each page, and once the files are
# imported (.cube or the .zip as downloaded) they are recognised by name and used instead of the
# built-in conversion curve for that log profile.

@dataclass(frozen=True)
class OfficialLut:
    profile: str                  # built-in log profile the LUT replaces
    maker: str
    label: str
    url: str                      # official download page ("" = see `hint`)
    patterns: tuple[str, ...]     # regexes on the lower-case file name, best match first
    hint: str = ""


OFFICIAL_LUTS = [
    OfficialLut("dlog", "DJI", "D-Log M / D-Log → Rec.709", "https://www.dji.com/lut",
                (r"d-?log[ _-]?m.*709", r"d-?log.*709", r"dlog.*srgb"),
                "D-Log M: Mini 4/5 Pro, Air 3/3S, Avata 2, Osmo Pocket 3, Action 4-6, Mavic 3. "
                "D-Log: Mavic 3/4 Pro, Inspire, Zenmuse."),
    OfficialLut("slog3_sg3c", "Sony", "S-Log3 / S-Gamut3.Cine → Rec.709",
                "https://pro.sony/bp_BR/technology/professional-video-lut-look-up-table",
                (r"slog3sgamut3\.?cine.*cine\+?709", r"slog3sgamut3\.?cine.*709", r"s-?log3.*gamut3\.?cine.*709")),
    OfficialLut("slog3_sg3", "Sony", "S-Log3 / S-Gamut3 → Rec.709",
                "https://pro.sony/bp_BR/technology/professional-video-lut-look-up-table",
                (r"slog3sgamut3to.*709", r"s-?log3.*gamut3(?!\.?cine).*709")),
    OfficialLut("slog2", "Sony", "S-Log2 → Rec.709",
                "https://pro.sony/bp_BR/technology/professional-video-lut-look-up-table", (r"s-?log2.*709",)),
    OfficialLut("vlog", "Panasonic", "V-Log / V-Log L → V-709",
                "https://av.jpn.support.panasonic.com/support/global/cs/dsc/download/index3.html",
                (r"v-?log.*v-?709", r"v-?log.*709")),
    OfficialLut("clog3", "Canon", "Canon Log 3 → BT.709", "",
                (r"canonlog3.*709", r"c-?log ?3.*709"),
                "Your camera's page on Canon's support site → Downloads → \"Canon lookup table\"."),
    OfficialLut("clog2", "Canon", "Canon Log 2 → BT.709", "",
                (r"canonlog2.*709", r"c-?log ?2.*709"),
                "Your camera's page on Canon's support site → Downloads → \"Canon lookup table\"."),
    OfficialLut("logc3", "ARRI", "LogC3 → Rec.709", "https://www.arri.com/log-c",
                (r"logc3.*709", r"logc2video.*709", r"logc(?!4).*709")),
    OfficialLut("logc4", "ARRI", "LogC4 → Rec.709", "https://www.arri.com/log-c", (r"logc4.*709",)),
    OfficialLut("flog", "Fujifilm", "F-Log → BT.709", "https://www.fujifilm-x.com/en-gb/support/download/lut/",
                (r"f-?log_?f-?gamut_to_.*709", r"f-?log(?!2).*709")),
    OfficialLut("flog2", "Fujifilm", "F-Log2 → BT.709", "https://www.fujifilm-x.com/en-gb/support/download/lut/",
                (r"f-?log2_?f-?gamut_to_.*709", r"f-?log2(?!c).*709")),
    OfficialLut("nlog", "Nikon", "N-Log → Rec.709", "https://downloadcenter.nikonimglib.com/en/download/sw/258.html",
                (r"^n-?log_bt2020_to_rec709", r"(^|[^a-z])n-?log.*709"),
                "Nikon ships the LUTs in an installer: run it, then import the .cube files it unpacks."),
    OfficialLut("bmdfilm5", "Blackmagic", "Film Gen5 → Rec.709", "",
                (r"(blackmagic|bmd).*gen ?5.*709",),
                "Installed with DaVinci Resolve (use \"Import from DaVinci Resolve\")."),
]

# Where DaVinci Resolve keeps the LUTs it ships (several makers' official ones among them).
RESOLVE_LUT_DIRS = [
    os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Blackmagic Design", "DaVinci Resolve", "Support", "LUT"),
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT",
    os.path.expanduser("~/.local/share/DaVinciResolve/LUT"),
]


def official_matches(entry: OfficialLut, luts: list[tuple[str, str]]) -> list[str]:
    """Library LUT paths that look like `entry`'s official file, best first."""
    out: list[str] = []
    for pat in entry.patterns:
        rx = re.compile(pat)
        out += [path for name, path in luts if rx.search(name.lower()) and path not in out]
    return out


def import_any(src: str) -> list[str]:
    """Import a .cube file, every .cube inside a .zip, or every .cube under a folder. Returns library paths."""
    if os.path.isdir(src):
        found = [os.path.join(root, n) for root, _, names in os.walk(src) for n in names
                 if n.lower().endswith(LUT_EXTENSIONS)]
        return [import_lut(p) for p in sorted(found)]
    if src.lower().endswith(".zip"):
        out = []
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(src) as zf:
            for info in zf.infolist():
                name = os.path.basename(info.filename)
                if info.is_dir() or not name.lower().endswith(LUT_EXTENSIONS) or name.startswith("._"):
                    continue
                dst = os.path.join(tmp, name)
                with zf.open(info) as fi, open(dst, "wb") as fo:
                    shutil.copyfileobj(fi, fo)
                try:
                    out.append(import_lut(dst))
                except LutError:
                    pass   # e.g. a 1D curve or a broken file next to the real LUTs
        if not out:
            raise LutError("no usable .cube LUT inside the .zip file")
        return out
    return [import_lut(src)]
