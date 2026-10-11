"""Media bin: imported footage with status, thumbnails and per-footage actions."""

from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QMenu, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .. import lut as lutlib
from ..analysis import Analysis
from ..color import LOG_CHOICES, lut_profile, profile_label
from ..config import VIDEO_EXTENSIONS, open_path
from ..models import Project
from .theme import fmt_time


class MediaBin(QWidget):
    import_requested = Signal()
    files_dropped = Signal(list)
    action = Signal(str, list)            # action name, footage ids
    log_profile = Signal(list, str)       # footage ids, profile key

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.analyses: dict[str, Analysis] = {}
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Footage", "Duration", "Format", "Camera", "Log", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setIconSize(QSize(64, 36))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemDoubleClicked.connect(lambda *_: self.action.emit("append_best", self.selected_ids()))
        self.tree.setColumnWidth(0, 210)
        self.tree.setColumnWidth(1, 60)
        self.tree.setColumnWidth(2, 120)
        self.tree.setColumnWidth(3, 90)
        self.tree.setColumnWidth(4, 70)

        b_import = QPushButton("＋ Import footage")
        b_import.setObjectName("primary")
        b_import.clicked.connect(self.import_requested.emit)
        b_remove = QPushButton("Remove")
        b_remove.clicked.connect(lambda: self.action.emit("remove", self.selected_ids()))
        top = QHBoxLayout()
        top.addWidget(b_import, 1)
        top.addWidget(b_remove)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
        lay.addWidget(self.tree, 1)
        self.setAcceptDrops(True)

    def set_project(self, project: Project):
        self.project = project
        self.refresh()

    def selected_ids(self) -> list[str]:
        return [it.data(0, Qt.UserRole) for it in self.tree.selectedItems()]

    def refresh(self):
        sel = set(self.selected_ids())
        self.tree.clear()
        for f in self.project.footage.values():
            status = []
            status.append("Proxy ✓" if f.has_proxy() else "No proxy")
            status.append("Analyzed ✓" if f.id in self.analyses else "Not analyzed")
            if f.transcript is not None:
                status.append("Captions ✓")
            used = sum(1 for c in self.project.clips if c.footage_id == f.id)
            if used:
                status.append(f"Used ×{used}")
            log = "—" if f.log_profile == "none" else profile_label(f.log_profile).split(" /")[0]
            it = QTreeWidgetItem([f.name, fmt_time(f.duration, frames=False),
                                  f"{f.width}×{f.height} {f.fps:.2f}p {f.bit_depth}bit", f.camera or "—", log,
                                  " · ".join(status)])
            it.setData(0, Qt.UserRole, f.id)
            it.setToolTip(0, f"{f.path}\n{f.codec} {f.pix_fmt} · {f.color_space or 'untagged'} "
                             f"{f.color_transfer or ''}")
            it.setToolTip(4, f"{profile_label(f.log_profile)}" + (f"\n{f.log_note}" if f.log_note else ""))
            a = self.analyses.get(f.id)
            if a is not None and len(a.thumbs):
                arr = np.ascontiguousarray(a.thumbs[len(a.thumbs) // 2])
                img = QImage(arr.data, arr.shape[1], arr.shape[0], arr.strides[0], QImage.Format_RGB888).copy()
                it.setIcon(0, QIcon(QPixmap.fromImage(img)))
            self.tree.addTopLevelItem(it)
            if f.id in sel:
                it.setSelected(True)

    def _menu(self, pos):
        ids = self.selected_ids()
        if not ids:
            return
        m = QMenu(self)
        for key, label in (("append_best", "Append best moment to timeline"),
                           ("append_whole", "Append whole clip to timeline"),
                           (None, None),
                           ("proxy", "Generate proxy"), ("delete_proxy", "Delete proxy"),
                           ("analyze", "Analyze (re-run)"), ("transcribe", "Transcribe for captions"),
                           (None, None)):
            if key is None:
                m.addSeparator()
                continue
            a = QAction(label, m)
            a.triggered.connect(lambda _=False, k=key: self.action.emit(k, ids))
            m.addAction(a)
        sub = m.addMenu("Log profile → Rec.709")
        a = QAction("Detect from camera", sub)
        a.setToolTip("Identify the camera and choose its log profile automatically")
        a.triggered.connect(lambda: self.action.emit("detect_log", ids))
        sub.addAction(a)
        sub.addSeparator()
        current = {self.project.footage[i].log_profile for i in ids if i in self.project.footage}
        choices = list(LOG_CHOICES)
        luts = lutlib.library()
        if luts:
            choices.append((None, None))
            choices += [(lut_profile(path), f"LUT: {name}") for name, path in luts]
        for key, label in choices:
            if key is None:
                sub.addSeparator()
                continue
            a = QAction(label, sub, checkable=True)
            a.setChecked(current == {key})
            a.triggered.connect(lambda _=False, k=key: self.log_profile.emit(ids, k))
            sub.addAction(a)
        sub.addSeparator()
        a = QAction("Import conversion LUT (.cube)…", sub)
        a.triggered.connect(lambda: self.action.emit("import_lut_log", ids))
        sub.addAction(a)
        m.addSeparator()
        a = QAction("Show in folder", m)
        a.triggered.connect(lambda: open_path(os.path.dirname(self.project.footage[ids[0]].path)))
        m.addAction(a)
        a = QAction("Remove from project", m)
        a.triggered.connect(lambda: self.action.emit("remove", ids))
        m.addAction(a)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = []
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if os.path.isdir(p):
                for root, _, files in os.walk(p):
                    paths += [os.path.join(root, f) for f in files if f.lower().endswith(VIDEO_EXTENSIONS)]
            elif p.lower().endswith(VIDEO_EXTENSIONS):
                paths.append(p)
        if paths:
            self.files_dropped.emit(sorted(paths))
