"""Media bin: imported footage with status, thumbnails and per-footage actions.

Two views of the same footage: a detail list (columns) and a thumbnail grid. In the grid, moving the
mouse across a thumbnail scrubs through the clip (analysis keeps one thumbnail per second).
"""

from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QListView, QListWidget, QListWidgetItem, QMenu,
                               QPushButton, QStackedWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from .. import lut as lutlib
from ..analysis import Analysis
from ..color import LOG_CHOICES, lut_profile, profile_label
from ..config import VIDEO_EXTENSIONS, open_path, settings
from ..models import Project
from .theme import fmt_time

GRID_COLUMNS = 2                          # thumbnails per row; they grow with the bin's width


def _pixmap(arr: np.ndarray) -> QPixmap:
    arr = np.ascontiguousarray(arr)
    img = QImage(arr.data, arr.shape[1], arr.shape[0], arr.strides[0], QImage.Format_RGB888).copy()
    return QPixmap.fromImage(img)


class _Grid(QListWidget):
    """Thumbnail grid: fixed number of columns, hovering scrubs through a clip's thumbnails."""
    resized = Signal()
    hover = Signal(object, float)        # item (or None), horizontal position over its icon 0..1

    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(6)
        self.setMouseTracking(True)
        self.viewport().installEventFilter(self)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()

    def eventFilter(self, obj, e):
        if e.type() == QEvent.MouseMove:
            pos = e.position().toPoint()
            it = self.itemAt(pos)
            if it is not None:
                r = self.visualItemRect(it)
                self.hover.emit(it, (pos.x() - r.left()) / max(1, r.width()))
            else:
                self.hover.emit(None, 0.0)
        elif e.type() == QEvent.Leave:
            self.hover.emit(None, 0.0)
        return super().eventFilter(obj, e)


class MediaBin(QWidget):
    import_requested = Signal()
    files_dropped = Signal(list)
    action = Signal(str, list)            # action name, footage ids
    log_profile = Signal(list, str)       # footage ids, profile key

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.analyses: dict[str, Analysis] = {}
        self._qs = settings()
        self._scrubbing = None            # grid item whose icon shows a scrubbed frame
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

        self.grid = _Grid()
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._menu)
        self.grid.itemDoubleClicked.connect(lambda *_: self.action.emit("append_best", self.selected_ids()))
        self.grid.resized.connect(self._apply_thumb_size)
        self.grid.hover.connect(self._scrub)
        self._apply_thumb_size()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.grid)

        b_import = QPushButton("＋ Import footage")
        b_import.setObjectName("primary")
        b_import.clicked.connect(self.import_requested.emit)
        b_remove = QPushButton("Remove")
        b_remove.clicked.connect(lambda: self.action.emit("remove", self.selected_ids()))
        self.btn_details = QToolButton(text="☰", checkable=True, autoExclusive=True)
        self.btn_details.setToolTip("Detail view: duration, format, camera, log profile and status")
        self.btn_thumbs = QToolButton(text="▦", checkable=True, autoExclusive=True)
        self.btn_thumbs.setToolTip("Thumbnail view (hover to scrub)")
        self.btn_details.clicked.connect(lambda: self.set_view_mode("details"))
        self.btn_thumbs.clicked.connect(lambda: self.set_view_mode("thumbnails"))
        top = QHBoxLayout()
        top.addWidget(b_import, 1)
        top.addWidget(b_remove)
        top.addWidget(self.btn_details)
        top.addWidget(self.btn_thumbs)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
        lay.addWidget(self.stack, 1)
        self.setAcceptDrops(True)
        self.set_view_mode(self._qs.value("bin_view", "details", type=str))

    # ------------------------------------------------------------------ view mode
    def set_view_mode(self, mode: str):
        """"details" (list with columns) or "thumbnails" (grid)."""
        thumbs = mode == "thumbnails"
        sel = self.selected_ids()
        self.stack.setCurrentWidget(self.grid if thumbs else self.tree)
        (self.btn_thumbs if thumbs else self.btn_details).setChecked(True)
        self._qs.setValue("bin_view", "thumbnails" if thumbs else "details")
        self._select(sel)

    def view_mode(self) -> str:
        return "thumbnails" if self.stack.currentWidget() is self.grid else "details"

    def _apply_thumb_size(self):
        """Size the thumbnails so GRID_COLUMNS of them fill the width (room kept for the scrollbar,
        so it appearing doesn't change the layout)."""
        g = self.grid
        avail = g.width() - 2 * g.frameWidth() - g.verticalScrollBar().sizeHint().width()
        cell = max(48, avail // GRID_COLUMNS - 1)
        w = max(32, cell - 2 * g.spacing() - 4)
        h = w * 9 // 16
        if g.iconSize() != QSize(w, h):
            g.setIconSize(QSize(w, h))
            g.setGridSize(QSize(cell, h + 2 * g.spacing() + 4))

    def _scrub(self, item, x: float):
        """Show the clip's frame under the mouse on its grid thumbnail."""
        if self._scrubbing is not None and self._scrubbing is not item:
            self._set_grid_icon(self._scrubbing, None)
            self._scrubbing = None
        if item is None:
            return
        a = self.analyses.get(item.data(Qt.UserRole))
        if a is None or not len(a.thumbs):
            return
        self._scrubbing = item
        self._set_grid_icon(item, min(len(a.thumbs) - 1, max(0, int(x * len(a.thumbs)))))

    def _set_grid_icon(self, item, index):
        """index None: the clip's representative (middle) thumbnail."""
        a = self.analyses.get(item.data(Qt.UserRole))
        if a is not None and len(a.thumbs):
            item.setIcon(QIcon(_pixmap(a.thumbs[len(a.thumbs) // 2 if index is None else index])))
        else:
            item.setIcon(QIcon(self._placeholder()))

    def _placeholder(self) -> QPixmap:
        pm = QPixmap(128, 72)
        pm.fill(QColor("#22252b"))
        p = QPainter(pm)
        p.setPen(QColor("#6b6e76"))
        p.drawText(pm.rect(), Qt.AlignCenter, "Analyzing…")
        p.end()
        return pm

    # ------------------------------------------------------------------ content
    def set_project(self, project: Project):
        self.project = project
        self.refresh()

    def selected_ids(self) -> list[str]:
        if self.stack.currentWidget() is self.grid:
            return [it.data(Qt.UserRole) for it in self.grid.selectedItems()]
        return [it.data(0, Qt.UserRole) for it in self.tree.selectedItems()]

    def _select(self, ids):
        ids = set(ids)
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            it.setSelected(it.data(0, Qt.UserRole) in ids)
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            it.setSelected(it.data(Qt.UserRole) in ids)

    def refresh(self):
        sel = set(self.selected_ids())
        self.tree.clear()
        self.grid.clear()
        self._scrubbing = None
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
                it.setIcon(0, QIcon(_pixmap(a.thumbs[len(a.thumbs) // 2])))
            self.tree.addTopLevelItem(it)

            badges = [b for b, on in (("P", f.has_proxy()), ("CC", f.transcript is not None),
                                      (f"×{used}", used)) if on]
            gi = QListWidgetItem()
            gi.setData(Qt.UserRole, f.id)
            gi.setToolTip(f"{f.name} · {fmt_time(f.duration, frames=False)}"
                          + (f" · {' '.join(badges)}" if badges else "") + f"\n{f.path}\n{f.width}×{f.height} {f.fps:.2f}p {f.bit_depth}bit · {f.camera or 'unknown camera'}"
                          f"\n{profile_label(f.log_profile)}\n{' · '.join(status)}")
            self.grid.addItem(gi)
            self._set_grid_icon(gi, None)
        self._select(sel)

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
        m.addSeparator()
        view = m.addMenu("View")
        for mode, label in (("details", "Details"), ("thumbnails", "Thumbnails")):
            a = QAction(label, view, checkable=True)
            a.setChecked(self.view_mode() == mode)
            a.triggered.connect(lambda _=False, md=mode: self.set_view_mode(md))
            view.addAction(a)
        m.exec(self.stack.currentWidget().viewport().mapToGlobal(pos))

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
