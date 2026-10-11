"""Animated titles: animation states, templates, the ASS export and persistence."""

import re

import pytest

from kangedit.models import Project, TextClip
from kangedit.titles import (ANIM_IN, ANIM_OUT, TEXT_PRESETS, anim_lengths, make_text, text_state, visible_lines,
                             write_titles_ass)


def clip(**kw) -> TextClip:
    return TextClip(**{"text": "Hello", "start": 2.0, "duration": 4.0, "anim_dur": 0.5, **kw})


def test_hidden_outside_its_time():
    tc = clip()
    assert text_state(tc, 1.99) is None and text_state(tc, 6.0) is None
    assert text_state(clip(text="  "), 3.0) is None


@pytest.mark.parametrize("kind", [k for k in ANIM_IN if k != "none"])
def test_every_in_animation_starts_hidden_and_settles(kind):
    tc = clip(anim_in=kind, anim_out="none")
    first, settled = text_state(tc, 2.0), text_state(tc, 2.6)
    hidden = first.alpha < 0.05 or first.scale < 0.05 or first.chars == 0 or abs(first.dx) + abs(first.dy) > 0.1
    assert hidden, kind
    assert (settled.alpha, settled.dx, settled.dy, settled.scale, settled.chars) == (1.0, 0.0, 0.0, 1.0, None)


@pytest.mark.parametrize("kind", [k for k in ANIM_OUT if k != "none"])
def test_every_out_animation_ends_hidden(kind):
    st = text_state(clip(anim_in="none", anim_out=kind), 5.999)
    assert st.alpha < 0.05 or st.scale < 0.05 or st.chars == 0 or abs(st.dx) + abs(st.dy) > 0.1


def test_slides_move_the_right_way():
    st = text_state(clip(anim_in="slide_up"), 2.1)          # arrives moving up: starts below
    assert st.dy > 0
    st = text_state(clip(anim_out="slide_left"), 5.9)       # leaves to the left
    assert st.dx < 0


def test_typewriter_types_across_lines():
    tc = clip(text="ab\ncd", anim_in="typewriter", anim_out="none", anim_dur=1.0)
    assert text_state(tc, 2.0).chars == 0
    assert visible_lines(tc, text_state(tc, 2.76).chars) == [("ab", ""), ("c", "d")]


def test_animations_fit_short_clips():
    assert anim_lengths(clip(duration=0.6, anim_dur=2.0)) == (0.3, 0.3)
    assert anim_lengths(clip(anim_in="none")) == (0.0, 0.5)


def test_templates_make_valid_titles():
    for key in TEXT_PRESETS:
        tc = make_text(key, 1.0, frame_duration=30.0)
        assert tc.preset == key and tc.text and tc.font
        assert tc.anim_in in ANIM_IN and tc.anim_out in ANIM_OUT
    late = make_text("title", 29.5, frame_duration=30.0)     # kept inside the timeline
    assert late.end <= 30.0 + 1e-9


def test_ass_events_follow_the_animation(tmp_path):
    tc = clip(anim_in="slide_right", anim_out="fade")
    n = write_titles_ass(str(tmp_path / "t.ass"), [tc], 1920, 1080, 25.0)
    text = (tmp_path / "t.ass").read_text(encoding="utf-8")
    events = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    assert n == len(events)
    # 0.5 s in + 0.5 s out at 25 fps, one hold event; the very first frame is invisible and skipped.
    assert 20 <= len(events) <= 26
    xs = [float(m.group(1)) for m in (re.search(r"\\pos\(([\d.]+),", e) for e in events)]
    assert xs[0] < xs[5] < xs[12] == 960.0                  # slides right into the centre
    times = [(e.split(",")[1], e.split(",")[2]) for e in events]
    assert all(a[1] == b[0] for a, b in zip(times, times[1:]))   # contiguous, no flicker


def test_texts_are_saved_and_undone(tmp_path):
    p = Project()
    p.texts.append(make_text("lower_third", 3.0))
    path = str(tmp_path / "p.kgproj")
    p.save(path)
    q = Project.load(path)
    assert [t.to_dict() for t in q.texts] == [t.to_dict() for t in p.texts]
    q.checkpoint()
    q.texts[0].text = "changed"
    q.undo()
    assert q.texts[0].text == p.texts[0].text
