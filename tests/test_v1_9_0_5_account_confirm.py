"""1.9.0.5 (E2, decisions/0036): an account-wide hold needs a second model to say the same.

An ACCOUNT-scope refusal that carries only a LABEL (``PerProject``, ``PerUser``, no time from the provider)
holds this model's key. It spans the provider's other models only when a DIFFERENT model reports the same on
the same key within ``_KAME_ACCOUNT_CONFIRM_S`` (10 minutes). Money, and a deadline the provider stated, stay
account-wide at once.
"""
from __future__ import annotations

import time

import pytest

from test_v1_8_2_7_speed_fallback import clean  # noqa: F401  (autouse: resets the carousel state)
import kame_engine as K

KEY = "AIzaSyACCOUNT" + "0" * 26
OTHER = "AIzaSyOTHERKEY" + "0" * 25
KEYS = [KEY, OTHER]
A, B, C = "gemini:model-a", "gemini:model-b", "gemini:model-c"
T0 = 1_800_000_000.0


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    now = {"t": T0}
    monkeypatch.setattr(time, "time", lambda: now["t"])
    K._KAME_ACCOUNT_PENDING.clear()
    for ident in (A, B, C):
        K._get_identity_state(ident, KEYS)
    yield now
    K._KAME_ACCOUNT_PENDING.clear()


def refuse(ident, key, kind="per_minute", delay=300.0, sized_by="", scope="account"):
    return K._mark_key_health(ident, key, False, delay, kind, sized_by=sized_by, scope=scope)


def resting(ident, key=KEY):
    kd = K._KAME_KEY_HEALTH[ident]["keys"][key]
    return K._effective_hold_until(ident, key, kd) > time.time()


def test_one_label_only_account_refusal_holds_only_this_models_key():
    refuse(A, KEY)
    assert resting(A)
    assert not resting(B) and not resting(C)
    assert ("gemini", KEY) not in K._KAME_ACCOUNT_HOLDS
    assert K._KAME_ACCOUNT_PENDING[("gemini", KEY)]["identity"] == A


def test_a_second_model_within_ten_minutes_makes_it_account_wide(clock):
    refuse(A, KEY)
    clock["t"] = T0 + 599.0
    refuse(B, KEY)
    assert ("gemini", KEY) in K._KAME_ACCOUNT_HOLDS
    assert ("gemini", KEY) not in K._KAME_ACCOUNT_PENDING          # the pending mark is dropped
    assert resting(C)                                              # a model that never refused rests too


def test_a_second_model_after_ten_minutes_is_model_only_again(clock):
    refuse(A, KEY)
    clock["t"] = T0 + 601.0
    refuse(B, KEY)
    assert ("gemini", KEY) not in K._KAME_ACCOUNT_HOLDS
    assert not resting(C)
    # and B's refusal is the pending one now
    assert K._KAME_ACCOUNT_PENDING[("gemini", KEY)]["identity"] == B


def test_the_same_model_twice_is_not_a_second_model(clock):
    refuse(A, KEY)
    clock["t"] = T0 + 30.0
    refuse(A, KEY)
    assert ("gemini", KEY) not in K._KAME_ACCOUNT_HOLDS
    assert not resting(B)


def test_another_key_is_its_own_suspicion(clock):
    refuse(A, KEY)
    clock["t"] = T0 + 10.0
    refuse(B, OTHER)
    assert not K._KAME_ACCOUNT_HOLDS


def test_money_is_account_wide_at_once():
    refuse(A, KEY, kind="insufficient_quota", delay=3600.0, scope="")
    assert ("gemini", KEY) in K._KAME_ACCOUNT_HOLDS
    assert resting(B) and resting(C)


def test_a_deadline_the_provider_stated_is_account_wide_at_once():
    refuse(A, KEY, kind="daily", delay=12660.0, sized_by="provider")
    assert ("gemini", KEY) in K._KAME_ACCOUNT_HOLDS
    assert resting(B)
    K._KAME_ACCOUNT_HOLDS.clear()
    refuse(A, OTHER, kind="per_minute", delay=60.0, sized_by="provider")
    assert ("gemini", OTHER) in K._KAME_ACCOUNT_HOLDS


def test_an_answer_drops_the_pending_mark():
    refuse(A, KEY)
    K._mark_key_health(A, KEY, True)
    assert ("gemini", KEY) not in K._KAME_ACCOUNT_PENDING


def test_reset_pool_clears_the_pending_marks():
    refuse(A, KEY)
    K.reset_pool()
    assert not K._KAME_ACCOUNT_PENDING


def test_a_hold_already_confirmed_is_extended_by_a_later_label():
    refuse(A, KEY, kind="insufficient_quota", delay=3600.0, scope="")
    first = K._KAME_ACCOUNT_HOLDS[("gemini", KEY)]["until"]
    time.time  # the clock is fixed; a longer label would move it
    refuse(B, KEY, delay=7200.0)
    assert K._KAME_ACCOUNT_HOLDS[("gemini", KEY)]["until"] >= first


def test_a_label_hold_is_kept_on_disk_as_a_model_row_not_an_account_row(monkeypatch):
    import kame_journal as J
    calls = []
    monkeypatch.setattr(K, "_KJ", type("J", (), {"note_hold": staticmethod(lambda *a: calls.append(a)),
                                                  "note_answer": staticmethod(lambda *a: None)}))
    refuse(A, KEY)
    assert calls and calls[-1][4] != "account"
    refuse(B, KEY, delay=300.0, sized_by="provider")
    assert calls[-1][4] == "account"
