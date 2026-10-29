"""1.8.2.9 (E2, decisions/0005) - the busy best model is asked beside the reply.

Until 1.8.2.8 a model known busy was skipped without a request for its whole window, so a
model that had recovered was only noticed when the window ended. Now, at the start of a
streamed conversation reply, a busy best model that is due (not asked in the last 20 s) is
asked on its best key AT THE SAME TIME as the first free model of the list: whoever speaks
first is shown. Losing that race is not proof the best model is still busy; a 503 from it
is; an answer from it ends the busy window. At most one such ask per busy model per 20 s,
across every conversation.

A virtual clock (kame_vclock.py). No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    FALLBACK_BEHAVIOUR, ID, KEYS, Refusal, clean, fake_models, make_ctx, streaming,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K

NEXT = "google/gemini-3.7-flash"


def overloaded():
    return Refusal("Error code: 503", 503,
                   {"error": {"code": 503, "message": "The model is overloaded. Please try again later.",
                              "status": "UNAVAILABLE"}})


@pytest.fixture(autouse=True)
def calm():
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    getattr(K, "_KAME_PROBED_AT", {}).clear()
    K._KAME_REPLIES.clear()
    K._KAME_TURNS.clear()
    yield
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()


def busy_and_due():
    """The best model was found busy a while ago: its window runs, and it is due another look."""
    K._kame_note_busy(ID)
    getattr(K, "_KAME_PROBED_AT", {}).clear()


def requests(log):
    return [k for k in log if not k.startswith("cancelled:")]


def test_a_busy_and_due_best_model_is_asked_at_the_same_instant_as_the_next_model(fake_models, vt):
    K.set_fallback_models(NEXT)
    busy_and_due()
    log = []
    ctx, _ = make_ctx(streaming({k: 2.0 for k in KEYS}, log=log))      # the best model is slow today
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    assert result[0] == f"fallback answer from {NEXT}"
    assert len(requests(log)) == 1 and len(fake_models) == 1            # both went out ...
    assert vt.elapsed() < 0.5                                            # ... at t = 0, the list model won
    assert ctx.get("probe_primary") is True


def test_when_the_best_model_answers_first_it_is_shown_and_its_window_ends(fake_models, vt):
    K.set_fallback_models(NEXT)
    FALLBACK_BEHAVIOUR[NEXT] = "slow"                                    # 3 s
    busy_and_due()
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    assert result[0].startswith("answer from ") and not result[0].endswith("flash")
    assert len(requests(log)) == 1                                       # the best model WAS asked
    assert ID not in K._KAME_BUSY_UNTIL and ID not in K._KAME_PROBED_AT


def test_losing_the_start_race_is_not_proof_that_the_best_model_is_busy(fake_models, vt):
    K.set_fallback_models(NEXT)
    busy_and_due()
    window, until = K._KAME_BUSY_WINDOW[ID], K._KAME_BUSY_UNTIL[ID]
    log = []
    ctx, _ = make_ctx(streaming({k: 5.0 for k in KEYS}, log=log))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0] == f"fallback answer from {NEXT}"
    assert requests(log)                                                 # it was asked ...
    assert K._KAME_BUSY_WINDOW[ID] == window and K._KAME_BUSY_UNTIL[ID] == until   # ... and not re-noted


def test_a_503_from_the_probe_doubles_the_window(fake_models, vt):
    K.set_fallback_models(NEXT)
    FALLBACK_BEHAVIOUR[NEXT] = "slow"                                    # the list model needs 3 s
    busy_and_due()
    assert K._KAME_BUSY_WINDOW[ID] == K._KAME_BUSY_FIRST_S
    ctx, _ = make_ctx(streaming({}, fail={k: overloaded for k in KEYS}))
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    assert result[0] == f"fallback answer from {NEXT}"
    assert K._KAME_BUSY_WINDOW[ID] == 2 * K._KAME_BUSY_FIRST_S           # 30 -> 60, once (not again for the loss)


def test_replies_every_five_seconds_probe_the_best_model_at_most_three_times_in_a_minute(fake_models, vt):
    K.set_fallback_models(NEXT)
    log = []

    async def conversation():
        for _ in range(12):
            ctx, _ = make_ctx(streaming({}, fail={k: overloaded for k in KEYS}, log=log))
            result = await K._kame_carousel(None, ctx)
            assert result[0] == f"fallback answer from {NEXT}"
            await asyncio.sleep(5)

    vt.run(conversation(), limit=100)
    asks = requests(log)
    assert 1 <= len(asks) - 2 <= 3, asks        # two requests prove it busy; then a few probes, not one per reply


# --- guards: where nothing changes ------------------------------------------------------

def test_saver_never_asks_the_busy_model_beside_the_reply(fake_models, vt):
    K.set_fallback_models(NEXT)
    K.set_hedge_mode("off")
    busy_and_due()
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0] == f"fallback answer from {NEXT}"
    assert log == []


def test_never_goes_on_asking_the_best_model_and_calls_no_other(fake_models, vt):
    K.set_fallback_models(NEXT)
    K.set_model_switch("never")
    busy_and_due()
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert len(requests(log)) == 1 and not fake_models


def test_a_background_call_is_never_probed(fake_models, vt):
    K.set_fallback_models(NEXT)
    busy_and_due()
    log = []
    ctx, _ = make_ctx(streaming({}, log=log), call_type="Util")
    assert vt.run(K._kame_carousel(None, ctx), limit=30)[0].startswith("answer from ")
    assert len(requests(log)) == 1 and not fake_models
    assert ctx.get("probe_primary") is not True


def test_the_start_probe_needs_everything_it_says(fake_models):
    K.set_fallback_models(NEXT)
    ctx, _ = make_ctx(streaming({}))
    assert K._kame_start_probe_allowed(ctx, ID) is False                 # the model is not known busy
    busy_and_due()
    ctx["kwargs"] = {"a0_api_mode": "responses"}
    assert K._kame_start_probe_allowed(ctx, ID) is False                 # Responses transport (G1)
    ctx["kwargs"] = {}
    assert K._kame_start_probe_allowed(ctx, ID) is True                  # and now it claims the ask
    assert K._kame_start_probe_allowed(ctx, ID) is False                 # a second conversation does not


def test_responses_mode_with_native_tools_makes_no_probe_and_no_cross_leg(fake_models, vt):
    K.set_fallback_models(NEXT)
    busy_and_due()
    log = []
    ctx, _ = make_ctx(streaming({}, log=log))
    ctx["kwargs"] = {"a0_api_mode": "responses", "previous_response_id": "resp_1"}
    result = vt.run(K._kame_carousel(None, ctx), limit=30)
    assert result[0] == f"fallback answer from {NEXT}"                   # sequential switching stays
    assert log == [] and ctx.get("probe_primary") is not True
