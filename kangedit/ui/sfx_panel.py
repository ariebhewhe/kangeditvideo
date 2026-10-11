"""Sounds tab: search MyInstants, preview, and add sound effects to the SFX layer (or from the library)."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QMimeData, QObject, Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .. import sfx
from ..config import open_path, sfx_dir


class _Bridge(QObject):
    """Results from worker threads, delivered on the UI thread."""
    searched = Signal(object, str, bool)     # sounds, error, appended (next page)
    downloaded = Signal(str, str, str)       # path, name, error


class _LibraryList(QListWidget):
    """Library items drag onto the timeline as audio files."""

    def mimeData(self, items):
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(it.data(Qt.UserRole)) for it in items])
        return md


class SoundPanel(QWidget):
    add_sound = Signal(str, str)   # library path, name -> add at the playhead

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bridge = _Bridge()
        self._bridge.searched.connect(self._on_searched)
        self._bridge.downloaded.connect(self._on_downloaded)
        self._query, self._page = "", 1
        self._audio = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(QLabel("<b>Find sound effects</b> (MyInstants)", wordWrap=True))
        row = QHBoxLayout()
        self.ed_search = QLineEdit(placeholderText="e.g. vine boom, whoosh, applause, ding…")
        self.ed_search.returnPressed.connect(self.search)
        b = QPushButton("Search")
        b.clicked.connect(self.search)
        row.addWidget(self.ed_search, 1)
        row.addWidget(b)
        lay.addLayout(row)
        self.results = QListWidget()
        self.results.itemDoubleClicked.connect(lambda it: self._preview_online(it))
        lay.addWidget(self.results, 2)
        row = QHBoxLayout()
        self.b_play = QPushButton("▶ Preview")
        self.b_play.clicked.connect(lambda: self._preview_online(self.results.currentItem()))
        self.b_add = QPushButton("＋ Add at playhead")
        self.b_add.setObjectName("primary")
        self.b_add.clicked.connect(self._add_online)
        self.b_more = QPushButton("More results")
        self.b_more.clicked.connect(lambda: self.search(more=True))
        self.b_more.setEnabled(False)
        for w in (self.b_play, self.b_add, self.b_more):
            row.addWidget(w)
        lay.addLayout(row)
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #8b909a;")
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)

        lay.addSpacing(6)
        lay.addWidget(QLabel("<b>Library</b> — drag onto the timeline, or add at the playhead", wordWrap=True))
        self.library = _LibraryList()
        self.library.setDragEnabled(True)
        self.library.itemDoubleClicked.connect(self._preview_local)
        lay.addWidget(self.library, 2)
        row = QGridLayout()         # two rows of buttons: fits a narrow panel
        for k, (text, slot) in enumerate((("▶ Play", lambda: self._preview_local(self.library.currentItem())),
                                          ("＋ Add", self._add_local), ("Import…", self._import),
                                          ("Delete", self._delete), ("Folder", lambda: open_path(sfx_dir())))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b, k // 3, k % 3)
        lay.addLayout(row)
        note = QLabel("MyInstants sounds are uploaded by its users: make sure you may use a sound before "
                      "publishing a video with it.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #6f737c; font-size: 11px;")
        lay.addWidget(note)
        self.refresh_library()

    # ------------------------------------------------------------------ online
    def search(self, more: bool = False):
        q = self.ed_search.text().strip()
        if not q:
            return
        if not more:
            self._query, self._page = q, 1
            self.results.clear()
        else:
            self._page += 1
        self.lbl_status.setText(f"Searching for “{self._query}”…")
        query, page = self._query, self._page

        def work():
            try:
                self._bridge.searched.emit(sfx.search_myinstants(query, page), "", more)
            except Exception as e:  # noqa: BLE001 — offline, site changed...
                self._bridge.searched.emit([], str(e), more)

        threading.Thread(target=work, daemon=True).start()

    def _on_searched(self, sounds, error: str, more: bool):
        if error:
            self.lbl_status.setText(f"Search failed: {error}")
            return
        for snd in sounds:
            it = QListWidgetItem(snd.name)
            it.setData(Qt.UserRole, snd)
            it.setToolTip(snd.page)
            self.results.addItem(it)
        self.b_more.setEnabled(len(sounds) >= 20)
        n = self.results.count()
        self.lbl_status.setText(f"{n} sounds" if n else "No sounds found")
        if n and not more:
            self.results.setCurrentRow(0)

    def _preview_online(self, item):
        if item is not None:
            self._play(QUrl(item.data(Qt.UserRole).url))

    def _add_online(self):
        item = self.results.currentItem()
        if item is None:
            return
        snd = item.data(Qt.UserRole)
        self.lbl_status.setText(f"Downloading “{snd.name}”…")

        def work():
            try:
                self._bridge.downloaded.emit(sfx.download(snd), snd.name, "")
            except Exception as e:  # noqa: BLE001
                self._bridge.downloaded.emit("", snd.name, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_downloaded(self, path: str, name: str, error: str):
        if error:
            self.lbl_status.setText(f"Download of “{name}” failed: {error}")
            return
        self.lbl_status.setText(f"Added “{name}” to the library")
        self.refresh_library()
        self.add_sound.emit(path, name)

    # ------------------------------------------------------------------ library
    def refresh_library(self):
        self.library.clear()
        for name, path in sfx.library():
            it = QListWidgetItem(name)
            it.setData(Qt.UserRole, path)
            it.setToolTip(path)
            self.library.addItem(it)

    def _preview_local(self, item):
        if item is not None:
            self._play(QUrl.fromLocalFile(item.data(Qt.UserRole)))

    def _add_local(self):
        item = self.library.currentItem()
        if item is not None:
            self.add_sound.emit(item.data(Qt.UserRole), item.text())

    def _import(self):
        exts = " ".join(f"*{e}" for e in sfx.AUDIO_EXTENSIONS)
        paths, _ = QFileDialog.getOpenFileNames(self, "Import sound effects", "", f"Audio ({exts});;All files (*)")
        for p in paths:
            try:
                sfx.import_file(p)
            except OSError as e:
                QMessageBox.warning(self, "Sounds", f"{os.path.basename(p)}: {e}")
        self.refresh_library()

    def _delete(self):
        item = self.library.currentItem()
        if item is None:
            return
        if QMessageBox.question(self, "Sounds", f"Delete “{item.text()}” from the library?\n\nSounds already on "
                                                "a timeline that use it will go silent.") != QMessageBox.Yes:
            return
        self._player.stop()
        self._player.setSource(QUrl())
        try:
            os.remove(item.data(Qt.UserRole))
        except OSError as e:
            QMessageBox.warning(self, "Sounds", str(e))
        self.refresh_library()

    def _play(self, url: QUrl):
        self._player.stop()
        self._player.setSource(url)
        self._player.play()

    def preview_file(self, path: str):
        self._play(QUrl.fromLocalFile(path))
