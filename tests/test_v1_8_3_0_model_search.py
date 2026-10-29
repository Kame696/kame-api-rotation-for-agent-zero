"""1.8.3.0 (A5) - model search in the fallback list rows.

On the live Agent Zero v2.14 server, typing "gem" in a row of the fallback editor showed nothing. The editor
fetched Agent Zero's `modelConfig` store once, at open, inside one `try` together with its provider list; a
store that registered later, or a provider list that was slow or failed, left every row on plain inputs for the
whole session. Now the store is looked up when the person types (`Alpine.store('modelConfig')`, the one Agent
Zero's own model field reads), the module import is only a second way to it and is retried, the provider list
may fail without taking the search down, and the box answers the keyboard (up, down, Enter, Escape).

The page store runs in node (`tests/js/hooks.mjs`); `Alpine` is a stub the test installs BEFORE the store is
loaded, or after, to play the late registration. Nothing is saved and no real setting is touched.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_v1_8_2_9_page_store import loaded  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
REGISTER = (ROOT / "tests" / "js" / "register.mjs").as_uri()
NODE = shutil.which("node")
PANEL = (ROOT / "webui" / "kame-panel.html").read_text(encoding="utf-8")
STORE_JS = (ROOT / "webui" / "kame-panel-store.js").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

#: A stand-in for Agent Zero's `Alpine.store('modelConfig')`: the search, the grouping, the providers.
STUB = """
globalThis.__searches = [];
globalThis.__loads = 0;
globalThis.makeModelConfig = (opts = {}) => ({
  chatProviders: opts.noProviders ? [] : [{ value: 'google', label: 'Google' }, { value: 'openai', label: 'OpenAI' }],
  async ensureLoaded() { globalThis.__loads++; if (opts.loadFails) throw new Error('model_config_get failed'); },
  async searchModelsDetailed(provider, query, modelType, apiBase) {
    globalThis.__searches.push([provider, query, modelType, apiBase]);
    if (opts.delay) await new Promise((r) => setTimeout(r, opts.delay(query)));
    const all = provider === 'google' ? ['gemini/gemini-3.5-flash', 'gemini/gemini-3.5-pro', 'gemma-4'] : ['gpt-5.4-nano', 'o4-mini'];
    return { models: all, provider, source: opts.source ?? 'litellm_registry', error: opts.error ?? '' };
  },
  groupResults(models, query) {
    const q = (query || '').trim().toLowerCase();
    if (!q) return { matched: [], rest: models };
    return { matched: models.filter((m) => m.toLowerCase().includes(q)), rest: models.filter((m) => !m.toLowerCase().includes(q)) };
  },
});
globalThis.installAlpine = (opts) => { const s = makeModelConfig(opts); globalThis.Alpine = { store: (n) => (n === 'modelConfig' ? s : undefined) }; return s; };
"""


def run(setup: str, body: str, responses: dict, fake_module: bool = False):
    """Run ``body`` with ``store`` = the page store; ``setup`` runs before the store module is loaded."""
    program = (
        "globalThis.__calls = [];\n"
        "const chunks = []; for await (const c of process.stdin) chunks.push(c);\n"
        "globalThis.__responses = JSON.parse(Buffer.concat(chunks).toString('utf8'));\n"
        f"{STUB}\n{setup}\n"
        'const { store } = await import("/plugins/api_rotation_by_kame/webui/kame-panel-store.js");\n'
        "const calls = globalThis.__calls;\n"
        "const wait = (ms) => new Promise((r) => setTimeout(r, ms));\n"
        f"const out = await (async () => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out));\n"
        "process.exit(0);\n"
    )
    env = dict(os.environ)
    env["KAME_FAKE_MODEL_CONFIG"] = "1" if fake_module else "0"
    done = subprocess.run([NODE, "--import", REGISTER, "--input-type=module", "-e", program],
                          input=json.dumps(responses), capture_output=True, text=True, encoding="utf-8",
                          timeout=60, env=env)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


OPEN = "await store.onOpen(); store.cleanup(); store.addRow(); const row = store.rows[0]; store.setProvider(row, 'google');"


# -- the cause ------------------------------------------------------------------------------------

def test_a_store_that_registers_after_the_page_opened_is_found_when_typing():
    """The 1.8.2.9 editor asked once at open and never again."""
    out = run("globalThis.Alpine = { store: () => undefined };",     # Agent Zero's store is not there yet
              OPEN + "const before = store.hasSearch;"
              "installAlpine();"                                     # ... and now it is
              "store.typeModel(row, 'gem'); await wait(400);"
              "return { before, after: store.hasSearch, searched: globalThis.__searches, results: row.results.length, open: row.open };",
              loaded())
    assert out["before"] is False and out["after"] is True
    assert out["searched"] == [["google", "gem", "chat", ""]] and out["results"] == 3 and out["open"] is True


def test_a_provider_list_that_fails_to_load_does_not_disable_the_search():
    out = run("installAlpine({ noProviders: true, loadFails: true });",
              OPEN + "store.search(row, true); await wait(30);"
              "return { has: store.hasSearch, results: row.results.length, loads: globalThis.__loads };",
              loaded())
    assert out["has"] is True and out["results"] == 3 and out["loads"] >= 1


def test_the_module_import_is_a_second_way_to_the_same_store():
    """The store may not be on `Alpine` (an older build): the module's own export is still tried."""
    out = run("globalThis.Alpine = undefined;",
              OPEN + "store.search(row, true); await wait(30);"
              "return { has: store.hasSearch, searched: globalThis.__searches.length };",
              loaded(), fake_module=True)
    assert out["has"] is True and out["searched"] == 1


def test_the_alpine_store_is_asked_at_typing_time_and_not_remembered():
    out = run("installAlpine();",
              OPEN + "store.search(row, true); await wait(30);"
              "globalThis.Alpine = { store: () => undefined };"      # the store goes away: the next search is a plain input
              "row.results = []; store.search(row, true); await wait(30);"
              "return globalThis.__searches.length;", loaded())
    assert out == 1


# -- the behaviour --------------------------------------------------------------------------------

def test_typing_waits_250_ms_and_asks_once():
    out = run("installAlpine();",
              OPEN + "store.typeModel(row, 'g'); store.typeModel(row, 'ge'); store.typeModel(row, 'gem');"
              "await wait(120); const early = globalThis.__searches.length;"
              "await wait(300); return { early, late: globalThis.__searches.slice() };", loaded())
    assert out["early"] == 0
    assert out["late"] == [["google", "gem", "chat", ""]]


def test_the_catalog_note_and_the_matches_first():
    out = run("installAlpine();",
              OPEN + "row.name = 'gemini'; store.search(row, true); await wait(30);"
              "return { source: row.source, items: store.items(row) };", loaded())
    assert out["source"] == "litellm_registry"
    assert [(i["name"], i["matched"], i["index"]) for i in out["items"]] == [
        ("gemini/gemini-3.5-flash", True, 0), ("gemini/gemini-3.5-pro", True, 1), ("gemma-4", False, 2)]
    assert "row.source === 'litellm_registry'" in PANEL and "Catalog suggestions" in PANEL
    assert "availability not verified" in PANEL


def test_no_note_when_the_source_is_not_the_catalog():
    out = run("installAlpine({ source: 'provider' });",
              OPEN + "store.search(row, true); await wait(30); return row.source;", loaded())
    assert out == "provider"      # the page shows the note only for 'litellm_registry'


def test_arrow_keys_move_enter_picks_escape_closes():
    out = run("installAlpine();",
              OPEN + "row.name = 'gem'; store.search(row, true); await wait(30);"
              "const key = (k) => { const e = { key: k, prevented: false, preventDefault() { this.prevented = true; } }; store.key(row, e); return e; };"
              "const steps = [];"
              "key('ArrowDown'); steps.push(row.active);"
              "key('ArrowDown'); steps.push(row.active);"
              "key('ArrowUp'); steps.push(row.active);"
              "key('ArrowUp'); steps.push(row.active);"                              # from the first: wraps to the last
              "key('Escape'); const closed = row.open;"
              "key('ArrowDown'); const reopened = row.open; const at = row.active;"
              "const enter = key('Enter'); "
              "return { steps, closed, reopened, at, name: row.name, openAfter: row.open, enterPrevented: enter.prevented };", loaded())
    assert out["steps"] == [0, 1, 0, 2]
    assert out["closed"] is False and out["reopened"] is True and out["at"] == 0
    assert out["name"] == "gemini/gemini-3.5-flash" and out["openAfter"] is False and out["enterPrevented"] is True


def test_enter_with_nothing_chosen_does_not_swallow_the_key():
    out = run("installAlpine();",
              OPEN + "row.name = 'gem'; store.search(row, true); await wait(30);"
              "const e = { key: 'Enter', prevented: false, preventDefault() { this.prevented = true; } };"
              "store.key(row, e); return { prevented: e.prevented, name: row.name };", loaded())
    assert out == {"prevented": False, "name": "gem"}


def test_a_click_picks_and_marks_the_list_changed():
    out = run("installAlpine();",
              OPEN + "store.rowsDirty = false; store.search(row, true); await wait(30);"
              "store.pick(row, 'gemma-4'); return { name: row.name, open: row.open, dirty: store.rowsDirty };", loaded())
    assert out == {"name": "gemma-4", "open": False, "dirty": True}


def test_a_stale_answer_is_not_shown():
    out = run("installAlpine({ delay: (q) => (q === 'g' ? 150 : 5) });",
              OPEN + "row.name = 'g'; store.search(row, true); row.name = 'gemini'; store.search(row, true);"
              "await wait(250); return { results: row.results.length, searches: globalThis.__searches.length };", loaded())
    assert out["results"] == 3 and out["searches"] == 2


def test_a_failed_search_says_so_and_shows_nothing():
    out = run("installAlpine({ error: 'unreachable' }); const s = Alpine.store('modelConfig');"
              "s.searchModelsDetailed = async () => { throw new Error('down'); };",
              OPEN + "store.search(row, true); await wait(30); return { results: row.results.length, error: row.error, searching: row.searching };",
              loaded())
    assert out == {"results": 0, "error": True, "searching": False}


# -- the providers --------------------------------------------------------------------------------

def test_the_provider_list_is_agent_zeros_when_it_offers_one():
    out = run("installAlpine();",
              "await store.onOpen(); store.cleanup(); return store.providerChoices.map(p => p.value);", loaded())
    assert out == ["google", "openai"]


def test_without_a_list_of_its_own_the_providers_are_the_ones_kame_already_knows():
    out = run("installAlpine({ noProviders: true });",
              "await store.onOpen(); store.cleanup(); return [store.hasSearch, store.providerChoices.map(p => p.value)];",
              loaded())
    assert out[0] is True and out[1] and set(out[1]) <= {"gemini", "google", "openai"}


# -- the store is absent ---------------------------------------------------------------------------

def test_without_the_store_or_the_module_the_inputs_are_plain_and_typing_asks_nothing():
    out = run("globalThis.Alpine = undefined;",
              OPEN + "store.typeModel(row, 'gem'); await wait(350);"
              "return { has: store.hasSearch, open: row.open, results: row.results.length, rows: store.rows.length };", loaded())
    assert out == {"has": False, "open": False, "results": 0, "rows": 1}
    assert "hasSearch &amp;&amp; $store.kamePanel.providerChoices.length" in PANEL
    assert "!$store.kamePanel.hasSearch" in PANEL, "the page says the provider and the model are typed"


def test_nothing_is_saved_by_searching():
    out = run("installAlpine();",
              OPEN + "store.typeModel(row, 'gem'); await wait(350); store.key(row, { key: 'ArrowDown' }); store.pick(row, 'gemma-4');"
              "return calls.map(c => c[0]).filter(p => p.includes('set'));", loaded())
    assert out == []


def test_the_markup_has_the_keyboard_and_the_listbox():
    assert '@keydown="$store.kamePanel.key(row, $event)"' in PANEL
    assert 'role="combobox"' in PANEL and 'role="listbox"' in PANEL and 'role="option"' in PANEL
    assert "kame-fb-active" in PANEL
    assert "SEARCH_DEBOUNCE_MS = 250" in STORE_JS
    assert 'globalThis.Alpine?.store?.("modelConfig")' in STORE_JS
