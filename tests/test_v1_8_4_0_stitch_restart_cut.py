"""1.8.4.0 round 2 (R2): a restart cut before it got past the shown text adds nothing.

An answer was cut after "the quick brown ", the continuation began the answer again and was cut a
word in ("the "), and the stitcher let that word out as new text: the user read "the quick brown
the". The fresh text was shorter than the tail overlap (``MIN_OVERLAP``) and the restart test
(``RESTART_HEAD``) can ask with. Every character of a continuation that is a prefix of the shown
text is text the user has already read. Same table as Hermes
``tests/test_1_8_4_0_stitch_restart_cut.py`` (found there by ``tools/race_chaos.py``, seed 0).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import kame_stitch as stitch  # noqa: E402

TABLE = [
    ("the quick brown ", "the ", ""),
    ("the quick brown ", "the quick", ""),
    ("the quick brown ", "The Quick", ""),
    ("Hello", "Hel", ""),
    ("Hello", "Hello", ""),
    ("the quick brown ", "fox jumps", "fox jumps"),
    ("the quick brown ", "the fox", "the fox"),
]


def _run(seen, fresh, cut=True):
    stitcher = stitch.Stitcher(seen)
    out = stitcher.feed(fresh) + stitcher.flush(cut=cut)
    return out, stitcher.mode


@pytest.mark.parametrize("seen,fresh,expected", TABLE)
def test_restart_cut_table(seen, fresh, expected):
    assert _run(seen, fresh)[0] == expected          # the continuation was cut: R2 reads it as a restart


# Round 2 review F7 (same table as Hermes): R2 never swallows a true short continuation that ENDED.
SHORT_ENDED = [
    ("Yes. Studies show X, so the short answer:", " yes", " yes"),
    ("the quick brown ", "the", "the"),
    ("Hello", "Hel", "Hel"),
]


@pytest.mark.parametrize("seen,fresh,expected", SHORT_ENDED)
def test_a_short_continuation_that_ended_normally_is_shown(seen, fresh, expected):
    assert _run(seen, fresh, cut=False)[0] == expected


def test_the_same_short_continuation_ended_by_a_cut_is_still_swallowed():
    seen = "Yes. Studies show X, so the short answer:"
    assert _run(seen, " yes", cut=True)[0] == ""


def test_a_long_fresh_text_that_repeats_the_start_is_a_restart_even_without_a_cut():
    assert _run("the quick brown ", "the quick", cut=False) == ("", "restart")


def test_a_cut_restart_is_named_restart():
    assert _run("the quick brown ", "the ")[1] == "restart"
    assert _run("the quick brown ", "fox jumps")[1] == "new"


def test_the_cut_restart_across_several_deltas():
    stitcher = stitch.Stitcher("the quick brown ")
    shown = "".join(stitcher.feed(chunk) for chunk in ["th", "e qu", "ick"]) + stitcher.flush(cut=True)
    assert shown == ""


def test_a_long_restart_is_unchanged():
    seen = "The quick brown fox jumps over the lazy dog while the cat sleeps"
    out, mode = _run(seen, seen + " and then it rains")
    assert mode == "continuation"
    assert out == " and then it rains"


# --- F7 where the engine calls flush: no answer at all is a cut, a returned answer ended normally --------------

import asyncio  # noqa: E402

import test_v1_8_2_7_speed_fallback  # noqa: E402,F401  (stands in for Agent Zero's modules)
import kame_engine as K  # noqa: E402


def _finish(seen, fresh, result):
    shown = []

    async def callback(delta, full):
        shown.append(delta)

    stitcher = stitch.Stitcher(seen)
    ctx = {"stitcher": stitcher, "stitch_seen": seen, "a0_response_callback": callback, "identity": "x:y"}
    early = stitcher.feed(fresh)
    out = asyncio.run(K._kame_finish_continuation(ctx, result))
    return early + "".join(shown), out, ctx


def test_a_short_continuation_that_ended_with_an_answer_is_shown():
    seen = "Yes. Studies show X, so the short answer:"
    text, out, _ctx = _finish(seen, " yes", (seen + " yes", ""))
    assert text == " yes"
    assert out[0] == seen + " yes"


def test_a_short_beginning_of_a_restart_with_no_answer_at_all_shows_nothing_and_keeps_the_seen_text():
    text, out, ctx = _finish("the quick brown ", "the ", None)
    assert text == "" and out is None
    assert ctx["stitch_seen"] == "the quick brown "           # the next key continues what the user read
