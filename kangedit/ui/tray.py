"""System tray icon: shows background-task progress and notifies when work finishes while the app is away."""

from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtWidgets import QMainWindow, QMenu, QSystemTrayIcon

from .. import APP_NAME
from ..tasks import TaskQueue
from .icons import tray_icon

LONG_TASK = 30.0   # seconds of background work worth a "finished" notification


class Tray(QSystemTrayIcon):
    def __init__(self, window: QMainWindow, tasks: TaskQueue):
        super().__init__(tray_icon(), window)
        self.window, self.tasks = window, tasks
        self._on_click: Optional[Callable[[], None]] = None
        self._busy_since: Optional[float] = None
        self._notified = False

        menu = QMenu(window)
        menu.addAction(f"Show {APP_NAME}", self.show_window)
        self.a_cancel = menu.addAction("Cancel background tasks", self._cancel)
        self.a_cancel.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Quit", window.close)
        self.setContextMenu(menu)
        self.setToolTip(APP_NAME)

        self.activated.connect(self._activated)
        self.messageClicked.connect(self._message_clicked)
        tasks.progress.connect(self._progress)
        tasks.pending_changed.connect(self._pending)
        tasks.idle.connect(self._idle)

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show_window(self):
        w = self.window
        if w.isMinimized():
            w.showNormal()
        w.show()
        w.raise_()
        w.activateWindow()

    def away(self) -> bool:
        """The user isn't looking at the app (it's minimized or another app is in front)."""
        return self.window.isMinimized() or not self.window.isActiveWindow()

    def notify(self, title: str, text: str, on_click: Optional[Callable[[], None]] = None,
               icon=QSystemTrayIcon.Information) -> None:
        """A desktop notification, only when the app is in the background (it shows its own dialogs otherwise)."""
        if not self.away() or not self.supportsMessages():
            return
        self._on_click = on_click
        self._notified = True
        self.showMessage(title, text, icon, 8000)

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def _cancel(self):
        self._notified = True     # no "finished" notification for work the user just stopped
        self.tasks.cancel_all()

    def _message_clicked(self):
        cb, self._on_click = self._on_click, None
        (cb or self.show_window)()

    def _progress(self, p: float, msg: str):
        self.setToolTip(f"{APP_NAME}\n{msg} — {int(p * 100)}%")

    def _pending(self, n: int):
        self.a_cancel.setEnabled(n > 0)
        if n > 0 and self._busy_since is None:
            self._busy_since, self._notified = time.monotonic(), False

    def _idle(self):
        self.setToolTip(APP_NAME)
        long = self._busy_since is not None and time.monotonic() - self._busy_since >= LONG_TASK
        self._busy_since = None
        if long and not self._notified:   # an export or error already said how it ended
            self.notify(APP_NAME, "Background work finished.")
