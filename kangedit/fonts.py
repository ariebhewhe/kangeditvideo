"""Installed fonts, read straight from the system font folders (Windows, macOS, Linux).

The caption font list, the viewer and the burned-in export all use these files, so a font that is only
installed for the current user (or that libass would not find by name) still looks the same everywhere:
the export hands its files to ffmpeg's subtitles filter (fontsdir).
"""

from __future__ import annotations

import os
import shutil
import struct
import sys
import threading
from pathlib import Path
from typing import Optional

FONT_EXTENSIONS = (".ttf", ".otf", ".ttc", ".otc")

_lock = threading.Lock()
_fonts: Optional[dict[str, list[str]]] = None


def font_dirs() -> list[Path]:
    home = Path.home()
    if os.name == "nt":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return [windir / "Fonts", local / "Microsoft" / "Windows" / "Fonts"]
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library" / "Fonts"]
    data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), data / "fonts", home / ".fonts"]


def _name_table_families(data: bytes, offset: int) -> list[str]:
    """Family names in one font (sfnt at `offset`): the typographic family (name id 16) and the legacy
    family (id 1, which names weights like "Arial Black" on their own). English names first."""
    num_tables = struct.unpack_from(">H", data, offset + 4)[0]
    for k in range(num_tables):
        tag, _, tbl_off, _ = struct.unpack_from(">4sIII", data, offset + 12 + 16 * k)
        if tag != b"name":
            continue
        _, count, str_off = struct.unpack_from(">HHH", data, tbl_off)
        found: dict[int, list[tuple[int, str]]] = {}
        for i in range(count):
            pid, eid, lang, nid, length, off = struct.unpack_from(">HHHHHH", data, tbl_off + 6 + 12 * i)
            if nid not in (1, 16):
                continue
            raw = data[tbl_off + str_off + off: tbl_off + str_off + off + length]
            if pid == 3 or pid == 0:            # Windows / Unicode: UTF-16 BE
                text = raw.decode("utf-16-be", "ignore")
                rank = 0 if (pid == 3 and lang == 0x409) else 1
            elif pid == 1 and eid == 0:         # Mac Roman
                text = raw.decode("mac_roman", "ignore")
                rank = 0 if lang == 0 else 2
            else:
                continue
            text = text.strip("\x00 ").strip()
            if text:
                found.setdefault(nid, []).append((rank, text))
        out = []
        for nid in (16, 1):     # "Arial" and "Arial Black": both are names people pick
            if nid in found:
                name = sorted(found[nid])[0][1]
                if name not in out:
                    out.append(name)
        return out
    return []


def families_in_file(path: str) -> list[str]:
    """Family names of a .ttf / .otf font file or of every font in a .ttc / .otc collection."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        if data[:4] == b"ttcf":
            n = struct.unpack_from(">I", data, 8)[0]
            offsets = struct.unpack_from(f">{n}I", data, 12)
        else:
            offsets = (0,)
        out: list[str] = []
        for off in offsets:
            for fam in _name_table_families(data, off):
                if fam not in out:
                    out.append(fam)
        return out
    except (OSError, struct.error, ValueError):
        return []


def system_fonts(refresh: bool = False) -> dict[str, list[str]]:
    """{family name: [font files]} for every installed font (scanned once, then cached)."""
    global _fonts
    with _lock:
        if _fonts is not None and not refresh:
            return _fonts
        fonts: dict[str, list[str]] = {}
        for d in font_dirs():
            if not d.is_dir():
                continue
            for root, _, files in os.walk(d):
                for name in files:
                    if not name.lower().endswith(FONT_EXTENSIONS):
                        continue
                    path = os.path.join(root, name)
                    for fam in families_in_file(path):
                        fonts.setdefault(fam, []).append(path)
        _fonts = dict(sorted(fonts.items(), key=lambda kv: kv[0].lower()))
        return _fonts


def font_files(family: str) -> list[str]:
    """Files of an installed family (case-insensitive), [] when it is not installed."""
    fonts = system_fonts()
    if family in fonts:
        return fonts[family]
    low = family.lower()
    return next((v for k, v in fonts.items() if k.lower() == low), [])


def copy_family(family: str, dest: str) -> bool:
    """Copy a family's files into `dest` (for ffmpeg's fontsdir). False when the family isn't installed."""
    files = font_files(family)
    if not files:
        return False
    os.makedirs(dest, exist_ok=True)
    for f in files:
        try:
            shutil.copy2(f, os.path.join(dest, os.path.basename(f)))
        except OSError:
            pass
    return True


def ensure_qt_font(family: str) -> None:
    """Make an installed font that Qt does not list (e.g. installed for this user only) available to Qt."""
    from PySide6.QtGui import QFontDatabase
    if not family or family in QFontDatabase.families():
        return
    for path in font_files(family):
        QFontDatabase.addApplicationFont(path)


_DEFAULT_FAMILIES = ("Arial", "Helvetica", "Helvetica Neue", "Liberation Sans", "DejaVu Sans", "Noto Sans")


def default_family() -> str:
    """A plain sans-serif font that is installed here (Arial on Windows, Helvetica on macOS...)."""
    for fam in _DEFAULT_FAMILIES:
        if font_files(fam):
            return fam
    return "Arial"


def first_installed(*families: str) -> str:
    """The first of `families` that is installed, else the default family."""
    for fam in families:
        if font_files(fam):
            return fam
    return default_family()
