"""1.8.3.1 (A3 / D3) - the chip's popover, redesigned, the same sections and words as the Hermes popover.

`chipPopover(data, nowMs)` in `webui/kame-view.js` is a pure function of the status report the chip already
reads (`data.view` = `status_view()`): a header, one sentence in the state colour, the key count and speed
mode, "Your models" (a ring and ONE status per model, up to six, then `+N more`), "Last replies" (up to five,
one sentence each, the words of `tests/fixtures/live_words_1831.json`, byte-identical in the Hermes port), the
retired-key sentence when a key is retired, and the button to the full page. The per-key blocks left the
popover (they stay on the full page). Run with node on plain data: no Agent Zero, no browser, no network.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VIEW_JS = (ROOT / "webui" / "kame-view.js").as_uri()
CHIP_HTML = (ROOT / "extensions" / "webui" / "model-context-strip-end" / "kame-rotation.html").read_text(encoding="utf-8")
TABLE = json.loads((Path(__file__).with_name("fixtures") / "live_words_1831.json").read_text(encoding="utf-8"))
NODE = shutil.which("node")
NOW = 1_800_000_000

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


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


def keys(*codes):
    return [[f"k{i}", code, None, ""] for i, code in enumerate(codes)]


def pool(identity, states, **extra):
    return {"identity": identity, "label": identity.split(":", 1)[1], "keys": len(states),
            "healthy": sum(1 for s in states if s[1] in ("r", "f")), "key_states": states, **extra}


def report(**extra):
    """The status report the chip reads, for three models of one provider."""
    view = {
        "activity": {"kind": "idle"},
        "fallback": {"entries": [{"identity": "gemini:main-model", "role": "primary"},
                                 {"identity": "gemini:second", "role": "fallback"},
                                 {"identity": "gemini:third", "role": "fallback"}], "state": "on"},
        "pools": [pool("gemini:main-model", keys("r", "r", "r")), pool("gemini:second", keys("r", "r", "r")),
                  pool("gemini:third", keys("r"))],
        "key_groups": [],
        "totals": {"ready": 7, "keys": 7},
        "replies": [],
        "speed": {"mode": "adaptive"},
        "generated_at": NOW,
    }
    top = {"available": True, "active": True, "version": "1.8.3.1", "build": {"complete": True}}
    for name, value in extra.items():
        if name in top or name == "build":
            top[name] = value
        else:
            view[name] = value
    return {**top, "view": view}


def popover(data, now=NOW):
    return run_js("return V.chipPopover(data.report, data.now * 1000);", {"report": data, "now": now})


# -- the sentences of the shared table ------------------------------------------------------------------

def test_every_reply_row_of_the_shared_table_reads_the_same():
    out = run_js("return data.rows.map(r => V.replyLineText(r.reply));", {"rows": TABLE["replies"]})
    assert out == [row["line"] for row in TABLE["replies"]]


def test_the_reply_of_nothing_or_of_an_unknown_kind_is_empty():
    assert run_js("return [V.replyLineText(null), V.replyLineText({kind: 'unknown'})];", {}) == ["", ""]


def test_ordinals_say_2nd_3rd_11th_22nd():
    out = run_js("return [1,2,3,4,11,12,13,21,22,23,101,111].map(V.ordinal);", {})
    assert out == ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "101st", "111th"]


@pytest.mark.parametrize("row,line", [
    ({"at": NOW - 5, "name": "m", "total_s": 2.3}, "✓ m answered in 2.3s"),
    ({"at": NOW - 5, "name": "m", "total_s": 2.3, "attempts": 2}, "↻ m answered on the 2nd key in 2.3s"),
    ({"at": NOW - 5, "name": "m", "total_s": 2.3, "backup": True}, "⚡ a backup key of m answered first (2.3s)"),
    ({"at": NOW - 5, "name": "m", "for_name": "o", "why": "busy", "for": "x/o", "total_s": 2.3},
     "↪ m answered for o (busy) in 2.3s"),
    ({"at": NOW - 5, "name": "m", "for_name": "o", "for": "x/o", "total_s": 2.3}, "↪ m answered for o in 2.3s"),
    ({"at": NOW - 5, "name": "m", "total_s": 2.3, "waited_s": 40}, "⏳ waited 40s for a key, then m answered in 2.3s"),
    ({"at": NOW - 5, "name": "m", "total_s": 2.3, "cut": True}, "↻ m's answer was cut off and finished on another key"),
    ({"at": NOW, "failed": True, "name": "m", "reason": "it timed out"}, "✕ no answer: it timed out"),
    # the most telling thing wins: a stand-in that also retried is still a stand-in
    ({"at": NOW - 5, "name": "m", "for_name": "o", "for": "x/o", "total_s": 2.3, "attempts": 3},
     "↪ m answered for o in 2.3s"),
    # an older row has only the label the engine always had: it is said as it was
    ({"at": NOW - 5, "model": "gemini/gemini-3.8-flash", "total_s": 1.0}, "✓ gemini/gemini-3.8-flash answered in 1.0s"),
])
def test_what_the_engine_publishes_becomes_one_of_those_sentences(row, line):
    assert run_js("return V.replyLineText(V.replyFromRow(data));", row) == line


# -- the live line, in the page ---------------------------------------------------------------------------

@pytest.mark.parametrize("row", TABLE["live"], ids=lambda r: f"{r['kind']}-{r['line'][:24]}")
def test_the_page_says_every_state_of_the_live_line_in_the_shared_words(row):
    out = run_js("return V.liveLineText(data.activity, data.activity.eta_s ?? null);", row)
    assert out == row["line"]


def test_the_page_says_the_live_line_with_the_other_ports_field_names():
    out = run_js("""return [
      V.liveLineText({kind: 'calling', model: 'm', healthy: 3, keys: 4, attempt: 1}, null),
      V.liveLineText({kind: 'calling', phase: 'racing', model: 'm', healthy: 3, keys: 3}, null),
      V.liveLineText({kind: 'calling', phase: 'standin', model: 'm', other: 'n', healthy: 3, keys: 3}, null),
      V.liveLineText({kind: 'stitching', model: 'm', resume: 2, budget: 5}, null),
      V.liveLineText({kind: 'stitching', model: 'm', resume: 2, budget: null}, null),
      V.liveLineText({kind: 'recovered', model: 'm', waited_s: 130}, null),
      V.liveLineText({kind: 'idle'}, null), V.liveLineText(null, null)];""", {})
    assert out == ["↻ m: trying another key · 3 of 4 keys ready",
                   "⚡ m is slow to start - a backup key is asking too",
                   "↪ m can't answer now - n is answering instead",
                   "↻ The answer was cut off - continuing it on another key (2 of 5)",
                   "↻ The answer was cut off - continuing it on another key",
                   "↻ m is back after 2m 10s", "", ""]


# -- the popover's content ------------------------------------------------------------------------------------

def test_a_ready_pool_reads_as_one_sentence_a_count_and_three_rows():
    v = popover(report())
    assert v["title"] == "API Rotation" and v["version"] == "KAME 1.8.3.1" and v["button"] == "Open API Rotation"
    assert v["sentence"] == "Ready - next reply: main-model"
    assert v["keysLine"] == "7 of 7 keys ready · Speed: Balanced"
    assert [m["name"] for m in v["models"]] == ["main-model", "second", "third"]
    assert v["models"][0]["tags"] == ["main"] and v["models"][1]["tags"] == []
    assert [m["status"] for m in v["models"]] == ["3 of 3 ready", "3 of 3 ready", "1 of 1 ready"]
    assert v["retired"] == "" and v["replies"] == [] and v["emptyReplies"] == "No reply yet in this session."
    assert v["tone"] == "good" and v["stale"] is False and v["more"] == 0


def test_every_row_has_a_ring_with_a_segment_per_key():
    v = popover(report())
    assert [len(m["segments"]) for m in v["models"]] == [3, 3, 1]
    assert {s["code"] for m in v["models"] for s in m["segments"]} == {"r"}


def test_a_refused_key_is_said_in_the_row_the_sentence_and_the_retired_paragraph():
    states = keys("x")
    v = popover(report(pools=[pool("gemini:main-model", keys("r", "r", "r")), pool("gemini:second", keys("r")),
                              pool("gemini:third", states, retired=1)],
                       totals={"ready": 4, "keys": 5, "retired": 1, "rejected": 0}))
    assert v["models"][2]["status"] == "1 key refused - replace it" and v["models"][2]["tone"] == "bad"
    assert v["sentence"] == "1 key to replace — the provider refused it as credentials."
    assert v["retired"].startswith("1 key is out of rotation")
    assert ".env" in v["retired"]                    # Agent Zero's own words for where the key lives


def test_the_speed_mode_is_said_in_one_word():
    assert popover(report(speed={"mode": "max"}))["keysLine"].endswith("Speed: Faster")
    assert popover(report(speed={"mode": "off"}))["keysLine"].endswith("Speed: Saver")


def test_the_live_line_is_the_sentence_while_a_call_runs():
    v = popover(report(activity={"kind": "calling", "identity": "gemini:main-model", "model": "gemini/main-model",
                                 "name": "main-model", "healthy": 3, "keys": 3, "attempt": 1}))
    assert v["sentence"] == "⏳ Asking main-model · 3 of 3 keys ready"
    assert v["models"][0]["tags"] == ["main"]
    v = popover(report(activity={"kind": "calling", "identity": "gemini:second", "name": "second",
                                 "healthy": 1, "keys": 3, "attempt": 1}))
    assert v["models"][1]["tags"] == ["in use"] and v["models"][0]["tags"] == ["main"]
    assert v["sentence"] == "↻ second: trying another key · 1 of 3 keys ready"


def test_a_ladder_model_answering_is_said_with_the_model_the_reply_started_on():
    v = popover(report(activity={"kind": "calling", "phase": "standin", "identity": "gemini:second",
                                 "name": "main-model", "other": "second", "healthy": 3, "keys": 3}))
    assert v["sentence"] == "↪ main-model can't answer now - second is answering instead"
    assert v["models"][1]["tags"] == ["in use"]


def test_a_resting_model_counts_down_to_its_first_key():
    v = popover(report(activity={"kind": "waiting", "identity": "gemini:main-model", "name": "main-model",
                                 "eta_s": 130, "healthy": 0, "keys": 3},
                       pools=[pool("gemini:main-model", [["k0", "s", NOW + 130, ""], ["k1", "s", NOW + 300, ""]])]), now=NOW)
    assert v["models"][0]["status"] == "resting · back in 2m 10s"


def test_a_busy_model_says_when_it_is_asked_again_and_the_sentence_is_the_worst_thing_wrong():
    busy = report(fallback={"state": "on", "entries": [
        {"identity": "gemini:main-model", "role": "primary", "busy_kind": "busy", "busy_until": NOW + 90, "probe_at": NOW + 30},
        {"identity": "gemini:second", "role": "fallback"}, {"identity": "gemini:third", "role": "fallback"}]})
    v = popover(busy)
    assert v["models"][0]["status"] == "busy · asked again in 30s" and v["models"][0]["tone"] == "warn"
    assert v["sentence"].startswith("main-model is busy")
    slow = report(fallback={"state": "on", "entries": [
        {"identity": "gemini:main-model", "role": "primary", "busy_kind": "slow", "busy_until": NOW + 600},
        {"identity": "gemini:second", "role": "fallback"}]})
    assert popover(slow)["models"][0]["status"] == "slow today · skipped for 10m 00s"


def test_resting_out_for_today_and_not_used_yet():
    v = popover(report(pools=[pool("gemini:main-model", [["k0", "s", NOW + 130, ""], ["k1", "s", NOW + 300, ""]])]))
    assert v["models"][0]["status"] == "resting · back in 2m 10s"
    v = popover(report(pools=[pool("gemini:main-model", keys("d", "d"))]))
    assert v["models"][0]["status"] == "out for today"
    assert v["models"][1]["status"] == "not used yet" and v["models"][1]["tone"] == "quiet"
    unused = popover(report(pools=[pool("gemini:main-model", keys("r", "r", "r"), unused=True)]))
    assert unused["models"][0]["status"] == "not used yet"


def test_the_last_replies_are_five_at_most_newest_first_one_sentence_each():
    rows = [{"at": NOW - i * 60, "name": "m", "model": "gemini/m", "total_s": 1 + i} for i in range(8)]
    v = popover(report(replies=rows))
    assert len(v["replies"]) == 5
    assert v["replies"][0]["line"] == "✓ m answered in 1.0s" and v["replies"][4]["line"] == "✓ m answered in 5.0s"
    assert re.fullmatch(r"\d\d:\d\d", v["replies"][0]["time"])


def test_more_than_six_models_show_six_and_a_count():
    entries = [{"identity": f"gemini:model-{i}", "role": "primary" if i == 0 else "fallback"} for i in range(9)]
    v = popover(report(fallback={"state": "on", "entries": entries}, pools=[]))
    assert len(v["models"]) == 6 and v["more"] == 3


def test_an_old_reading_is_said_to_be_old_never_presented_as_live():
    v = popover(report(), now=NOW + 600)
    assert v["stale"] is True and v["tone"] == "muted"


def test_nothing_read_yet_and_not_rotating_have_their_own_sentences():
    out = run_js("return [V.chipPopover(null, 0), V.chipPopover({available: true, active: false, reason: 'no hook', view: {}}, 0)];", {})
    assert out[0]["sentence"] == "KAME API Rotation has not been read yet." and out[0]["version"] == "KAME"
    assert out[1]["sentence"] == "KAME API Rotation is loaded but not rotating: no hook" and out[1]["models"] == []


def test_without_a_list_the_first_model_is_not_called_main():
    one = report(fallback={"state": "no_chain", "entries": [{"identity": "gemini:main-model", "role": "primary"}]},
                 pools=[pool("gemini:main-model", keys("r", "r"))], totals={"ready": 2, "keys": 2})
    assert popover(one)["models"][0]["tags"] == []


# -- the markup ----------------------------------------------------------------------------------------------------------

def test_the_chip_draws_the_popover_from_that_model_and_the_per_key_blocks_are_gone():
    assert "chipPopover" in (ROOT / "webui" / "kame-rotation-store.js").read_text(encoding="utf-8")
    for section in ("Your models", "Last replies", "Open API Rotation"):
        assert section in CHIP_HTML, section
    assert "kame-rot-pool" not in CHIP_HTML.split("<style>", 1)[0], "the per-key blocks stay on the full page"
    assert "view.providers" not in CHIP_HTML and "kame-rot-key" not in CHIP_HTML.split("<style>", 1)[0]


def test_the_popover_closes_on_a_click_outside_and_on_escape():
    assert "@click.outside" in CHIP_HTML and "@keydown.escape.window" in CHIP_HTML
