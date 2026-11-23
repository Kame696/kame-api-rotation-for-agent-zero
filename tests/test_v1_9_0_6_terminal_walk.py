"""1.9.0.6 (Agent Zero decisions/0019 A6) - a request refused on one model is tried on the user's list first.

For a main-chat call, a terminal request fault on one model (404 model not found, 410 gone, 413 too large, a 400 the
provider calls the request's fault, a blocked prompt) walks the list once - each model asked at most once in the reply -
before anything is raised. If every list model refuses too, the FIRST model's error is raised (today's text). A model
that answered 404 "not found" is remembered as missing for 6 h: every picker skips it, the page's list shows it with the
provider's "did you mean" when there is one.

A virtual clock (kame_vclock.py). No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio

import pytest

from test_v1_9_0_6_never_stop import (  # noqa: F401  (fixtures are used by name)
    ONE_PROVIDER, prov, reply, vt,
)
from test_v1_8_2_7_speed_fallback import ID, Refusal, clean  # noqa: F401
from test_v1_8_4_3_quiet_guard import NEXT, THIRD  # noqa: F401
import kame_engine as K


def gone():
    return Refusal("Error code: 410", 410, {"error": {"code": 410, "message": "This model has reached its end of life."}})


def bad_request(who):
    return Refusal(f"Error code: 400 - {who} says the request is invalid", 400,
                   {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": f"{who}: invalid argument"}})


def not_found(did_you_mean=""):
    text = "The model `x` does not exist or you do not have access to it."
    if did_you_mean:
        text += f" Did you mean {did_you_mean}?"
    return Refusal("Error code: 404", 404, {"error": {"code": 404, "message": text}})


@pytest.fixture(autouse=True)
def forget_missing():
    K._KAME_MISSING.clear()
    yield
    K._KAME_MISSING.clear()


def script(prov, vt, plan):
    """plan: who -> a factory of the exception it raises (missing = answers)."""
    original = prov.serve

    async def serve(who, ctx):
        if who in plan:
            prov.requests.append((vt.elapsed(), who))
            await asyncio.sleep(0.05)
            raise plan[who]()
        return await original(who, ctx)

    prov.serve = serve


def asked(prov):
    return [w for _t, w in prov.requests]


def test_a6_a_410_on_the_main_model_is_answered_by_a_healthy_list_model(vt, prov):
    K.set_fallback_models(ONE_PROVIDER)
    script(prov, vt, {"home": gone})
    result, ctx, lines = reply(vt, prov)
    assert not isinstance(result, BaseException), f"{type(result).__name__}: {result}"
    assert result[0] == f"answer from {NEXT}"
    assert asked(prov).count("home") == 1


def test_a6_every_model_refusing_raises_the_first_models_error_once(vt, prov):
    K.set_fallback_models(ONE_PROVIDER)
    script(prov, vt, {"home": lambda: bad_request("home"), NEXT: lambda: bad_request("next"),
                      THIRD: lambda: bad_request("third")})
    result, ctx, lines = reply(vt, prov)
    assert isinstance(result, BaseException)
    assert "home says the request is invalid" in str(result), str(result)
    assert sorted(asked(prov)) == sorted(["home", NEXT, THIRD])         # each model asked once, no more


def test_a6_without_a_list_the_error_is_raised_as_before(vt, prov):
    script(prov, vt, {"home": lambda: bad_request("home")})
    result, ctx, lines = reply(vt, prov)
    assert isinstance(result, BaseException) and "home says" in str(result)
    assert asked(prov) == ["home"]


def test_a6_a_404_model_is_remembered_missing_and_skipped_on_the_next_reply(vt, prov):
    K.set_fallback_models(ONE_PROVIDER)
    script(prov, vt, {"home": gone, NEXT: lambda: not_found("gemini-3.7-flash-001")})
    result, ctx, lines = reply(vt, prov)
    assert result[0] == f"answer from {THIRD}"
    assert K._kame_is_missing("google:gemini-3.7-flash")
    assert not K._kame_is_missing("google:gemini-3.6-flash")
    prov.requests.clear()
    result, ctx, lines = reply(vt, prov)
    assert result[0] == f"answer from {THIRD}"
    assert NEXT not in asked(prov), "the missing model was asked again"
    entry = [e for e in K._kame_fallback_view(__import__("time").time())["entries"] if e["identity"] == "google:gemini-3.7-flash"][0]
    assert entry["missing_hint"] == "gemini-3.7-flash-001" and entry["missing_until"] > __import__("time").time() + 5.9 * 3600


def test_a6_a_missing_model_is_forgotten_after_six_hours(vt, prov):
    K.set_fallback_models(ONE_PROVIDER)
    script(prov, vt, {"home": gone, NEXT: not_found})
    reply(vt, prov)
    assert K._kame_is_missing("google:gemini-3.7-flash")
    vt.advance(6 * 3600 + 10)
    assert not K._kame_is_missing("google:gemini-3.7-flash")


def test_a6_a_main_model_that_answered_404_starts_the_next_reply_on_the_list(vt, prov):
    K.set_fallback_models(ONE_PROVIDER)
    script(prov, vt, {"home": not_found})
    result, ctx, lines = reply(vt, prov)
    assert result[0] == f"answer from {NEXT}"
    prov.requests.clear()
    result, ctx, lines = reply(vt, prov)
    assert result[0] == f"answer from {NEXT}"
    assert "home" not in asked(prov), "the missing main model was asked again"


def test_a6_a_blocked_prompt_walks_the_list_too_and_a_list_failure_benches_nobody(vt, prov):
    K.set_fallback_models(ONE_PROVIDER)
    blocked = lambda: Refusal("blocked", 400, {"promptFeedback": {"blockReason": "SAFETY"}})  # noqa: E731
    script(prov, vt, {"home": blocked, NEXT: blocked})
    result, ctx, lines = reply(vt, prov)
    assert result[0] == f"answer from {THIRD}"
    # a refused REQUEST says nothing about the model's health: the other conversations are not told to skip it
    assert K._KAME_FALLBACK_SKIP.get("google:gemini-3.7-flash", 0.0) == 0.0


def test_a6_a_404_of_a_route_is_not_a_missing_model(forget_missing):
    """Review fix (parity with Hermes ``model_missing.detect``): "404 page not found" (a wrong api_base) names no model."""
    K._kame_note_terminal("google:gemini-3.7-flash",
                          Refusal("404 page not found", 404, {"error": {"message": "404 page not found"}}))
    assert not K._kame_is_missing("google:gemini-3.7-flash")
    K._kame_note_terminal("google:gemini-3.7-flash", not_found())
    assert K._kame_is_missing("google:gemini-3.7-flash")
