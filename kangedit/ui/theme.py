"""Dark editing-suite theme."""

import os
import tempfile

ACCENT = "#4f8cff"
CLIP_COLORS = ["#3b6fb6", "#3f8f6b", "#8a5cb8", "#b5733a", "#3a8fa3", "#a34a6a", "#6f8a3a", "#5a62b8"]


def _arrow_file(name: str, path_d: str) -> str:
    """A small arrow as an SVG file (style sheets can only load images from files)."""
    path = os.path.join(tempfile.gettempdir(), f"kangedit_arrow_{name}.svg")
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
           f'<path d="{path_d}" fill="none" stroke="#c4c7ce" stroke-width="1.6"/></svg>')
    try:
        if not os.path.exists(path):
            with open(path, "w", encoding="ascii") as fh:
                fh.write(svg)
    except OSError:
        return ""
    return path.replace(os.sep, "/")


_ARROW = _arrow_file("down", "M1 3 L5 7 L9 3")
_ARROW_UP = _arrow_file("up", "M1 7 L5 3 L9 7")

STYLE = f"""
QWidget {{ background: #1c1d21; color: #d8dae0; font-size: 12px; }}
QMainWindow::separator {{ background: #111214; width: 4px; height: 4px; }}
QDockWidget::title {{ background: #24262b; padding: 5px 8px; font-weight: 600; }}
QToolBar {{ background: #24262b; border: none; spacing: 4px; padding: 3px; }}
QToolButton {{ padding: 5px 9px; border-radius: 4px; }}
QToolButton:hover {{ background: #33363d; }}
QToolButton:checked {{ background: {ACCENT}; color: white; }}
QPushButton {{ background: #2e3138; border: 1px solid #3a3d45; padding: 5px 12px; border-radius: 4px; }}
QPushButton:hover {{ background: #383c44; }}
QPushButton:pressed {{ background: #24262b; }}
QPushButton:disabled {{ color: #6b6e76; }}
QPushButton#toggle:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: #6a9dff; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
    background: #141518; border: 1px solid #34373e; border-radius: 3px; padding: 3px 5px; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: url("{_ARROW}"); width: 10px; height: 10px; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 16px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
    width: 15px; border: none; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
    width: 15px; border: none; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: #2e3138; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{_ARROW_UP}"); width: 8px; height: 8px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{_ARROW}"); width: 8px; height: 8px; }}
QToolButton#reset {{ padding: 0px; }}
QComboBox QAbstractItemView {{ background: #202227; selection-background-color: {ACCENT}; }}
QTreeWidget, QTableWidget, QListWidget {{ background: #17181b; alternate-background-color: #1b1c20;
    border: 1px solid #2a2c31; gridline-color: #2a2c31; }}
QHeaderView::section {{ background: #24262b; border: none; border-right: 1px solid #1c1d21; padding: 4px; }}
QTreeWidget::item:selected, QTableWidget::item:selected, QListWidget::item:selected {{ background: #2f4d80; }}
QSlider::groove:horizontal {{ height: 4px; background: #34373e; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #e6e7ea; width: 12px; margin: -5px 0; border-radius: 6px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QGroupBox {{ border: 1px solid #2c2e34; border-radius: 5px; margin-top: 14px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #9aa0ab; font-weight: 600; }}
QProgressBar {{ background: #141518; border: 1px solid #34373e; border-radius: 3px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
QScrollBar:horizontal {{ background: #17181b; height: 12px; }}
QScrollBar::handle:horizontal {{ background: #3a3d45; border-radius: 5px; min-width: 30px; margin: 2px; }}
QScrollBar:vertical {{ background: #17181b; width: 12px; }}
QScrollBar::handle:vertical {{ background: #3a3d45; border-radius: 5px; min-height: 30px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QTabWidget::pane {{ border: 1px solid #2a2c31; }}
QTabBar::tab {{ background: #24262b; padding: 6px 12px; }}
QTabBar::tab:selected {{ background: #1c1d21; border-bottom: 2px solid {ACCENT}; }}
QMenu {{ background: #24262b; border: 1px solid #34373e; }}
QMenu::item:selected {{ background: #2f4d80; }}
QMenuBar::item:selected {{ background: #33363d; }}
QStatusBar {{ background: #24262b; }}
QToolTip {{ background: #2e3138; color: #e6e7ea; border: 1px solid #45484f; }}
QRadioButton::indicator, QCheckBox::indicator {{ width: 14px; height: 14px; }}
"""


def fmt_time(t: float, fps: float = 30.0, frames: bool = True) -> str:
    t = max(0.0, t)
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    base = f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    if frames:
        return f"{base}:{int((t - int(t)) * fps):02d}"
    return base


def compact_combos(root, chars: int = 10) -> None:
    """Let the drop-downs under `root` shrink to a narrow panel (long names are elided); their open lists
    still grow to show every name in full."""
    from PySide6.QtWidgets import QComboBox

    def fit_popup(cb):
        view = cb.view()
        view.setMinimumWidth(min(600, view.sizeHintForColumn(0) + 30))

    for cb in root.findChildren(QComboBox):
        cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        cb.setMinimumContentsLength(chars)
        m = cb.model()
        for sig in (m.rowsInserted, m.rowsRemoved, m.modelReset):
            sig.connect(lambda *_, c=cb: fit_popup(c))
        fit_popup(cb)
