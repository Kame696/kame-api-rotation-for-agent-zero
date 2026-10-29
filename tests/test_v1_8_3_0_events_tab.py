"""1.8.3.0 (A7, A8) - the Events tab and the Background jobs tab say what the Hermes pages say.

Hermes' Events page: tallies that are also the filter ("in the last 4m", "KAME rotating", "providers
refusing"), rows with a coloured rail, a relative time, the label, the reason with what KAME did next, a
muted tail (model, fingerprint, HTTP code, rest, where the rest came from), a click on a failure opens
what the provider sent (already scrubbed), a "proof of life" line, the legend "Reading this list", "Last
replies" and "Still waiting". The Background jobs page is the same list for the side jobs, one filter per
job. Logic runs in node over the real report; markup is read as text.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from test_v1_8_2_9_page_store import loaded, run_store, report, fresh  # noqa: F401
from test_v1_8_2_7_speed_fallback import clean  # noqa: F401
import kame_engine as K
import kame_journal as J

ROOT = Path(__file__).resolve().parent.parent
PANEL = (ROOT / "webui" / "kame-panel.html").read_text(encoding="utf-8")
STORE = (ROOT / "webui" / "kame-panel-store.js").read_text(encoding="utf-8")


def with_events(events, **extra):
    base = loaded()
    for body in base.values():
        body["events"] = events
        body.update(extra)
    return base


def ev(seq, kind, **kw):
    row = {"seq": seq, "at": 1000.0 - seq, "kind": kind, "identity": "gemini:gemini-3.5-flash", "key": "ab12cd34",
           "reason": "", "code": None, "seconds": None, "detail": "", "sized_by": "", "job": "chat", "task": ""}
    row.update(kw)
    return row


# -- the data the page needs --------------------------------------------------------------------

def test_a_cut_answer_that_was_continued_has_its_own_kind_and_counts_as_kame_working():
    assert J.STITCH == "stitch" and "stitch" in J.GOOD_KINDS
    row = J.EVENTS.add("stitch", reason="cut answer continued")
    assert row["kind"] == "stitch"


def test_an_event_remembers_which_background_job_made_the_call():
    row = J.EVENTS.add("rotation", job="utility", task="title_generation")
    assert row["task"] == "title_generation"
    assert J.EVENTS.add("rotation")["task"] == ""


def test_the_report_carries_the_call_counters_the_proof_of_life_line_reads():
    out = K.pool_report()
    assert set(out["counters"]) >= {"calls", "last_call_at"}
    assert out["counters"]["calls"] >= 0


# -- the words ----------------------------------------------------------------------------------

def test_hermes_kinds_for_the_kinds_agent_zero_records_under_other_names():
    out = run_store(
        "await store.refresh();"
        "return ['model_switch','backup','switch','rotation','x'].map(k => store.eventKind({kind: k}));", loaded())
    assert out == ["handover", "switch", "switch", "rotation", "x"]


def test_each_kind_has_hermes_label_tone_and_a_meaning_sentence():
    out = run_store(
        "await store.refresh();"
        "return ['rotation','switch','quarantine','invalid_key','denied_model','storm','stream_drop','wait','recovery',"
        "        'surfaced','setting','model_switch','gave_up','stitch'].map(k => [k, store.eventLabel({kind: k}).word,"
        "        store.eventLabel({kind: k}).tone, store.eventMeaning({kind: k}).length > 20]);", loaded())
    labels = {k: (w, t) for k, w, t, long in out}
    assert labels["rotation"] == ("Key rests", "plain") and labels["quarantine"] == ("Key benched", "bad")
    assert labels["invalid_key"] == ("Bad key", "bad") and labels["denied_model"] == ("Not for this model", "warn")
    assert labels["model_switch"] == ("Fallback", "warn") and labels["gave_up"] == ("Stopped waiting", "bad")
    assert labels["surfaced"] == ("Passed to Agent Zero", "bad") and labels["stitch"] == ("Answer continued", "good")
    assert labels["wait"] == ("All keys resting", "warn") and labels["switch"] == ("Next key", "good")
    assert all(long for *_x, long in out)


def test_the_sentence_is_the_reason_then_an_arrow_then_what_kame_did_next():
    out = run_store(
        "await store.refresh();"
        "return [store.eventSentence({kind: 'rotation', reason: 'per_minute'}),"
        "        store.eventSentence({kind: 'switch', reason: ''}),"
        "        store.eventSentence({kind: 'surfaced', reason: 'timeout'}),"
        "        store.eventSentence({kind: 'rotation', reason: 'rate_limit', sized_by: 'backoff.2'}),"
        "        store.eventSentence({kind: 'quarantine', reason: 'brand new reason'}),"
        "        store.eventSentence({kind: 'rotation', reason: 'other', code: 400})];", loaded())
    assert out[0] == "a per-minute rate limit → the next request uses another key"
    assert out[1] == ""
    assert out[2].startswith("the provider timed out twice in a row — Agent Zero takes over the retry")
    assert out[3].startswith("a rate limit with no quota number — usually the provider is busy, not your quota")
    assert out[4] == "brand new reason → the key sits out, the others carry on"
    assert out[5].startswith("the provider rejected the request itself")


def test_the_job_prefixes_the_sentence_with_the_hermes_style_task_words():
    out = run_store(
        "await store.refresh(); return ['title_generation','compression','memory_recall','utility','something_new']"
        ".map(t => store.taskWord(t));", loaded())
    assert out[0] == "Naming this chat" and out[1] == "Shrinking a long conversation"
    assert out[4] == "something new"


def test_where_a_rest_came_from_is_a_chip_good_when_the_provider_said_it():
    out = run_store(
        "await store.refresh(); return ['header','catalog','backoff.3','dropped','zzz',''].map(s => store.sizedChip({sized_by: s}));",
        loaded())
    assert out[0]["word"] == "header" and out[0]["good"] is True
    assert out[0]["title"] == "The provider said how long. This wait is its number, not ours."
    assert out[2]["word"] == "bare-429 backoff" and out[2]["good"] is False
    assert out[2]["title"] == "Nothing in the response said how long. This wait is a fallback."
    assert out[3]["word"] == "guess" and out[4]["word"] == "unknown source" and out[5] is None


# -- the list and its filters -------------------------------------------------------------------

EVENTS = [
    ev(1, "switch"), ev(2, "rotation", reason="per_minute", code=429, seconds=30, detail="quota exceeded"),
    ev(3, "model_switch", reason="x"), ev(4, "quarantine", reason="daily", seconds=3600),
    ev(5, "rotation", job="utility", task="title_generation", reason="server"),
]


def test_the_tallies_are_also_the_filter_and_background_calls_are_kept_apart():
    out = run_store(
        "await store.refresh(); const t = store.tallies;"
        "const first = { all: t.all.count, did: t.did.count, wrong: t.wrong.count, bg: t.background.count };"
        "store.setEventView('did'); const did = store.shownEvents.map(e => e.seq);"
        "store.setEventView('wrong'); const wrong = store.shownEvents.map(e => e.seq);"
        "store.setEventView('all'); const all = store.shownEvents.map(e => e.seq);"
        "return { first, did, wrong, all, labels: [t.all.label, t.did.label, t.wrong.label, t.background.label] };",
        with_events(EVENTS, generated_at=1000.0 + 240))
    assert out["first"] == {"all": 4, "did": 2, "wrong": 2, "bg": 1}
    assert out["did"] == [1, 3] and out["wrong"] == [2, 4] and out["all"] == [1, 2, 3, 4]
    assert out["labels"][1:3] == ["KAME rotating", "providers refusing"]
    assert out["labels"][3] == "background jobs →"
    assert out["labels"][0].startswith("in the last ")


def test_empty_states_say_nothing_of_that_kind_or_nothing_recorded():
    none = run_store("await store.refresh(); return store.eventsEmpty;", with_events([]))
    assert none == "Nothing recorded yet. Every rotation, refusal, cut answer and continuation appears here as it happens."
    kind = run_store("await store.refresh(); store.setEventView('wrong'); return store.eventsEmpty;",
                     with_events([ev(1, "switch")]))
    assert kind == "Nothing of that kind yet."


def test_only_a_failure_can_be_opened_and_the_inspector_closes():
    out = run_store(
        "await store.refresh(); const rows = store.shownEvents;"
        "const can = rows.map(e => store.canInspect(e));"
        "store.inspect(rows[1]); const open = store.inspecting && store.inspecting.seq;"
        "const head = store.inspectorHead; store.closeInspect();"
        "return { can, open, head, closed: store.inspecting };", with_events(EVENTS))
    assert out["can"] == [False, True, False, False]       # only a failure that kept what the provider said
    assert out["open"] == 2 and out["closed"] is None
    assert out["head"]["label"] == "Key rests"


def test_a_row_says_how_long_ago_in_hermes_words():
    out = run_store(
        "await store.refresh(); const at = 1000; store.data.generated_at = at; store.fetchedAt = 1; const ago = (ms) => { store.now = 1 + ms; return store.eventAgo({at}); };"
        "return [ago(10e3), ago(60e3), ago(300e3), ago(3 * 3600e3), ago(2 * 86400e3)];", loaded())
    assert out == ["just now", "a minute ago", "5m ago", "3h ago", "2d ago"]


def test_the_row_tail_names_the_model_the_fingerprint_the_code_and_the_rest():
    out = run_store(
        "await store.refresh(); return [store.eventTail({kind: 'rotation', identity: 'gemini:gemini-3.5-flash', key: 'ab12cd34',"
        "  code: 429, seconds: 30}), store.eventTail({kind: 'recovery', seconds: 61}), store.eventTail({kind: 'wait', seconds: 5})];",
        loaded())
    assert out[0] == {"model": "gemini-3.5-flash", "key": "ab12cd34", "code": "HTTP 429", "rest": "rested 30s"}
    assert out[1]["rest"] == "after 1m 01s" and out[2]["rest"] == "for 5s"


# -- proof of life ------------------------------------------------------------------------------

def test_the_heartbeat_tells_a_quiet_stretch_from_a_stopped_plugin():
    def line(counters, **extra):
        body = with_events([], counters=counters, **extra)
        return run_store("await store.refresh(); return store.heartbeat;", body)

    assert line({"calls": 0, "last_call_at": 0}, generated_at=1000.0) == \
        "KAME is running and has not been asked to route a call yet in this process."
    one = line({"calls": 1, "last_call_at": 990.0}, generated_at=1000.0)
    assert one.startswith("KAME is running. 1 call routed, the last one ") and one.endswith(
        "An empty list above means nothing failed.")
    many = line({"calls": 5, "last_call_at": 0}, generated_at=1000.0)
    assert many == "KAME is running. 5 calls routed. An empty list above means nothing failed."


def test_a_reading_that_stopped_updating_is_called_old_and_not_current():
    body = with_events([], counters={"calls": 3, "last_call_at": 900.0}, generated_at=1000.0)
    out = run_store("await store.refresh(); store.now = store.fetchedAt + 120e3; return store.heartbeat;", body)
    assert out.startswith("This reading is ") and "Everything below is what it last knew, not what is happening now." in out


# -- still waiting ------------------------------------------------------------------------------

def test_a_call_waiting_a_minute_or_more_gets_a_still_waiting_line_on_its_own_tab():
    flights = [
        {"job": "chat", "identity": "gemini:gemini-3.5-flash", "started": 0, "refusals": 294, "last": "server", "seconds": 3420},
        {"job": "utility", "task": "title_generation", "identity": "gemini:flash-lite", "started": 0, "refusals": 0, "last": "", "seconds": 75},
    ]
    out = run_store("await store.refresh(); return { main: store.stillWaiting('main'), side: store.stillWaiting('secondary') };",
                    with_events([], in_flight=flights))
    assert out["main"] == [
        "chat · gemini-3.5-flash — no answer for 57m 00s, 294 refusals so far; last: the provider had a server error. KAME keeps trying, as configured."]
    assert out["side"] == ["Naming this chat · flash-lite — no answer for 1m 15s. KAME keeps trying, as configured."]


# -- the legend ---------------------------------------------------------------------------------

def test_the_legend_lists_every_kind_agent_zero_records_in_hermes_order_with_its_sentence():
    out = run_store("await store.refresh(); return store.legendKinds.map(k => k.word);", loaded())
    assert out == ["Not for this model", "Stopped waiting", "Bad key", "Key benched", "Answered on retry", "Key rests",
                   "Setting changed", "Answer continued", "Provider outage", "Answer cut", "Fallback",
                   "Passed to Agent Zero", "Next key", "All keys resting"]


# -- the background tab -------------------------------------------------------------------------

def test_the_background_tab_has_one_filter_per_job_and_falls_back_to_all():
    out = run_store(
        "await store.refresh(); const names = store.bgTasks.map(t => [t.name, t.count]);"
        "store.setBgTask('title_generation'); const only = store.bgShown.map(e => e.seq);"
        "store.setBgTask('gone'); const back = store.bgShown.length; return { names, only, back, active: store.bgActive };",
        with_events(EVENTS + [ev(6, "rotation", job="compress", task="compression", reason="server")]))
    assert out["names"] == [["title_generation", 1], ["compression", 1]]
    assert out["only"] == [5] and out["back"] == 2 and out["active"] == "all"


def test_the_background_empty_state_is_the_hermes_sentence_for_agent_zero_jobs():
    assert "No auxiliary model calls yet. Titles, compression, memory and the other background jobs appear here as Agent Zero runs them." in PANEL


# -- the markup ---------------------------------------------------------------------------------

@pytest.mark.parametrize("words", [
    "Reading this list", "Each word on this list, and what it means.", "What the provider actually sent, with anything key-shaped removed before it was stored.",
    "Click to see what the provider actually said.", "see the error", "Last replies", "Still waiting",
    "Auxiliary model calls", "Click a task to see only its calls.",
])
def test_the_events_and_background_markup_carries_the_sentence(words):
    assert words in PANEL or words in STORE, words
