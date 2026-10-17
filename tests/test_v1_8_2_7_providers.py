"""1.8.2.7 - every provider learned, graded on the carousel's own decision path.

`tools/provider_doc_gate.py` runs the 774 documented error shapes of 82
providers (`tests/provider_keys/`, the answer keys the Hermes port is graded on)
through `_is_terminal_error` and `_kame_decide_failure`. The floors below are
what this build reaches; a change that loses one shape fails here. Hermes
1.8.2.7 on the same keys: family 670, action 670, window 116, scope 92.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("a0_1827_gate", ROOT / "tools" / "provider_doc_gate.py")
G = importlib.util.module_from_spec(spec)
sys.modules["a0_1827_gate"] = G
spec.loader.exec_module(G)
K = G.load_engine()

FLOORS = {"family": 674, "action": 675, "window": 116, "scope": 96, "rest": 6}
HERMES_1827 = {"family": 670, "action": 670, "window": 116, "scope": 92}


@pytest.fixture(scope="module")
def graded():
    return G.evaluate()


def test_every_answer_key_is_read(graded):
    report, _ = graded
    assert len(report) == 774
    assert len({e["key"] for e in report}) == 7


@pytest.mark.parametrize("field", sorted(FLOORS))
def test_floor(graded, field):
    _, tally = graded
    assert tally[(field, "agree")] >= FLOORS[field], dict(tally)


@pytest.mark.parametrize("field", sorted(HERMES_1827))
def test_never_behind_the_hermes_port(graded, field):
    _, tally = graded
    assert tally[(field, "agree")] >= HERMES_1827[field]


def test_no_stated_wait_is_misread(graded):
    _, tally = graded
    assert tally[("rest", "DISAGREE")] == 0


def _row(**kw):
    row = {"exception_type": "APIStatusError", "headers": {}}
    row.update(kw)
    return G.build_exception(row)


def _decide(exc):
    G._fresh(K)
    with K._KAME_LOCK:
        K._get_identity_state("p:m", ["k1", "k2"])
    return K._kame_decide_failure("p:m", "k1", exc)


def test_the_quirks_work_without_the_hermes_carousel():
    # The Hermes module imports its tables from `.carousel`; the A0 copy carries
    # them. A failing import used to switch every 403 rule off in silence.
    import kame_quirks as Q
    assert Q.names_bad_key("Authentication failed", None)
    assert not Q.names_bad_key("You do not have access to this model", None)
    assert Q.reinterpret(403, "auth", "Your plan does not include this model", None) == "denied"


@pytest.mark.parametrize("message", ["Authentication failed", "API Key not provided / validation failed"])
def test_a_403_that_says_the_key_is_bad_is_a_credential(message):
    _applied, kind, _st, _l, _s = _decide(_row(status=403, body={"error": message}))
    assert kind == "auth"


def test_a_403_that_does_not_say_so_is_a_refused_pairing():
    _applied, kind, _st, _l, _s = _decide(_row(status=403, body={"error": {"message": "Model not available on your tier"}}))
    assert kind == "denied"


@pytest.mark.parametrize("status,body", [
    (503, {"error": {"message": "No instances available for this model"}}),
    (503, {"error": {"code": "maintenance", "message": "Scheduled maintenance"}}),
    (502, {"error": {"message": "No provider available for model x"}}),
    (503, {"error": {"message": "Loading model"}}),
])
def test_a_closed_door_is_seen_and_left_to_the_carousel(status, body):
    exc = _row(status=status, body=body)
    assert K._kame_closed_door(exc)
    assert not K._is_terminal_error(exc)  # the carousel decides: fallback list, or the pool waits


def test_a_busy_503_still_rotates():
    exc = _row(status=503, body={"error": {"message": "Service temporarily unavailable"}})
    assert not K._kame_closed_door(exc)
    assert not K._is_terminal_error(exc)


@pytest.mark.parametrize("status", [408, 499, 504, 524])
def test_timeout_statuses_rest_zero(status):
    delay, kind, sc = K._classify_error(_row(status=status, body={"error": "upstream"}))
    assert (delay, kind, sc) == (0, "timeout", status)


def test_a_timeout_status_that_states_a_wait_obeys_it():
    delay, kind, _sc = K._classify_error(_row(status=504, headers={"retry-after": "7"}, body={"error": "x"}))
    assert (delay, kind) == (7.0, "server")


def test_a_403_saying_timeout_is_not_a_timeout():
    exc = _row(status=403, body={"message": "your IP address has been put into timeout for 60 more seconds"})
    assert K._classify_error(exc)[1] != "timeout"


def test_a_400_naming_a_timeout_parameter_is_the_request():
    exc = _row(status=400, body={"error": {"message": "invalid value for timeout"}})
    assert K._classify_error(exc)[1] != "timeout"
    assert K._is_terminal_error(exc)


def test_a_bare_429_saying_overloaded_rests_one_second():
    delay, kind, _sc = K._classify_error(_row(status=429, body={"error": "The model is currently overloaded."}))
    assert (delay, kind) == (K._KAME_SERVER_BASE_S, "server")


def test_a_prompt_block_inside_a_body_is_terminal():
    assert K._is_terminal_error(_row(status=200, body={"promptFeedback": {"blockReason": "SAFETY"}}))


def test_416_is_terminal():
    assert K._is_terminal_error(_row(status=416, body={"error": "range"}))


# The same rows the way LiteLLM hands them to Agent Zero (mapped class, body only
# in the message and on `response.text`). Before 1.8.2.7 an `APIConnectionError`
# carrying a 402 "insufficient balance" was read as a dropped socket.
LITELLM_FLOORS = {"family": 670, "action": 674, "window": 115, "scope": 96, "rest": 6}


@pytest.fixture(scope="module")
def graded_litellm():
    return G.evaluate(shape="litellm")


@pytest.mark.parametrize("field", sorted(LITELLM_FLOORS))
def test_litellm_shape_floor(graded_litellm, field):
    _, tally = graded_litellm
    assert tally[(field, "agree")] >= LITELLM_FLOORS[field], dict(tally)


def test_a_litellm_connection_error_with_a_billing_body_is_billing():
    cls = type("APIConnectionError", (Exception,), {})
    exc = cls('litellm.APIConnectionError: DeepinfraException - {"error": {"message": "Payment required: insufficient balance"}}')
    exc.status_code = 500
    assert K._classify_error(exc)[1] != "timeout"


def test_a_bare_litellm_connection_error_is_still_a_dropped_socket():
    cls = type("APIConnectionError", (Exception,), {})
    exc = cls("litellm.APIConnectionError: Connection refused")
    exc.status_code = 500
    assert K._classify_error(exc) == (3, "timeout", None)
