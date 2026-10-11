"""The preview engine against a small generated clip: playback order and pacing, stills, color bypass."""

import threading
import time

import av
import numpy as np
import pytest

from kangedit.color import Grade
from kangedit.models import Footage
from kangedit.preview import Look, PreviewEngine, Shot

FPS = 25


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """4 s of a moving gradient, 320x180, long GOP (forces decoding from keyframes after a seek)."""
    path = str(tmp_path_factory.mktemp("media") / "clip.mp4")
    with av.open(path, "w") as out:
        st = out.add_stream("mpeg4", rate=FPS)
        st.width, st.height, st.pix_fmt = 320, 180, "yuv420p"
        st.codec_context.gop_size = 50
        x = np.linspace(0, 255, 320, dtype=np.float32)
        for i in range(4 * FPS):
            img = np.empty((180, 320, 3), np.uint8)
            img[..., 0] = (x + i * 3) % 256
            img[..., 1] = 128
            img[..., 2] = 255 - img[..., 0]
            for pkt in st.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                out.mux(pkt)
        for pkt in st.encode():
            out.mux(pkt)
    return Footage(path, 4.0, 320, 180, FPS, False, log_profile="none")


def shots_for(f, grade=None):
    g = grade or Grade()
    return [Shot(0, 0.0, 1.0, 2.5, f.id, g, f.log_profile, f.tonemap, 1.0, 0.0, 0.0, f),
            Shot(1, 1.0, 1.0, 0.3, f.id, g, f.log_profile, f.tonemap, 1.0, 0.0, 0.0, f)]


def play(engine, shots, look, seconds):
    engine.start_playback(shots, look, 0.0, None, FPS)
    deadline = time.perf_counter() + 10
    while not engine.ready_to_start():
        assert time.perf_counter() < deadline, "playback never became ready"
        time.sleep(0.005)
    clock = time.perf_counter()
    engine.set_clock(clock)
    got = []
    while (t := time.perf_counter() - clock) < seconds:
        fr = engine.take_frame(t)
        if fr is not None:
            got.append(fr)
        time.sleep(0.004)
    engine.stop_playback()
    return got


@pytest.fixture
def engine():
    e = PreviewEngine(lambda r: None)
    yield e
    e.close()


LOOK = Look(False, "fill", 160, 90, 90)


def test_playback_delivers_frames_in_order(clip, engine):
    frames = play(engine, shots_for(clip), LOOK, 2.0)
    times = [f.t for f in frames]
    assert times == sorted(times) and len(set(times)) == len(times)
    assert len(frames) >= 0.5 * 2.0 * FPS, f"only {len(frames)} frames in 2 s"
    assert all(f.image.shape == (90, 160, 4) for f in frames)


def test_still_frame(clip):
    got, done = [], threading.Event()
    e = PreviewEngine(lambda r: (got.append(r), done.set()))
    try:
        shots = shots_for(clip)
        e.request_still(shots[1], 0.3 + 0.5, 1.5, LOOK, shots)
        assert done.wait(10)
        assert got[0].image is not None and got[0].image.shape == (90, 160, 4)
    finally:
        e.close()


def test_color_bypass_skips_the_grade(clip, engine):
    bright = Grade(exposure=2.0)
    graded = play(engine, shots_for(clip, bright), LOOK, 0.6)
    raw = play(engine, shots_for(clip, bright), Look(False, "fill", 160, 90, 90, color=False), 0.6)
    assert graded and raw
    assert graded[0].image[..., :3].mean() > raw[0].image[..., :3].mean() + 20
    assert "COLOR OFF" in raw[0].info
