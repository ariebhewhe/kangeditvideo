"""Fade toggles, font list, other moment, undo / redo, new auto edits, silence, sharpness / halation, full screen."""

import numpy as np
import pytest

from kangedit.autoedit import AutoEditSettings, score_samples, select
from kangedit.color import Grade, apply_spatial, ffmpeg_halation, ffmpeg_sharpen, has_spatial, needs_color
from kangedit.models import Footage, TimelineClip

from .test_edit_features import fake_analysis, two_files


@pytest.fixture
def window(qapp):
    from kangedit.ui.main_window import MainWindow
    w = MainWindow()
    w._set_recent([])
    yield w
    w.project.dirty = False
    w.close()


def _project_with_clip(w, start=8.0, dur=2.0):
    footage, analyses = two_files()
    fid = next(iter(footage))
    w.project.footage = footage
    w.analyses.update(analyses)
    clip = TimelineClip(fid, start, start + dur)
    w.project.clips = [clip]
    w.tl.selected = clip.id
    return clip


def test_fade_toggles_set_the_length(window):
    tp = window.timeline
    tp.btn_fade_in.setChecked(True)
    s = window.project.settings
    assert s.fade_in == 1.0 and s.fade_out == 0.0
    tp.fade_len_group.button(2).click()          # 2 s
    assert s.fade_in == 2.0
    tp.btn_fade_out.setChecked(True)
    tp.btn_fade_in.setChecked(False)
    assert (s.fade_in, s.fade_out) == (0.0, 2.0)
    tp.sync_fades()
    assert not tp.btn_fade_in.isChecked() and tp.btn_fade_out.isChecked()


def test_font_list_is_a_dropdown_that_keeps_a_missing_font(qapp):
    from kangedit.ui.dialogs import font_combo
    cb = font_combo("No Such Font 123")
    assert not cb.isEditable()
    assert cb.currentText() == "No Such Font 123"


def test_other_moment_gives_a_new_nearby_moment_each_time(window):
    clip = _project_with_clip(window, start=12.0)
    home = window._slip_window(clip)
    seen = {round(clip.src_in, 2)}
    for k in range(4):
        before = clip.src_in
        window.clip_action("alt_moment", clip.id)
        assert abs(clip.src_in - before) >= 0.2           # never the moment it already shows
        if k < 2:
            assert round(clip.src_in, 2) not in seen      # new moments until the nearby ones run out
        seen.add(round(clip.src_in, 2))
        assert 12.0 - home - 1e-6 <= clip.src_in <= 14.0 + home


def test_other_moment_length_follows_the_moment(window):
    window.last_auto.clip_min, window.last_auto.clip_max = 1.0, 4.0
    clip = _project_with_clip(window, start=12.0)
    for _ in range(4):
        window.clip_action("alt_moment", clip.id)
        assert 1.0 - 1e-6 <= clip.duration <= 4.0 + 1e-6
        assert window.project.duration == pytest.approx(clip.duration)


def test_other_moment_length_changes_on_every_press(window):
    window.last_auto.clip_min, window.last_auto.clip_max = 1.0, 6.0
    clip = _project_with_clip(window, start=12.0)
    lengths = set()
    for _ in range(5):
        window.clip_action("alt_moment", clip.id)
        lengths.add(round(clip.duration, 2))
    assert len(lengths) >= 3          # each moment keeps its own length, not the first one's


def test_other_moment_without_selection_explains(window):
    window.tl.selected = ""
    window.clip_action("alt_moment", "")
    assert "Select a clip" in window.lbl_status.text()


def test_undo_redo_buttons(window):
    clip = _project_with_clip(window, start=12.0)
    tp = window.timeline
    window._update_title()
    assert not tp.btn_undo.isEnabled() and not tp.btn_redo.isEnabled()
    before = clip.src_in
    window.clip_action("alt_moment", clip.id)
    assert tp.btn_undo.isEnabled() and not tp.btn_redo.isEnabled()
    tp.btn_undo.click()
    assert window.project.clips[0].src_in == before
    assert tp.btn_redo.isEnabled()
    tp.btn_redo.click()
    assert window.project.clips[0].src_in != before


def test_new_auto_edit_prefers_other_moments():
    footage, analyses = two_files()
    s = AutoEditSettings(target_duration=6.0, clip_min=2.0, clip_max=2.0)
    first = select(footage, analyses, s)
    again = select(footage, analyses, s, avoid=first, seed=1)
    overlap = sum(1 for a in again for b in first
                  if a.footage_id == b.footage_id and a.src_in < b.src_out and b.src_in < a.src_out)
    assert overlap < len(first)


def test_silent_moments_score_lower():
    f = Footage("a.mp4", 30.0, 1920, 1080, 30.0, True)
    a = fake_analysis(30.0, 25.0, 1)
    a.audio[:] = -30.0
    a.audio[40:60] = -70.0
    sc = score_samples({f.id: a}, {f.id: f}, "balanced")[f.id]
    assert sc[50] < sc[80] - 0.3


def test_sharpness_and_halation_stay_out_of_the_lut():
    g = Grade(sharpness=40, halation=60)
    assert g.key() == Grade().key() and not needs_color(g, "none") and has_spatial(g)
    img = np.zeros((90, 160, 3), np.uint8)
    img[40:50, 70:90] = 255
    out = apply_spatial(img, Grade(halation=100))
    assert out[35, 80, 0] > out[35, 80, 2] > 0          # a warm glow just outside the highlight
    assert ffmpeg_halation(Grade(), 1080, "a", "b") == [] and ffmpeg_sharpen(Grade(), 1080) == ""
    assert "unsharp=5:5:-1.000" in ffmpeg_sharpen(Grade(sharpness=-100), 1080)


def test_full_screen_and_back(window):
    v = window.viewer
    parent = v.parentWidget()
    v.toggle_fullscreen()
    assert v.is_fullscreen() and v.isWindow()
    v.toggle_fullscreen()
    assert not v.is_fullscreen() and v.parentWidget() is parent and parent.indexOf(v) == 0


def test_platform_overlay_is_remembered(window):
    window._set_platform_overlay("tiktok")
    assert window.viewer.canvas.platform_overlay == "tiktok" and window.a_platform["tiktok"].isChecked()
    window._set_platform_overlay("")
    assert window.viewer.canvas.platform_overlay == ""


def test_inspector_edits_every_selected_clip(window):
    footage, analyses = two_files()
    window.project.footage = footage
    fa, fb = footage
    clips = [TimelineClip(fa, 1.0, 3.0), TimelineClip(fb, 4.0, 6.0), TimelineClip(fa, 10.0, 12.0)]
    window.project.clips = clips
    window.tl.select_all()
    insp = window.inspector
    assert len(insp.targets) == 3
    insp.params["exposure"].changed.emit(1.5)
    insp.rf_zoom.changed.emit(2.0)
    assert all(c.grade.exposure == 1.5 and c.zoom == 2.0 for c in clips)
    insp.sp_tdur.setValue(1.2)
    assert clips[0].transition_dur != 1.2 and clips[1].transition_dur == clips[2].transition_dur == 1.2
    insp.cb_tone.setCurrentIndex((insp.cb_tone.currentIndex() + 1) % insp.cb_tone.count())
    assert footage[fa].tonemap == footage[fb].tonemap == insp.cb_tone.currentData()


def test_face_framing_centres_the_face():
    from kangedit.reframe import face_framing, place
    zoom, ox, oy = face_framing((0.7, 0.3, 0.1), 1920, 1080, 1080, 1920, "fill", 0.34)
    assert 1.0 < zoom <= 4.0
    p = place(1920, 1080, 1080, 1920, "fill", zoom, ox, oy)
    assert abs((0.7 * p.scaled_w - p.crop_x) / p.crop_w - 0.5) < 0.02     # face in the middle
    assert abs(0.1 * p.scaled_h / p.crop_h - 0.34) < 0.02                  # at the asked size


def test_long_form_hook_is_a_short_teaser():
    from kangedit.autoedit import hook_plan
    footage, analyses = two_files()
    s = AutoEditSettings(video_format="long", target_duration=20.0, clip_min=4.0, clip_max=6.0, order="hook",
                         min_gap=1.0)
    clips = select(footage, analyses, s)
    n, length = hook_plan(s)
    hooks = [c for c in clips if c.label == "hook"]
    assert 2 <= len(hooks) <= n and clips[:len(hooks)] == hooks
    assert all(abs(c.duration - length) < 0.05 for c in hooks)
    rest = clips[len(hooks):]
    assert rest == sorted(rest, key=lambda c: (list(footage).index(c.footage_id), c.src_in))   # story order


def test_new_hook_goes_before_the_opening(window):
    footage, analyses = two_files()
    window.project.footage = footage
    window.analyses.update(analyses)
    window.project.settings.video_format = "long"
    window.last_auto = AutoEditSettings(video_format="long", clip_min=4.0, clip_max=6.0, min_gap=1.0)
    fa = next(iter(footage))
    opening = TimelineClip(fa, 0.5, 4.5, label="opening")
    window.project.clips = [opening, TimelineClip(fa, 10.0, 15.0)]
    window.new_hook(choose=False)
    clips = window.project.clips
    hooks = [c for c in clips if c.label == "hook"]
    assert len(hooks) >= 2 and clips[len(hooks)] is opening
    first = [(c.src_in, c.footage_id) for c in hooks]
    window.new_hook(choose=False)                      # replaces the teaser, keeps the opening
    assert [c.label for c in window.project.clips].count("hook") == len(hooks)
    assert [(c.src_in, c.footage_id) for c in window.project.clips[:len(hooks)]] != first


def test_text_tab_selects_a_title_to_edit(window):
    from kangedit.titles import make_text
    window.project.clips = []
    tc = make_text("title", 0.0)
    window.project.texts = [tc]
    tp = window.text_panel
    tp.text = None
    tp.refresh()
    assert not tp.editor.isEnabled()
    window.show()
    window.right_tabs.setCurrentWidget(tp)
    assert tp.text is tc and tp.editor.isEnabled()
    tp.chk_bold.setChecked(not tc.bold)
    assert tc.bold == tp.chk_bold.isChecked()


def test_rolling_backups(tmp_path):
    from kangedit import autosave
    from kangedit.models import Project
    path = str(tmp_path / ("p" + Project.FILE_EXT))
    p = Project()
    assert autosave.make_backup(path) is None                    # nothing saved yet
    p.save(path)
    first = autosave.make_backup(path, now=1000.0)
    assert first is not None and autosave.backup_source(str(first)) == path
    assert autosave.make_backup(path, now=1100.0) is None        # too soon after the last one
    for k in range(5):
        autosave.make_backup(path, keep=3, now=2000.0 + k * 400)
    assert len(autosave.backups(path)) == 3


def test_autosave_saves_in_place_and_keeps_untitled_work(window, tmp_path):
    from kangedit import autosave
    from kangedit.models import Project
    _project_with_clip(window)
    window.a_autosave.setChecked(True)
    window.project.dirty = True
    window._autosave()                                           # untitled: a recovery copy only
    assert autosave.recovery_path().exists() and window.project.dirty and window.project.path is None
    path = str(tmp_path / ("a" + Project.FILE_EXT))
    window.project.save(path)
    window.project.clips[0].volume = 0.5
    window.project.dirty = True
    window._autosave()
    assert not window.project.dirty and Project.load(path).clips[0].volume == 0.5
    assert len(autosave.backups(path)) == 1                      # the version before the change
    window.set_project(Project())
    assert not autosave.recovery_path().exists()


def test_inspector_sets_dynamic_zoom_and_stabilize(window, monkeypatch):
    clip = _project_with_clip(window)
    measured = []
    monkeypatch.setattr(window, "measure_shake", lambda: measured.append(1))
    ins = window.inspector
    ins.set_clip(clip)
    assert ins.grp_motion.isVisibleTo(ins) and ins.grp_stab.isVisibleTo(ins)
    assert not ins.mo_amount.isEnabled()                     # off: nothing to set
    ins.cb_motion.setCurrentIndex(ins.cb_motion.findData("in_left"))
    assert clip.motion == "in_left" and ins.mo_amount.isEnabled()
    ins.mo_amount.set_value(20.0, emit=True)
    assert clip.motion_amount == pytest.approx(0.2)
    ins.cb_ease.setCurrentIndex(ins.cb_ease.findData("linear"))
    assert clip.motion_ease == "linear"
    ins.chk_stab.setChecked(True)
    assert clip.stabilize == pytest.approx(0.5) and measured
    assert "Measuring" in ins.lbl_stab.text()
    ins.st_strength.set_value(80.0, emit=True)
    assert clip.stabilize == pytest.approx(0.8)
    ins.chk_stab.setChecked(False)
    assert clip.stabilize == 0.0
    window.undo()
    assert window.project.clips[0].stabilize == pytest.approx(0.8)


def test_slip_shows_the_transcript_and_slips_to_a_sentence(window):
    clip = _project_with_clip(window, start=1.0, dur=2.0)
    f = window.project.footage[clip.footage_id]
    f.has_audio = True
    f.transcript = [{"s": 0.5, "e": 0.9, "w": "Hello"}, {"s": 1.0, "e": 1.6, "w": "there."},
                    {"s": 6.0, "e": 6.4, "w": "Look"}, {"s": 6.5, "e": 7.2, "w": "at"}, {"s": 7.3, "e": 7.9, "w": "this!"}]
    panel = window.timeline.slip_text
    window._on_select(clip.id)
    assert panel.isHidden()                                   # Move mode: no transcript
    window.timeline.btn_slip.setChecked(True)
    assert not panel.isHidden() and panel.list.count() == 2
    assert panel.list.item(0).text().endswith("Hello there.") and panel.list.item(0).font().bold()
    assert not panel.list.item(1).font().bold()               # the clip (1-3 s) doesn't show the second
    panel.search.setText("look")
    assert panel.list.item(0).isHidden() and not panel.list.item(1).isHidden()
    panel._clicked(panel.list.item(1))                        # slip to "Look at this!"
    c = window.project.clips[0]
    assert (c.src_in, c.src_out) == (6.0, 8.0) and panel.list.item(1).font().bold()
    window.undo()
    assert window.project.clips[0].src_in == 1.0
    window.timeline.btn_move.setChecked(True)
    assert panel.isHidden()
    # a temporary slip (Alt / Shift drag in Move mode) shows it for the drag
    window._slip_follow(clip.id, 1.5)
    assert not panel.isHidden()
    window.tl.slip_end.emit()
    assert panel.isHidden()


def test_hook_button_offers_the_strongest_moments_as_thumbnails(window, monkeypatch):
    from kangedit.autoedit import HOOK_SUGGESTIONS
    from kangedit.ui.hook_picker import HookPicker
    footage, analyses = two_files()
    for a in analyses.values():                          # a thumbnail a second, each a different grey
        n = int(a.times[-1]) + 1
        a.thumbs = np.stack([np.full((36, 64, 3), 5 * k, np.uint8) for k in range(n)])
        a.thumb_times = np.arange(n, dtype=np.float32)
    window.project.footage = footage
    window.analyses.update(analyses)
    window.project.settings.video_format = "short"
    window.last_auto = AutoEditSettings(clip_min=1.5, clip_max=3.0, min_gap=1.0)
    fa = next(iter(footage))
    window.project.clips = [TimelineClip(fa, 2.0, 5.0), TimelineClip(fa, 12.0, 15.0)]
    seen = {}

    def run(dlg):
        seen["first"] = [(c.footage_id, c.src_in) for _, c in dlg.cards]
        assert len(dlg.cards) == HOOK_SUGGESTIONS
        assert not dlg.cards[0][0].icon().isNull()                  # a picture of the moment
        scores = [c.score for _, c in dlg.cards]
        assert scores == sorted(scores, reverse=True)               # strongest first...
        assert [b.isChecked() for b, _ in dlg.cards] == [True] + [False] * (HOOK_SUGGESTIONS - 1)   # ...ticked
        assert all(abs(c.duration - 3.0) < 0.05 for _, c in dlg.cards)   # as long as the hook it replaces
        dlg.sp_count.setValue(2)                                    # a 2-clip teaser: 2 s moments, top two ticked
        assert [b.isChecked() for b, _ in dlg.cards][:3] == [True, True, False]
        assert all(abs(c.duration - 2.0) < 0.05 for _, c in dlg.cards)
        before = [(c.footage_id, c.src_in) for _, c in dlg.cards]
        dlg.more()                                                  # others, not offered yet; the ticked stay
        now = [(c.footage_id, c.src_in) for _, c in dlg.cards]
        assert now[:2] == before[:2] and [b.isChecked() for b, _ in dlg.cards][:3] == [True, True, False]
        assert not set(before) & set(now[2:])
        dlg.cards[0][0].setChecked(False)
        dlg.cards[2][0].setChecked(True)
        seen["chosen"] = [(c.footage_id, c.src_in) for c in dlg.chosen()]
        return HookPicker.Accepted

    monkeypatch.setattr(HookPicker, "exec", run)
    window.new_hook()
    clips = window.project.clips
    assert [c.label for c in clips[:2]] == ["hook", "hook"] and clips[2].label == ""
    assert [(c.footage_id, c.src_in) for c in clips[:2]] == seen["chosen"]
    assert len(clips) == 3 and clips[2].src_in == 12.0               # the hook replaced the first clip
    assert window.last_auto.hook_clips == 2                         # the next auto edit's hook has 2 clips
    window.undo()
    assert [(c.src_in, c.label) for c in window.project.clips] == [(2.0, ""), (12.0, "")]


def test_hook_plays_the_moments_in_the_order_they_were_ticked(window, monkeypatch):
    from kangedit.ui.hook_picker import HookPicker
    footage, analyses = two_files()
    window.project.footage = footage
    window.analyses.update(analyses)
    window.project.settings.video_format = "short"
    window.last_auto = AutoEditSettings(clip_min=1.5, clip_max=3.0, min_gap=1.0)
    fa = next(iter(footage))
    window.project.clips = [TimelineClip(fa, 2.0, 5.0), TimelineClip(fa, 12.0, 15.0)]
    picked = []

    def moment(c):
        return (c.footage_id, c.src_in)

    def run(dlg):
        dlg.sp_count.setValue(3)
        cards = dlg.cards
        for b, _ in cards:                                # start from nothing ticked
            b.setChecked(False)
        a, b_, c = cards[0], cards[1], cards[2]
        for card in (c, a, b_):                           # tick C, then A, then B
            card[0].setChecked(True)
        assert [moment(x) for x in dlg.chosen()] == [moment(c[1]), moment(a[1]), moment(b_[1])]
        assert c[0].text().startswith("✔ 1") and a[0].text().startswith("✔ 2") and b_[0].text().startswith("✔ 3")
        a[0].setChecked(False)                            # untick A: B moves up
        a[0].setChecked(True)                             # and ticked again, it goes last
        assert [moment(x) for x in dlg.chosen()] == [moment(c[1]), moment(b_[1]), moment(a[1])]
        before = [moment(x) for x in dlg.chosen()]
        dlg.more()                                        # other suggestions keep the ticked ones, in order
        assert [moment(x) for x in dlg.chosen()] == before
        dlg.sp_count.setValue(2)                          # fewer clips: the last ticked goes
        assert [moment(x) for x in dlg.chosen()] == before[:2]
        dlg.sp_count.setValue(3)
        dlg.cards[-1][0].setChecked(True)
        assert len(dlg.chosen()) == 4
        picked[:] = [moment(x) for x in dlg.chosen()]
        return HookPicker.Accepted

    monkeypatch.setattr(HookPicker, "exec", run)
    window.new_hook()
    hooks = [c for c in window.project.clips if c.label == "hook"]
    assert [moment(c) for c in hooks] == picked and window.project.clips[:len(hooks)] == hooks
