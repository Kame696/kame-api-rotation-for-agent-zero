"""1.8.4.0 E2: a very short cut answer is not repeated.

With fewer than ``MIN_OVERLAP`` comparable characters shown, a model that starts the answer
again ("Hello" then "Hello world") was shown twice, because neither the tail overlap nor the
long-head restart test can ask with so little text. The same table is in the Hermes port.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import kame_stitch as stitch  # noqa: E402

TABLE = [
    ("Hello", "Hello world", " world"),
    ("I", "I think so", " think so"),
    ("Hello", "hello world", " world"),
    ("The", " cat sat", " cat sat"),
    ("Hi", "Hi", ""),
    ("Hello", "Goodbye now", "Goodbye now"),
]


def _run(seen, fresh):
    stitcher = stitch.Stitcher(seen)
    out = stitcher.feed(fresh) + stitcher.flush()
    return out, stitcher.mode


@pytest.mark.parametrize("seen,fresh,expected", TABLE)
def test_short_seen_table(seen, fresh, expected):
    assert _run(seen, fresh)[0] == expected


def test_restart_mode_is_named():
    assert _run("Hello", "Hello world")[1] == "restart"
    assert _run("The", " cat sat")[1] == "new"


def test_restart_across_several_deltas():
    stitcher = stitch.Stitcher("Hello")
    shown = ""
    for chunk in ["Hel", "lo wor", "ld, and more"]:
        shown += stitcher.feed(chunk)
    shown += stitcher.flush()
    assert shown == " world, and more"


def test_empty_seen_is_untouched():
    assert _run("", "Hello world")[0] == "Hello world"


def test_long_seen_rules_unchanged():
    out, mode = _run("The quick brown fox jumps over", "jumps over the lazy dog")
    assert mode == "continuation"
    assert out == " the lazy dog"
