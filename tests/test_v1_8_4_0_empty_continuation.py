"""1.8.4.0 round 2 (R4, review F11) - a continuation that returns nothing does not end the continuation.

A cut after "Hello ", then a continuation attempt that returns no answer at all (``None``). The
carousel asks the next key, and that key must continue what the user has read: with the seen text
dropped on the empty return it started the answer again, and "Hello " then "Hello world" went to the
screen's chunks twice (``tools/chaos.py``, seed 78). R4 keeps ``stitch_seen`` on that return.

A virtual clock (kame_vclock.py). No Agent Zero installation, no network.
"""
from __future__ import annotations

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS, clean, make_ctx  # noqa: F401  (fixture used by name)
from kame_vclock import vt  # noqa: F401
import kame_engine as K

CUT = ConnectionError("peer closed connection without sending complete message body")


@pytest.fixture(autouse=True)
def an_assistant_message_type(monkeypatch):
    """A real Agent Zero has one, so a cut answer is continued; the stand-in modules do not."""
    if K.AIMessage is None:
        class _AIMessage:
            def __init__(self, content=""):
                self.content = content
        monkeypatch.setattr(K, "AIMessage", _AIMessage)


def _script(seen_at_attempt, steps):
    """An attempt that does what ``steps`` say, one step per request, and notes what KAME carried."""
    asked = []

    async def attempt(owner, key, ctx):
        asked.append(key)
        seen_at_attempt.append(ctx.get("stitch_seen"))
        kind, payload = steps[min(len(asked), len(steps)) - 1]
        callback = ctx.get("response_callback")
        full = ""
        if kind == "none":
            return None
        for part in payload:
            full += part
            if callback is not None:
                await callback(part, full)
        if kind == "cut":
            raise CUT
        return (full, "")
    return attempt, asked


def test_a_continuation_that_returns_nothing_leaves_the_seen_text_for_the_next_key(vt):
    carried = []
    attempt, asked = _script(carried, [("cut", ["Hello "]), ("none", None), ("text", ["world"])])
    ctx, forwarded = make_ctx(attempt)
    result = vt.run(K._kame_carousel(None, ctx), limit=600)
    assert len(asked) == 3, asked
    # the third request (the one after the empty return) was told the user had read "Hello "
    assert carried[2] == "Hello ", carried
    # and what reached the screen never repeats a word: "Hello " once, then the rest
    assert "".join(forwarded) == "Hello world", forwarded
    assert forwarded.count("Hello ") == 1, forwarded
    assert result[0] == "Hello world"
