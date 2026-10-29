"""1.8.3.0 (A4) - the Fallback models tab carries Hermes' shelf "If your model cannot answer".

Fallback is not a number of seconds, it is a choice: Automatic / Set time / Never, the seconds only under
"Set time" (Hermes' Settings tab, and Agent Zero's since 1.8.2.8). The Fallback models tab used to keep a
second, contradicting field "Switch to the next model after (seconds)". Now the tab shows the SAME shelf,
drawn by the same markup from the same data, above the list editor. The page logic runs in node against the
real status report (`tests/js/hooks.mjs`); the markup is checked in the file.
"""
from __future__ import annotations

import re
from pathlib import Path

from test_v1_8_2_9_page_store import run_store, loaded  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
WEBUI = ROOT / "webui"
PANEL = (WEBUI / "kame-panel.html").read_text(encoding="utf-8")


def card_block(loop_source):
    """The markup of the shelf loop that iterates ``loop_source``, up to its closing template."""
    head = f'<template x-for="card in {loop_source}" :key="card.id">'
    assert PANEL.count(head) == 1, loop_source
    start = PANEL.index(head)
    end = PANEL.index("\n        </template>", start)
    return PANEL[start + len(head):end]


# -- the markup -----------------------------------------------------------------------------------

def test_the_old_seconds_field_is_gone_from_every_file_of_the_page():
    for path in list(WEBUI.glob("*")) + list((ROOT / "extensions").rglob("*")):
        if path.suffix in {".html", ".js", ".css"}:
            assert "Switch to the next model after" not in path.read_text(encoding="utf-8"), path.name
    fallback = PANEL.split("<!-- ===== Fallback models ===== -->", 1)[1].split("<!-- ===== Events", 1)[0]
    assert "failover_after_seconds" not in fallback.replace("fallbackCards", "")


def test_the_fallback_tab_draws_the_shelf_with_the_markup_of_the_settings_tab():
    settings_markup = card_block("$store.kamePanel.settingCards")
    fallback_markup = card_block("$store.kamePanel.fallbackCards")
    assert fallback_markup == settings_markup, "one shelf, drawn the same on both tabs"
    fallback = PANEL.split("<!-- ===== Fallback models ===== -->", 1)[1].split("<!-- ===== Events", 1)[0]
    assert fallback.index("fallbackCards") < fallback.index("kame-fb-row"), "above the list editor"


def test_the_shelf_does_not_offer_to_open_the_tab_it_is_on():
    assert "card.shows_fallback &amp;&amp; $store.kamePanel.tab !== 'fallback'" in card_block("$store.kamePanel.fallbackCards")


def test_the_intro_names_the_main_model_rule_and_the_old_wording_is_gone():
    fallback = PANEL.split("<!-- ===== Fallback models ===== -->", 1)[1].split("<!-- ===== Events", 1)[0]
    intro = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fallback.split("kame-fb-row", 1)[0]))
    assert "main model" in intro and "background jobs never switch model" in intro.lower()
    assert "every key resting longer than the time below" not in intro
    assert "Agent Zero has no fallback list of its own" not in intro


# -- the data -------------------------------------------------------------------------------------

def test_fallback_is_three_buttons_in_hermes_order_and_the_seconds_wait_for_set_time():
    out = run_store(
        "await store.refresh();"
        "const [card] = store.fallbackCards; const m = card.rows.find(r => r.name === 'model_switch');"
        "return { id: card.id, title: card.title, rows: card.rows.map(r => r.name),"
        "        buttons: store.choicesOf(m).map(c => store.choiceLabel(m, c)) };", loaded())
    assert out["title"] == "If your model cannot answer"
    assert out["buttons"] == ["Automatic", "Set time", "Never"]
    assert out["rows"] == ["model_switch", "reply_notice"], "no seconds under Automatic, and the advanced two wait to be asked"


def test_the_seconds_appear_under_set_time_and_only_there():
    timed = run_store("await store.refresh(); return store.fallbackCards[0].rows.map(r => r.name);",
                      loaded(model_switch="timed"))
    assert timed == ["model_switch", "failover_after_seconds", "reply_notice"]
    never = run_store("await store.refresh(); return store.fallbackCards[0].rows.map(r => r.name);",
                      loaded(model_switch="never"))
    assert "failover_after_seconds" not in never


def test_the_advanced_two_follow_the_advanced_rule():
    plain = run_store("await store.refresh(); return store.fallbackCards[0].rows.map(r => r.name);", loaded())
    assert "reply_notice" in plain and "max_total_wait_seconds" not in plain and "never_fall_back_to_another_model" not in plain
    shown = run_store(
        "await store.refresh(); store.showAdvanced = true;"
        "return [store.fallbackCards[0].rows.map(r => r.name), store.settingCards.find(c => c.id === 'extra').rows.map(r => r.name)];",
        loaded())
    assert sorted(shown[0]) == ["max_total_wait_seconds", "model_switch", "never_fall_back_to_another_model", "reply_notice"]
    assert shown[0] == shown[1], "the same rows in the same order as the Settings tab"
    changed = run_store("await store.refresh(); return store.fallbackCards[0].rows.map(r => r.name);",
                        loaded(max_total_wait_seconds=600))
    assert "max_total_wait_seconds" in changed, "a changed setting never hides"


def test_the_two_tabs_share_one_card_and_one_save_path():
    out = run_store(
        "await store.refresh();"
        "const a = store.settingCards.find(c => c.id === 'extra'), b = store.fallbackCards[0];"
        "return { same: JSON.stringify(a) === JSON.stringify(b), count: store.fallbackCards.length,"
        "        extra: store.extraAdvancedCount };", loaded())
    assert out["same"] is True and out["count"] == 1 and out["extra"] == 2


def test_choosing_a_button_saves_the_setting_through_the_settings_endpoint():
    out = run_store(
        "await store.refresh(); await store.save('model_switch', 'timed');"
        "return calls.map(c => c[0]).filter(p => p.includes('settings_set') || p.includes('kame_settings'));",
        {**loaded(), "/plugins/api_rotation_by_kame/kame_settings_set": {"ok": True, "settings": {"model_switch": "timed"}}})
    assert out, "the choice was sent to the host"
