"""1.8.4.0 round 2 (R5): a true continuation does not repeat the whitespace already shown.

The continuation request carries the shown text with its trailing whitespace stripped, so a model
that continues opens with that whitespace again: "fox " then " jumps" showed "fox  jumps", and in a
code block a newline and four spaces then a newline and "    return x" doubled the line, on the screen and in the
answer kept in the history. Found by ``tools/race_chaos.py`` (seed 32). Only whitespace identical to
what was shown is consumed. Same table as Hermes ``tests/test_1_8_4_0_stitch_boundary.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import kame_stitch as stitch  # noqa: E402

TABLE = [
    ("the quick brown fox ", " jumps", "jumps"),
    ("def f():\n    ", "\n    return x", "return x"),
    ("def f():\n    ", "    return x", "    return x"),
    ("First paragraph.\n\n", "\n\nSecond.", "Second."),
    ("First paragraph.\n", "\n\nSecond.", "\nSecond."),
    ("the quick brown fox ", "jumps", "jumps"),
    ("the quick brown fox ", "\n    next", "\n    next"),
    ("The", " cat sat", " cat sat"),
]


def _run(seen, fresh):
    stitcher = stitch.Stitcher(seen)
    out = stitcher.feed(fresh) + stitcher.flush()
    return out, stitcher.mode


@pytest.mark.parametrize("seen,fresh,expected", TABLE)
def test_boundary_table(seen, fresh, expected):
    out, mode = _run(seen, fresh)
    assert mode == "new"
    assert out == expected


def test_the_boundary_across_several_deltas():
    stitcher = stitch.Stitcher("def f():\n    ")
    shown = "".join(stitcher.feed(c) for c in ["\n  ", "  ret", "urn x"]) + stitcher.flush()
    assert shown == "return x"


def test_a_long_continuation_keeps_its_own_text():
    seen = "x" * 30 + " "
    fresh = " " + "y" * 500
    stitcher = stitch.Stitcher(seen)
    assert stitcher.feed(fresh) + stitcher.flush() == "y" * 500
