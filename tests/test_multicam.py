"""Multicam: syncing angles by sound, laying them out, and keeping them in step with the edit."""

import numpy as np
import pytest

from kangedit import multicam, reframe
from kangedit.models import Footage, OverlayClip, Project, TimelineClip
from kangedit.sync import RATE, SyncResult, sync_audio

rng = np.random.default_rng(7)


def _speech(seconds: float) -> np.ndarray:
    """Noise in syllable-like bursts with pauses: enough like speech for onsets and fine matching."""
    n = int(seconds * RATE)
    x = np.convolve(rng.standard_normal(n), np.hanning(9) / 4, "same").astype(np.float32)
    env = np.zeros(n, np.float32)
    t = 0
    while t < n:
        k = int(rng.uniform(0.08, 0.35) * RATE)
        env[t:t + k] = rng.uniform(0.2, 1.0) * np.hanning(min(k, n - t))
        t += k + int(rng.exponential(0.15) * RATE)
    return x * env * 0.3


def _angle(a: np.ndarray, offset: float, seconds: float, drift: float = 0.0) -> np.ndarray:
    """What a second camera starting `offset` s into recording a would hear (another mic, a little noise)."""
    ta = offset + np.arange(int(seconds * RATE)) / RATE * (1 + drift)
    b = np.interp(ta * RATE, np.arange(len(a)), a, left=0, right=0).astype(np.float32)
    return np.convolve(b, [0.6, 0.3, 0.1], "same") * 2.0 + rng.standard_normal(len(b)).astype(np.float32) * 0.01


A = _speech(240)


@pytest.mark.parametrize("offset, drift", [(13.3721, 0.0), (61.25, 40e-6), (-9.5, -30e-6)])
def test_sync_is_sub_millisecond_and_finds_drift(offset, drift):
    b = _angle(A, offset, 150, drift)
    r = sync_audio(A, b)
    assert r.reliable and r.confidence > 0.8
    for tb in (5.0, 75.0, 145.0):
        assert r.a_time(tb) == pytest.approx(offset + tb * (1 + drift), abs=0.001)    # within 1 ms
        assert r.b_time(r.a_time(tb)) == pytest.approx(tb, abs=1e-6)
    assert r.drift == pytest.approx(drift, abs=5e-6)


def test_unrelated_sound_does_not_sync():
    assert not sync_audio(A, _speech(100)).reliable


# ----------------------------------------------------------------------------- layout and timing
def _project(aspect="16:9 (Landscape)"):
    main = Footage("main.mp4", 120.0, 1920, 1080, 30.0, True)
    cam2 = Footage("cam2.mp4", 60.0, 1080, 1920, 30.0, True, face=[0.3, 0.35, 0.2])
    p = Project()
    p.settings.aspect = aspect
    p.footage = {main.id: main, cam2.id: cam2}
    p.clips = [TimelineClip(main.id, 30.0, 33.0), TimelineClip(main.id, 50.0, 52.0),
               TimelineClip(main.id, 5.0, 8.0)]                        # angle 2 starts at 20 s: not here
    angles = {main.id: 1, cam2.id: 2}
    syncs = {cam2.id: (main.id, SyncResult(20.0, 0.0, 1.0, 60.0))}    # cam2's 0 s = main's 20 s
    return p, main, cam2, angles, syncs


def _store(p, angles, syncs, template):
    p.overlays = multicam.build(p, p.clips, angles, syncs, template)
    p.settings.multicam = {"angles": angles, "template": template, "audio": "main",
                           "syncs": {f: [ref, r.offset, r.drift, r.confidence, r.overlap] for f, (ref, r) in syncs.items()},
                           "clips": [c.id for c in p.clips]}


def test_angles_cut_at_the_same_moments():
    p, main, cam2, angles, syncs = _project()
    _store(p, angles, syncs, "pip")
    starts = p.clip_starts()
    ov = sorted(p.overlays, key=lambda o: o.start)
    assert [o.angle for o in ov] == [2, 2]                     # the third clip is before cam2 started
    for o, c, t0 in zip(ov, p.clips, starts):
        assert o.link == c.id and o.start == pytest.approx(t0) and o.src_in == pytest.approx(c.src_in - 20.0)
        assert o.duration == pytest.approx(c.duration) and o.volume == 0.0 and o.box_w == 0.0
    assert all(c.volume == 1.0 for c in p.clips)


def test_stacked_layout_boxes_every_angle_and_crops_to_the_face():
    p, main, cam2, angles, syncs = _project("9:16 (Vertical)")
    _store(p, angles, syncs, "stack")
    mains = [o for o in p.overlays if o.angle == 1]
    others = [o for o in p.overlays if o.angle == 2]
    assert len(mains) == 3 and len(others) == 2
    assert (mains[0].y, mains[0].box_h, others[0].y, others[0].box_h) == (0.25, 0.5, 0.75, 0.5)
    assert others[0].track > mains[0].track
    assert others[0].pan_y != 0.0                                 # moved toward the face


def test_audio_from_another_angle():
    p, main, cam2, angles, syncs = _project()
    p.overlays = multicam.build(p, p.clips, angles, syncs, "pip", "angle:2")
    assert all(c.volume == 0.0 for c in p.clips) and all(o.volume == 1.0 for o in p.overlays)


def test_angles_follow_trims_splits_and_deletes():
    p, main, cam2, angles, syncs = _project()
    _store(p, angles, syncs, "pip")
    first = next(o for o in p.overlays if o.link == p.clips[0].id)
    first.x, first.scale = 0.2, 0.5                              # placed by hand
    p.clips[0].src_in += 1.0                                     # trimmed
    assert multicam.follow(p)
    o = next(o for o in p.overlays if o.link == p.clips[0].id)
    assert o.src_in == pytest.approx(p.clips[0].src_in - 20.0) and (o.x, o.scale) == (0.2, 0.5)
    assert p.overlays[[x.id for x in p.overlays].index(o.id)] is o and o.id == first.id
    # a deleted angle clip stays deleted
    p.overlays = [x for x in p.overlays if x.link != p.clips[1].id]
    multicam.follow(p)
    assert not any(x.link == p.clips[1].id for x in p.overlays)
    # a new main clip (e.g. after a split) gets its angle, placed like the others
    p.clips.insert(1, TimelineClip(main.id, 40.0, 41.0))
    multicam.follow(p)
    new = next(x for x in p.overlays if x.link == p.clips[1].id)
    assert new.src_in == pytest.approx(20.0) and (new.x, new.scale) == (0.2, 0.5)
    # a removed main clip takes its angle with it
    gone = p.clips.pop(0).id
    multicam.follow(p)
    assert not any(x.link == gone for x in p.overlays)


def test_relayout_adds_and_removes_main_boxes():
    p, main, cam2, angles, syncs = _project()
    _store(p, angles, syncs, "pip")
    assert multicam.relayout(p, "side") == 3 + 2
    assert sum(o.angle == 1 for o in p.overlays) == 3 and p.settings.multicam["template"] == "side"
    multicam.relayout(p, "talking")                               # landscape: main full frame again
    assert not any(o.angle == 1 for o in p.overlays)
    box = next(o for o in p.overlays if o.angle == 2)
    w, h = reframe.overlay_size(1080, 1920, 1920, 1080, box.scale, box.box_w, box.box_h)
    assert abs(w - h) <= 2 and box.x + w / 1920 / 2 == pytest.approx(1 - multicam.MARGIN, abs=0.01)


def test_coverage_blockers_leave_only_shared_moments():
    p, main, cam2, angles, syncs = _project()
    blocks = multicam.coverage_blockers(p.footage, angles, syncs)
    assert [(b.src_in, b.src_out) for b in blocks] == [(0.0, 20.0), (80.0, 120.0)]
    assert multicam.coverage_blockers(p.footage, angles, {}) == []       # nothing synced: nothing blocked


def test_box_crop_geometry():
    pl = reframe.overlay_place(1920, 1080, 1080, 1920, 1.0, 1.0, 0.5)    # a 16:9 picture in half a 9:16 frame
    assert (pl.crop_w, pl.crop_h) == (1080, 960) and pl.scaled_h == 960
    x, _ = reframe.pan_for_point(0.2, 0.5, 1920, 1080, 1080, 1920, 1.0, 0.5)
    assert x < 0


def test_multicam_settings_saved(tmp_path):
    p, main, cam2, angles, syncs = _project()
    _store(p, angles, syncs, "stack")
    path = str(tmp_path / "m.kgproj")
    p.save(path)
    q = Project.load(path)
    assert q.settings.multicam["template"] == "stack" and q.footage[cam2.id].face == [0.3, 0.35, 0.2]
    assert {(o.angle, o.link) for o in q.overlays} == {(o.angle, o.link) for o in p.overlays}
    assert multicam.stored_syncs(q)[cam2.id][1].offset == 20.0
    assert not multicam.follow(q)                                     # already in step


def test_dialog_multicam_choices(qapp):
    from kangedit.autoedit import AutoEditSettings
    from kangedit.ui.dialogs import AutoEditDialog
    p, main, cam2, angles, syncs = _project()
    d = AutoEditDialog(p, [], AutoEditSettings())
    d.chk_multi.setChecked(True)
    d.angle_rows[cam2.id].setCurrentIndex(1)
    mc = d.multicam()
    assert mc["angles"] == {main.id: 1, cam2.id: 2} and d.footage_ids() == [main.id]
    assert d.cb_audio.findData("angle:2") >= 0 and not d.lbl_angles.text()
    d.angle_rows[cam2.id].setCurrentIndex(2)                      # "Angle 3" with no angle 2
    assert d.lbl_angles.text() and isinstance(OverlayClip("x", 0).angle, int)
