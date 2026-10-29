"""1.8.3.0 (A6) - a 503 is ALWAYS a busy server: one second, never escalated, whatever its body says.

The owner's closed rule ("503 never escalates"); the Hermes port reads a 5xx before any words
(`core/carousel.py classify`: "A provider under load has been seen to return 503 with a body mentioning
quota"). Agent Zero read the evidence first, so a 503 whose body said "payment required" or "insufficient
credits" was filed as a spent account and rested a healthy key for an hour. These tests pin the rule for
every body, and keep the 402 (the status that really means a spent account) as it was.
"""
from __future__ import annotations

import pytest

from test_v1_8_2_7_speed_fallback import Refusal  # noqa: F401  (also stubs Agent Zero)
import kame_engine as K

BODIES = [
    ("Error code: 503 - payment required", {"error": {"message": "payment required"}}),
    ("Error code: 503 - insufficient credits, please add funds", {"error": {"message": "insufficient credits"}}),
    ("Error code: 503 - quota exceeded", {"error": {"message": "You exceeded your current quota"}}),
    ("Error code: 503 - daily limit reached", {"error": {"message": "daily limit reached"}}),
    ("Error code: 503", {"error": {"message": "credits exhausted for this account"}}),
    ("Error code: 503 - billing hard limit has been reached", {"error": {"message": "billing hard limit has been reached"}}),
    ("503 Service Unavailable", {"error": {"message": "overloaded"}}),
    ("Error code: 503", {}),
]


@pytest.mark.parametrize("message,body", BODIES)
def test_a_503_is_a_busy_server_whatever_its_body_says(message, body):
    delay, kind, status = K._classify_error(Refusal(message, 503, body))
    assert (delay, kind, status) == (1.0, "server", 503)


@pytest.mark.parametrize("message,body", BODIES)
def test_the_failure_path_rests_the_key_one_second_and_files_it_as_server(message, body):
    applied, kind, status, _label, _sized = K._kame_rest_for_failure("x:model", "k" * 20, Refusal(message, 503, body))
    assert kind == "server" and status == 503
    assert applied == 1.0


def test_a_503_that_names_its_own_wait_is_obeyed():
    error = Refusal("Error code: 503 - payment required", 503, {"error": {"message": "payment required"}})
    error.headers = {"retry-after": "7"}
    delay, kind, status = K._classify_error(error)
    assert kind == "server" and status == 503 and delay == 7.0


def test_a_402_is_still_a_spent_account():
    delay, kind, status = K._classify_error(
        Refusal("Error code: 402 - payment required", 402, {"error": {"message": "payment required"}}))
    assert kind == "insufficient_quota" and status == 402
