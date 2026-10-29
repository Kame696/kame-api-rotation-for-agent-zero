"""1.8.2.9 (E1 helpers, decisions/0005) - the clock that decides when a busy model is asked again.

`_kame_probe_due`, `_kame_claim_probe`, `_kame_entry_free` and `_kame_circle_due_in` are small
and pure: a busy model is asked at most once per `_KAME_PROBE_EVERY_S` (20 s) across every
conversation, whoever asks. E1 (circle back), E2 (start probe) and E3 (widen) all read them.
No Agent Zero installation, no network.
"""
from __future__ import annotations

import threading
import time

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, clean, fake_models, make_ctx, streaming,
)
import kame_engine as K

OTHER = "google:gemini-3.7-flash"      # as the list writes it
OTHER_LIVE = "google:gemini/gemini-3.7-flash"   # as LiteLLM names the running model


@pytest.fixture(autouse=True)
def calm():
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_WINDOW, K._KAME_PROBED_AT if hasattr(K, "_KAME_PROBED_AT") else {}):
        table.clear()
    yield
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_WINDOW, K._KAME_PROBED_AT if hasattr(K, "_KAME_PROBED_AT") else {}):
        table.clear()


def test_the_probe_clock_is_twenty_seconds_like_hermes():
    assert K._KAME_PROBE_EVERY_S == 20.0


def test_a_model_that_is_not_busy_is_always_due():
    assert K._kame_probe_due(ID) is True
    assert K._kame_claim_probe(ID) is True
    assert ID not in K._KAME_PROBED_AT          # nothing to stamp: it was not busy


def test_a_busy_model_is_due_again_twenty_seconds_after_it_was_asked():
    K._kame_note_busy(ID)                       # the failure that proved it busy IS the last ask
    now = time.time()
    assert K._kame_probe_due(ID, now + 1) is False
    assert K._kame_probe_due(ID, now + 19.5) is False
    assert K._kame_probe_due(ID, now + 20.5) is True


def test_noting_busy_stamps_the_ask_and_an_answer_clears_both():
    K._kame_note_busy(ID)
    assert K._KAME_PROBED_AT[ID] > 0 and ID in K._KAME_BUSY_UNTIL
    K._kame_clear_busy(ID)
    assert ID not in K._KAME_PROBED_AT and ID not in K._KAME_BUSY_UNTIL


def test_an_answer_clears_the_stamp_under_either_spelling():
    K._kame_note_busy(OTHER_LIVE)
    K._kame_clear_busy(OTHER)
    assert not K._KAME_PROBED_AT and not K._KAME_BUSY_UNTIL


def test_a_claim_is_atomic_exactly_one_conversation_gets_the_ask():
    K._kame_note_busy(ID)
    later = time.time() + 25                    # due
    won = []

    def ask():
        won.append(K._kame_claim_probe(ID, later))

    threads = [threading.Thread(target=ask) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert won.count(True) == 1 and won.count(False) == 7
    assert K._kame_probe_due(ID, later + 1) is False       # stamped: not asked again for 20 s
    assert K._kame_claim_probe(ID, later + 21) is True


def test_a_clock_that_goes_backwards_counts_as_due():
    K._kame_note_busy(ID)
    assert K._kame_probe_due(ID, time.time() - 500) is True


def test_the_stamp_is_found_under_the_other_spelling():
    K._kame_note_busy(OTHER_LIVE)               # the nested call noted the live name
    assert K._kame_probe_due(OTHER, time.time() + 1) is False   # the list asks with its own
    assert K._kame_claim_probe(OTHER, time.time() + 1) is False


def test_an_entry_is_free_when_usable_and_not_busy_or_busy_but_due():
    keys = KEYS[:3]
    K._get_identity_state(ID, keys)
    assert K._kame_entry_free(ID, keys) is True
    K._kame_note_busy(ID)
    now = time.time()
    assert K._kame_entry_free(ID, keys, now + 1) is False       # busy, asked a moment ago
    assert K._kame_entry_free(ID, keys, now + 21) is True       # busy, but due another look


def test_an_entry_whose_keys_all_rest_long_is_not_free_even_when_due():
    keys = KEYS[:3]
    K._get_identity_state(ID, keys)
    for k in keys:
        K._mark_key_health(ID, k, False, 3600, "insufficient_quota")
    assert K._kame_entry_free(ID, keys) is False
    K._kame_note_busy(ID)
    assert K._kame_entry_free(ID, keys, time.time() + 60) is False


def test_free_can_be_asked_without_the_keys_from_what_kame_already_knows():
    K._get_identity_state(ID, KEYS[:3])
    for k in KEYS[:3]:
        K._mark_key_health(ID, k, False, 3600, "insufficient_quota")
    assert K._kame_entry_free(ID) is False                      # the race code has no model built yet
    assert K._kame_entry_free("google:never-seen") is True


# --- circle_due_in: when may the first model of [primary] + list be asked again? ---

def test_circle_due_in_names_the_soonest_model_and_is_zero_when_one_can_be_asked(fake_models):
    K.set_fallback_models("google/gemini-3.7-flash\ngoogle/gemini-3.6-flash")
    ctx, _ = make_ctx(streaming({}))
    assert K._kame_circle_due_in(ctx)[0] == 0.0                 # nothing is busy
    now = time.time()
    K._kame_note_busy(ID)
    K._kame_note_busy(OTHER)
    K._kame_note_busy("google:gemini-3.6-flash")
    due, label = K._kame_circle_due_in(ctx, now + 2)
    assert 17.5 < due < 18.5 and label                           # all stamped about now
    K._KAME_PROBED_AT["google:gemini-3.6-flash"] = now - 10      # this one was asked 10 s earlier
    due, label = K._kame_circle_due_in(ctx, now + 2)
    assert 7.5 < due < 8.5 and label == "google/gemini-3.6-flash"


def test_circle_due_in_ties_go_to_the_best_model_in_list_order(fake_models):
    K.set_fallback_models("google/gemini-3.7-flash")
    ctx, _ = make_ctx(streaming({}))
    K._kame_note_busy(ID)
    K._kame_note_busy(OTHER)
    now = time.time()
    K._KAME_PROBED_AT[ID] = now
    K._KAME_PROBED_AT[OTHER] = now
    assert K._kame_circle_due_in(ctx, now)[1] == K._kame_model_label(ID)


def test_circle_due_in_counts_a_skip_listed_entry_until_its_skip_ends(fake_models):
    K.set_fallback_models("google/gemini-3.7-flash")
    ctx, _ = make_ctx(streaming({}))
    K._kame_note_busy(ID)
    K._KAME_PROBED_AT[ID] = time.time()                          # the primary: 20 s
    K._KAME_FALLBACK_SKIP[OTHER] = time.monotonic() + 7          # the entry failed ordinarily
    due, label = K._kame_circle_due_in(ctx)
    assert 6.5 < due < 7.5 and label == "google/gemini-3.7-flash"


def test_circle_due_in_counts_the_wait_for_a_resting_pool(fake_models):
    K.set_fallback_models("google/gemini-3.7-flash")
    ctx, _ = make_ctx(streaming({}))
    for k in KEYS:
        K._mark_key_health(ID, k, False, 5, "per_minute")        # every key of the primary back in 5 s
    due, label = K._kame_circle_due_in(ctx)
    assert due == 0.0 and label == "google/gemini-3.7-flash"     # the entry is askable now
    K._kame_note_busy(OTHER)
    K._KAME_PROBED_AT[OTHER] = time.time()
    due, label = K._kame_circle_due_in(ctx)
    assert 4.0 < due < 5.5 and label == K._kame_model_label(ID)  # the primary's keys return first


def test_reset_pool_forgets_the_stamps():
    K._kame_note_busy(ID)
    K.reset_pool()
    assert not K._KAME_PROBED_AT
