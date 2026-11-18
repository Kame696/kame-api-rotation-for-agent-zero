"""1.9.0.3 (C4, C5, C8, C9, C10) - the words on the screens, in step with the Hermes port.

C4: what a person READS names the product "API Rotation", not "KAME". Unchanged on purpose: log lines, exception
    texts, command and tool names (`/kame`), setting keys, environment names (`KAME_...`), ids, the author, and
    the full title "KAME API Rotation".
C5: "slow today" and "busy" said little; they say what happened.
C8: a background job stopped before anything was sent does not claim a timeout.
C9: the help of the automatic first-word wait states the numbers the code uses.
C10: "Never" keeps background jobs on your model too - here the only hop a background job can take is KAME's own
     walk down the list, which "Never" already refuses.
"""
from __future__ import annotations

import html
import importlib.util
import re
import shutil
from pathlib import Path

import pytest

import kame_aux as A
import kame_settings as S

ROOT = Path(__file__).resolve().parent.parent
WEBUI = ROOT / "webui"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

SCREEN_FILES = [
    WEBUI / "kame-panel.html", WEBUI / "kame-panel-store.js", WEBUI / "kame-view.js",
    WEBUI / "kame-rotation-store.js", WEBUI / "config.html", WEBUI / "main.html",
    ROOT / "extensions" / "webui" / "model-context-strip-end" / "kame-rotation.html",
    ROOT / "extensions" / "webui" / "sidebar-quick-actions-dropdown-start" / "kame-entry.html",
]

#: What may still say KAME on a screen: the full title, the author, the environment names.
ALLOWED = [r"KAME API Rotation", r"[Mm]ade by KAME", r"built by KAME", r"is made by KAME", r"KAME on GitHub",
           r"KAME_[A-Z_]+", r"Made by <b>KAME</b>"]


def literals(path: Path):
    """Every string a person could read in one file: JS string literals (comments skipped), HTML outside comments."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".js":
        spec = importlib.util.spec_from_file_location("kame_extract_for_words", ROOT / "tools" / "extract_hermes_ui_strings.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return [value for _line, _s, _e, value in module.js_literals(text)]
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    return [html.unescape(text)]


def leftovers(path: Path):
    bad = []
    for value in literals(path):
        stripped = value
        for allowed in ALLOWED:
            stripped = re.sub(allowed, "", stripped)
        if re.search(r"\bKAME\b(?!_)", stripped):
            bad.append(re.sub(r"\s+", " ", value)[:140])
    return bad


@pytest.mark.parametrize("path", SCREEN_FILES, ids=lambda p: p.name)
def test_no_screen_sentence_says_kame_where_it_means_the_product(path):
    assert leftovers(path) == []


def test_the_settings_titles_and_help_say_api_rotation():
    seen = []
    for name, row in S.SETTINGS.items():
        for field in (row[6], row[7], S.WHEN.get(name, ""), S.DETAIL.get(name, "")):
            if re.search(r"\bKAME\b(?!_)", str(field)):
                seen.append((name, str(field)[:80]))
    for _gid, title, note, _keys in S.GROUPS:
        for field in (title, note):
            if re.search(r"\bKAME\b(?!_)", field):
                seen.append((title, field[:80]))
    assert seen == []
    assert S.SETTINGS["rotation_disabled"][6] == "Turn API Rotation off"
    assert [t for _g, t, _n, _k in S.GROUPS][-1] == "Troubleshooting: turn parts of API Rotation off"


def test_the_environment_names_and_setting_keys_keep_kame():
    assert S.SETTINGS["rotation_disabled"][5] == "KAME_ROTATION_DISABLED"
    assert "kame_log_level" in S.SETTINGS


def test_the_refusal_to_set_an_unknown_name_says_api_rotation():
    source = (ROOT / "kame_settings.py").read_text(encoding="utf-8")
    assert "is not an API Rotation setting" in source and "is not a KAME setting" not in source


def test_the_background_job_suggestion_names_api_rotation():
    text = A.suggest({"g:best": {"main": 10, "side": 90, "tasks": {"memory": 90}}}, "g:best", ["g:small"],
                     lambda identity: str(identity).split(":", 1)[1], 0.0)
    assert text and text.endswith("API Rotation never changes this for you.")


# -- C5 ----------------------------------------------------------------------------------------------------

def run_js(body, data):
    from test_v1_8_2_9_view_logic import run_js as run
    return run(body, data)


def busy_model(kind, again):
    return {"entry": {"identity": "g:m", "busy_until": 1_800_000_600, "busy_kind": kind, "probe_at": again},
            "pool": {"identity": "g:m", "keys": 3, "key_states": [["a", "r", None, ""]] * 3}}


@needs_node
def test_a_model_that_did_not_say_its_first_word_in_time_is_slow_to_start():
    out = run_js("return V.modelState(data.m, {}, 1_800_000_000_000);", {"m": busy_model("slow", None)})
    assert out["word"] == "slow to start (no first word in time) — skipped for 10 min"


@needs_node
def test_a_model_the_provider_called_overloaded_says_so():
    soon = run_js("return V.modelState(data.m, {}, 1_800_000_000_000);", {"m": busy_model("busy", None)})
    assert soon["word"] == "overloaded (the provider said so) — asked again soon"
    again = run_js("return V.modelState(data.m, {}, 1_800_000_000_000);", {"m": busy_model("busy", 1_800_000_030)})
    assert again["word"] == "overloaded (the provider said so) — asked again in 30 s"


@needs_node
def test_the_hover_of_a_skipped_model_says_what_the_words_mean():
    slow = run_js("return V.modelStateTitle(data.m, 1_800_000_000_000);", {"m": busy_model("slow", None)})
    assert slow == ("This model did not say its first word within the wait limit, so the next replies start on another "
                    "model of your list for a while. It is asked again after 10 min.")
    busy = run_js("return V.modelStateTitle(data.m, 1_800_000_000_000);", {"m": busy_model("busy", None)})
    assert busy == ("The provider answered 'overloaded' or timed out on several keys. Each key rests 1 s; the model is "
                    "skipped for a while.")
    ready = {"entry": {"identity": "g:m"}, "pool": {"identity": "g:m", "keys": 1, "key_states": [["a", "r", None, ""]]}}
    assert run_js("return V.modelStateTitle(data.m, 1_800_000_000_000) ?? null;", {"m": ready}) is None


def test_the_ring_state_word_carries_the_hover_on_the_page():
    panel = (WEBUI / "kame-panel.html").read_text(encoding="utf-8")
    store = (WEBUI / "kame-panel-store.js").read_text(encoding="utf-8")
    assert panel.count(':title="c.stateTitle"') == 2
    assert "stateTitle: modelStateTitle(" in store


@needs_node
def test_the_chip_row_status_says_the_same_words_with_a_dot():
    row = run_js("return V.chipRowStatus(data.m, {}, 1_800_000_000_000);", {"m": busy_model("slow", None)})
    assert row["text"] == "slow to start (no first word in time) · skipped for 10m 00s"
    row = run_js("return V.chipRowStatus(data.m, {}, 1_800_000_000_000);", {"m": busy_model("busy", None)})
    assert row["text"] == "overloaded (the provider said so) · asked again soon"


@needs_node
def test_several_slow_models_are_slow_to_start_when_the_list_joins_in():
    view = {"activity": {"kind": "calling", "model": "X", "racing": ["Y", "Z"], "started_at": 1_799_999_990},
            "fallback": {"entries": []}, "pools": []}
    out = run_js("return V.nowSentences(data, 1_800_000_000_000);", view)
    assert "X and Y are slow to start — Z joined" in out["line1"]


# -- C8 ----------------------------------------------------------------------------------------------------

def test_a_job_stopped_before_it_was_sent_does_not_claim_a_timeout():
    text = A.stop_message("title_generation", "google:gemini-3.8", 0, 0)
    assert text == ("kame: background job title_generation on google:gemini-3.8 was stopped before it was sent"
                    " - its keys are kept for your chat")


def test_a_job_that_was_tried_still_says_how_long():
    text = A.stop_message("title_generation", "google:gemini-3.8", 3, 21)
    assert text == ("kame: background job title_generation on google:gemini-3.8 timed out after 3 tries in 21s"
                    " - its keys are kept for your chat")


def test_the_host_words_are_still_kept_out_of_the_new_sentence():
    text = A.stop_message("rate_limit", "quota-model", 0, 0).lower()
    for word in A._HOST_WORDS:
        assert word not in text, word


# -- C9 ----------------------------------------------------------------------------------------------------

def test_the_first_word_wait_help_states_the_numbers_the_code_uses():
    import kame_engine as K
    floor_plain, ceiling_plain, _ = K._KAME_AUTO_SILENCE_S[False]
    floor_think, ceiling_think, _ = K._KAME_AUTO_SILENCE_S[True]
    assert (floor_plain, floor_think, ceiling_plain, ceiling_think) == (30.0, 120.0, 120.0, 120.0)
    assert K._KAME_AUTO_SILENCE_TOKENS_PER_S == 2000.0
    help_text = re.sub(r"\s+", " ", S.SETTINGS["first_token_wait"][7])
    assert "(30 s at least, 120 s for thinking models, one more second for every 2,000 tokens of the prompt, never more than 120 s)" in help_text
    assert "20 s at least" not in help_text and "60 s for thinking" not in help_text
    assert K._KAME_FIRST_TOKEN_OFF_S == 120 and "Off: 120 s." in help_text


# -- C10 ---------------------------------------------------------------------------------------------------

def test_never_and_ignore_my_list_refuse_the_only_hop_a_background_job_has():
    import kame_engine as K
    source = (ROOT / "kame_engine.py").read_text(encoding="utf-8")
    switch = source.split("async def _kame_switch_model(", 1)[1].split("now_m = time.monotonic()", 1)[0]
    assert "_kame_switch_on()" in switch                      # the aux walk needs the switch on
    on, saved = K._kame_switch_on(), (K._KAME_MODEL_SWITCH, K._KAME_NO_MODEL_FALLBACK)
    try:
        K._KAME_MODEL_SWITCH, K._KAME_NO_MODEL_FALLBACK = "never", False
        assert K._kame_switch_on() is False
        K._KAME_MODEL_SWITCH, K._KAME_NO_MODEL_FALLBACK = "auto", True
        assert K._kame_switch_on() is False
    finally:
        K._KAME_MODEL_SWITCH, K._KAME_NO_MODEL_FALLBACK = saved
    assert K._kame_switch_on() == on
