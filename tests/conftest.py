"""Test setup: settings and the LUT/sound libraries go to a throwaway folder, Qt runs offscreen."""

import os
import tempfile

import pytest

# Before anything imports kangedit: never touch the user's real settings or libraries.
_TMP = tempfile.mkdtemp(prefix="kangedit-tests-")
os.environ["KANGEDIT_SETTINGS_DIR"] = os.path.join(_TMP, "settings")
os.environ["KANGEDIT_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def lut_library():
    """The (test) LUT library folder, emptied before and after the test."""
    from kangedit import lut as lutlib
    from kangedit.config import lut_dir

    def clear():
        for p in lut_dir().iterdir():
            p.unlink()
        lutlib.set_official_choices({})

    clear()
    yield lut_dir()
    clear()


def write_cube(path, size=2, fn=lambda r, g, b: (r, g, b)):
    """A size^3 .cube file mapping (r, g, b) -> fn(r, g, b)."""
    lines = [f"LUT_3D_SIZE {size}"]
    for b in range(size):
        for g in range(size):
            for r in range(size):
                lines.append("%.6f %.6f %.6f" % fn(r / (size - 1), g / (size - 1), b / (size - 1)))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)
