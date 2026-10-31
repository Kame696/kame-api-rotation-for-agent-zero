"""1.8.3.0 (A9) - the Overview, the header, the notes and the credit say what the Hermes page says.

Hermes' page: a header (name, version, build fingerprint, the count of keys ready, "N to replace", "live" or
"reading 2m 05s old"), notes above the page (an incomplete install, not rotating, an old reading, keys that
left rotation, keys just refused), the three sentences, the rings, "What KAME did", "Quick controls" built
from the same rows as the Settings tab, the "Details" fold with the counters of this process, a footer and
the "Made by KAME" card. Logic runs in node; markup is read as text.
"""
from __future__ import annotations

from pathlib import Path

from test_v1_8_2_9_page_store import loaded, run_store, fresh  # noqa: F401
from test_v1_8_2_7_speed_fallback import clean  # noqa: F401
import kame_engine as K

ROOT = Path(__file__).resolve().parent.parent
PANEL = (ROOT / "webui" / "kame-panel.html").read_text(encoding="utf-8")
VIEW = (ROOT / "webui" / "kame-view.js").read_text(encoding="utf-8")
REPO = "https://github.com/Kame696/kame-api-rotation-for-agent-zero"


def with_data(**extra):
    base = loaded()
    for body in base.values():
        body.update(extra)
    return base


# -- Quick controls ------------------------------------------------------------------------------

def test_quick_controls_are_the_settings_rows_in_hermes_order():
    out = run_store("await store.refresh(); return store.quickCards[0].rows.map(m => m.name);", loaded())
    assert out == ["hedge_mode", "model_switch", "reply_notice"]
    timed = run_store("await store.refresh(); return store.quickCards[0].rows.map(m => m.name);", loaded(model_switch="timed"))
    assert timed == ["hedge_mode", "model_switch", "failover_after_seconds", "reply_notice"]


def test_quick_controls_draw_the_same_row_as_the_settings_tab():
    quick = PANEL.split('<div class="kame-card-title">Quick controls</div>', 1)[1].split("All settings", 1)[0]
    assert "When to change it: " in quick and "kame-tag" in quick and "saveNumber" in quick


# -- the header ----------------------------------------------------------------------------------

def test_the_header_shows_the_build_beside_the_version_and_whether_the_reading_is_live():
    out = run_store(
        "await store.refresh(); const live = store.headerStatus.age; store.now = store.fetchedAt + 125e3;"
        "return { live, old: store.headerStatus.age, stale: store.headerStatus.stale, build: store.buildFingerprint };",
        with_data(build={"fingerprint": "abc123def456", "complete": True}))
    assert out["live"] == "live" and out["old"] == "reading 2m 05s old" and out["stale"] is True
    assert out["build"] == "abc123def456"


def test_the_page_says_what_it_reads_while_nothing_has_loaded():
    assert "Reading the pool…" in PANEL
    assert "This page reads Agent Zero’s status. If Agent Zero has just started, give it a moment." in PANEL


# -- the notes above the page ---------------------------------------------------------------------

def notes(**extra):
    return run_store("await store.refresh(); return store.notes.map(n => [n.id, n.tone, n.text]);", with_data(**extra))


def test_an_incomplete_install_is_the_loudest_note():
    out = notes(build={"fingerprint": "x", "complete": False, "missing": ["core/a.py", "core/b.py"]})
    assert out[0][0] == "incomplete" and out[0][1] == "bad"
    assert out[0][2] == ("This install is incomplete — KAME is not rotating. Missing: core/a.py, core/b.py. "
                         "Re-deploy the whole plugin directory. A copy that does not recurse drops core/ "
                         "and leaves a plugin that registers, reports itself active, and does nothing.")


def test_a_plugin_that_is_loaded_but_not_rotating_says_why_it_is_inert():
    out = notes(active=False, reason="layer 3")
    ids = [n[0] for n in out]
    assert "not-rotating" in ids
    text = dict((n[0], n[2]) for n in out)["not-rotating"]
    assert text.startswith("KAME is loaded but every call keeps the key Agent Zero resolved")


def test_an_old_reading_is_called_old_with_the_hermes_sentence():
    out = run_store("await store.refresh(); store.now = store.fetchedAt + 125e3; return store.notes.map(n => [n.id, n.text]);", loaded())
    stale = dict(out)["stale"]
    assert stale == ("Nothing has been read for 2m 05s. The numbers below are the last reading, not the current one — "
                     "the backend is probably restarting.")


def test_keys_that_left_rotation_and_keys_just_refused_are_told_apart_in_the_hermes_words():
    # the keys the provider refused as credentials: one has left rotation, one was refused just now
    data = loaded()
    for body in data.values():
        body["view"]["totals"].update(rejected=1, retired=1)
    out = run_store("await store.refresh(); return store.notes.map(n => [n.id, n.tone, n.text]);", data)
    by = {n[0]: n for n in out}
    assert by["retired"][1] == "bad" and by["retired"][2].startswith(
        "1 key has left rotation — the provider refused it as a credential, so KAME no longer offers it and nothing is being spent on it.")
    assert "paste the replacement over it in Agent Zero’s Settings > API keys and it comes back by itself." in by["retired"][2]
    assert by["invalid"][2] == ("1 key was just refused and is still being tried. One refusal is not proof — an expired token "
                                "a second from refreshing sends the same thing — so it takes 3 in a row, with nothing working in between.")


# -- the sentences --------------------------------------------------------------------------------

def test_the_third_sentence_about_switching_is_the_hermes_sentence_with_the_host_named(clean):
    K.set_fallback_models("")
    assert K.status_view()["fallback"]["why"] == (
        "Switching: off - no fallback models are set in Agent Zero, so KAME waits for your keys. "
        "Add some in the Fallback models tab and switching is automatic.")
    K.set_fallback_models("google/gemini-3.7-flash")
    K.set_model_switch("never")
    assert K.status_view()["fallback"]["why"] == "Fallback: off - 'Fallback' is set to Never."
    K.set_model_switch("timed")
    K.set_failover_after(60)
    assert K.status_view()["fallback"]["why"].startswith("Switching: after your time - once you have waited 60 s with nothing on screen, Agent Zero answers")
    K.set_model_switch("auto")
    K.set_no_model_fallback(True)
    try:
        view = K.status_view()["fallback"]
        assert view["state"] == "off_by_flag" and "Ignore my fallback list" in view["why"]
    finally:
        K.set_no_model_fallback(False)
    why = K.status_view()["fallback"]["why"]
    assert why.startswith("Switching: automatic - when your provider is busy, Agent Zero answers with the next model below at once;")


# -- the Details fold -----------------------------------------------------------------------------

def test_the_process_card_lists_the_hermes_counters_that_agent_zero_has():
    counters = {"calls": 12, "last_call_at": 0, "rotations": 4, "recovered": 2, "surfaced": 1, "handed_over": 3,
                "probe_bursts": 1, "waits": 2, "waited_s": 95, "stream_drops": 1, "stitched": 1}
    out = run_store("await store.refresh(); return store.processFields.map(f => [f.label, f.value, f.hint]);", with_data(counters=counters))
    labels = [row[0] for row in out]
    assert labels == ["Calls", "Rotations", "Recovered", "Surfaced", "Fallbacks", "Keys asked together", "Waited",
                      "Answers cut", "Continued"]
    by = {row[0]: row for row in out}
    assert by["Recovered"][2] == "answered after a rotation"
    assert by["Surfaced"][2] == "errors KAME let through"
    assert by["Waited"][1] == "1m 35s" and by["Waited"][2] == "across 2 pause(s)"
    assert by["Fallbacks"][2] == "replies your fallback list answered: the model was busy, resting or too slow"


def test_the_engine_counts_what_the_process_card_reads():
    K._kame_event("recovery", "gemini:g")
    K._kame_event("surfaced", "gemini:g")
    K._kame_event("wait", "gemini:g", seconds=30)
    counters = K.pool_report()["counters"]
    assert counters["recovered"] >= 1 and counters["surfaced"] >= 1 and counters["waits"] >= 1
    assert counters["waited_s"] >= 30


# -- the credit and the footer ----------------------------------------------------------------------

def test_the_made_by_card_and_the_compact_credit_carry_the_hermes_words_and_the_agent_zero_repository():
    for words in (
        "Made by KAME",
        "KAME API Rotation is designed and built by KAME. Free and MIT-licensed. If it saves your conversations from rate limits, "
        "a star on GitHub helps other people find it — it costs nothing.",
        "⭐ Star on GitHub", "KAME on GitHub (Kame696)", "Report a problem",
        "KAME API Rotation is made by KAME · ", "github.com/Kame696", "⭐ Star it on GitHub",
        REPO, REPO + "/issues", "https://github.com/Kame696",
        "/kame for the same picture in the chat, /kame-quota per key and model, /kame-keys to add keys in bulk",
        "reading refreshed every",
    ):
        assert words in PANEL, words
    assert PANEL.count("Made by KAME") == 1                 # the card title; the footer says "is made by KAME"


def test_the_first_run_card_is_the_hermes_card_for_agent_zero():
    for words in ("Getting started", "KAME is running, and no pooled key has been used yet.",
                  "Send a message. The chip above the message box starts showing your keys from the first call."):
        assert words in PANEL, words


def test_right_now_says_what_hermes_says_idle_calling_rotated_waiting():
    sentence = "await store.refresh(); return store.rightNow;"
    assert run_store(sentence, loaded()) == "Idle. A key is ready, so the next call goes straight out."
    for kind, fields, said in (
        ("calling", dict(model="gemini-3.5-flash", attempt=1, healthy=2, keys=2), "Calling gemini-3.5-flash, with 2 of 2 keys ready."),
        ("calling", dict(model="gemini-3.5-flash", attempt=2, healthy=1, keys=2),
         "Rotated. Attempt 2 on gemini-3.5-flash, with 1 of 2 keys still ready."),
        ("waiting", dict(model="gemini-3.5-flash", eta_s=125),
         "Every key is resting. Waiting 2m 05s rather than failing the turn — press Esc to stop."),
    ):
        data = loaded()
        for body in data.values():
            body["view"]["activity"].update(kind=kind, **fields)
        assert run_store(sentence, data) == said
    assert "Right now" in PANEL and "x-show=\"$store.kamePanel.activity.kind !== 'idle'\"" not in PANEL


def test_the_fallback_list_is_shown_inside_its_shelf_with_how_each_model_is_doing():
    out = run_store("await store.refresh(); return store.fallbackList;", loaded(fallback_models="google/gemini-3.7-flash"))
    assert out["why"].startswith("Switching: automatic - when your provider is busy, Agent Zero answers")
    assert [e["n"] for e in out["entries"]] == [1, 2]
    assert out["entries"][0]["yours"] is True and out["entries"][1]["yours"] is False
    assert out["entries"][1]["state"] == "KAME has not served this model yet"
    assert out["how"] == "Change this list in the Fallback models tab. Background jobs are never raced and never switch on their own account; a stopped important job goes down your list once."
    assert PANEL.count("fallbackList.why") == 2             # the Settings tab and the Fallback tab
