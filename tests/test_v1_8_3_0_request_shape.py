"""1.8.3.0 (A2 / D3) - a request-shape 400 is not a key fault; a 402 is a spent account.

The four lines are the real ones NVIDIA sent (``tests/fixtures/nvidia_refusals_20261016.json``, copied from
the Hermes port's evidence): ``400 Validation: Unsupported parameter(s): `reasoning```. No other key would
answer differently, so the first key's answer goes back to Agent Zero: no rest on the key, no rotation, no
second request. Found by reading the loop: Agent Zero already did this (the classifier calls it `other`,
the loop treats a 400 as terminal); these tests pin it, and the 402 words, so a later change cannot undo it.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures used by name)
    clean, make_ctx, streaming, Refusal, KEYS, ID,
)
import kame_engine as K

FIXTURE = Path(__file__).parent / "fixtures" / "nvidia_refusals_20261016.json"
RECORDED = json.loads(FIXTURE.read_text(encoding="utf-8"))["recorded"]
THREE = KEYS[:3]


def error_of(row):
    return Refusal(row["message"], row["status"], json.loads(row["body"]))


def ask(error, keys=THREE, wait=1.5):
    """Every key answers with ``error``. Returns ``(how it ended, keys asked, keys resting)``."""
    log = []
    ctx, _ = make_ctx(streaming({}, fail={k: (lambda e=error: e) for k in keys}, log=log), keys=keys)
    try:
        asyncio.run(asyncio.wait_for(K._kame_carousel(None, ctx), wait))
        ended = "answered"
    except asyncio.TimeoutError:
        ended = "kept rotating"
    except Refusal:
        ended = "handed back"
    state = K._get_identity_state(ID, keys)["keys"]
    resting = [k for k in keys if (state.get(k) or {}).get("sick_until", 0) > 0]
    return ended, log, resting


def test_the_fixture_holds_the_recorded_lines_and_no_key():
    assert len(RECORDED) == 4 and {row["status"] for row in RECORDED} == {400}
    text = FIXTURE.read_text(encoding="utf-8")
    assert "Unsupported parameter(s)" in text and "Hermes" in text   # the note names where it came from
    assert "nvapi-" not in text and "Bearer" not in text


@pytest.mark.parametrize("index", range(4))
def test_a_recorded_nvidia_400_goes_back_after_the_first_key(index):
    ended, asked, resting = ask(error_of(RECORDED[index]))
    assert ended == "handed back"
    assert len(asked) == 1, "the request was refused, not the key: no second key is asked"
    assert resting == [], "and the key does not rest"


@pytest.mark.parametrize("message,body", [
    ("Error code: 400 - Unrecognized request argument supplied: foo",
     {"error": {"message": "Unrecognized request argument supplied: foo", "type": "invalid_request_error"}}),
    ("Error code: 400 - Unknown parameter: 'foo'",
     {"error": {"message": "Unknown parameter: 'foo'", "type": "invalid_request_error", "param": "foo"}}),
    ("Error code: 400 - Invalid request",
     {"error": {"message": "Invalid request", "type": "invalid_request_error"}}),
])
def test_other_providers_request_shape_errors_read_the_same_way(message, body):
    ended, asked, resting = ask(Refusal(message, 400, body))
    assert ended == "handed back" and len(asked) == 1 and resting == []


def test_a_400_that_names_the_key_is_still_a_key_fault():
    ended, asked, resting = ask(Refusal("API key not valid. Please pass a valid API key. INVALID_ARGUMENT", 400, {}))
    assert ended == "kept rotating", "a bad key is the key's fault: the next key is tried"
    assert len(asked) >= 2 and resting, "and it rests"


# -- the 402 -------------------------------------------------------------------------------------------
PAYMENT = [
    (402, "Error code: 402 - {'message': 'Payment Required: credits exhausted', 'type': 'payment_required'}"),
    (402, "Payment Required"),
    (402, "your credits exhausted for this account"),
]


@pytest.mark.parametrize("status,message", PAYMENT)
def test_a_402_payment_required_is_a_spent_account(status, message):
    delay, kind, got = K._classify_error(Refusal(message, status))
    assert kind == "insufficient_quota" and got == 402
    assert delay >= 3600.0, "an account that needs paying does not recover in seconds"


def test_credits_exhausted_without_a_status_reads_the_same():
    _delay, kind, _status = K._classify_error(Exception("Payment Required: credits exhausted"))
    assert kind == "insufficient_quota"


def test_a_402_rests_the_key_for_the_long_window_and_rotates():
    error = Refusal("Error code: 402 - Payment Required: credits exhausted", 402,
                    {"message": "Payment Required: credits exhausted", "type": "payment_required"})
    ended, asked, resting = ask(error)
    assert ended == "kept rotating" and len(asked) >= 2 and resting


def test_a_429_and_a_503_are_untouched():
    assert K._classify_error(Refusal("Too many requests", 429))[1] == "per_minute"
    assert K._classify_error(Refusal("overloaded", 503))[1] == "server"
