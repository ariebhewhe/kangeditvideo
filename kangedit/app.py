"""Application bootstrap."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME
from .ffmpeg import ffmpeg_bin, ffprobe_bin


def _icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#4f8cff"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setPen(QColor("white"))
    f = QFont("Segoe UI", 26, QFont.Bold)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "K")
    p.end()
    return QIcon(pm)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    from .ui.theme import STYLE
    app.setStyleSheet(STYLE)
    app.setWindowIcon(_icon())

    if not ffmpeg_bin() or not ffprobe_bin():
        QMessageBox.critical(None, APP_NAME, "FFmpeg was not found.\n\nInstall FFmpeg and make sure 'ffmpeg' and "
                                             "'ffprobe' are on your PATH (e.g. `winget install Gyan.FFmpeg`).")
        return 1

    from .ui.main_window import MainWindow
    win = MainWindow()
    win.show()
    args = [a for a in argv[1:] if not a.startswith("-")]
    if args and args[0].endswith(".kgproj"):
        win.load_project_file(args[0])
    elif args:
        win.import_files(args)
    return app.exec()
