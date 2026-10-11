"""The app and tray icons, from the PNGs scripts/make_icons.py renders out of kangedit/assets/*.svg."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def _from_pngs(prefix: str) -> QIcon:
    icon = QIcon()
    for png in sorted(ASSETS.glob(f"{prefix}_*.png")):
        icon.addFile(str(png))
    return icon


def app_icon() -> QIcon:
    return _from_pngs("icon")


def tray_icon() -> QIcon:
    """The full-colour icon, except in the macOS menu bar: a black mark the system tints for light / dark."""
    if sys.platform != "darwin":
        return app_icon()
    icon = _from_pngs("tray")
    icon.setIsMask(True)
    return icon
