"""Sounds tab: background music (NCS suggestions or a YouTube link), MyInstants sound effects and the library."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QMimeData, QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .. import music, sfx
from ..config import open_path, sfx_dir
from .inspector import ParamSlider


class _Bridge(QObject):
    """Results from worker threads, delivered on the UI thread."""
    searched = Signal(object, str, bool)     # sounds, error, appended (next page)
    downloaded = Signal(str, str, str)       # path, name, error
    music_progress = Signal(float, str)
    music_done = Signal(object, str)         # music.Downloaded or None, error


class _LibraryList(QListWidget):
    """Library items drag onto the timeline as audio files."""

    def mimeData(self, items):
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(it.data(Qt.UserRole)) for it in items])
        return md


class SoundPanel(QWidget):
    add_sound = Signal(str, str)          # library path, name -> add at the playhead
    add_music = Signal(str, str, str)     # library path, title, video page -> background music for the edit
    volume_changed = Signal(float)        # new volume (1.0 = 100%) for the sound selected on the timeline

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bridge = _Bridge()
        self._bridge.searched.connect(self._on_searched)
        self._bridge.downloaded.connect(self._on_downloaded)
        self._bridge.music_progress.connect(lambda p, msg: self.lbl_music.setText(f"{msg} {p * 100:.0f}%"))
        self._bridge.music_done.connect(self._on_music)
        self._music_busy = False
        self._query, self._page = "", 1
        self._audio = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self._volume_box())
        lay.addWidget(self._music_box(), 2)
        lay.addSpacing(6)
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

    # ------------------------------------------------------------------ selected sound
    def _volume_box(self) -> QGroupBox:
        box = QGroupBox("Selected sound")
        v = QVBoxLayout(box)
        self.lbl_selected = QLabel("")
        self.lbl_selected.setWordWrap(True)
        self.lbl_selected.setStyleSheet("color: #8b909a;")
        v.addWidget(self.lbl_selected)
        row = QHBoxLayout()
        row.addWidget(QLabel("Volume"))
        self.vol = ParamSlider(0, 200, 100, 1, 0, "%")
        self.vol.setToolTip("Volume of the sound effect or music selected on the timeline")
        self.vol.changed.connect(lambda val: self.volume_changed.emit(val / 100.0))
        row.addWidget(self.vol, 1)
        v.addLayout(row)
        self.set_selected_sound(None)
        return box

    def set_selected_sound(self, x) -> None:
        """Show the volume of a timeline SoundClip (None: nothing selected, slider disabled)."""
        self.vol.setEnabled(x is not None)
        self.lbl_selected.setText(x.name or os.path.basename(x.path) if x is not None
                                  else "Click a sound effect or music on the timeline to change its volume.")
        if x is not None:
            self.vol.set_value(x.volume * 100.0)

    # ------------------------------------------------------------------ music
    def _music_box(self) -> QGroupBox:
        box = QGroupBox("Background music (NCS)")
        v = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(QLabel("For a"))
        self.cb_mood = QComboBox()
        self.cb_mood.addItems(list(music.SUGGESTIONS))
        self.cb_mood.setToolTip("Suggested NoCopyrightSounds tracks for this kind of video")
        self.cb_mood.currentTextChanged.connect(self._fill_suggestions)
        row.addWidget(self.cb_mood, 1)
        v.addLayout(row)
        self.suggestions = QListWidget()
        self.suggestions.currentItemChanged.connect(
            lambda it, _prev: it is not None and self.ed_link.setText(""))
        self.suggestions.itemDoubleClicked.connect(lambda _it: self._get_music())
        v.addWidget(self.suggestions, 1)
        self.ed_link = QLineEdit(placeholderText="…or paste a YouTube link")
        self.ed_link.setClearButtonEnabled(True)
        self.ed_link.returnPressed.connect(self._get_music)
        v.addWidget(self.ed_link)
        row = QHBoxLayout()
        self.b_music = QPushButton("♪ Add to timeline")
        self.b_music.setObjectName("primary")
        self.b_music.setToolTip("Download the track, extract its sound and lay it under the whole edit "
                                "(at 50% volume: select it on the timeline to change)")
        self.b_music.clicked.connect(self._get_music)
        b_yt = QPushButton("Open on YouTube")
        b_yt.setToolTip("Listen to the track first")
        b_yt.clicked.connect(self._open_music)
        row.addWidget(self.b_music, 1)
        row.addWidget(b_yt)
        v.addLayout(row)
        self.lbl_music = QLabel("")
        self.lbl_music.setStyleSheet("color: #8b909a;")
        self.lbl_music.setWordWrap(True)
        v.addWidget(self.lbl_music)
        note = QLabel("NCS music is free to use when the video's description credits it: the credit is copied "
                      "to the clipboard when you add a track. Other YouTube music needs its owner's permission.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #6f737c; font-size: 11px;")
        v.addWidget(note)
        self._fill_suggestions(self.cb_mood.currentText())
        return box

    def _fill_suggestions(self, mood: str):
        self.suggestions.clear()
        for t in music.SUGGESTIONS.get(mood, []):
            it = QListWidgetItem(t.name)
            it.setData(Qt.UserRole, t)
            self.suggestions.addItem(it)

    def _music_source(self):
        """The pasted link, else the selected suggestion (a music.Track), else None."""
        link = self.ed_link.text().strip()
        if link:
            return link
        it = self.suggestions.currentItem()
        return it.data(Qt.UserRole) if it is not None else None

    def _open_music(self):
        src = self._music_source()
        if isinstance(src, music.Track):
            QDesktopServices.openUrl(QUrl(music.search_url(src)))
        elif src:
            QDesktopServices.openUrl(QUrl(src if "://" in src else "https://" + src))

    def _get_music(self):
        if self._music_busy:
            return
        src = self._music_source()
        if not src:
            self.lbl_music.setText("Pick a suggested track or paste a YouTube link.")
            return
        if isinstance(src, str) and not music.is_youtube_link(src):
            self.lbl_music.setText("That is not a YouTube link.")
            return
        if not music.available():
            self.lbl_music.setText("Music download needs yt-dlp: run install.py again (or pip install yt-dlp).")
            return
        self._music_busy = True
        self.b_music.setEnabled(False)
        name = src.name if isinstance(src, music.Track) else src
        self.lbl_music.setText(f"Looking up “{name}”…")

        def work():
            try:
                self._bridge.music_done.emit(music.download(src, self._bridge.music_progress.emit), "")
            except Exception as e:  # noqa: BLE001 — offline, video unavailable, YouTube changed...
                msg = str(e).replace("ERROR: ", "")
                self._bridge.music_done.emit(None, msg.splitlines()[0][:300] if msg else type(e).__name__)

        threading.Thread(target=work, daemon=True).start()

    def _on_music(self, got, error: str):
        self._music_busy = False
        self.b_music.setEnabled(True)
        if got is None:
            self.lbl_music.setText(f"Couldn't get the music: {error}")
            return
        self.lbl_music.setText(f"Added “{got.title}”")
        self.refresh_library()
        self.add_music.emit(got.path, got.title, got.url)

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
