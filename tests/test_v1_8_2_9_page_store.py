"""1.8.2.9 (U2, U4, U5) - the logic behind the API Rotation page, run in node against the real status report.

`webui/kame-panel-store.js` is loaded outside Agent Zero: `tests/js/hooks.mjs` stands in for the host's
`createStore` and `callJsonApi` (every call is recorded) and, when asked, for Agent Zero's model search.
The status report fed to it is the one the real handler builds from the real engine. No browser, no network.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import clean  # noqa: F401  (fixture used by name; also stubs Agent Zero)
from test_v1_8_2_9_status_view import calm, seed_owner_case, GEM, SHARED  # noqa: F401
import kame_api_stub as stub
import kame_engine as K
import kame_settings as S

ROOT = Path(__file__).resolve().parent.parent
REGISTER = (ROOT / "tests" / "js" / "register.mjs").as_uri()
STATUS_PATH = "/plugins/api_rotation_by_kame/kame_status"
NODE = shutil.which("node")
STATUS = stub.load_api("kame_status", K, S)

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture(autouse=True)
def fresh():
    stub.reset()
    yield
    stub.reset()


def report(**config):
    """The report the page fetches (`{"panel": true}`), with the saved settings as the engine holds them."""
    stub.CONFIG.update(config)
    if "fallback_models" in config:
        K.set_fallback_models(config["fallback_models"])
    out = stub.call(STATUS.KameStatus, {"panel": True})
    # the engine is in the test fixtures state (logs silenced...); the page is shown an install at its defaults
    out["settings"] = {**{name: S.default(name) for name in S.SETTINGS}, **config}
    out["active"] = True                                    # KAME is bound to the host in a real run
    return json.loads(json.dumps(out, default=str))


def run_store(body: str, responses: dict, fake_model_config: bool = False):
    """Run `body` with `store` = the page store and `calls` = what it asked the host for; return its JSON."""
    program = (
        "globalThis.__calls = []; globalThis.__searches = [];\n"
        "const chunks = []; for await (const c of process.stdin) chunks.push(c);\n"
        "globalThis.__responses = JSON.parse(Buffer.concat(chunks).toString('utf8'));\n"
        'const { store } = await import("/plugins/api_rotation_by_kame/webui/kame-panel-store.js");\n'
        "const calls = globalThis.__calls;\n"
        f"const out = await (async () => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out));\n"
        "process.exit(0);\n"
    )
    env = dict(os.environ)
    env["KAME_FAKE_MODEL_CONFIG"] = "1" if fake_model_config else "0"
    done = subprocess.run([NODE, "--import", REGISTER, "--input-type=module", "-e", program],
                          input=json.dumps(responses), capture_output=True, text=True, encoding="utf-8",
                          timeout=60, env=env)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def loaded(**config):
    seed_owner_case()
    K._KAME_PRIMARY["identity"] = "gemini:gemini-3.5-flash"
    return {STATUS_PATH: report(**config)}


# -- U4: settings groups = Hermes' groups -------------------------------------------------------------

HERMES_TITLES = ["If your model cannot answer", "Speed", "Your API keys", "Rest times (advanced)"]


def test_the_settings_shelves_are_hermes_shelves_in_hermes_order_plus_the_agent_zero_one():
    out = run_store("await store.refresh(); store.showAdvanced = true; return store.settingCards.map(c => c.id + '|' + c.title);", loaded())
    ids = [row.split("|")[0] for row in out]
    titles = [row.split("|", 1)[1] for row in out]
    assert ids == ["extra", "speed", "keyring", "tuning", "agentzero", "off"]
    assert titles[:4] == HERMES_TITLES
    assert titles[4] == "Agent Zero log and chat"
    assert titles[5] == "Troubleshooting: turn parts of KAME off"


def test_the_fallback_list_is_not_a_settings_row_it_has_its_own_tab():
    out = run_store("await store.refresh(); return store.settingCards.flatMap(c => c.rows.map(m => m.name));", loaded(show=1))
    assert "fallback_models" not in out
    out = run_store("await store.refresh(); store.showAdvanced = true; return store.settingCards.flatMap(c => c.rows.map(m => m.name));",
                    loaded())
    assert "fallback_models" not in out


def test_only_the_first_two_of_each_shelf_show_until_asked_and_a_changed_one_never_hides():
    base = run_store("await store.refresh(); return store.settingCards.find(c => c.id === 'speed').rows.map(m => m.name);", loaded())
    assert base == ["hedge_mode", "first_token_wait"]
    more = run_store("await store.refresh(); store.showAdvanced = true; return store.settingCards.find(c => c.id === 'speed').rows.length;",
                     loaded())
    assert more == 10      # the eleventh (first-token seconds) waits for "Set time"
    # a number that was changed stays on show with the toggle off
    changed = run_store(
        "await store.refresh(); const c = store.settingCards.find(c => c.id === 'speed'); return [c.title, c.rows.map(m => m.name)];",
        loaded(hedge_max_legs=2))
    assert changed[0] == "Speed · 1 changed" and "hedge_max_legs" in changed[1]


def test_a_number_that_belongs_to_one_answer_shows_only_under_that_answer():
    auto = run_store("await store.refresh(); return store.settingCards.find(c => c.id === 'extra').rows.map(m => m.name);",
                     loaded())
    assert "failover_after_seconds" not in auto
    timed = run_store("await store.refresh(); return store.settingCards.find(c => c.id === 'extra').rows.map(m => m.name);",
                      loaded(model_switch="timed"))
    assert "failover_after_seconds" in timed


def test_the_toolbar_says_how_many_settings_changed():
    none = run_store("await store.refresh(); return store.toolbarTitle;", loaded())
    assert none == "KAME settings · all at their defaults"
    some = run_store("await store.refresh(); return store.toolbarTitle;", loaded(hedge_max_legs=2, model_switch="timed"))
    assert some == "KAME settings · 2 changed"


def test_the_speed_buttons_say_what_each_costs_and_the_sentence_follows_the_choice():
    out = run_store(
        "await store.refresh(); const m = store.quickRow('hedge_mode');"
        "const labels = store.choicesOf(m).map(c => store.choiceLabel(m, c));"
        "const sentences = {}; for (const c of store.choicesOf(m)) { store.drafts.hedge_mode = c; sentences[c] = store.choiceSentence(m); }"
        "return { labels, sentences };", loaded())
    assert out["labels"] == ["Saver · 1x", "Balanced · up to 1.1x", "Faster · up to 1.3x"]
    assert out["sentences"]["off"] == "One request per reply, never more: the cheapest, and a slow reply simply waits."
    assert out["sentences"]["adaptive"].startswith("A second key races only a reply that is slower than twice")
    assert out["sentences"]["adaptive"].endswith("what was really spent.")        # the footnote, not for the cheapest
    assert not out["sentences"]["off"].endswith("what was really spent.")
    assert out["sentences"]["max"].startswith("Races a little sooner")


def test_the_first_page_has_the_three_controls_and_the_failover_number_only_for_set_time():
    names = "await store.refresh(); return store.quickCards[0].rows.map(m => m.name);"
    assert run_store(names, loaded()) == ["hedge_mode", "model_switch", "reply_notice"]
    assert run_store(names, loaded(model_switch="timed")) == ["hedge_mode", "model_switch", "failover_after_seconds", "reply_notice"]


# -- U2: the page ------------------------------------------------------------------------------------------

def test_the_details_are_folded_until_asked():
    assert run_store("return store.detailsOpen;", {}) is False


def test_the_header_count_is_the_one_count_over_keys():
    out = run_store("await store.refresh(); return [store.headerStatus, store.said.line1];", loaded())
    assert out[0]["text"] == "2 of 3 keys ready"
    # the first sentence counts the keys of the model it names (Hermes' wording), not the whole pool
    assert out[1].endswith("(2 of 2 keys ready).")


def test_opening_a_ring_lists_its_keys_and_the_buttons_that_act_on_that_model():
    out = run_store("await store.refresh(); store.toggleRing('gemini:gemini-3.5-flash'); const t = store.keyTable;"
                    "return { summary: t.summary, keys: t.keys.length, busy: t.busy, label: t.label };", loaded())
    assert out == {"summary": "2 of 2 keys ready", "keys": 2, "busy": False, "label": "gemini-3.5-flash"}


def test_a_ring_for_a_dead_shared_key_says_why_and_when_it_is_back():
    out = run_store("await store.refresh(); store.toggleRing('openai:claude-sonnet-5'); return store.keyTable;", loaded())
    assert out["keys"][0]["words"].startswith("out for today · back")
    assert "out of credit" in out["keys"][0]["words"]
    assert out["summary"].startswith("0 of 1 keys ready")


def test_a_ring_for_a_model_never_used_says_so():
    K.set_fallback_models("mmm/middle")
    out = run_store("await store.refresh(); store.toggleRing('mmm:middle'); return store.keyTable.empty;", loaded())
    assert out == "middle has not been used yet — its keys show here after the first call."


def test_wake_and_try_post_to_the_one_door_with_the_model():
    responses = loaded()
    responses["/plugins/api_rotation_by_kame/kame_pool_action"] = {"ok": True, "message": "2 keys of this model start again."}
    out = run_store("await store.refresh(); await store.act('clear_pool', 'gemini:gemini-3.5-flash');"
                    "await store.act('forget_busy', 'gemini:gemini-3.5-flash');"
                    "return [calls.filter(c => c[0].endsWith('kame_pool_action')).map(c => c[1]), store.message];", responses)
    assert out[0] == [{"action": "clear_pool", "identity": "gemini:gemini-3.5-flash"},
                      {"action": "forget_busy", "identity": "gemini:gemini-3.5-flash"}]
    assert out[1] == "2 keys of this model start again."


def test_the_status_is_asked_for_with_the_panel_flag():
    out = run_store("await store.refresh(); return calls[0];", loaded())
    assert out == [STATUS_PATH, {"panel": True}]


def test_a_dead_status_shows_an_error_and_keeps_the_page_standing():
    out = run_store("store.refresh = store.refresh; globalThis.__responses = { [STATUS]: () => { throw new Error('down'); } };"
                    "await store.refresh(); return [store.error, store.said.line1];".replace("STATUS", json.dumps(STATUS_PATH)), {})
    assert out[0].startswith("KAME's status did not load (down)")
    assert out[1].startswith("Ready — nothing has been asked yet")


# -- U5: the fallback editor ---------------------------------------------------------------------------------

def test_the_rows_start_as_the_saved_list_and_drafts_are_marked():
    K.set_fallback_models("openai/gpt-5.4-nano\ngoogle/gemini/gemini-3.5-flash")
    out = run_store(
        "await store.refresh(); const before = store.rows.map(r => [r.provider, r.name]); store.addRow();"
        "return { before, drafts: store.rows.map(r => store.isDraft(r)), incomplete: store.fallbackIncomplete, dirty: store.rowsDirty };",
        loaded(fallback_models="openai/gpt-5.4-nano\ngoogle/gemini/gemini-3.5-flash"))
    assert out["before"] == [["openai", "gpt-5.4-nano"], ["google", "gemini/gemini-3.5-flash"]]
    assert out["drafts"] == [False, False, True] and out["incomplete"] == 1 and out["dirty"] is True


def test_rows_move_up_and_down_and_are_removed_by_their_x():
    out = run_store(
        "await store.refresh(); store.rows = []; for (const n of ['a','b','c']) { store.addRow(); store.rows.at(-1).provider='p'; store.rows.at(-1).name=n; }"
        "store.moveRow(2, -1); const moved = store.rows.map(r => r.name).join('');"
        "store.moveRow(0, -1); const edge = store.rows.map(r => r.name).join('');"
        "store.removeRow(0); return [moved, edge, store.rows.map(r => r.name).join('')];", loaded())
    assert out == ["acb", "acb", "cb"]


def test_a_refresh_does_not_overwrite_rows_the_person_is_editing():
    out = run_store("await store.refresh(); store.addRow(); store.rows[0].provider = 'groq'; await store.refresh(); return store.rows.length;",
                    loaded(fallback_models="openai/gpt-5.4-nano"))
    assert out == 2


def test_save_posts_the_rows_and_keeps_the_drafts_on_screen():
    responses = loaded(fallback_models="openai/gpt-5.4-nano")
    responses["/plugins/api_rotation_by_kame/kame_fallback_set"] = {
        "ok": True, "message": "1 model saved; 1 row left out - choose a provider and a model",
        "entries": [{"provider": "openai", "name": "gpt-5.4-nano", "api_base": "https://x/v1"}]}
    out = run_store(
        "await store.refresh(); store.addRow(); await store.saveRows();"
        "const post = calls.find(c => c[0].endsWith('kame_fallback_set'))[1];"
        "return { post, rows: store.rows.length, drafts: store.fallbackIncomplete, base: store.rows[0].api_base, message: store.message };",
        responses)
    assert out["post"]["rows"][0] == {"provider": "openai", "name": "gpt-5.4-nano", "api_base": ""}
    assert len(out["post"]["rows"]) == 2
    assert out["rows"] == 2 and out["drafts"] == 1 and out["base"] == "https://x/v1"
    assert out["message"].startswith("1 model saved")


def test_without_agent_zeros_model_search_the_provider_and_model_are_plain_inputs():
    out = run_store("await store.onOpen(); const r = [store.hasSearch, store.providers.length]; store.cleanup(); return r;", loaded())
    assert out == [False, 0]


def test_with_agent_zeros_model_search_the_provider_is_a_select_and_the_name_completes():
    out = run_store(
        "await store.onOpen(); store.cleanup(); store.addRow(); const row = store.rows[0]; store.setProvider(row, 'openai');"
        "row.name = 'gpt-5'; store.search(row, true); await new Promise(r => setTimeout(r, 20));"
        "const g = store.grouped(row);"
        "return { has: store.hasSearch, providers: store.providers.map(p => p.value), open: row.open, source: row.source,"
        " matched: g.matched, rest: g.rest, searched: globalThis.__searches };",
        loaded(), fake_model_config=True)
    assert out["has"] is True and out["providers"] == ["openai", "google"]
    assert out["open"] is True and out["source"] == "litellm_registry"
    assert out["matched"] == ["gpt-5.4-nano", "gpt-5.4"] and out["rest"] == ["o4-mini"]
    assert out["searched"] == [["openai", "gpt-5"]]


def test_a_stale_search_answer_is_not_shown():
    out = run_store(
        "await store.onOpen(); store.cleanup(); store.addRow(); const row = store.rows[0]; store.setProvider(row, 'openai');"
        "row.name = 'g'; const pending = store.search(row, true); row.name = 'gemini'; await new Promise(r => setTimeout(r, 20));"
        "return row.results.length;",
        loaded(), fake_model_config=True)
    assert out == 0


def test_a_provider_written_by_hand_is_still_offered_in_the_select():
    out = run_store(
        "await store.onOpen(); store.cleanup(); store.addRow(); store.rows[0].provider = 'my_own_gateway';"
        "return store.providerOptions(store.rows[0]).map(p => p.value);",
        loaded(), fake_model_config=True)
    assert out == ["openai", "google", "my_own_gateway"]
