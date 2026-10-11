"""Main window features, offscreen: recent projects, footage list views, viewer color modes."""

import os

import pytest

from kangedit.models import Project


@pytest.fixture
def window(qapp):
    from kangedit.ui.main_window import MainWindow
    w = MainWindow()
    w._set_recent([])
    yield w
    w.project.dirty = False
    w.close()


def test_recent_projects_most_recent_first_without_duplicates(window, tmp_path):
    paths = []
    for i in range(12):
        p = str(tmp_path / f"p{i}.kgproj")
        Project().save(p)
        paths.append(p)
        window.load_project_file(p)
    window.load_project_file(paths[5])
    recent = window._recent_projects()
    assert len(recent) == window.MAX_RECENT
    assert recent[0] == os.path.abspath(paths[5])
    assert len({os.path.normcase(p) for p in recent}) == len(recent)
    window._fill_recent_menu()
    assert window.recent_menu.isEnabled()
    assert window.recent_menu.actions()[-1].text() == "Clear recent projects"


def test_saving_adds_to_recent(window, tmp_path):
    window.project.path = str(tmp_path / "saved.kgproj")
    assert window.save_project()
    assert window._recent_projects()[0] == os.path.abspath(window.project.path)


def test_footage_list_view_modes_are_remembered(window, qapp):
    window.bin.set_view_mode("thumbnails")
    assert window.bin.view_mode() == "thumbnails"
    from kangedit.ui.media_bin import MediaBin
    assert MediaBin(window.project).view_mode() == "thumbnails"
    window.bin.set_view_mode("details")


@pytest.mark.parametrize("mode, playing, color", [
    ("on", False, True), ("on", True, True),
    ("playback", False, True), ("playback", True, False),
    ("off", False, False), ("off", True, False),
])
def test_viewer_color_modes(window, mode, playing, color):
    window.viewer.set_adjust_mode(mode)
    window.viewer.playing = playing
    try:
        assert window.viewer._look().color is color
    finally:
        window.viewer.playing = False
    assert window.a_adjust[mode].isChecked()
