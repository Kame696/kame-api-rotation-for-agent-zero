"""1.8.2.9 (U0, U2, U3) - the view logic the API Rotation page and the chip share.

`webui/kame-view.js` is a pure ES module: it turns the one status view (`status_view()`) into rings,
sentences, grouped keys and grouped decisions. It is run here with node, on a view built by the real
engine, so a number the page shows is a number the engine produced. No Agent Zero, no browser, no network.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import clean  # noqa: F401  (fixture used by name)
from test_v1_8_2_9_status_view import calm, seed_owner_case, GEM, SHARED, OPENAI_MODELS  # noqa: F401
import kame_engine as K

ROOT = Path(__file__).resolve().parent.parent
VIEW_JS = (ROOT / "webui" / "kame-view.js").as_uri()
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture(autouse=True)
def quiet_busy():
    """Busy and probe state belongs to one test."""
    tables = (K._KAME_BUSY_UNTIL, K._KAME_BUSY_KIND, K._KAME_PROBED_AT, K._KAME_BUSY_WINDOW)
    for table in tables:
        table.clear()
    yield
    for table in tables:
        table.clear()


def run_js(body: str, data) -> object:
    """Run `body` (an async function body that sees `V` = kame-view.js and `data`) and return its JSON."""
    program = (
        f'import * as V from "{VIEW_JS}";\n'
        "const chunks = []; for await (const c of process.stdin) chunks.push(c);\n"
        "const data = JSON.parse(Buffer.concat(chunks).toString('utf8'));\n"
        f"const out = await (async () => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    done = subprocess.run([NODE, "--input-type=module", "-e", program], input=json.dumps(data, default=str),
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def now_ms(view) -> float:
    return float(view["generated_at"]) * 1000.0


# -- U0: the formatter, the same words as the engine ------------------------------------------------

def test_fmt_left_says_the_same_words_as_the_engine():
    samples = [0.2, 1, 12, 44.4, 59, 61, 90, 719, 3569, 3600, 4000, 7300, 3 * 3600 - 10]
    now = time.time()
    js = run_js("return data.samples.map(s => V.fmtLeft(s, data.now));", {"samples": samples, "now": now * 1000})
    assert js == [K._kame_fmt_left(s, now) for s in samples]


def test_fmt_left_past_three_hours_is_a_clock_time_never_decimals():
    now = time.time()
    js = run_js("return V.fmtLeft(5 * 3600, data.now);", {"now": now * 1000})
    assert js.startswith("back at ") and ":" in js and "." not in js
    assert js == K._kame_fmt_left(5 * 3600, now)


# -- U2: rings, one segment per key ------------------------------------------------------------------

def test_a_ring_has_one_segment_per_key_and_shape_as_well_as_colour():
    seed_owner_case()
    view = K.status_view()
    gem = next(p for p in view["pools"] if p["identity"].startswith("gemini:"))
    out = run_js("return { seg: V.ringSegments(data.states), state: V.KEY_STATE };", {"states": gem["key_states"]})
    assert len(out["seg"]) == 2 and all(s["count"] == 1 for s in out["seg"])
    assert out["seg"][0]["start"] < out["seg"][0]["end"] <= out["seg"][1]["start"] + 1e-9
    # out-for-today is dotted, resting is thin, ready is thick: shape AND colour
    st = out["state"]
    assert st["d"]["dash"] and not st["r"]["dash"] and st["s"]["width"] < st["r"]["width"]
    assert len({st[c]["glyph"] for c in "rsdxo"}) >= 4


def test_past_forty_keys_a_ring_has_one_arc_per_state():
    states = [[f"k{i}", "r" if i % 3 else "s", None, ""] for i in range(60)]
    seg = run_js("return V.ringSegments(data);", states)
    assert len(seg) == 2 and sum(s["count"] for s in seg) == 60


def test_rings_come_in_the_order_of_the_users_list_and_the_rest_go_to_also():
    for ident in ("aaa:first", "mmm:middle", "zzz:last", "qqq:only-background"):
        K._get_identity_state(ident, GEM[:1])
    K._KAME_PRIMARY["identity"] = "zzz:last"
    K.set_fallback_models("mmm/middle\naaa/first")
    view = K.status_view()
    out = run_js("const r = V.ringModels(data); return { main: r.main.map(m => [m.rank, m.label]), also: r.also.map(m => m.label) };", view)
    assert out["main"] == [[1, "last"], [2, "middle"], [3, "first"]]
    assert out["also"] == ["only-background"]


def test_a_list_model_without_keys_is_a_hollow_ring_that_says_not_used_yet():
    K._get_identity_state("zzz:last", GEM[:1])
    K._KAME_PRIMARY["identity"] = "zzz:last"
    K.set_fallback_models("mmm/middle")
    view = K.status_view()
    out = run_js(
        "const m = V.ringModels(data).main[1]; return { word: V.modelState(m, data, data.generated_at*1000).word, svg: V.ringSvg(m, data, data.generated_at*1000) };",
        view)
    assert out["word"] == "not used yet"
    assert "kame-ring-hollow" in out["svg"] and "–" in out["svg"]


def test_the_ring_svg_has_one_arc_per_key_and_a_dashed_halo_when_the_model_is_busy():
    seed_owner_case()
    ident = "gemini:gemini-3.5-flash"
    K._KAME_PRIMARY["identity"] = ident
    K._KAME_BUSY_UNTIL[ident] = time.time() + 120
    K._KAME_BUSY_KIND[ident] = "busy"
    view = K.status_view()
    out = run_js("const m = V.ringModels(data).main[0]; return V.ringSvg(m, data, data.generated_at*1000);", view)
    assert out.count("<path") == 2
    assert 'stroke-dasharray="5 3"' in out            # busy: dashed halo
    K._KAME_BUSY_KIND[ident] = "slow"
    view = K.status_view()
    out = run_js("const m = V.ringModels(data).main[0]; return V.ringSvg(m, data, data.generated_at*1000);", view)
    assert 'stroke-dasharray="1 3"' in out            # slow: dotted halo


def test_a_busy_model_says_when_it_is_asked_again():
    seed_owner_case()
    ident = "gemini:gemini-3.5-flash"
    K._KAME_PRIMARY["identity"] = ident
    K._KAME_BUSY_UNTIL[ident] = time.time() + 120
    K._KAME_BUSY_KIND[ident] = "busy"
    K._KAME_PROBED_AT[ident] = time.time() - 5
    view = K.status_view()
    out = run_js("const m = V.ringModels(data).main[0]; return V.modelState(m, data, data.generated_at*1000);", view)
    assert out["tone"] == "warn" and out["word"].startswith("busy — asked again in ")


def test_a_ring_never_carries_a_key():
    seed_owner_case()
    view = K.status_view()
    text = json.dumps(run_js("return V.ringModels(data).main.map(m => V.ringSvg(m, data, data.generated_at*1000) + V.ringAria(m, data, data.generated_at*1000));", view))
    for secret in [SHARED] + GEM:
        assert secret not in text


# -- C8: one dead key shared by four models is ONE row --------------------------------------------------

def test_four_models_on_one_dead_key_are_one_key_row_with_their_full_names():
    seed_owner_case()
    view = K.status_view()
    out = run_js("return V.keyRows(data, data.generated_at*1000);", view)
    by = {p["provider"]: p["rows"] for p in out}
    assert len(by["openai"]) == 1
    row = by["openai"][0]
    assert row["models"] == sorted(OPENAI_MODELS) and row["shared"] is True
    assert row["modelsLine"].startswith("4 models: claude-sonnet-5, deepseek-v4-pro")
    assert "out of credit" in row["words"] and "back" in row["words"]
    assert len(by["gemini"]) == 2


# -- C3: one count, over physical keys -------------------------------------------------------------------

def test_the_chip_the_page_and_the_engine_say_the_same_count():
    seed_owner_case()
    report = K.pool_report()
    report["available"] = True
    report["active"] = True
    t = report["view"]["totals"]
    out = run_js(
        "const now = data.view.generated_at*1000; const chip = V.chipSummary(data, now);"
        "const keys = V.physicalKeys(data.view);"
        "return { label: chip.label, counts: chip.counts, line1: V.nowSentences(data.view, now).line1,"
        " physical: keys.length, ready: keys.filter(k => k.code === 'r').length, ring: (chip.ring.match(/<path|<circle/g) || []).length };",
        report)
    assert out["label"] == f"{t['ready']}/{t['keys']}" == "2/3"
    assert t["phrase"] == "2 of 3 keys ready"
    assert t["phrase"] in out["counts"]
    assert out["line1"].endswith("(2 of 2 keys ready).")      # the model's own pool, as on Hermes
    assert out["physical"] == t["keys"] and out["ready"] == t["ready"]
    assert out["ring"] == t["keys"]                 # Q5: one segment per key


def test_the_chip_with_nothing_seen_does_not_look_like_a_healthy_zero():
    report = {"available": True, "active": True, "version": "1.8.2.9",
              "view": {"pools": [], "key_groups": [], "totals": {"keys": 0, "ready": 0}, "fallback": {"entries": []}, "generated_at": 1.0}}
    out = run_js("return V.chipSummary(data, 1000);", report)
    assert out["tone"] == "idle" and out["label"] == "–"


# -- the three sentences ------------------------------------------------------------------------------------

def sentences(view, extra=None):
    merged = dict(view)
    merged.update(extra or {})
    return run_js("return V.nowSentences(data, data.generated_at*1000);", merged)


def test_three_sentences_when_everything_is_fine():
    seed_owner_case()
    K._KAME_PRIMARY["identity"] = "gemini:gemini-3.5-flash"
    K.set_fallback_models("openai/claude-sonnet-5")
    said = sentences(K.status_view())
    assert said["line1"].startswith("Ready — your next reply goes to gemini-3.5-flash (")
    assert "(2 of 2 keys ready)." in said["line1"]
    assert said["tone"] in ("good", "warn")
    assert said["line3"].startswith("Next reply: ")


def test_a_wait_is_said_with_the_time_left_and_nothing_is_spent():
    ident = "gemini:gemini-3.5-flash"
    K._get_identity_state(ident, GEM[:1])
    K._mark_key_health(ident, GEM[0], False, 600, "per_minute")
    K._KAME_PRIMARY["identity"] = ident
    K._KAME_IN_FLIGHT["w"] = {"job": "chat", "identity": ident, "started": time.time() - 30, "waiting": True,
                              "eta_until": time.time() + 600, "waiting_since": time.time() - 30}
    said = sentences(K.status_view())
    assert said["icon"] == "⏸" and "Every key is resting — back in 10 min." in said["line1"]
    assert "Nothing is spent while waiting" in said["line1"]
    assert said["tone"] == "bad"


def test_a_racing_reply_names_the_model_that_joined():
    ident = "gemini:gemini-3.5-flash"
    K._get_identity_state(ident, GEM)
    K._KAME_PRIMARY["identity"] = ident
    K._KAME_IN_FLIGHT["r"] = {"job": "chat", "identity": ident, "started": time.time() - 4, "racing": ["groq/llama"]}
    said = sentences(K.status_view())
    assert "is late" in said["line1"] and "groq/llama races it" in said["line1"]


def test_the_probe_beside_a_reply_says_the_home_model_is_not_late():
    ident = "gemini:gemini-3.5-flash"
    K._get_identity_state(ident, GEM)
    K._KAME_PRIMARY["identity"] = ident
    K._KAME_IN_FLIGHT["p"] = {"job": "chat", "identity": ident, "started": time.time() - 2, "probe": "openai/gpt-x"}
    said = sentences(K.status_view())
    assert "asking openai/gpt-x again beside it" in said["line1"]


def test_a_key_to_replace_is_the_one_thing_said_wrong():
    ident = "gemini:gemini-3.5-flash"
    K._get_identity_state(ident, GEM)
    K._mark_key_health(ident, GEM[0], False, 600, "auth")
    K._KAME_PRIMARY["identity"] = ident
    said = sentences(K.status_view())
    assert said["line2"].startswith("1 key to replace") and said["tone"] == "bad"


def test_a_page_that_is_not_rotating_says_so_without_calling_it_a_fault():
    seed_owner_case()
    said = sentences(K.status_view(), {"active": False})
    assert said["line2"].startswith("KAME is loaded but not rotating") and said["tone"] == "warn"


def test_with_no_list_the_third_sentence_is_what_the_view_says():
    seed_owner_case()
    view = K.status_view()
    view["fallback"].update({"state": "no_chain", "why": "Switching: off - no fallback models are set."})
    said = sentences(view)
    assert said["line3"] == "Switching: off - no fallback models are set."


# -- U2: what KAME did -------------------------------------------------------------------------------------

def test_six_identical_hourly_decisions_are_one_line_that_says_how_often():
    now = time.time()
    for i in range(6):
        K._KJ.EVENTS.add("rotation", identity="openai:gpt-5.4-nano", key="ka5206", reason="out of credit",
                         at=now - (5 - i) * 3600)
    K._KJ.EVENTS.add("setting", reason="you changed something", at=now)
    view = K.status_view()
    out = run_js("return V.groupMoves(data, 6);", view)
    assert len(out) == 1
    assert out[0]["count"] == 6 and "6 times, every hour" in out[0]["text"]
    assert "ka5206" in out[0]["text"] and "the account is out of credit" in out[0]["text"]
    assert out[0]["icon"] == "↻"


def test_an_older_engine_without_moves_is_grouped_here_the_same_way():
    now = time.time()
    events = [{"kind": "rotation", "key": "ka5206", "reason": "out of credit", "identity": "x:y", "at": now - i * 3600}
              for i in range(3)]
    out = run_js("return V.groupMoves({ events: data }, 6);", events)
    assert len(out) == 1 and "3 times, every hour" in out[0]["text"]


def test_no_moves_is_an_empty_list_for_the_page_to_explain():
    assert run_js("return V.groupMoves({}, 6);", {}) == []


# -- U2: speed cost and background use ---------------------------------------------------------------------

def test_the_speed_cost_line_says_what_was_spent_against_what_may_be():
    speed = {"cap_pct": 10, "hour": {"replies": 12, "extra_pct": 4}, "since_start": {"replies": 30, "extra_pct": 6}}
    out = run_js("return V.speedCostLine(data);", speed)
    assert out["text"] == ("Extra requests this hour: +4% (12 replies). Your setting allows about +10%. "
                           "Since Agent Zero started: +6%.")
    assert out["over"] is False
    quiet = run_js("return V.speedCostLine(data);", {"cap_pct": 10, "hour": {"replies": 0, "extra_pct": None}})
    assert quiet["text"] == "No replies this hour yet. Your setting allows about +10% extra requests."
    assert run_js("return V.speedCostLine(data);", {"cap_pct": 0, "hour": {}}) is None
    over = run_js("return V.speedCostLine(data);", {"cap_pct": 10, "hour": {"replies": 5, "extra_pct": 20}})
    assert over["over"] is True


def test_background_jobs_are_a_line_under_the_ring_only_from_ten_percent_up():
    view = {"quota_use": {"since": 1.0, "models": {
        "gemini:gemini-3.5-flash": {"main": 80, "side": 20, "tasks": {"title": 15, "memory": 5}},
        "openai:gpt-x": {"main": 99, "side": 1, "tasks": {"title": 1}}}, "stopped": {"title": 2},
        "suggestion": "Move titles to another model."}}
    out = run_js("const s = V.quotaShare(data); return { a: V.sideJobsLine(s, 'gemini:gemini/gemini-3.5-flash'),"
                 " b: V.sideJobsLine(s, 'openai:gpt-x'), lines: V.backgroundUseLines(data) };", view)
    assert out["a"].startswith("side jobs: 20% of requests since ") and out["b"] is None
    assert any("20 (20%) were background jobs - title 15, memory 5" in line for line in out["lines"])
    assert "Stopped early to save your keys: title 2" in out["lines"]
    assert "Move titles to another model." in out["lines"]


# -- U0: it is pure ----------------------------------------------------------------------------------------

def test_the_view_module_is_pure_no_dom_no_network_no_alpine():
    source = (ROOT / "webui" / "kame-view.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    code = "\n".join(line for line in code.splitlines() if not line.strip().startswith("//"))
    for word in ("document.", "window.", "Alpine", "fetch(", "callJsonApi", "localStorage", "import {"):
        assert word not in code, word
    assert "export function nowSentences" in code and "export function groupMoves" in code


def test_a_key_that_is_in_two_states_is_two_rows_but_one_key_in_the_provider_count():
    keys = ["AIzaSyCNT-one-000000000000000", "AIzaSyCNT-two-000000000000000", "AIzaSyCNT-three-0000000000000"]
    K._get_identity_state("gemini:gemini-3.5-flash", keys)
    K._get_identity_state("gemini:gemini-3.5-pro", keys)
    K._mark_key_health("gemini:gemini-3.5-flash", keys[0], False, 600, "per_minute")   # resting on flash only
    out = run_js("return V.keyRows(data, data.generated_at*1000);", K.status_view())
    gemini = next(p for p in out if p["provider"] == "gemini")
    assert len(gemini["rows"]) == 4                  # the first key is shown twice, once per state
    assert gemini["keyCount"] == 3                   # but the provider has three keys, as the totals say
