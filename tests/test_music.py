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


def test_fit_to_timeline_repeats_short_sounds_with_crossfades():
    from kangedit import sfx
    from kangedit.models import SoundClip
    x = SoundClip("loop.mp3", 3.0, 0.0, 5.0, 0.5, "loop", fade_in=1.0)
    copies = sfx.fit_to_timeline(x, 10.0, 25.0)
    c = sfx.FIT_CROSSFADE
    assert copies[0].id == x.id and copies[0].start == 0.0 and copies[0].fade_in == 1.0
    assert copies[-1].end == pytest.approx(25.0)
    for a, b in zip(copies, copies[1:]):
        assert a.end - b.start == pytest.approx(c)          # neighbours overlap by the crossfade
        assert a.fade_out == b.fade_in == pytest.approx(c)
    assert all(y.volume == 0.5 and y.src_in == 0.0 for y in copies)


def test_fit_to_timeline_cuts_long_sounds_with_a_fade_out():
    from kangedit import sfx
    from kangedit.models import SoundClip
    x = SoundClip("song.mp3", 2.0, 1.0, 4.0)
    [y] = sfx.fit_to_timeline(x, 200.0, 30.0)
    assert (y.start, y.src_in, y.duration) == (0.0, 1.0, pytest.approx(30.0)) and y.fade_out > 0


def test_sound_fades_fit_in_the_sound():
    from kangedit.models import SoundClip
    x = SoundClip("a.mp3", 0.0, 0.0, 2.0, fade_in=3.0, fade_out=1.0)
    fi, fo = x.fades()
    assert fi + fo == pytest.approx(2.0) and fi == pytest.approx(3 * fo)
    assert SoundClip.from_dict(x.to_dict()).fade_in == 3.0
