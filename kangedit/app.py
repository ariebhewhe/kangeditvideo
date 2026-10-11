"""Application bootstrap."""

from __future__ import annotations

import sys
import threading
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

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


def _download_ffmpeg() -> bool:
    """Offer to download FFmpeg next to the app; True once it is there."""
    from . import ffmpeg_setup
    if QMessageBox.question(None, APP_NAME, "KangEdit needs FFmpeg, which was not found.\n\nDownload it now "
                                            "(about 80-160 MB, into the app's ffmpeg folder)?") != QMessageBox.Yes:
        QMessageBox.information(None, APP_NAME, "Install FFmpeg and make sure 'ffmpeg' and 'ffprobe' are on your "
                                                "PATH, or start KangEdit again to download it.")
        return False
    dlg = QProgressDialog("Downloading FFmpeg…", "Cancel", 0, 1000)
    dlg.setWindowTitle(APP_NAME)
    dlg.setWindowModality(Qt.ApplicationModal)
    dlg.setMinimumDuration(0)
    cancel = threading.Event()
    dlg.canceled.connect(cancel.set)
    state = {"p": 0.0, "msg": "Downloading FFmpeg…", "error": None, "done": False}

    def work():
        try:
            ffmpeg_setup.install(lambda p, m: state.update(p=p, msg=m), cancel)
        except Exception as e:  # noqa: BLE001
            state["error"] = str(e)
        state["done"] = True

    threading.Thread(target=work, daemon=True).start()
    while not state["done"]:
        dlg.setValue(int(state["p"] * 1000))
        dlg.setLabelText(state["msg"])
        QApplication.processEvents()
        time.sleep(0.03)
    dlg.close()
    if state["error"] or not (ffmpeg_bin() and ffprobe_bin()):
        if not cancel.is_set():
            QMessageBox.critical(None, APP_NAME, f"FFmpeg could not be downloaded:\n{state['error']}\n\n"
                                                 "Install FFmpeg and put it on your PATH instead.")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    from .ui.theme import STYLE
    app.setStyleSheet(STYLE)
    app.setWindowIcon(_icon())

    if (not ffmpeg_bin() or not ffprobe_bin()) and not _download_ffmpeg():
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
