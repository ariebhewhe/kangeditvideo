"""Background music: link checks, NCS title matching, credit, and laying a track under the edit."""

import shutil

import pytest

from kangedit import music
from kangedit.ffmpeg import ffmpeg_bin, run
from kangedit.models import TimelineClip

from .test_edit_features import two_files


def test_youtube_links():
    for ok in ("https://www.youtube.com/watch?v=abc", "youtu.be/abc", "https://music.youtube.com/watch?v=x",
               "https://m.youtube.com/watch?v=x"):
        assert music.is_youtube_link(ok)
    for bad in ("https://evil.com/youtube.com", "https://notyoutube.com/x", "Alan Walker Fade", ""):
        assert not music.is_youtube_link(bad)


def test_ncs_titles():
    t = music.Track("Rival x Cadmium", "Seasons (feat. Harley Bird)")
    assert music.matches(t, "Rival x Cadmium - Seasons (feat. Harley Bird) [NCS Release]")
    assert not music.matches(t, "Rival x Cadmium - Seasons (ft Harley Bird) [Futuristik & Whogaux Remix]")
    assert music.matches(music.Track("Alex Skrindo", "Jumbo"), "Alex Skrindo - Jumbo | House | NCS")
    assert not music.matches(music.Track("Jim Yosef", "Link"), "Jim Yosef - Linkage [NCS Release]")
    assert music.clean_title("Lensko - Cetus | House | NCS - Copyright Free Music") == "Lensko - Cetus"
    assert music.clean_title("Disfigure - Blank [NCS Release]") == "Disfigure - Blank"


def test_every_mood_has_suggestions():
    assert {"Vlog", "Cinematic", "Aftermovie / party"} <= set(music.SUGGESTIONS)
    assert all(len(v) >= 5 for v in music.SUGGESTIONS.values())


def test_credit():
    c = music.credit("Disfigure - Blank", "https://www.youtube.com/watch?v=p7ZsBPK656s")
    assert "Disfigure - Blank" in c and "NoCopyrightSounds" in c and "p7ZsBPK656s" in c


@pytest.mark.skipif(not ffmpeg_bin(), reason="needs FFmpeg")
def test_music_goes_under_the_whole_edit(qapp, tmp_path):
    from kangedit.ui.main_window import MainWindow
    path = str(tmp_path / "song.mp3")
    run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=30", "-y", path])
    w = MainWindow()
    try:
        footage, _ = two_files()
        fid = next(iter(footage))
        w.project.footage = footage
        w.project.clips = [TimelineClip(fid, 0.0, 4.0), TimelineClip(fid, 10.0, 13.0)]
        w.add_music(path, "Disfigure - Blank", "https://youtu.be/p7ZsBPK656s")
        x = w.project.sounds[-1]
        assert x.start == 0.0 and x.duration == pytest.approx(7.0) and x.volume == w.MUSIC_VOLUME
        assert "NoCopyrightSounds" in qapp.clipboard().text()
        w.undo()
        assert not w.project.sounds
    finally:
        w.project.dirty = False
        w.close()
        shutil.rmtree(tmp_path, ignore_errors=True)
