"""1.9.0.6 (Hermes failures/0034, the owner's test of 2026-11-24 07:43) - one reply never floods the provider.

Google answered "429: The query-per-second (QPS) limit for identical or near-identical request payloads has been
exceeded" on every key; rotating into it renewed it (one Hermes reply: 184 copies of one request in two minutes, no
answer). The same rule here, on a virtual clock: a payload asked again sooner than GAP after its last ask is refused.
"""
from __future__ import annotations

import asyncio

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, Refusal, clean, make_ctx,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K

@pytest.fixture(autouse=True)
def _forget_what_the_429s_taught():
    """The 429s here teach the pacer and the latency tracker; the suites after this one must not inherit it."""
    yield
    for name in ("_KP", "_KL"):
        mod = getattr(K, name, None)
        for attr in ("PACER", "LATENCY"):
            clear = getattr(getattr(mod, attr, None), "clear", None)
            if callable(clear):
                clear()


IDENTICAL = ("The query-per-second (QPS) limit for identical or near-identical request payloads has been exceeded. "
             "See https://ai.dev/rate-limit to manage your rate limits.")


def identical():
    return Refusal("Error code: 429 - " + IDENTICAL, 429,
                   {"error": {"code": 429, "message": IDENTICAL, "status": "RESOURCE_EXHAUSTED"}})


def throttled():
    return Refusal("Error code: 429", 429, {"error": {"code": 429, "message": "Resource has been exhausted "
                                                       "(e.g. check quota).", "status": "RESOURCE_EXHAUSTED"}})


class Google:
    """Every key, one rule for the payload: asked again within ``gap`` of its last ask -> 429 identical.
    The limit is already on for ``on_s`` (the owner's 07:43)."""

    def __init__(self, vt, gap=1.5, on_s=1.0, plain_s=0.0):
        self.vt, self.gap, self.on_s, self.plain_s = vt, gap, on_s, plain_s
        self.sent = []
        self.last = None

    def attempt(self):
        async def call(owner, key, ctx):
            now = self.vt.elapsed()
            self.sent.append((now, key))
            last, self.last = self.last, now
            await asyncio.sleep(0.1)
            if now < self.plain_s:
                raise throttled()
            if now < self.on_s or (last is not None and now - last < self.gap):
                raise identical()
            if ctx.get("response_callback") is not None:
                await ctx["response_callback"]("ok", "ok")
            return ("answer", "")
        return call


def test_the_owners_0743_the_limit_is_on_and_the_reply_waits_instead_of_rotating(vt):
    g = Google(vt)
    ctx, _ = make_ctx(g.attempt())
    result = vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == "answer"
    assert len(g.sent) <= 4, f"{len(g.sent)} requests: {g.sent}"
    assert vt.elapsed() < 10.0


def test_the_hold_rests_no_key(vt):
    g = Google(vt)
    ctx, _ = make_ctx(g.attempt())
    vt.run(K._kame_carousel(None, ctx), limit=120)
    assert K._kame_healthy_count(ID, KEYS) == len(KEYS) if hasattr(K, "_kame_healthy_count") else True
    kd = K._KAME_KEY_HEALTH.get(ID, {}).get("keys", {})
    assert not any(float(v.get("sick_until") or 0) > 0 for v in kd.values()), "no key rests for the payload's refusal"


def test_a_storm_of_plain_429s_is_paced_not_hammered(vt):
    g = Google(vt, gap=0.0, on_s=0.0, plain_s=6.0)
    ctx, _ = make_ctx(g.attempt(), keys=KEYS)
    result = vt.run(K._kame_carousel(None, ctx), limit=200)
    assert result[0] == "answer"
    in_storm = [s for s in g.sent if s[0] < 6.0]
    assert len(in_storm) <= 16, f"{len(in_storm)} requests in 6 s of 429s"


def test_one_refusal_then_an_answer_is_as_fast_as_before(vt):
    g = Google(vt, gap=0.0, on_s=0.0, plain_s=0.05)
    ctx, _ = make_ctx(g.attempt())
    result = vt.run(K._kame_carousel(None, ctx), limit=60)
    assert result[0] == "answer"
    assert vt.elapsed() < 1.0


def test_the_pace_itself():
    t = [100.0]
    pace = K._KamePayloadPace(clock=lambda: t[0])
    assert pace.note_identical("p") == 2.0
    t[0] += 0.3
    assert pace.note_identical("p") == pytest.approx(1.7)
    t[0] += 2.0
    assert pace.note_identical("p") == 4.0
    pace.note_answered("p")
    for n in range(8):
        pace.note_sent("q")
        pace.note_refusal("q", "rate_limit")
    assert pace.wait_s("q") == 1.0
    pace.note_refusal("q", "timeout")
    pace.note_refusal("q", "server")
    assert pace.streak("q") == 8, "a timeout or a 503 is not a throttle"
    assert K._kame_is_identical_refusal(identical()) and not K._kame_is_identical_refusal(throttled())


def test_the_payload_is_the_messages():
    a = {"messages": ["hello"]}
    b = {"messages": ["hello"]}
    c = {"messages": ["other"]}
    assert K._kame_payload_fp(a) == K._kame_payload_fp(b) != K._kame_payload_fp(c)
    assert K._kame_payload_fp({"messages": []}) == ""
