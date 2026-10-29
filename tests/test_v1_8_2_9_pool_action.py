"""1.8.2.9 (U2) - the page's buttons: "Wake this model", "Try this model now", Clear pool, Clear events, Reset.

`wake_model(identity)` starts the keys of ONE model again; `forget_busy(identity)` makes the next reply ask
that model first again. Both live in the engine and are reached through `api/kame_pool_action`.
No Agent Zero, no network.
"""
from __future__ import annotations

import time

import pytest

from test_v1_8_2_7_speed_fallback import clean  # noqa: F401  (fixture used by name; also stubs Agent Zero)
import kame_api_stub as stub
import kame_engine as K
import kame_settings as S

API = stub.load_api("kame_pool_action", K, S)
A = ["AIzaSyACT-one-000000000000000", "AIzaSyACT-two-000000000000000"]
B = ["sk-ACT-openai-000000000000000"]


@pytest.fixture(autouse=True)
def fresh():
    stub.reset()
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_KIND, K._KAME_PROBED_AT, K._KAME_BUSY_WINDOW):
        table.clear()
    yield
    stub.reset()
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_KIND, K._KAME_PROBED_AT, K._KAME_BUSY_WINDOW):
        table.clear()


def seed():
    K._get_identity_state("gemini:gemini-3.5-flash", A)
    K._get_identity_state("openai:gpt-5.4-nano", B)
    K._mark_key_health("gemini:gemini-3.5-flash", A[0], False, 3600, "daily")
    K._mark_key_health("gemini:gemini-3.5-flash", A[1], False, 600, "per_minute")
    K._mark_key_health("openai:gpt-5.4-nano", B[0], False, 600, "per_minute")


def codes(ident):
    pool = next(p for p in K.status_view()["pools"] if p["identity"] == ident)
    return [c for _fp, c, _u, _k in pool["key_states"]]


def test_wake_this_model_starts_its_keys_again_and_leaves_the_other_models_alone():
    seed()
    assert sorted(codes("gemini:gemini-3.5-flash")) == ["d", "s"]
    out = stub.call(API.KamePoolAction, {"action": "clear_pool", "identity": "gemini:gemini-3.5-flash"})
    assert out["ok"] is True and out["count"] == 2 and "2 keys" in out["message"]
    assert codes("gemini:gemini-3.5-flash") == ["r", "r"]
    assert codes("openai:gpt-5.4-nano") == ["s"]                 # the other model keeps its rest


def test_waking_a_model_works_under_the_other_spelling_of_its_name():
    seed()
    assert K.wake_model("gemini:gemini/gemini-3.5-flash") == 2
    assert codes("gemini:gemini-3.5-flash") == ["r", "r"]


def test_waking_a_model_forgets_that_it_was_busy():
    seed()
    ident = "gemini:gemini-3.5-flash"
    K._KAME_BUSY_UNTIL[ident] = time.time() + 300
    K._KAME_BUSY_KIND[ident] = "busy"
    K._KAME_PROBED_AT[ident] = time.time()
    K.wake_model(ident)
    assert not K._kame_busy_state(ident, time.time())[0] and ident not in K._KAME_PROBED_AT


def test_waking_a_model_clears_the_account_hold_of_its_keys():
    seed()
    ident = "gemini:gemini-3.5-flash"
    K._KAME_ACCOUNT_HOLDS[("gemini", A[0])] = {"until": time.time() + 3600, "kind": "daily", "scope": "account", "at": time.time()}
    K.wake_model(ident)
    assert ("gemini", A[0]) not in K._KAME_ACCOUNT_HOLDS


def test_try_this_model_now_clears_busy_and_touches_no_key():
    seed()
    ident = "gemini:gemini-3.5-flash"
    K._KAME_BUSY_UNTIL[ident] = time.time() + 300
    K._KAME_BUSY_KIND[ident] = "busy"
    K._KAME_PROBED_AT[ident] = time.time()
    out = stub.call(API.KamePoolAction, {"action": "forget_busy", "identity": ident})
    assert out["ok"] and out["message"] == "This model will be asked again now."
    assert not K._kame_busy_state(ident, time.time())[0]
    assert sorted(codes(ident)) == ["d", "s"]                    # its keys keep resting
    again = stub.call(API.KamePoolAction, {"action": "forget_busy", "identity": ident})
    assert again["message"] == "This model was not marked busy."


def test_try_this_model_needs_a_model():
    assert stub.call(API.KamePoolAction, {"action": "forget_busy"})["ok"] is False


def test_the_toolbar_clears_every_pool():
    seed()
    out = stub.call(API.KamePoolAction, {"action": "clear_pool"})
    assert out["ok"] and out["count"] == 3
    assert codes("gemini:gemini-3.5-flash") == ["r", "r"] and codes("openai:gpt-5.4-nano") == ["r"]


def test_clear_events_empties_the_list_and_nothing_else():
    K._KJ.EVENTS.add("rotation", identity="x:y", key="k1", reason="out of credit", at=time.time())
    seed()
    out = stub.call(API.KamePoolAction, {"action": "clear_events"})
    assert out["ok"] and K._KJ.EVENTS.recent(50) == []
    assert codes("openai:gpt-5.4-nano") == ["s"]


def test_reset_to_defaults_keeps_the_fallback_list():
    stub.CONFIG.update({"hedge_mode": "max", "model_switch": "never", "fallback_models": "openai/gpt-5.4-nano"})
    out = stub.call(API.KamePoolAction, {"action": "reset_settings"})
    assert out["ok"] is True
    assert stub.CONFIG["hedge_mode"] == S.default("hedge_mode") == "adaptive"
    assert stub.CONFIG["model_switch"] == "auto"
    assert stub.CONFIG["fallback_models"] == "openai/gpt-5.4-nano"
    assert stub.ACTIVATED


def test_an_unknown_action_is_refused_by_name():
    out = stub.call(API.KamePoolAction, {"action": "delete_keys"})
    assert out["ok"] is False and "delete_keys" in out["message"]


def test_no_key_is_in_any_answer():
    seed()
    text = repr([stub.call(API.KamePoolAction, {"action": a, "identity": "gemini:gemini-3.5-flash"})
                 for a in ("clear_pool", "forget_busy", "clear_events")])
    for secret in A + B:
        assert secret not in text
