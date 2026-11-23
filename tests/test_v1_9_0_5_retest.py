"""1.9.0.5 (E1, decisions/0036): the live re-test while every key rests on a label.

Google labels a 429 "PerDay" on keys that are not out of quota. When the label lands on the whole pool, every
key used to sit for an hour in silence. Now the pending request itself is sent to ONE label-held key after 20 s,
then +40, +80, +160, then every +300 s. A refusal re-holds that key through the normal path; an answer is the
reply. A virtual clock and a fake provider: nothing sleeps, nothing touches the network.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS as ALL_KEYS, Refusal, clean, make_ctx  # noqa: F401
from kame_vclock import vt  # noqa: F401
import kame_engine as K

KEYS = ALL_KEYS[:3]
GEMINI_DAILY = (
    '{"error":{"code":429,"message":"Quota exceeded for ... generate_content_free_tier_requests, limit:20",'
    '"status":"RESOURCE_EXHAUSTED","details":[{"@type":"type.googleapis.com/google.rpc.QuotaFailure",'
    '"violations":[{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}')


def daily():
    return Refusal(GEMINI_DAILY, 429, body=GEMINI_DAILY)


@pytest.fixture(autouse=True)
def calm(clean):  # noqa: F811
    K.set_hedge_mode("off")
    K._KAME_RETEST_INFLIGHT.clear()
    yield
    K._KAME_RETEST_INFLIGHT.clear()


def rest_on_a_label(identity=ID, keys=KEYS):
    """Every key daily-labelled, no time from the provider, the doubt window long closed: an hour each."""
    K._get_identity_state(identity, keys)
    K._KAME_NO_ANSWER_SINCE[identity] = time.time() - 10_000.0
    for key in keys:
        K._mark_key_health(identity, key, False, 3600.0, "daily")
    assert K._next_recovery_seconds(identity, keys) > 3000


class Provider:
    """Answers (``healthy``) or refuses with the daily label; records when each key was asked."""

    def __init__(self, vt, healthy, takes=0.0):
        self.vt, self.healthy, self.takes = vt, healthy, takes
        self.asked = []   # (virtual seconds, key)

    async def __call__(self, owner, key, ctx):
        self.asked.append((self.vt.elapsed(), key))
        if self.takes:
            await asyncio.sleep(self.takes)
        if not self.healthy:
            raise daily()
        if ctx.get("response_callback") is not None:
            await ctx["response_callback"]("back", "back")
        return ("back", "")


def events(kind):
    return [r for r in K._KJ.EVENTS.recent(50) if r["kind"] == kind]


def test_a_healthy_provider_behind_a_daily_label_answers_at_about_twenty_seconds(vt):
    provider = Provider(vt, healthy=True)
    ctx, _ = make_ctx(provider, keys=KEYS)
    rest_on_a_label()
    result = vt.run(K._kame_carousel(None, ctx), limit=300)
    assert result[0] == "back"
    assert len(provider.asked) == 1
    at, key = provider.asked[0]
    assert 19.5 <= at <= 22.0
    rows = events("retest")
    assert len(rows) == 1 and rows[0]["key"] and rows[0]["key"] not in KEYS
    assert rows[0]["reason"] == "every key resting - asking one again"
    # the answer ended the episode: the key is usable again and nothing stays claimed
    assert K._pool_has_ready_key(ID, KEYS)
    assert not K._KAME_RETEST_INFLIGHT


def test_a_provider_that_really_refuses_is_asked_at_20_60_140_300_600_900_and_no_more(vt):
    provider = Provider(vt, healthy=False)
    ctx, _ = make_ctx(provider, keys=KEYS)
    rest_on_a_label()
    with pytest.raises(asyncio.TimeoutError):
        vt.run(K._kame_carousel(None, ctx), limit=1000)
    # intervals 20, 40, 80, 160, 300, 300 (Hermes): cumulative 20, 60, 140, 300, 600, 900
    assert [round(at) for at, _ in provider.asked] == [20, 60, 140, 300, 600, 900]
    # soonest first, one at a time: the first three rounds reach three different keys
    assert len({key for _, key in provider.asked[:3]}) == 3
    assert not K._KAME_RETEST_INFLIGHT


def test_a_refused_re_test_goes_through_the_normal_path_and_rests_the_key_again(vt):
    provider = Provider(vt, healthy=False)
    ctx, _ = make_ctx(provider, keys=KEYS)
    rest_on_a_label()
    with pytest.raises(asyncio.TimeoutError):
        vt.run(K._kame_carousel(None, ctx), limit=30)
    asked_key = provider.asked[0][1]
    kd = K._KAME_KEY_HEALTH[ID]["keys"][asked_key]
    assert kd["last_hold_kind"] == "daily" and kd["sick_until"] > time.time() + 3000
    assert not K._pool_has_ready_key(ID, KEYS)


def test_a_stated_deadline_is_never_re_tested(vt):
    provider = Provider(vt, healthy=False)
    ctx, _ = make_ctx(provider, keys=KEYS)
    K._get_identity_state(ID, KEYS)
    for key in KEYS:     # the provider said when: 10 minutes
        K._mark_key_health(ID, key, False, 600.0, "per_minute", sized_by="provider")
    assert K._kame_retest_candidate(ID, KEYS, time.time() + 100) is None
    with pytest.raises(asyncio.TimeoutError):
        vt.run(K._kame_carousel(None, ctx), limit=400)
    assert provider.asked == []


def test_nothing_is_re_tested_when_a_key_is_back_within_the_first_interval(vt):
    provider = Provider(vt, healthy=True)
    ctx, _ = make_ctx(provider, keys=KEYS)
    rest_on_a_label()
    with K._KAME_LOCK:
        K._KAME_KEY_HEALTH[ID]["keys"][KEYS[0]]["sick_until"] = time.time() + 15.0
    assert K._next_recovery_seconds(ID, KEYS) <= 20.0
    result = vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == "back"
    assert provider.asked[0][1] == KEYS[0] and 14.0 <= provider.asked[0][0] <= 19.5
    assert events("retest") == []


def test_money_auth_and_server_holds_are_never_picked():
    K._get_identity_state(ID, KEYS)
    K._mark_key_health(ID, KEYS[0], False, 3600.0, "insufficient_quota")
    K._mark_key_health(ID, KEYS[1], False, 3600.0, "auth")
    K._mark_key_health(ID, KEYS[2], False, 90.0, "server")
    assert K._kame_retest_candidate(ID, KEYS, time.time()) is None
    # a label-held key among them is the one
    keys = KEYS + [ALL_KEYS[3]]
    K._get_identity_state(ID, keys)
    K._KAME_NO_ANSWER_SINCE[ID] = time.time() - 10_000.0
    K._mark_key_health(ID, ALL_KEYS[3], False, 3600.0, "daily")
    assert K._kame_retest_candidate(ID, keys, time.time()) == ALL_KEYS[3]
    K._KAME_RETEST_INFLIGHT.clear()


def test_the_soonest_key_goes_first_and_a_reserve_key_goes_last(monkeypatch):
    K._get_identity_state(ID, KEYS)
    K._KAME_NO_ANSWER_SINCE[ID] = time.time() - 10_000.0
    for i, key in enumerate(KEYS):
        K._mark_key_health(ID, key, False, 3600.0, "daily")
        with K._KAME_LOCK:      # the daily rest is floored at an hour: set the order of the returns by hand
            K._KAME_KEY_HEALTH[ID]["keys"][key]["sick_until"] = time.time() + 3600.0 - 100 * i
    assert K._kame_retest_candidate(ID, KEYS, time.time()) == KEYS[2]
    K._KAME_RETEST_INFLIGHT.clear()
    monkeypatch.setattr(K, "_kame_is_reserve", lambda identity, key: key == KEYS[2])
    assert K._kame_retest_candidate(ID, KEYS, time.time()) == KEYS[1]


def test_who_sized_a_hold_is_stored_on_the_key_and_forgotten_on_an_answer():
    K._get_identity_state(ID, KEYS)
    K._mark_key_health(ID, KEYS[0], False, 300.0, "per_minute", sized_by="provider")
    kd = K._KAME_KEY_HEALTH[ID]["keys"][KEYS[0]]
    assert kd["last_hold_sized_by"] == "provider"
    K._mark_key_health(ID, KEYS[0], True)
    assert kd["last_hold_sized_by"] == ""
    K._mark_key_health(ID, KEYS[1], False, 30.0, "per_minute")
    assert K._KAME_KEY_HEALTH[ID]["keys"][KEYS[1]]["last_hold_sized_by"] == ""


def test_at_most_one_re_test_is_in_flight_per_identity(vt):
    # two replies ask the same pool; the first re-test takes 5 s to come back refused
    provider = Provider(vt, healthy=False, takes=5.0)
    ctx_a, _ = make_ctx(provider, keys=KEYS)
    ctx_b, _ = make_ctx(provider, keys=KEYS)
    rest_on_a_label()

    async def both():
        await asyncio.gather(K._kame_carousel(None, ctx_a), K._kame_carousel(None, ctx_b), return_exceptions=True)
    with pytest.raises(asyncio.TimeoutError):
        vt.run(both(), limit=24)
    assert len([at for at, _ in provider.asked if at < 24]) == 1
    assert len(events("retest")) == 1


def test_a_stop_during_the_wait_or_during_the_re_test_leaves_nothing_claimed(vt):
    provider = Provider(vt, healthy=False, takes=50.0)
    ctx, _ = make_ctx(provider, keys=KEYS)
    rest_on_a_label()
    with pytest.raises(asyncio.TimeoutError):
        vt.run(K._kame_carousel(None, ctx), limit=10)        # stopped while waiting
    assert not K._KAME_RETEST_INFLIGHT and provider.asked == []
    ctx2, _ = make_ctx(provider, keys=KEYS)
    with pytest.raises(asyncio.TimeoutError):
        vt.run(K._kame_carousel(None, ctx2), limit=30)       # the re-test goes out at 30 s, stopped at 40 s
    assert len(provider.asked) == 1
    assert not K._KAME_RETEST_INFLIGHT


def test_a_background_job_never_re_tests(vt):
    provider = Provider(vt, healthy=True)
    ctx, _ = make_ctx(provider, keys=KEYS, call_type="Utility")
    rest_on_a_label()
    with pytest.raises(asyncio.TimeoutError):
        vt.run(K._kame_carousel(None, ctx), limit=200)
    assert provider.asked == []
