"""Caption list: generate, review and edit captions."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..captions import Caption
from .theme import fmt_time


class CaptionsPanel(QWidget):
    generate = Signal()
    style_requested = Signal()
    export_srt = Signal()
    text_edited = Signal(int, str)
    seek = Signal(float)
    toggled = Signal(bool)
    delete_requested = Signal(list)    # caption rows; every row for "Delete all"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.captions: list[Caption] = []
        b_gen = QPushButton("🎙 Generate captions")
        b_gen.setObjectName("primary")
        b_gen.setToolTip("Transcribe speech in the footage used on the timeline (Whisper, runs offline)")
        b_gen.clicked.connect(self.generate.emit)
        b_style = QPushButton("Style…")
        b_style.clicked.connect(self.style_requested.emit)
        b_srt = QPushButton("Save .srt")
        b_srt.clicked.connect(self.export_srt.emit)
        self.chk_show = QCheckBox("Show")
        self.chk_show.setChecked(True)
        self.chk_show.toggled.connect(self.toggled.emit)
        tools = QHBoxLayout()
        tools.addWidget(b_style)
        tools.addWidget(b_srt)
        tools.addStretch(1)
        tools.addWidget(self.chk_show)
        top = QVBoxLayout()         # two rows: fits a narrow panel
        top.addWidget(b_gen)
        top.addLayout(tools)
        self.b_delete = QPushButton("🗑 Delete selected")
        self.b_delete.setToolTip("Remove the selected captions (Del). Select several with Ctrl / Shift.")
        self.b_delete.clicked.connect(self._delete_selected)
        self.b_delete_all = QPushButton("Delete all")
        self.b_delete_all.setToolTip("Remove every caption on the timeline")
        self.b_delete_all.clicked.connect(lambda: self.delete_requested.emit(list(range(len(self.captions)))))
        bottom = QHBoxLayout()
        bottom.addWidget(self.b_delete)
        bottom.addWidget(self.b_delete_all)
        bottom.addStretch(1)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Start", "End", "Text (double-click to edit)"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        sc = QShortcut(QKeySequence.Delete, self.table, activated=self._delete_selected)
        sc.setContext(Qt.WidgetShortcut)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 70)
        self.table.itemChanged.connect(self._changed)
        self.table.cellClicked.connect(lambda r, _c: self.seek.emit(self.captions[r].start + 0.01)
                                       if r < len(self.captions) else None)
        self.lbl = QLabel("No captions yet. Captions follow your edits automatically.")
        self.lbl.setStyleSheet("color: #8b909a;")
        self.lbl.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addLayout(bottom)
        lay.addWidget(self.lbl)
        self._sync_buttons()

    def set_captions(self, caps: list[Caption]):
        self.captions = caps
        self.table.blockSignals(True)
        self.table.setRowCount(len(caps))
        for i, c in enumerate(caps):
            for col, text in enumerate((fmt_time(c.start), fmt_time(c.end), c.text)):
                it = QTableWidgetItem(text)
                if col < 2:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(i, col, it)
        self.table.blockSignals(False)
        if caps:
            self.lbl.setText(f"{len(caps)} captions. Edits to text are kept even when you re-trim clips.")
        else:
            self.lbl.setText("No captions yet. Captions follow your edits automatically.")
        self._sync_buttons()

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectedIndexes() if i.row() < len(self.captions)})

    def _delete_selected(self):
        rows = self._selected_rows()
        if rows and self.table.state() != QAbstractItemView.EditingState:
            self.delete_requested.emit(rows)

    def _sync_buttons(self):
        self.b_delete.setEnabled(bool(self._selected_rows()))
        self.b_delete_all.setEnabled(bool(self.captions))

    def select(self, i: int):
        if 0 <= i < self.table.rowCount():
            self.table.selectRow(i)
            self.table.scrollToItem(self.table.item(i, 0))

    def _changed(self, item: QTableWidgetItem):
        if item.column() == 2:
            self.text_edited.emit(item.row(), item.text())
