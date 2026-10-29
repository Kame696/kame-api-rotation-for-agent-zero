"""1.8.2.9 review round (the review of 2026-10-28) - the findings, one test each.

H1  a short 503 storm must not destroy a compression (busy answers cost no quota: they do not
    count toward the request cap, the job waits on the probe clock inside its time budget, and a
    busy stop leaves no memo behind);
H2  a job nobody vetted (an agent tool, a mail handler, a third-party plugin) keeps the 1.8.2.8
    wait: only the named frames are budgeted;
M1  a stop or a message in the middle of the circle wait gives the heading back;
M2  the wait line counts the keys of the model being waited on;
L3  a log that cannot be rotated stops growing; L4 a row never saves a separator or the old endpoint;
L6 a failing view does not take the status report
    down; L7 the first busy answer after a window reopens it once (the key still rests 1 s);
L2  the billing paragraph names the fallback walk of background jobs.

Second review: N1 a quota refusal costs the job a request only when no other key is ready (and a
rest stop, not a busy or spent one, writes the memo); N3 a short blip keeps the 1.8.2.8 cadence for
the job's first probe clock; N2 the stop text counts the requests really sent.

A virtual clock (kame_vclock.py); no Agent Zero, no network.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, Refusal, StopTest, clean, fake_models, make_ctx, streaming, _rest_every_key,
)
from test_v1_8_2_9_aux_budget import calm as aux_calm, overloaded, requests, util_ctx  # noqa: F401
from test_v1_8_2_9_circle_back import (  # noqa: F401
    World, calm as circle_calm, primary_ctx, world,
)
from test_v1_8_2_9_status_line import (  # noqa: F401
    FakeAgent, PLAIN, calm as status_calm, rest_all,
)
from test_v1_8_2_9_page_store import loaded, run_store  # noqa: F401
from test_v1_8_2_9_silent_two_models import world as silent_world  # noqa: F401
from kame_vclock import vt  # noqa: F401
import kame_engine as K
import kame_journal as J

ROOT = Path(__file__).resolve().parents[1]
NEXT = "google/gemini-3.7-flash"


# Stand-ins for Agent Zero's own frames: the carousel is awaited from inside each one.
async def summarize_messages(ctx):
    return await K._kame_carousel(None, ctx)


async def update_behaviour(ctx):                 # plugins/_memory/tools/behaviour_adjustment.py
    return await K._kame_carousel(None, ctx)


def storm_until(vt, seconds, log=None):
    """An attempt that answers 503 until ``seconds`` of virtual time have passed, then answers."""
    async def attempt(owner, key, ctx):
        if log is not None:
            log.append((vt.elapsed(), key))
        await asyncio.sleep(0.01)
        if vt.elapsed() < seconds:
            raise overloaded()
        return ("the real summary", "")
    return attempt


# --- H1 ---------------------------------------------------------------------------------------

def test_h1_a_twelve_second_storm_does_not_cost_a_compression_its_summary(vt):
    log = []
    ctx = util_ctx(storm_until(vt, 12.0, log))
    result = vt.run(summarize_messages(ctx), limit=600)
    assert result[0] == "the real summary"
    assert vt.elapsed() < 60.0
    assert K._KAME_AUX_STOPPED["compression"] == 0
    assert K._KAME_AUX_MEMO == {}                        # a 12 s storm leaves no memo behind


def test_h1_busy_answers_do_not_count_toward_the_six_request_cap(vt):
    log = []
    ctx = util_ctx(storm_until(vt, 250.0, log))
    result = vt.run(summarize_messages(ctx), limit=600)  # asked more than six times, still answered
    assert result[0] == "the real summary"
    assert len(log) > 6 and vt.elapsed() < 300.0


def test_h1_a_storm_longer_than_the_budget_stops_the_job_without_a_memo(vt):
    log = []
    ctx = util_ctx(storm_until(vt, 10_000.0, log))
    with pytest.raises(K.KameAuxTimeout):
        vt.run(summarize_messages(ctx), limit=900)
    assert vt.elapsed() <= 301.0
    assert K._KAME_AUX_STOPPED["compression"] == 1
    assert K._KAME_AUX_MEMO == {}                        # busy is not a spent quota: no memo
    # the model is back: the next important job is asked, not turned away
    nxt = util_ctx(storm_until(vt, 0.0))
    assert vt.run(summarize_messages(nxt), limit=60)[0] == "the real summary"


def test_h1_a_busy_wait_follows_the_probe_clock_not_one_ask_per_key_round_trip(vt):
    log = []
    ctx = util_ctx(storm_until(vt, 10_000.0, log))
    with pytest.raises(K.KameAuxTimeout):
        vt.run(summarize_messages(ctx), limit=900)
    # the first probe clock keeps the 1.8.2.8 cadence (N3); after it, two asks (to prove it busy) per 20 s
    assert len([t for t, _k in log if t > 21.0]) <= 2 * (300 // 20 + 1)


def test_h1_a_title_keeps_its_three_request_cap_and_its_memo(vt):
    from test_v1_8_2_9_aux_budget import generate_name
    log = []
    ctx = util_ctx(storm_until(vt, 10_000.0, log))
    with pytest.raises(K.KameAuxTimeout):
        vt.run(generate_name(ctx), limit=120)
    assert len(log) <= 3 and vt.elapsed() <= 30.0
    assert list(K._KAME_AUX_MEMO) and all(klass == "cosmetic" for _i, klass in K._KAME_AUX_MEMO)


# --- H2 ---------------------------------------------------------------------------------------

def test_h2_an_unnamed_job_is_never_budgeted():
    assert K._kame_aux_task() == ("utility", False)
    ctx = util_ctx(streaming({}))
    assert K._kame_aux_begin(ctx) is None


def test_h2_an_agent_tool_waits_for_keys_that_are_back_in_400_seconds(vt):
    ctx = util_ctx(streaming({}))
    _rest_every_key(400, "per_minute")
    result = vt.run(update_behaviour(ctx), limit=900)
    assert result[0].startswith("answer from ")
    assert 399.0 < vt.elapsed() < 460.0


def test_h2_an_agent_tool_rides_out_a_twelve_second_storm(vt):
    ctx = util_ctx(storm_until(vt, 12.0))
    result = vt.run(update_behaviour(ctx), limit=900)
    assert result[0] == "the real summary"


def test_h2_an_unnamed_job_is_still_counted_in_the_tally(vt):
    ctx = util_ctx(streaming({}))
    vt.run(update_behaviour(ctx), limit=60)
    assert ctx.get("aux_task") in (None, "utility")      # counted as "utility", never as a vetted job


# --- M1 ---------------------------------------------------------------------------------------

class Intervened(Exception):
    pass


def _only_waiting(headings):
    return [h for h in headings if "asked again in" in h]


def test_m1_a_stop_in_the_middle_of_the_circle_wait_gives_the_heading_back(world, vt):
    agent = FakeAgent()
    ctx = primary_ctx(world)
    with pytest.raises(asyncio.TimeoutError):
        vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=10)
    assert _only_waiting(agent.headings()), agent.headings()       # the wait was said ...
    assert agent.item.heading == PLAIN                              # ... and is not left standing


def test_m1_a_message_in_the_middle_of_the_circle_wait_gives_the_heading_back(world, vt, monkeypatch):
    async def honor():
        if vt.elapsed() >= 8.0:
            raise Intervened()

    monkeypatch.setattr(K, "_kame_honor_intervention", honor)
    agent = FakeAgent()
    ctx = primary_ctx(world)
    with pytest.raises(Intervened):
        vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert _only_waiting(agent.headings()), agent.headings()
    assert agent.item.heading == PLAIN


def test_m1_a_message_in_the_middle_of_the_exhaustion_sleep_gives_the_heading_back(vt, monkeypatch):
    rest_all(KEYS[:1], 2700)

    async def honor():
        if vt.elapsed() >= 3.0:
            raise Intervened()

    monkeypatch.setattr(K, "_kame_honor_intervention", honor)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    with pytest.raises((Intervened, asyncio.TimeoutError)):
        vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=10)
    assert any("waiting on a key" in h for h in agent.headings())
    assert agent.item.heading == PLAIN


def test_m1_a_stop_in_the_middle_of_the_race_line_gives_the_heading_back(silent_world, vt):
    K.set_fallback_models("google/gemini-3.7-flash")
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({k: 30.0 for k in KEYS}))
    silent_world.delay["google/gemini-3.7-flash"] = 30.0      # the raced model is silent too
    with pytest.raises(asyncio.TimeoutError):
        vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=3.0)
    assert any("races it" in h for h in agent.headings()), agent.headings()
    assert agent.item.heading == PLAIN


# --- M2 ---------------------------------------------------------------------------------------

def test_m2_the_wait_line_counts_the_keys_of_the_model_waited_on(vt):
    rest_all(KEYS[:1], 2700)
    K._get_identity_state("gemini:gemini-3.5-flash", ["AIzaSyLINE-other-000000", "AIzaSyLINE-other-111111",
                                                      "AIzaSyLINE-other-222222"])   # three healthy keys
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    with pytest.raises(asyncio.TimeoutError):
        vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=3)
    waiting = [h for h in agent.headings() if "waiting on a key" in h]
    assert waiting and "0 of 1 keys ready" in waiting[0], waiting
    assert "3 of 4" not in waiting[0]
    assert K.status_view()["totals"]["phrase"] == "3 of 4 keys ready"   # the chip's count is the global one


# --- L3 ---------------------------------------------------------------------------------------

def test_l3_a_log_that_cannot_rotate_stops_growing_past_twice_its_ceiling(tmp_path, monkeypatch):
    monkeypatch.setenv("KAME_DATA_DIR", str(tmp_path))
    J._reset_for_tests()
    try:
        big = tmp_path / J.CALLS_FILE
        big.write_text("y" * 2500 + "\n", encoding="utf-8")
        monkeypatch.setattr(J.os, "replace", lambda *a, **k: (_ for _ in ()).throw(PermissionError("in use")))
        before = big.stat().st_size
        for i in range(130):
            J._append(J.CALLS_FILE, 1000, {"n": i, "pad": "x" * 40})
        assert big.stat().st_size == before               # nothing landed: the disk stays bounded
    finally:
        J._reset_for_tests()


def test_l3_a_log_that_can_rotate_again_takes_rows_again(tmp_path, monkeypatch):
    monkeypatch.setenv("KAME_DATA_DIR", str(tmp_path))
    J._reset_for_tests()
    try:
        big = tmp_path / J.CALLS_FILE
        big.write_text("y" * 2500 + "\n", encoding="utf-8")
        real = J.os.replace
        monkeypatch.setattr(J.os, "replace", lambda *a, **k: (_ for _ in ()).throw(PermissionError("in use")))
        assert J._append(J.CALLS_FILE, 1000, {"n": 0}) is False
        monkeypatch.setattr(J.os, "replace", real)
        assert J._append(J.CALLS_FILE, 1000, {"n": 1}) is True
        assert (tmp_path / "calls.1.jsonl").exists()
    finally:
        J._reset_for_tests()


# --- L4 ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("field, value", [("name", "gpt-5, other/model"), ("name", "a;b"), ("name", "a\r\nb"),
                                          ("api_base", "https://x/v1, evil/model"), ("api_base", "https://x\nline")])
def test_l4_a_separator_inside_a_row_is_not_saved(field, value):
    row = {"provider": "openai", "name": "gpt-5.4-nano", "api_base": ""}
    row[field] = value
    out = K.merge_fallback_rows([row])
    assert out["saved"] == 0 and out["incomplete"] == 1 and out["text"] == ""


def test_l4_changing_a_rows_provider_clears_its_endpoint():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    out = run_store(
        "await store.onOpen(); store.cleanup(); store.addRow(); const row = store.rows[0];"
        "store.setProvider(row, 'openai'); row.api_base = 'https://my-openai-proxy/v1';"
        "store.setProvider(row, 'openai'); const same = row.api_base;"
        "store.setProvider(row, 'google'); return { same, changed: row.api_base };",
        loaded())
    assert out == {"same": "https://my-openai-proxy/v1", "changed": ""}


# --- second review: N1, N2, N3 ------------------------------------------------------------------

def limited(retry_after="30"):
    return Refusal("Error code: 429", 429, {"error": {"message": "slow down"}}, {"retry-after": retry_after})


def six_keys_limited(vt, log):
    """The first six distinct keys asked answer 429; the other two are healthy."""
    seen = []

    async def attempt(owner, key, ctx):
        log.append((vt.elapsed(), key))
        await asyncio.sleep(0.01)
        if key not in seen:
            seen.append(key)
        if seen.index(key) < 6:
            raise limited()
        return ("the real summary", "")
    return attempt


def test_n1_six_limited_keys_do_not_stop_a_compression_while_two_keys_are_healthy(vt):
    log = []
    ctx = util_ctx(six_keys_limited(vt, log))
    result = vt.run(summarize_messages(ctx), limit=600)
    assert result[0] == "the real summary" and vt.elapsed() < 2.0     # 1.8.2.8: 0.07 s from key 7
    assert len(log) == 7
    assert K._KAME_AUX_MEMO == {}


def test_n1_a_title_job_in_the_same_case_still_stops_at_three_requests(vt):
    from test_v1_8_2_9_aux_budget import generate_name
    log = []
    ctx = util_ctx(six_keys_limited(vt, log))
    with pytest.raises(K.KameAuxTimeout):
        vt.run(generate_name(ctx), limit=120)
    assert len(log) == 3


def test_n1_every_key_limited_for_five_seconds_still_gets_its_summary(vt):
    async def attempt(owner, key, ctx):
        await asyncio.sleep(0.01)
        if vt.elapsed() < 5.0:
            raise limited("5")
        return ("the real summary", "")

    ctx = util_ctx(attempt)
    result = vt.run(summarize_messages(ctx), limit=600)
    assert result[0] == "the real summary" and vt.elapsed() < 70.0     # 1.8.2.8: 65.7 s
    assert K._KAME_AUX_MEMO == {}


def test_n1_a_stop_because_every_key_rests_past_the_budget_keeps_its_memo(vt):
    ctx = util_ctx(streaming({}))
    _rest_every_key()                                    # an hour: past 300 s
    with pytest.raises(K.KameAuxTimeout):
        vt.run(summarize_messages(ctx), limit=60)
    assert ("google:gemini/gemini-3.8-flash", "important") in K._KAME_AUX_MEMO


def test_n3_a_one_second_blip_costs_a_background_job_seconds_not_a_probe_clock(vt):
    ctx = util_ctx(storm_until(vt, 1.0))
    result = vt.run(summarize_messages(ctx), limit=600)
    assert result[0] == "the real summary"
    assert vt.elapsed() < 5.0                            # 1.8.2.8: 3.2 s; the probe clock made it 20 s


def test_n3_after_its_first_twenty_seconds_an_endless_storm_is_asked_twice_per_clock(vt):
    log = []
    ctx = util_ctx(storm_until(vt, 10_000.0, log))
    with pytest.raises(K.KameAuxTimeout):
        vt.run(summarize_messages(ctx), limit=900)
    late = [t for t, _k in log if t > 21.0]
    assert len(late) <= 2 * (280 // 20 + 1), len(late)


def test_n2_the_stop_text_and_the_event_name_the_requests_really_sent(vt):
    import re
    log = []
    ctx = util_ctx(storm_until(vt, 10_000.0, log))
    with pytest.raises(K.KameAuxTimeout) as caught:
        vt.run(summarize_messages(ctx), limit=900)
    said = int(re.search(r"after (\d+) tries", str(caught.value)).group(1))
    assert said == len(log) and said > 6
    event = next(e for e in K._KJ.EVENTS.recent(30) if e["kind"] == "gave_up")
    assert f"after {len(log)} tries" in event["reason"]


# --- third review: N6, important jobs are bounded by time, not by a count of waits ------------------

def exhausted():
    return Refusal("Error code: 429", 429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                                      "message": "Resource has been exhausted (e.g. check quota)."}})


def refused_until(vt, seconds, make, log=None):
    async def attempt(owner, key, ctx):
        if log is not None:
            log.append(vt.elapsed())
        await asyncio.sleep(0.01)
        if vt.elapsed() < seconds:
            raise make()
        return ("the real summary", "")
    return attempt


@pytest.mark.parametrize("make", [exhausted, lambda: limited("1")], ids=["resource_exhausted", "retry_after_1s"])
def test_n6_a_sixty_second_429_storm_on_every_key_still_gets_its_summary(vt, make):
    log = []
    ctx = util_ctx(refused_until(vt, 60.0, make, log))
    result = vt.run(summarize_messages(ctx), limit=600)
    assert result[0] == "the real summary"              # 1.8.2.8: 69.4 s / 68.3 s; the six-waits cap stopped it at 24-38 s
    assert vt.elapsed() < 100.0
    assert K._KAME_AUX_MEMO == {}


def test_n6_an_endless_short_rest_storm_stops_at_its_time_bound_and_sends_no_more_than_1_8_2_8(vt, monkeypatch):
    log = []
    ctx = util_ctx(refused_until(vt, 10_000.0, lambda: limited("1"), log))
    with pytest.raises(K.KameAuxTimeout):
        vt.run(summarize_messages(ctx), limit=900)
    assert 270.0 <= vt.elapsed() <= 300.5
    budgeted = len(log)
    monkeypatch.setattr(K, "_KAME_AUX_UNBOUNDED", True)  # 1.8.2.8: no budget at all
    ref = []
    ctx = util_ctx(refused_until(vt, 10_000.0, lambda: limited("1"), ref))
    start = vt.elapsed()
    with pytest.raises(TimeoutError):
        vt.run(summarize_messages(ctx), limit=300.0)
    assert budgeted <= len(ref), (budgeted, len(ref))


# --- L6 ---------------------------------------------------------------------------------------

def test_l6_a_failing_status_view_does_not_take_the_status_report_down(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("the view is having a bad day")

    monkeypatch.setattr(K, "status_view", boom)
    report = K.pool_report()
    for name in ("tally", "events", "settings", "speed", "replies", "quota_use", "generated_at"):
        assert name in report, name
    assert report["view"] == {}


# --- L7 ---------------------------------------------------------------------------------------

def test_l7_the_first_busy_answer_after_a_window_reopens_it_once_and_the_key_rests_one_second(vt):
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    now = time.time()
    K._KAME_BUSY_UNTIL[ID] = now - 5.0                   # the window ended five seconds ago
    K._KAME_BUSY_WINDOW[ID] = 30.0
    calls = []

    async def attempt(owner, key, ctx):
        calls.append(key)
        if len(calls) == 1:
            raise overloaded()
        raise StopTest()

    ctx, _ = make_ctx(attempt, keys=KEYS[:4])
    with pytest.raises(StopTest):
        vt.run(K._kame_carousel(None, ctx), limit=30)
    assert K._KAME_BUSY_UNTIL[ID] > time.time()          # reopened by that one knock
    with K._KAME_LOCK:
        rested = K._KAME_KEY_HEALTH[ID]["keys"][calls[0]]["sick_until"] - time.time()
    assert rested <= 1.5                                 # a 503 still rests its key for a second, no more


# --- L2 and the texts -------------------------------------------------------------------------

def test_l2_the_billing_paragraph_names_the_walk_down_the_list():
    for path in ("README.md", "deliverables/release-1.8.2.9.md", "CHANGELOG.md"):
        base = ROOT if path in ("README.md", "CHANGELOG.md") else ROOT.parent
        if not (base / path).is_file():
            continue
        text = (base / path).read_text(encoding="utf-8")
        flat = " ".join(text.split())
        assert "down your list" in flat or "down your fallback list" in flat, path
        assert "again before it gives up" in flat, path       # N4: the walk is twice, not "once"


def test_the_shipped_texts_no_longer_claim_what_the_review_proved_wrong():
    for path in ("README.md", "CHANGELOG.md"):
        flat = " ".join((ROOT / path).read_text(encoding="utf-8").split())
        assert "every other job sends at most 6 requests and waits at most 5 minutes" not in flat, path
        assert "6 requests that can spend quota" not in flat, path
        assert "fewer requests" not in flat, path       # N8: not measured; "no more than 1.8.2.8" is      # N6: bounded by time, not by a count
    path = ROOT.parent / "deliverables" / "release-1.8.2.9.md"
    if path.is_file():
        release = " ".join(path.read_text(encoding="utf-8").split())
        assert "every other job at most 6 requests and 5 minutes" not in release
        assert "6 requests that can spend quota" not in release
