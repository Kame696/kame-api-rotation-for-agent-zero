"""1.9.0.3 (C1) - every failure row can be opened and read.

Evidence on the Hermes side: a request the provider itself refused ("404 page not found") became a ``surfaced``
event with the reason "other" and no text, and the row could not be opened. Here every ``surfaced`` event carries
the provider's text (through the redaction, never a key) and the error's class name, and a request the provider
refused says so with the reason ``request``. The page opens every bad or warning row; with no text it says so.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS, Refusal, clean, make_ctx, streaming  # noqa: F401
import kame_engine as K
import kame_journal as J

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
KEYLIKE = "AIza" + "B" * 35


class NotFoundError(Refusal):
    """The class name an OpenAI-style client gives a 404."""


def surfaced():
    return [e for e in J.EVENTS.recent() if e["kind"] == "surfaced"]


def run_carousel(attempt, **kw):
    ctx, _ = make_ctx(attempt, **kw)
    return asyncio.run(K._kame_carousel(None, ctx)), ctx


def test_the_event_row_can_carry_the_class_of_the_error():
    row = J.EVENTS.add("surfaced", identity="g:m", reason="request", detail="boom", error_type="NotFoundError")
    assert row["error_type"] == "NotFoundError"
    plain = J.EVENTS.add("rotation", identity="g:m", reason="throttled")
    assert plain.get("error_type", "") == ""


def test_a_request_the_provider_refused_is_one_surfaced_row_with_its_text_and_class(clean):
    J.EVENTS.clear()

    async def attempt(owner, key, ctx):
        raise NotFoundError("404 page not found", 404, {"error": {"message": "404 page not found"}})

    with pytest.raises(NotFoundError):
        run_carousel(attempt)
    rows = surfaced()
    assert len(rows) == 1
    row = rows[0]
    assert row["reason"] == "request"
    assert "page not found" in row["detail"]
    assert row["error_type"] == "NotFoundError"
    assert row["code"] == 404


def test_a_key_shaped_string_in_the_error_never_reaches_the_stored_row(clean):
    J.EVENTS.clear()

    async def attempt(owner, key, ctx):
        raise NotFoundError(f"404 page not found for key {KEYLIKE}", 404, {"error": {"message": f"nope {KEYLIKE}"}})

    with pytest.raises(NotFoundError):
        run_carousel(attempt)
    stored = json.dumps(J.EVENTS.recent(), default=str)
    assert KEYLIKE not in stored
    assert surfaced() and surfaced()[0]["detail"]


def test_stopping_after_the_waiting_bound_still_carries_the_last_error(clean):
    """The circle step and the carousel's own stop raise the last error: their row has its text too."""
    J.EVENTS.clear()
    K._kame_event("surfaced", ID, reason="stopped waiting after 1m (max_total_wait_seconds)",
                  detail="Error code: 503 overloaded", error_type="ServerError")
    rows = surfaced()
    assert rows[0]["detail"] == "Error code: 503 overloaded" and rows[0]["error_type"] == "ServerError"


def _calls(source, head):
    """The text of every ``head(...)`` call, up to its balanced closing parenthesis."""
    out, at = [], 0
    while True:
        at = source.find(head, at)
        if at < 0:
            return out
        depth, i = 0, at + len('_kame_event')
        while i < len(source):
            depth += {"(": 1, ")": -1}.get(source[i], 0)
            if depth == 0:
                break
            i += 1
        out.append(source[at:i + 1])
        at = i


def test_every_surfaced_event_in_the_engine_carries_the_text_and_the_class():
    source = (ROOT / "kame_engine.py").read_text(encoding="utf-8")
    calls = _calls(source, '_kame_event("surfaced"')
    assert len(calls) >= 6
    for call in calls:
        assert "detail=" in call and "error_type=" in call, call


# -- the page ----------------------------------------------------------------------------------------

pytestmark_js = pytest.mark.skipif(NODE is None, reason="node is not installed")


def store_js(body, responses=None):
    from test_v1_8_2_9_page_store import run_store
    return run_store(body, responses or {})


@pytestmark_js
def test_the_reason_words_say_request_and_other_as_hermes_does():
    out = store_js("return [store.reasonWords('request'), store.reasonWords('other')];")
    assert out == ["the provider refused the request itself, not a key - often a model name or address it does not know",
                   "an error API Rotation does not recognise - click to read it"]


@pytestmark_js
def test_a_bad_or_warning_row_always_opens_and_a_good_one_never_does():
    body = """
      const bad = {kind: 'surfaced', reason: 'request', code: 404, error_type: 'NotFoundError', identity: 'g:m', at: 1};
      const good = {kind: 'recovery', reason: '', detail: 'x', at: 1};
      const warn = {kind: 'denied_model', reason: 'denied', at: 1};
      store.inspect(bad);
      const opened = store.inspecting === bad;
      store.closeInspect();
      store.inspect(good);
      return [store.canInspect(bad), store.canInspect(good), store.canInspect(warn), opened, store.inspecting === null];
    """
    assert store_js(body) == [True, False, True, True, True]


@pytestmark_js
def test_a_row_with_no_text_says_so_with_the_error_type_and_status():
    body = """
      return [
        store.inspectorText({kind: 'surfaced', error_type: 'NotFoundError', code: 404}),
        store.inspectorText({kind: 'surfaced'}),
        store.inspectorText({kind: 'surfaced', detail: 'the provider said this'}),
      ];
    """
    out = store_js(body)
    assert out[0] == "The provider sent no text with this error.\nError type: NotFoundError\nHTTP status: 404"
    assert out[1] == "The provider sent no text with this error."
    assert out[2] == "the provider said this"


def test_the_inspector_shows_the_text_the_store_makes():
    page = (ROOT / "webui" / "kame-panel.html").read_text(encoding="utf-8")
    assert "$store.kamePanel.inspectorText($store.kamePanel.inspecting)" in page
    assert "$store.kamePanel.inspecting.detail\"" not in page
