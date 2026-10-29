"""1.8.3.0 (A6) - the Settings tab draws a row the way Hermes draws it.

Hermes' row: title, a power tag, a "changed" tag, the sentence (long text on hover), "When to change it",
the key / environment name and a Source badge (with "Show advanced settings" or when changed), a Save button for
numbers with their range and a readable refusal, a confirmation before the consequential switches are turned
on, and the shelves sorted by power. The logic runs in node against the real report; the markup is read as text.
"""
from __future__ import annotations

import json
from pathlib import Path

from test_v1_8_2_9_page_store import loaded, run_store, report, fresh  # noqa: F401
from test_v1_8_2_7_speed_fallback import clean  # noqa: F401
import kame_settings as S

ROOT = Path(__file__).resolve().parent.parent
PANEL = (ROOT / "webui" / "kame-panel.html").read_text(encoding="utf-8")
STORE = (ROOT / "webui" / "kame-panel-store.js").read_text(encoding="utf-8")
CONFIG_HTML = (ROOT / "webui" / "config.html").read_text(encoding="utf-8")

RANK = {"high": 0, "medium": 1, "low": 2, "escape": 3}


def cards(**config):
    return run_store("await store.refresh(); store.showAdvanced = true;"
                     "return store.settingCards.map(c => [c.id, c.title, c.rows.map(m => m.name)]);", loaded(**config))


def test_within_a_shelf_the_rows_go_by_power_high_first_and_ties_keep_the_table_order():
    for _id, _title, names in cards():
        ranks = [RANK[S.IMPACT[n]] for n in names]
        assert ranks == sorted(ranks), (_id, names)
    speed = dict((c[0], c[2]) for c in cards())["speed"]
    assert speed[0] == "hedge_mode"          # high before medium, low
    assert speed.index("hedge_max_legs") < speed.index("hedge_burst")


def test_the_basic_rows_are_on_show_and_the_advanced_ones_wait_for_the_toggle():
    base = run_store("await store.refresh(); return store.settingCards.flatMap(c => c.rows.map(m => m.name));", loaded())
    assert set(base) == (set(S.VISIBLE) - {"failover_after_seconds", "stream_silence_timeout_seconds"}) | set(S.TROUBLESHOOTING)
    assert "hedge_burst" not in base and "hedge_mode" in base
    count = run_store("await store.refresh(); return store.advancedCount;", loaded())
    assert count == len([n for n in S.ADVANCED if n != "fallback_models"])
    shown = run_store("await store.refresh(); store.showAdvanced = true; return store.settingCards.flatMap(c => c.rows.map(m => m.name));", loaded())
    assert "hedge_burst" in shown and "share_pool_health" in shown


def test_a_changed_advanced_row_never_hides_and_says_changed_with_its_source():
    out = run_store(
        "await store.refresh(); const m = store.quickRow('hedge_burst');"
        "return { shown: store.settingCards.flatMap(c => c.rows.map(r => r.name)).includes('hedge_burst'),"
        "  source: store.sourceOf(m), label: store.sourceLabel(m), tip: store.sourceTip(m), changed: store.isChanged(m) };",
        loaded(hedge_burst=5))
    assert out["shown"] and out["source"] == "config" and out["changed"] is True
    assert out["label"] == "set in the settings"
    assert out["tip"] == "Set in this plugin's settings. The environment would outrank it."


def test_the_default_source_says_nothing_mentions_it_and_the_environment_outranks():
    out = run_store(
        "await store.refresh(); const m = store.quickRow('hedge_mode');"
        "const e = {...m, env_override: 'KAME_HEDGE_MODE'};"
        "return { s: store.sourceOf(m), l: store.sourceLabel(m), t: store.sourceTip(m),"
        "        es: store.sourceOf(e), el: store.sourceLabel(e), et: store.sourceTip(e), ec: store.isChanged(e) };", loaded())
    assert out["s"] == "default" and out["l"] == "default"
    assert out["t"] == "Nothing anywhere mentions this one, so the built-in default is in force."
    assert out["es"] == "environment" and out["el"] == "set in the environment" and out["ec"] is True
    assert out["et"] == "KAME_HEDGE_MODE is set in the environment, which outranks everything else."


def test_the_power_tag_wording_is_hermes_wording():
    out = run_store("await store.refresh(); return ['high','medium','low','escape','x'].map(i => store.impactTag({impact: i}));", loaded())
    assert out == ["●●● High", "●●○ Medium", "●○○ Low", "Off switch", ""]


def test_a_number_is_checked_with_hermes_refusals_before_it_is_sent():
    program = (
        "await store.refresh(); const n = (name) => store.quickRow(name);"
        "const v = (name, raw) => store.validateNumber(n(name), raw);"
        "return { empty: v('failover_after_seconds', ''), word: v('failover_after_seconds', 'abc'),"
        "  range: v('hedge_max_legs', '9'), whole: v('hedge_max_legs', '2.5'), ok: v('hedge_max_legs', '2'),"
        "  shortfall: v('stream_silence_timeout_seconds', '2'), off: v('stream_silence_timeout_seconds', '0'),"
        "  rangeText: store.rangeText(n('stream_silence_timeout_seconds')), rangeLegs: store.rangeText(n('hedge_max_legs')),"
        "  none: store.rangeText(n('hedge_mode')) };")
    out = run_store(program, loaded())
    assert out["empty"] == "Enter a number of seconds."
    assert out["word"].endswith('takes a number of seconds; "abc" is not one.')
    assert out["range"] == "Accepts 1 to 3."
    assert out["whole"] == "This one counts whole things, so it takes a whole number."
    assert out["ok"] == "" and out["off"] == ""
    assert out["shortfall"].startswith("Either 0 (off) or at least 5 ")
    assert out["rangeText"] == "0–3600 seconds, 0 or 5+"
    assert out["rangeLegs"] == "1–3 legs"
    assert out["none"] == ""


def test_a_refused_number_is_never_sent_and_a_good_one_goes_through_save():
    out = run_store(
        "await store.refresh(); const m = store.quickRow('hedge_max_legs');"
        "store.draft('hedge_max_legs', '9'); await store.saveNumber(m);"
        "const refused = calls.filter(c => c[0].includes('settings_set')).length;"
        "const error = store.errorOf('hedge_max_legs');"
        "store.draft('hedge_max_legs', '2'); await store.saveNumber(m);"
        "return { refused, error, sent: calls.filter(c => c[0].includes('settings_set')).length, dirty: store.isDirty('hedge_max_legs') };",
        {**loaded(), "/plugins/api_rotation_by_kame/kame_settings_set": {"ok": True, "message": "saved"}})
    assert out["refused"] == 0 and out["error"] == "Accepts 1 to 3."
    assert out["sent"] == 1 and out["dirty"] is False


def test_typing_marks_the_field_unsaved_and_clears_an_old_refusal():
    out = run_store(
        "await store.refresh(); const m = store.quickRow('hedge_max_legs');"
        "store.draft('hedge_max_legs', '9'); await store.saveNumber(m); const had = store.errorOf('hedge_max_legs');"
        "store.draft('hedge_max_legs', '2');"
        "return { had, now: store.errorOf('hedge_max_legs'), dirty: store.isDirty('hedge_max_legs') };", loaded())
    assert out["had"] and out["now"] == "" and out["dirty"] is True


def test_turning_a_consequential_switch_on_asks_first_and_turning_it_off_does_not():
    out = run_store(
        "await store.refresh(); const m = store.quickRow('rotation_disabled');"
        "await store.flagChanged(m, true);"
        "const asked = { ask: store.askingFlag, saved: calls.filter(c => c[0].includes('settings_set')).length,"
        "  title: store.flagQuestion(m), text: store.flagDescription(m) };"
        "await store.confirmFlag(m);"
        "return { asked, after: calls.filter(c => c[0].includes('settings_set')).length, cleared: store.askingFlag };",
        {**loaded(), "/plugins/api_rotation_by_kame/kame_settings_set": {"ok": True, "message": "saved"}})
    assert out["asked"]["ask"] == "rotation_disabled" and out["asked"]["saved"] == 0
    assert out["asked"]["title"] == f"{S.SETTINGS['rotation_disabled'][6]}?"
    assert out["asked"]["text"].endswith(
        "This is the switch people reach for when they suspect KAME of breaking something — it is reversible, and nothing is uninstalled.")
    assert out["after"] == 1 and out["cleared"] == ""


def test_a_switch_that_is_not_consequential_saves_at_once():
    out = run_store(
        "await store.refresh(); const m = store.quickRow('spread_disabled'); await store.flagChanged(m, true);"
        "return { ask: store.askingFlag, saved: calls.filter(c => c[0].includes('settings_set')).length };",
        {**loaded(), "/plugins/api_rotation_by_kame/kame_settings_set": {"ok": True, "message": "saved"}})
    assert out == {"ask": "", "saved": 1}


def test_the_troubleshooting_shelf_opens_by_itself_when_a_switch_is_on_and_counts_the_ones_shown():
    closed = run_store("await store.refresh(); const c = store.settingCards.find(c => c.id === 'off');"
                       "return { open: c.open, total: c.total };", loaded())
    assert closed == {"open": False, "total": len(S.TROUBLESHOOTING)}
    opened = run_store("await store.refresh(); const c = store.settingCards.find(c => c.id === 'off');"
                       "return { open: c.open, title: c.title };", loaded(spread_disabled=True))
    assert opened["open"] is True and opened["title"].endswith("· 1 changed")


def test_a_setting_on_no_shelf_lands_on_an_other_card_at_the_end():
    program = (
        "await store.refresh();"
        "store.data.settings_meta.push({name: 'a_new_one', type: 'flag', default: false, title: 'A new one', sentence: 's',"
        " group: 'nowhere', impact: 'low', advanced: false});"
        "const last = store.settingCards[store.settingCards.length - 1];"
        "return [last.id, last.title, last.note, last.rows.map(m => m.name)];")
    out = run_store(program, loaded())
    assert out == ["_rest", "Other", "Not sorted into any of the groups above.", ["a_new_one"]]


def test_a_report_with_no_settings_says_so():
    out = run_store("await store.refresh(); store.data.settings_meta = []; return [store.noSettings, store.settingCards.length];", loaded())
    assert out == [True, 0]
    assert "This KAME reports no settings." in PANEL


def test_the_choice_note_and_the_footnote_are_two_lines():
    out = run_store(
        "await store.refresh(); const m = store.quickRow('hedge_mode'); store.drafts.hedge_mode = 'max';"
        "return { note: store.choiceNote(m), foot: store.choiceFoot(m), both: store.choiceSentence(m) };", loaded())
    assert out["note"] and out["foot"] and out["both"] == f"{out['note']} {out['foot']}"
    cheap = run_store("await store.refresh(); const m = store.quickRow('hedge_mode'); store.drafts.hedge_mode = 'off';"
                      "return store.choiceFoot(m);", loaded())
    assert cheap == ""


# -- the markup ---------------------------------------------------------------------------------

def test_the_row_carries_every_part_of_the_hermes_row():
    for words in (
        "Power: how much this changes what you notice. High = every conversation, Medium = one situation, Low = fine-tuning.",
        "When to change it: ",
        ">changed<",
        "empty = not used",
        "Turn it on",
        "Reset every setting?",
        "Clear the pool?",
        "Saving…",
    ):
        assert words in PANEL, words
    assert PANEL.count("When to change it: ") == 3           # Settings tab, the shelf on the Fallback tab, Quick controls
    assert "(set by " not in PANEL


def test_the_toolbar_buttons_carry_their_tooltips():
    for words in (
        "Reads Agent Zero's settings again and rebuilds this page on the spot.",
        "Only clears rotation and quarantine; it does not delete any key.",
        "Empties the event list on the Events page. Nothing else changes.",
        "Settings that are already tuned or only one unusual setup needs. Hidden unless you changed them.",
    ):
        assert words in PANEL, words


def test_the_native_settings_screen_shows_the_same_sentences_the_panel_does():
    for name, row in S.SETTINGS.items():
        if name == "fallback_models":
            continue
        title, sentence = row[6], row[7]
        assert title.replace("&", "&amp;") in CONFIG_HTML or title in CONFIG_HTML, name
        assert sentence.split(".")[0][:40].replace("&", "&amp;") in CONFIG_HTML, name
    assert "When to change it:" in CONFIG_HTML


def test_the_generated_page_is_what_the_generator_writes_today(tmp_path):
    import subprocess
    import sys
    out = tmp_path / "config.html"
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "gen_config_page.py"), str(out)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert out.read_text(encoding="utf-8").replace("\r\n", "\n") == CONFIG_HTML.replace("\r\n", "\n")
