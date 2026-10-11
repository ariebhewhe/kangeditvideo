#!/usr/bin/env python3
"""Render the app and tray icons from their SVG sources in kangedit/assets/.

    python scripts/make_icons.py

Writes icon_<size>.png and tray_<size>.png (loaded by the app at run time, so it doesn't need Qt's SVG plugin),
kangedit.ico (Windows executable) and kangedit.icns (macOS bundle). Run it after changing an SVG and commit
the results.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "kangedit" / "assets"
APP_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
TRAY_SIZES = (16, 22, 32, 44, 64)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_TYPES = {32: b"ic11", 64: b"ic12", 128: b"ic07", 256: b"ic08", 512: b"ic09"}   # PNG-coded entries


def render(svg: Path, size: int) -> bytes:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    QSvgRenderer(str(svg)).render(p)
    p.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def write_ico(path: Path, images: dict[int, bytes]) -> None:
    """ICO with PNG-coded entries (Windows Vista and later)."""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = len(head) + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images.items():
        dim = 0 if size >= 256 else size      # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    path.write_bytes(head + entries + blobs)


def write_icns(path: Path, images: dict[int, bytes]) -> None:
    body = b"".join(ICNS_TYPES[s] + struct.pack(">I", 8 + len(png)) + png for s, png in images.items())
    path.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


def main() -> int:
    app = QGuiApplication(sys.argv[:1])  # noqa: F841 - QPainter needs an application
    icon = {s: render(ASSETS / "icon.svg", s) for s in APP_SIZES}
    for s, png in icon.items():
        (ASSETS / f"icon_{s}.png").write_bytes(png)
    for s in TRAY_SIZES:
        (ASSETS / f"tray_{s}.png").write_bytes(render(ASSETS / "tray.svg", s))
    write_ico(ASSETS / "kangedit.ico", {s: icon[s] for s in ICO_SIZES})
    write_icns(ASSETS / "kangedit.icns", {s: icon[s] for s in ICNS_TYPES})
    print(f"Wrote icons to {ASSETS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
