"""1.8.3.1 (A3 / D3) - what a reply row tells besides the model and the time.

The chip's "Last replies" says, in one sentence per reply, whether the model answered at once, on the Nth
key, through a backup key, for the model the reply started on (and why), after a wait, after a cut, or not at
all. The facts come from the engine's reply rows (`replies_report()`): ``name`` (the model as the user wrote
it), ``attempts``, ``backup``, ``cut``, ``waited_s``, ``for_name`` + ``why`` and the failed row. A virtual
clock makes the seconds exact; no Agent Zero.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS, Refusal, clean, make_ctx, streaming  # noqa: F401
from test_v1_8_2_9_silent_two_models import world  # noqa: F401  (fixture used by name)
from kame_vclock import vt  # noqa: F401
import kame_engine as K


@pytest.fixture(autouse=True)
def calm(clean):
    K._KAME_REPLIES.clear()
    K._KAME_IN_FLIGHT.clear()
    K.set_current_agent(None)
    yield
    K._KAME_REPLIES.clear()
    K._KAME_IN_FLIGHT.clear()


def one(vt_, ctx, limit=120):
    return vt_.run(K._kame_carousel(None, ctx), limit=limit)


def the_row():
    rows = K.replies_report()
    assert rows, "no reply row was written"
    return rows[0]


def test_a_plain_answer_names_the_model_as_the_user_wrote_it_and_carries_no_extras(vt):
    ctx, _ = make_ctx(streaming({}))
    one(vt, ctx)
    row = the_row()
    assert row["name"] == "gemini-3.8-flash" and row["model"] == "gemini/gemini-3.8-flash"
    for extra in ("attempts", "backup", "cut", "waited_s", "failed", "why", "for_name"):
        assert extra not in row, (extra, row)
    assert row["for"] == ""


def test_an_answer_on_the_second_key_says_so(vt):
    ctx, _ = make_ctx(streaming({}))
    base = ctx["attempt"]
    order = []

    async def attempt(owner, key, c):
        order.append(key)
        if len(order) == 1:
            await asyncio.sleep(0.01)
            raise Refusal("overloaded", 503)
        return await base(owner, key, c)

    ctx["attempt"] = attempt
    one(vt, ctx)
    assert the_row()["attempts"] == 2


def test_an_answer_cut_off_and_finished_on_another_key_says_so(vt):
    ctx, _ = make_ctx(streaming({}))
    ctx["resumes"] = 1                         # the stitcher has already continued the answer once
    one(vt, ctx)
    assert the_row()["cut"] is True


def test_a_backup_key_that_answered_first_says_so(vt, monkeypatch):
    from test_v1_8_2_7_speed_fallback import H, _first_then_rest
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (0.05, 0.10))
    first = KEYS[0]
    ctx, _ = make_ctx(streaming({k: (5.0 if k == first else 0.0) for k in KEYS}))
    monkeypatch.setattr(K, "_get_best_key", _first_then_rest(first))      # the slow key is asked first
    one(vt, ctx)
    assert K._KAME_SPEED_STATS["backup_wins"] == 1
    assert the_row()["backup"] is True


def test_a_wait_for_a_key_is_in_the_row_when_it_took_five_seconds_or_more(vt):
    with K._KAME_LOCK:
        K._get_identity_state(ID, KEYS[:1])
        K._KAME_KEY_HEALTH[ID]["keys"][KEYS[0]]["sick_until"] = time.time() + 12
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    one(vt, ctx)
    waited = the_row().get("waited_s")
    assert waited is not None and 11.0 <= waited <= 14.0, waited
    assert K._KAME_REPLY_WAITED_NOTE_S == 5.0


def test_a_short_rest_is_not_called_a_wait(vt):
    with K._KAME_LOCK:
        K._get_identity_state(ID, KEYS[:1])
        K._KAME_KEY_HEALTH[ID]["keys"][KEYS[0]]["sick_until"] = time.time() + 2
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    one(vt, ctx)
    assert "waited_s" not in the_row()


# -- a ladder model answering --------------------------------------------------------------------

def test_a_ladder_model_that_answers_names_the_model_the_reply_started_on_and_why(vt):
    hop = K._KAME_FALLBACK_HOP.set({"origin": "google:gemini/gemini-3.8-flash", "to": "google:gemini/gemini-3.7-flash",
                                    "why": "busy"})
    try:
        ctx, _ = make_ctx(streaming({}))
        ctx["identity"] = "google:gemini/gemini-3.7-flash"
        K._get_identity_state(ctx["identity"], KEYS)
        one(vt, ctx)
    finally:
        K._KAME_FALLBACK_HOP.reset(hop)
    row = the_row()
    assert row["name"] == "gemini-3.7-flash" and row["for_name"] == "gemini-3.8-flash"
    assert row["for"] == "gemini/gemini-3.8-flash" and row["why"] == "busy"


def test_a_hop_that_comes_without_a_reason_has_none_in_the_row(vt):
    hop = K._KAME_FALLBACK_HOP.set({"origin": "google:gemini/gemini-3.8-flash", "to": ID})
    try:
        ctx, _ = make_ctx(streaming({}))
        one(vt, ctx)
    finally:
        K._KAME_FALLBACK_HOP.reset(hop)
    assert "why" not in the_row() and the_row()["for_name"] == "gemini-3.8-flash"


def test_the_hops_the_engine_makes_carry_their_reason(world, vt, monkeypatch):
    seen = []
    real = type(next(iter([K._kame_fallback_model])))        # (keeps the import honest)
    from test_v1_8_2_9_silent_two_models import SilentModel
    original = SilentModel.unified_call

    async def spy(self, **kwargs):
        hop = K._KAME_FALLBACK_HOP.get()
        if hop is not None:
            seen.append(dict(hop))
        return await original(self, **kwargs)

    monkeypatch.setattr(SilentModel, "unified_call", spy)
    K.set_fallback_models("google/gemini-3.7-flash")
    ctx, _ = make_ctx(streaming({k: 30.0 for k in KEYS}))
    result = vt.run(K._kame_carousel(None, ctx), limit=60)
    assert result[0] == "answer from google/gemini-3.7-flash"
    assert seen and all(h.get("why") in ("busy", "slow", "resting") for h in seen), seen


def test_the_reason_is_one_short_word():
    assert [K._kame_short_why(w) for w in ("your provider is busy", "slower than usual", "every key is resting",
                                           "no answer in time", "", None)] == \
        ["busy", "slow", "resting", "", "", ""]


# -- no answer ---------------------------------------------------------------------------------------

class FakeStatus(Exception):
    def __init__(self, status):
        super().__init__("x")
        self.status_code = status


def failed_rows():
    return [r for r in K.replies_report() if r.get("failed")]


def test_a_conversation_reply_that_ends_without_an_answer_gets_a_row_with_a_fixed_sentence():
    ctx = {"call_type": "Chat", "identity": ID}
    K._kame_note_failed(ctx, FakeStatus(429))
    K._KAME_FAILED_ROWS.clear()         # review R8: a repeat inside the window is the same reply; these are three
    K._kame_note_failed(ctx, TimeoutError("took too long"))
    K._KAME_FAILED_ROWS.clear()
    K._kame_note_failed(ctx, RuntimeError("secret provider text AIzaSyLEAK"))
    reasons = [r["reason"] for r in K.replies_report() if r.get("failed")]
    assert reasons == ["the request failed", "it timed out", "the provider answered 429"]
    assert all(r["name"] == "gemini-3.8-flash" and r["total_s"] is None for r in failed_rows())
    assert "AIzaSy" not in repr(K.replies_report())             # never the provider's own text


def test_a_stop_a_hand_over_a_background_job_and_a_ladder_hop_are_not_failures():
    K._kame_note_failed({"call_type": "Chat", "identity": ID}, asyncio.CancelledError())
    K._kame_note_failed({"call_type": "Chat", "identity": ID}, K._KameSwitchModel("busy", None))
    K._kame_note_failed({"call_type": "Util", "identity": ID, "main_chat": False}, TimeoutError())
    token = K._KAME_FALLBACK_HOP.set({"origin": ID, "to": ID, "probe": False})
    try:
        K._kame_note_failed({"call_type": "Chat", "identity": ID}, TimeoutError())
    finally:
        K._KAME_FALLBACK_HOP.reset(token)
    assert failed_rows() == []


def test_the_entry_wrapper_notes_the_failure_of_the_reply():
    src = open(K.__file__, encoding="utf-8").read()
    start = src.index("_flight_id = secrets.token_hex(6)")
    assert "_kame_note_failed(ctx" in src[start:start + 4000]
