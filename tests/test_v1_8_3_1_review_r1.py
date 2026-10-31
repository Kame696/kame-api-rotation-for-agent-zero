"""1.8.3.1, review round 1 (PLAN_1.8.3.1 section 5), Agent Zero rows.

R5  the first-token wait: Automatic = Hermes' 120 s ceiling for both classes, Off = 120 s; no allowlist entry left
R7  inside a ladder hop a model this reply already left is not a candidate again
R8  a failed reply that is retried writes one "no answer" row
R9  the shared fixture resets the call counters (a leftover timestamp made the key-leak test false-fail in a group run)
R10 the popover names a model without LiteLLM's route (`gemini-3.8-flash`, not `gemini/gemini-3.8-flash`)
R11 words held back by the one-a-second rule are written when the second is over
R12 "back after ..." is said after the answer, measured from the start of the call
R14 when the next model leads a race, the chat's line says who answers instead
R15 the popover's colours are the Hermes popover's
R16 the Events tab's Last replies uses the popover's sentences
R17 a stand-in that has to wait for a key says so ("Every key of <it> is resting")
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS, Refusal, clean, make_ctx, streaming, _in_order  # noqa: F401
from test_v1_8_2_9_silent_two_models import world  # noqa: F401  (fixture used by name)
from test_v1_8_2_9_status_line import FakeAgent, PLAIN, rest_all  # noqa: F401
from kame_vclock import vt  # noqa: F401
import kame_engine as K

ROOT = Path(__file__).resolve().parent.parent
WEBUI = ROOT / "webui"
VIEW_JS = (WEBUI / "kame-view.js").as_uri()
CHIP_HTML = (ROOT / "extensions" / "webui" / "model-context-strip-end" / "kame-rotation.html").read_text(encoding="utf-8")
PANEL_HTML = (WEBUI / "kame-panel.html").read_text(encoding="utf-8")
HERMES = ROOT.parent.parent / "hermes"
HERMES_PLUGIN_JS = HERMES / "hermes-kame-api-rotation" / "desktop" / "plugin.js"
NODE = shutil.which("node")
NAME = "gemini-3.8-flash"


@pytest.fixture(autouse=True)
def calm(clean):
    K.set_current_agent(None)
    K.set_wait_notice(True)
    K._KAME_IN_FLIGHT.clear()
    yield
    K.set_current_agent(None)
    K.set_wait_notice(False)
    K._KAME_IN_FLIGHT.clear()


def lines(agent):
    return [h.split(": ", 1)[1] for h in agent.headings() if h != PLAIN]


# -- R5 ---------------------------------------------------------------------------------------------------

def test_r5_automatic_waits_hermes_120_seconds_for_both_classes():
    assert K._KAME_AUTO_SILENCE_S == {False: (30.0, 120.0, 120.0), True: (120.0, 120.0, 120.0)}
    assert K._KAME_FIRST_TOKEN_OFF_S == 120.0
    # Off never waits less than Automatic can
    assert max(c for floor, ceiling, cold in K._KAME_AUTO_SILENCE_S.values() for c in (ceiling, cold)) \
        <= K._KAME_FIRST_TOKEN_OFF_S


def test_r5_a_long_prompt_still_asks_for_more_patience_up_to_the_ceiling():
    import kame_hedging as H
    for _ in range(40):
        H.HEDGE_TRACKER.record_latency(ID, 1.0, stream=True, reasoning=False)
    assert K._kame_auto_silence(ID, False, 0) == 30.0
    assert K._kame_auto_silence(ID, False, 80_000) == 70.0
    assert K._kame_auto_silence(ID, False, 400_000) == 120.0


def test_r5_the_parity_check_has_no_first_token_exception_left():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import parity_check
    finally:
        sys.path.pop(0)
    assert not [k for k in parity_check.ALLOWED if "first-token" in str(k)]


# -- R7 ---------------------------------------------------------------------------------------------------

LIST = "groq/a, groq/b, groq/c"


def in_hop(ctx_identity, visited, origin="groq:a"):
    return K._KAME_FALLBACK_HOP.set({"origin": origin, "to": ctx_identity, "visited": list(visited)})


def test_r7_a_model_the_reply_already_left_is_not_a_candidate_inside_a_hop(world):
    K.set_fallback_models(LIST)
    ctx = {"call_type": "Chat", "main_chat": True, "identity": "groq:b"}
    assert [e["identity"] for e in K._kame_ladder_after(ctx, "groq:b")] == ["groq:a", "groq:c"]
    token = in_hop("groq:b", ["groq:a"])
    try:
        assert [e["identity"] for e in K._kame_ladder_after(ctx, "groq:b")] == ["groq:c"]
    finally:
        K._KAME_FALLBACK_HOP.reset(token)


def test_r7_the_widen_and_the_slow_watch_inside_a_hop_skip_the_visited_models(world):
    K.set_fallback_models(LIST)
    ctx = {"call_type": "Chat", "main_chat": True, "identity": "groq:b", "kwargs": {}, "messages": []}
    token = in_hop("groq:b", ["groq:a", "groq:c"])
    try:
        race = types.SimpleNamespace(cross_legs={})
        assert K._kame_free_entry_outside(ctx, "groq:b", race) is None
        assert K._kame_slow_after(ctx, "groq:b", False) == 0.0, "nothing left to fall back to"
    finally:
        K._KAME_FALLBACK_HOP.reset(token)


def test_r7_the_walk_hands_each_hop_the_models_it_has_left(world, monkeypatch):
    K.set_fallback_models(LIST)
    hops = []
    original = world.__class__  # noqa: F841  (the world's models record nothing; read the hop in the call)
    import test_v1_8_2_9_silent_two_models as S

    async def spy(self, **kwargs):
        hops.append((self.model_name, dict(K._KAME_FALLBACK_HOP.get() or {})))
        if self.model_name in ("groq/a", "groq/b"):
            raise K._KameSwitchModel("every key resting")
        return (f"answer from {self.model_name}", "")
    monkeypatch.setattr(S.SilentModel, "unified_call", spy)
    ctx, _ = make_ctx(streaming({}))
    rest_all(KEYS, 3600)
    result = world.vt.run(K._kame_carousel(None, ctx), limit=60)
    assert result[0] == "answer from groq/c"
    seen = {name: hop.get("visited") for name, hop in hops}
    same = lambda got, want: len(got) == len(want) and all(K._kame_names_match(g, w) for g, w in zip(got, want))
    assert same(seen["groq/a"], [ID])
    assert same(seen["groq/b"], [ID, "groq:a"]), seen
    assert same(seen["groq/c"], [ID, "groq:a", "groq:b"]), seen
    assert K._KAME_FALLBACK_HOP.get() is None, "the hop does not outlive the call"


# -- R8 ---------------------------------------------------------------------------------------------------

class FakeStatus(Exception):
    def __init__(self, status):
        super().__init__("x")
        self.status_code = status


def failed_rows():
    return [r for r in K.replies_report() if r.get("failed")]


def test_r8_retries_of_one_failed_reply_write_one_row():
    for _ in range(5):
        K._kame_note_failed({"call_type": "Chat", "identity": ID}, FakeStatus(429))
    rows = failed_rows()
    assert len(rows) == 1 and rows[0]["reason"] == "the provider answered 429"


def test_r8_another_model_is_another_row_and_the_latest_reason_wins():
    K._kame_note_failed({"call_type": "Chat", "identity": ID}, FakeStatus(429))
    K._kame_note_failed({"call_type": "Chat", "identity": ID}, FakeStatus(503))
    K._kame_note_failed({"call_type": "Chat", "identity": "groq:a"}, FakeStatus(500))
    rows = failed_rows()
    assert len(rows) == 2
    assert {r["reason"] for r in rows} == {"the provider answered 503", "the provider answered 500"}


def test_r8_a_failure_after_the_window_is_a_new_row(monkeypatch):
    K._kame_note_failed({"call_type": "Chat", "identity": ID}, FakeStatus(429))
    later = time.time() + K._KAME_FAILED_ROW_WINDOW_S + 5
    monkeypatch.setattr(time, "time", lambda: later)
    K._kame_note_failed({"call_type": "Chat", "identity": ID}, FakeStatus(429))
    assert len(failed_rows()) == 2


# -- R9 ---------------------------------------------------------------------------------------------------

def test_r9_a_dirty_test_leaves_a_call_timestamp_behind():
    # what a virtual-clock test leaves behind: a timestamp whose digits contain the key fixtures' "000000"
    K._KAME_CALLS["last_call_at"] = 1_800_000_005.987
    K._KAME_REPLIES.append({"at": 1_800_000_005.987, "model": "m", "for": "", "first_s": 1.0, "total_s": 1.0})


def test_r9_the_clean_fixture_resets_the_counters(clean):
    assert K._KAME_CALLS["last_call_at"] == 0.0
    assert not list(K._KAME_REPLIES)
    assert not K._KAME_FAILED_ROWS


# -- R10 / R15 / R16 (node) -------------------------------------------------------------------------------

pytestmark_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_js(body: str, data) -> object:
    program = (
        f'import * as V from "{VIEW_JS}";\n'
        "const chunks = []; for await (const c of process.stdin) chunks.push(c);\n"
        "const data = JSON.parse(Buffer.concat(chunks).toString('utf8'));\n"
        f"const out = await (async () => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    done = subprocess.run([NODE, "--input-type=module", "-e", program], input=json.dumps(data),
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def pool(identity, label, states):
    return {"identity": identity, "label": label, "keys": len(states),
            "healthy": sum(1 for s in states if s[1] in ("r", "f")), "key_states": states}


def report():
    keys = [[f"k{i}", "r", None, ""] for i in range(3)]
    view = {
        "activity": {"kind": "idle"},
        "fallback": {"entries": [{"identity": "google:gemini/gemini-3.8-flash", "role": "primary"},
                                 {"identity": "openrouter:meta-llama/llama-4", "role": "fallback"}], "state": "on"},
        "pools": [pool("google:gemini/gemini-3.8-flash", "gemini/gemini-3.8-flash", keys),
                  pool("openrouter:meta-llama/llama-4", "meta-llama/llama-4", keys)],
        "key_groups": [], "totals": {"ready": 6, "keys": 6}, "replies": [], "speed": {"mode": "adaptive"},
        "generated_at": 1_800_000_000,
    }
    return {"available": True, "active": True, "version": "1.8.3.1", "build": {"complete": True}, "view": view}


@pytestmark_node
def test_r10_the_popover_names_a_model_without_the_route():
    out = run_js("const p = V.chipPopover(data, 1800000000 * 1000); return [p.sentence, p.models.map(m => m.name)];",
                 report())
    assert out[0] == "Ready - next reply: gemini-3.8-flash"
    assert out[1] == ["gemini-3.8-flash", "meta-llama/llama-4"], "a vendor path that belongs to the model stays"


@pytestmark_node
@pytest.mark.parametrize("identity,name", [
    ("google:gemini/gemini-3.8-flash", "gemini-3.8-flash"),
    ("google:gemini-3.7-flash", "gemini-3.7-flash"),
    ("groq:groq/llama-3.3-70b", "llama-3.3-70b"),
    ("openrouter:openrouter/meta-llama/llama-4", "meta-llama/llama-4"),
    ("openrouter:meta-llama/llama-4", "meta-llama/llama-4"),
])
def test_r10_the_js_name_is_the_engines_name(identity, name):
    assert run_js("return V.modelName({identity: data.i, label: 'x'});", {"i": identity}) == name
    assert K._kame_live_name(identity) == name


def test_r15_the_popover_colours_are_the_hermes_popovers():
    hermes = HERMES_PLUGIN_JS.read_text(encoding="utf-8") if HERMES_PLUGIN_JS.exists() else None
    if hermes is None:
        pytest.skip("the Hermes tree is not here (public repository)")
    ring = dict(re.findall(r"(bad|day|ready|rest):\s*['\"]([^'\"]+)['\"]", hermes.split("const RING_COLOUR = {", 1)[1].split("}", 1)[0]))
    view = (WEBUI / "kame-view.js").read_text(encoding="utf-8")
    mine = dict(re.findall(r"(bad|day|ready|rest):\s*\"([^\"]+)\"", view.split("export const RING_COLOUR = {", 1)[1].split("}", 1)[0]))
    # the two fallbacks of the host's own theme variables differ by the host (var(--ui-red, ...) / var(--color-error, ...))
    hex_of = lambda text: re.search(r"#[0-9a-fA-F]{6}", text).group(0).lower()
    assert hex_of(mine["ready"]) == hex_of(ring["ready"]) == "#38bdf8"
    assert hex_of(mine["rest"]) == hex_of(ring["rest"]) == "#f59e0b"
    assert hex_of(mine["bad"]) == hex_of(ring["bad"]) == "#ef4444"


def test_r15_the_dots_and_the_status_words_use_the_rings_colours():
    assert ".kame-rot-dot-good { background: #38bdf8; }" in CHIP_HTML
    assert ".kame-rot-dot-warn { background: #f59e0b; }" in CHIP_HTML
    assert ".kame-rot-dot-bad { background: #ef4444; }" in CHIP_HTML
    assert ".kame-rot-t-warn { color: #f59e0b; }" in CHIP_HTML
    assert ".kame-rot-t-bad { color: #ef4444; }" in CHIP_HTML
    assert "#d9534f" not in CHIP_HTML and "#d99a2b" not in CHIP_HTML and "#22c55e" not in CHIP_HTML.lower()


def test_r16_the_events_tab_says_the_replies_in_the_popovers_sentences():
    card = PANEL_HTML.split("<div class=\"kame-card-title\">Last replies</div>", 1)[1].split("</template>", 1)[0]
    assert "$store.kamePanel.replyLine(r)" in card
    assert "x-text=\"r.model\"" not in card, "the old words (model, for, first word, total) are gone"
    store = (WEBUI / "kame-panel-store.js").read_text(encoding="utf-8")
    assert "replyLineText(replyFromRow(r))" in store


@pytestmark_node
def test_r16_a_reply_row_reads_the_same_in_the_events_tab_and_the_popover():
    rows = [
        {"at": 1_800_000_000, "name": "m", "total_s": 2.3},
        {"at": 1_800_000_000, "name": "m", "for_name": "o", "for": "x/o", "why": "busy", "total_s": 3.0},
        {"at": 1_800_000_000, "failed": True, "name": "m", "reason": "it timed out"},
    ]
    out = run_js("return data.map(r => V.replyLineText(V.replyFromRow(r)));", rows)
    assert out == ["✓ m answered in 2.3s", "↪ m answered for o (busy) in 3.0s", "✕ no answer: it timed out"]


# -- R11 --------------------------------------------------------------------------------------------------

def test_r11_the_last_words_held_by_the_one_a_second_rule_are_written_when_the_second_is_over(vt):
    agent = FakeAgent()

    async def go():
        for i in range(3):
            K._kame_write_heading(f"line {i}", ("waiting",))      # 0, 0.25, 0.5 s: only line 0 is written at once
            await asyncio.sleep(0.25)
        await asyncio.sleep(2.0)                                  # nothing else says anything
    vt.run(agent.call_chat_model(go()))
    written = [(t, h) for t, h in agent.item.updates]
    assert [h for _t, h in written] == ["A0: line 0", "A0: line 2"], "line 1 was superseded, line 2 is not lost"
    assert written[1][0] - written[0][0] >= 0.99


def test_r11_a_newer_state_or_the_release_drops_what_was_held(vt):
    agent = FakeAgent()

    async def go():
        K._kame_write_heading("line 0", ("waiting",))
        K._kame_write_heading("line 1", ("waiting",))            # held
        K._kame_write_heading("another state", ("calling",))     # a different state: written at once
        await asyncio.sleep(2.0)
        K._kame_write_heading("line 2", ("waiting",))
        K._kame_write_heading("line 3", ("waiting",))            # held
        K._kame_write_heading(None)                               # Agent Zero's words are back
        await asyncio.sleep(2.0)
    vt.run(agent.call_chat_model(go()))
    assert agent.headings() == ["A0: line 0", "A0: another state", "A0: line 2", PLAIN]


# -- R12 --------------------------------------------------------------------------------------------------

def test_r12_back_after_is_said_after_the_answer_measured_from_the_call_start(vt):
    rest_all(KEYS[:1], 5)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}), keys=KEYS[:1])
    result = vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=120)
    assert result[0].startswith("answer from")
    said = lines(agent)
    assert re.fullmatch(rf"↻ {NAME} is back after [5-8]s", said[-1]), said      # the 5 s rest, from the start of the call
    asking = [i for i, line in enumerate(said) if line.startswith("⏳ Asking") or "trying another key" in line]
    assert asking and asking[-1] < len(said) - 1, "the attempt after the wait is an ordinary asking line"
    assert not any("is back" in line for line in said[:-1]), "said once, after the answer"
    assert agent.headings()[-1] == PLAIN


def test_r12_a_call_that_never_waited_says_nothing_of_the_kind(vt):
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}))
    vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert not any("is back" in line for line in lines(agent))


# -- R14 --------------------------------------------------------------------------------------------------

def test_r14_when_the_next_model_leads_the_race_the_line_says_who_answers_instead(world):
    K.set_fallback_models("google/gemini-3.7-flash")
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({k: 30.0 for k in KEYS}))
    result = world.vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert result[0] == "answer from google/gemini-3.7-flash"
    assert ctx.get("answered_by") == "google:gemini-3.7-flash"
    assert f"↪ {NAME} can't answer now - gemini-3.7-flash is answering instead" in lines(agent)
    assert agent.headings()[-1] == PLAIN


# -- R17 --------------------------------------------------------------------------------------------------

def test_r17_a_stand_in_that_waits_for_a_key_says_every_key_is_resting(world, monkeypatch):
    K.set_fallback_models("google/gemini-3.7-flash")
    standin = "google:gemini-3.7-flash"
    import test_v1_8_2_9_silent_two_models as S

    async def nested(self, **kwargs):
        # the stand-in is a KAME-wrapped model: its own carousel runs inside the hop, with its keys resting 4 s
        ctx2, _ = make_ctx(streaming({}), keys=KEYS[:1])
        ctx2["identity"] = standin
        rest_all(KEYS[:1], 4, identity=standin)
        return await K._kame_carousel(None, ctx2)
    monkeypatch.setattr(S.SilentModel, "unified_call", nested)
    rest_all(KEYS, 3600)
    agent = FakeAgent()
    ctx, _ = make_ctx(streaming({}))
    result = world.vt.run(agent.call_chat_model(K._kame_carousel(None, ctx)), limit=60)
    assert result[0].startswith("answer from")
    said = lines(agent)
    stood = f"↪ {NAME} can't answer now - gemini-3.7-flash is answering instead"
    waited = [line for line in said if "Every key of gemini-3.7-flash is resting" in line]
    assert waited, said
    assert said.index(stood) < said.index(waited[0]), "first who answers instead, then the wait"
    assert said.count(stood) >= 2, "after the wait the line says again who is answering instead"
