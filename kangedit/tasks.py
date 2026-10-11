"""Sequential background task queue with progress and cancellation."""

from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Signal

from .ffmpeg import Cancelled

TaskFn = Callable[[Callable[[float, str], None], threading.Event], Any]


@dataclass
class Task:
    name: str
    fn: TaskFn
    on_done: Optional[Callable[[Any], None]] = None
    on_error: Optional[Callable[[str], None]] = None


class TaskQueue(QObject):
    progress = Signal(float, str)
    started = Signal(str)
    idle = Signal()
    pending_changed = Signal(int)
    _done = Signal(object, object)    # task, result
    _error = Signal(object, str)      # task, message

    def __init__(self):
        super().__init__()
        self._q: "queue.Queue[Optional[Task]]" = queue.Queue()
        self._cancel = threading.Event()
        self._pending = 0
        self._lock = threading.Lock()
        self.current: Optional[str] = None
        self._done.connect(self._on_done)
        self._error.connect(self._on_error)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @property
    def busy(self) -> bool:
        return self._pending > 0

    def submit(self, name: str, fn: TaskFn, on_done=None, on_error=None) -> None:
        with self._lock:
            self._pending += 1
        self.pending_changed.emit(self._pending)
        self._q.put(Task(name, fn, on_done, on_error))

    def cancel_all(self) -> None:
        self._cancel.set()
        try:
            while True:
                t = self._q.get_nowait()
                if t is not None:
                    with self._lock:
                        self._pending -= 1
        except queue.Empty:
            pass
        self.pending_changed.emit(self._pending)

    def shutdown(self) -> None:
        self.cancel_all()
        self._q.put(None)

    def _loop(self):
        while True:
            task = self._q.get()
            if task is None:
                return
            self._cancel.clear()
            self.current = task.name
            self.started.emit(task.name)
            try:
                res = task.fn(lambda p, m="": self.progress.emit(float(p), m or task.name), self._cancel)
                self._done.emit(task, res)
            except Cancelled:
                self._error.emit(task, "")
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self._error.emit(task, f"{e}")

    def _finish(self):
        with self._lock:
            self._pending = max(0, self._pending - 1)
        self.current = None
        self.pending_changed.emit(self._pending)
        if self._pending == 0:
            self.idle.emit()

    def _on_done(self, task: Task, res):
        try:
            if task.on_done:
                task.on_done(res)
        finally:
            self._finish()

    def _on_error(self, task: Task, msg: str):
        try:
            if task.on_error:
                task.on_error(msg)
        finally:
            self._finish()
