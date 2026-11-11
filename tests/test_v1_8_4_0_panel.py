"""1.8.4.0 U1 to U6 and E4/E6 on the page: one count, a truthful empty line, a neutral dot, single
numbering, quick controls with one line and a "Why?" toggle, the lifetime line and the star.

Logic runs in node against the real status report; markup is read as text. The same words as the Hermes
port (`tests/test_1_8_4_0_panel.py`).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_v1_8_2_9_page_store import loaded, run_store, fresh  # noqa: F401
from test_v1_8_2_7_speed_fallback import clean  # noqa: F401
import kame_engine as K
import kame_settings as S

ROOT = Path(__file__).resolve().parent.parent
PANEL = (ROOT / "webui" / "kame-panel.html").read_text(encoding="utf-8")
REPO = "https://github.com/Kame696/kame-api-rotation-for-agent-zero"
REGISTER = (ROOT / "tests" / "js" / "register.mjs").as_uri()
NODE = shutil.which("node")


def pure(body: str):
    """Run `body` with the pure helpers of kame-view.js in scope."""
    program = ('const v = await import("/plugins/api_rotation_by_kame/webui/kame-view.js");\n'
               f"const out = await (async () => {{ {body} }})();\n"
               "process.stdout.write(JSON.stringify(out)); process.exit(0);")
    done = subprocess.run([NODE, "--import", REGISTER, "--input-type=module", "-e", program],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def pool(identity, states, unused=False):
    return {"identity": identity, "label": identity.split(":", 1)[1], "keys": len(states), "healthy": sum(s in "rf" for s in states),
            "unused": unused, "key_states": [[f"k{n:05d}", code, None, ""] for n, code in enumerate(states)]}


# -- U1 ------------------------------------------------------------------------------------------
def test_the_header_the_chip_and_the_sentence_say_the_same_fraction():
    view = {"pools": [pool("gemini:a", "rrrrrrrsssssss"), pool("gemini:b", "rrrrrrrrrrrrrr")],
            "totals": {"keys": 14, "ready": 14, "resting": 0}, "fallback": {"entries": []}}
    out = pure(f"const view = {json.dumps(view)}; const one = v.oneCount(view, 0);"
               "return { one, said: v.nowSentences(view, 0).line1, chip: v.chipSummary({available: true, active: true, view}, 0) };")
    assert out["one"]["phrase"] == "7 of 14 keys ready"
    assert "7 of 14 keys ready" in out["said"]
    assert out["chip"]["label"] == "7/14" and "7 of 14" in out["chip"]["counts"]


def test_with_no_model_to_ask_the_physical_keys_are_counted_once():
    view = {"pools": [pool("gemini:a", "ss"), pool("gemini:b", "ss")], "totals": {"keys": 2, "ready": 0}, "fallback": {"entries": []}}
    out = pure(f"return v.oneCount({json.dumps(view)}, 0);")
    assert out["model"] is None and (out["ready"], out["total"]) == (0, 2)


# -- U2 ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("view,words", [
    ({"replies": [], "totals": {"keys": 3, "ready": 3}}, "No replies yet."),
    ({"replies": [{"at": 1}], "totals": {"keys": 3, "ready": 0}}, "Waiting for your keys. Nothing went wrong on your side."),
    ({"replies": [{"at": 1}], "totals": {"keys": 3, "ready": 2}}, "Nothing to report: every reply went straight out."),
    ({"replies": [], "lifetime": {"replies": 4}, "totals": {"keys": 3, "ready": 3}}, "Nothing to report: every reply went straight out."),
])
def test_the_empty_line_depends_on_the_state(view, words):
    assert pure(f"return v.movesEmptyLine({json.dumps(view)});") == words
    assert "—" not in words


def test_the_page_uses_the_getter_not_a_fixed_sentence():
    assert "every reply went straight out" not in PANEL
    assert "$store.kamePanel.movesEmpty" in PANEL


# -- U3 ------------------------------------------------------------------------------------------
def test_the_dot_is_neutral_at_zero_of_zero():
    out = run_store("await store.refresh(); return store.headerStatus.tone;", loaded())
    assert out in ("good", "ok", "idle", "warn", "bad")
    empty = {"pools": [], "key_groups": [], "totals": {"keys": 0, "ready": 0}, "fallback": {"entries": []}}
    tone = run_store("await store.refresh(); store.data.view = " + json.dumps(empty) + "; return store.headerStatus.tone;", loaded())
    assert tone == "idle"
    assert ".kame-dot-idle" in PANEL


# -- U4 ------------------------------------------------------------------------------------------
def test_the_list_is_numbered_once():
    assert "e.n + '.'" not in PANEL
    for block in PANEL.split('<div class="kame-fblist">')[1:]:
        items = block.split("</ol>", 1)[0]
        assert "<li><span class=\"kame-mono\" x-text=\"e.identity\">" in items      # the <ol> numbers the rows
    # the editable list hides its list marker and writes its own number: one number there too
    assert ".kame-fb { list-style: none;" in PANEL


# -- U5 ------------------------------------------------------------------------------------------
def test_the_three_quick_controls_have_their_one_line():
    assert S.LINE == {"hedge_mode": "How hard KAME works to answer sooner.",
                      "model_switch": "When your model cannot answer, the next one on your list does.",
                      "reply_notice": "A short notice after each reply."}
    out = run_store("await store.refresh(); return store.quickCards[0].rows.map(m => [m.name, m.line]);", loaded())
    assert out == [[k, S.LINE[k]] for k in ("hedge_mode", "model_switch", "reply_notice")]


def test_the_long_text_is_behind_the_toggle_and_closed_by_default():
    out = run_store("await store.refresh(); const m = store.quickCards[0].rows[1]; const closed = store.details(m);"
                    "store.toggleWhy(m.name); const open = store.details(m);"
                    "return { closed, open, sentence: m.sentence.length > 0, other: store.details({name: 'x', line: ''}) };", loaded())
    assert out == {"closed": False, "open": True, "sentence": True, "other": True}
    quick = PANEL.split('<div class="kame-card-title">Quick controls</div>', 1)[1].split("All settings", 1)[0]
    assert ">Why?<" in quick and "When to change it: " in quick     # no word deleted: moved
    assert "$store.kamePanel.details(m)" in quick


def test_the_settings_tab_still_shows_all_of_it():
    settings_tab = PANEL.split("All settings", 1)[1]
    assert "$store.kamePanel.details(m)" not in settings_tab


# -- U6 and E6 -----------------------------------------------------------------------------------
def test_the_lifetime_line_and_the_star():
    none = pure("return v.lifetimeLine({replies: 0, saved: 0});")
    assert none is None
    few = pure("return v.lifetimeLine({replies: 1240, saved: 9, since: 1760000000}, 'en-US');")
    assert few["star"] is False and "1,240 replies, 9 saved from a provider error." in few["text"] and few["text"].startswith("Since ")
    many = pure("return v.lifetimeLine({replies: 1240, saved: 10, since: 1760000000}, 'en-US');")
    assert many["star"] is True and many["starText"] == "A star helps others find it."
    assert pure("return v.lifetimeLine({replies: 1, saved: 0}, 'en-US').text;") == "1 reply, 0 saved from a provider error."
    block = PANEL.split("What KAME did", 1)[1].split("Quick controls", 1)[0]
    assert REPO in block and "lifetime.star" in block


def test_the_sent_line_never_says_left_today():
    assert pure("return v.sentLine({sent_24h: 143, daily_limit: 20, daily_scope: 'project'});") == \
        "Sent in the last 24 hours: 143. The provider's own limit: 20 a day per project."
    assert pure("return v.sentLine({sent_24h: 3, daily_limit: null});") == "Sent in the last 24 hours: 3."
    assert pure("return v.sentLine({});") == ""
    assert pure("return v.sentLine({sent_24h: 5, daily_limit: 50, daily_scope: 'key'});").endswith("50 a day per key.")


def test_the_key_table_marks_a_reserve_key():
    out = run_store("await store.refresh(); store.data.view.pools[0].reserve_keys = [store.data.view.pools[0].key_states[0][0]];"
                    "store.openRing = store.data.view.pools[0].identity; const t = store.keyTable;"
                    "return t ? t.keys[0].words : 'none';", loaded())
    assert out == "none" or "reserve" in out


# -- review of the 1.8.4.0 diff: every kind the engine records has words ---------------------------
def test_every_recorded_kind_is_said_in_words_never_as_its_id():
    """`cache_off` (E1) reached the page as its raw id: "gemini-2.5-flash · cache_off · ...". The first
    event a free Gemini user sees after the update must read as a sentence."""
    import kame_journal as J
    words = pure("return v.KIND_WORDS;")
    store = (ROOT / "webui" / "kame-panel-store.js").read_text(encoding="utf-8")
    labels = store.split("const EVENT_LABELS = {", 1)[1].split("};", 1)[0]
    meaning = store.split("const EVENT_MEANING = {", 1)[1].split("};", 1)[0]
    shown_as = {"model_switch": "handover", "backup": "switch"}     # the store's own mapping (Hermes' names)
    for kind in sorted(J._KINDS - {"setting"}):
        assert kind in words, kind
        name = shown_as.get(kind, kind)
        assert f"{name}:" in labels and f"{name}:" in meaning, kind
    line = pure("return v.moveSentence({kind: 'cache_off', identity: 'gemini:gemini-2.5-flash', reason: ''});")
    assert "cache_off" not in line and "prompt cache off" in line
