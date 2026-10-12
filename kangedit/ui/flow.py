"""A row of widgets that wraps onto more rows when it doesn't fit, so nothing is hidden on a small display."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """Lays its items out left to right and starts a new row when the next one doesn't fit. Items with a
    horizontally expanding size policy share the space left over in their row (a spacer pushes the rest of
    the row to the right)."""

    def __init__(self, parent=None, hspacing: int = 4, vspacing: int = 4):
        super().__init__(parent)
        self._items: list = []
        self._hspacing, self._vspacing = hspacing, vspacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:   # noqa: N802 (Qt name)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):   # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):   # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):   # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:   # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:   # noqa: N802
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:   # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def minimumSize(self) -> QSize:   # noqa: N802
        size = QSize()
        for item in self._visible():
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def sizeHint(self) -> QSize:   # noqa: N802
        """Everything on one row."""
        items = self._visible()
        m = self.contentsMargins()
        width = sum(self._width(i) for i in items) + self._hspacing * max(0, len(items) - 1)
        height = max((i.sizeHint().height() for i in items), default=0)
        return QSize(width + m.left() + m.right(), height + m.top() + m.bottom())

    def _visible(self) -> list:
        return [i for i in self._items if not i.isEmpty()]

    @staticmethod
    def _expanding(item) -> bool:
        """Only a widget whose own policy expands (a spacer, a label meant to fill the row) stretches; a
        group that merely contains a slider keeps its natural width."""
        w = item.widget()
        return w is not None and w.sizePolicy().horizontalPolicy() in (QSizePolicy.Expanding,
                                                                        QSizePolicy.MinimumExpanding)

    def _width(self, item) -> int:
        """An expanding item (a spacer) takes no room of its own; it only gets what a row has left."""
        return item.minimumSize().width() if self._expanding(item) else item.sizeHint().width()

    def _layout(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        rows: list[list] = [[]]
        x = 0
        for item in self._visible():
            w = self._width(item)
            if rows[-1] and x + self._hspacing + w > area.width():
                rows.append([])
                x = 0
            x += (self._hspacing if rows[-1] else 0) + w
            rows[-1].append(item)
        y = area.y()
        for row in (r for r in rows if r):
            height = max(i.sizeHint().height() for i in row)
            if apply:
                used = sum(self._width(i) for i in row) + self._hspacing * (len(row) - 1)
                stretch = [i for i in row if self._expanding(i)]
                extra = max(0, area.width() - used) // len(stretch) if stretch else 0
                x = area.x()
                for item in row:
                    w = self._width(item) + (extra if item in stretch else 0)
                    h = min(height, max(item.sizeHint().height(), item.minimumSize().height()))
                    item.setGeometry(QRect(x, y + (height - h) // 2, w, h))
                    x += w + self._hspacing
            y += height + self._vspacing
        return y - self._vspacing - area.y() + m.top() + m.bottom() if any(rows) else 0


class FlowBar(QWidget):
    """A widget holding a FlowLayout. Its size hint follows its current width (one row when there's room,
    more when there isn't), so it also works where the parent ignores height-for-width, such as a QToolBar
    or a QMainWindow's toolbar area."""

    def __init__(self, hspacing: int = 4, vspacing: int = 4, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self, hspacing, vspacing)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._height = -1

    def add(self, *widgets: QWidget) -> None:
        for w in widgets:
            self.flow.addWidget(w)

    def _rows_height(self) -> int:
        width = self.width() if self.width() > 0 else self.flow.sizeHint().width()
        return self.flow.heightForWidth(width)

    def sizeHint(self) -> QSize:   # noqa: N802
        # Narrow on purpose: a QToolBar would otherwise hide the bar in its » menu when the single-row width
        # doesn't fit. The Expanding policy gives it the full width anyway.
        return QSize(self.flow.minimumSize().width(), self._rows_height())

    def minimumSizeHint(self) -> QSize:   # noqa: N802
        return self.sizeHint()

    def hasHeightForWidth(self) -> bool:   # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:   # noqa: N802
        return self.flow.heightForWidth(width)

    def resizeEvent(self, e) -> None:   # noqa: N802
        super().resizeEvent(e)
        h = self.flow.heightForWidth(self.width())
        if h != self._height:   # one more or one less row: let the parent give us that height
            self._height = h
            self.updateGeometry()
