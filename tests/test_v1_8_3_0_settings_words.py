"""1.8.3.0 (A6) - every setting says what the Hermes setting says, in the same order, with the same numbers.

The owner's rule: the Agent Zero plugin is IDENTICAL to the Hermes plugin; the only differences are the host's
name, the clauses that name a Hermes-only thing, and the Agent Zero-only extras. `fixtures/hermes_1829_ui_strings.json`
freezes Hermes' `describe_all()` (made by `tools/extract_hermes_ui_strings.py`); these tests compare every field of
every setting with it. A sentence that differs must be listed in ``ADAPTED`` below, with the reason.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import clean  # noqa: F401  (fixture used by name; also stubs Agent Zero)
import kame_api_stub as stub
import kame_engine as K
import kame_settings as S

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "hermes_1829_ui_strings.json").read_text(encoding="utf-8"))
STATUS = stub.load_api("kame_status", K, S)

HERMES = {row["key"]: row for row in FIXTURE["settings"]}
RENAMED = FIXTURE["groups_renamed_keys"]
HERMES_ONLY = set(FIXTURE["groups_hermes_only_keys"])
TWIN = {key: RENAMED.get(key, key) for key in HERMES if key not in HERMES_ONLY}

#: (Agent Zero key, field) -> why the sentence is not Hermes' with the host's name changed.
ADAPTED = {
    ("model_switch", "help"): "names the Fallback models tab instead of `hermes fallback add`",
    ("failover_after_seconds", "detail"): "Hermes hands the reply to Hermes' own chain; here the next model answers inside KAME",
    ("max_total_wait_seconds", "help"): "Agent Zero has no scheduled-job lane with its own bound",
    ("max_total_wait_seconds", "detail"): "same: no scheduled-job bound",
    ("never_fall_back_to_another_model", "help"): "names the Fallback models tab instead of Hermes",
    ("never_fall_back_to_another_model", "when"): "no Hermes",
    ("never_fall_back_to_another_model", "detail"): "no `hermes fallback add`, no Hermes classification hook",
    ("first_token_wait", "help"): "Hermes' own 120 s wait does not exist in Agent Zero",
    ("stream_silence_timeout_seconds", "help"): "same",
    ("stream_silence_timeout_seconds", "detail"): "same",
    ("share_pool_health", "title"): "Agent Zero has no profiles; the switch keeps holds across restarts and processes",
    ("share_pool_health", "help"): "same",
    ("share_pool_health", "when"): "same",
    ("share_pool_health", "detail"): "same",
    ("pacing_rpm_overrides", "help"): "Agent Zero's provider id for Gemini is google",
    ("spread_disabled", "help"): "Hermes' own fill_first does not exist in Agent Zero",
    ("stream_stitch_disabled", "help"): "Hermes' '[System: ...]' restart note does not exist in Agent Zero",
    ("live_status_disabled", "help"): "the spinner line is the chat's own status line in Agent Zero",
}
FIELDS = ("title", "help", "when", "detail")
HOST_WORDS = re.compile(r"Hermes|hermes|\.env\b|profile|/kame-keys split|auth\.json|config\.yaml", re.I)


def host(text: str) -> str:
    for old, new in FIXTURE["substitutions"]:
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text)


def ours(key: str, field: str) -> str:
    if field == "title":
        return S.SETTINGS[key][6]
    if field == "help":
        return S.SETTINGS[key][7]
    if field == "when":
        return S.WHEN.get(key, "")
    return S.DETAIL.get(key, "")


def test_the_table_has_a_twin_for_every_hermes_key_but_the_two_without_a_function_here():
    assert HERMES_ONLY == {"resolver_disabled", "quota_id_disabled"}
    for key, mine in TWIN.items():
        assert mine in S.SETTINGS, f"{key} has no Agent Zero twin ({mine})"
    assert "never_fall_back_to_another_model" in S.SETTINGS


@pytest.mark.parametrize("hermes_key", sorted(TWIN))
def test_title_help_when_and_detail_are_hermes_words_with_the_hosts_name(hermes_key):
    mine, row = TWIN[hermes_key], HERMES[hermes_key]
    for field in FIELDS:
        if field == "detail":
            # what the tooltip over the sentence says: the long text, else the sentence itself
            wanted = host(row["detail"] or row["help"])
            got = re.sub(r"\s+", " ", ours(mine, "detail") or ours(mine, "help"))
            adapted = (mine, "detail") in ADAPTED or (mine, "help") in ADAPTED
        else:
            wanted = host(row[field] or "")
            got = re.sub(r"\s+", " ", ours(mine, field))
            adapted = (mine, field) in ADAPTED
        if adapted:
            assert got and got != wanted, f"{mine}.{field} is listed as adapted but is the Hermes text"
            assert not HOST_WORDS.search(got), f"{mine}.{field}: a Hermes-only word is left: {got!r}"
        else:
            assert got == wanted, f"{mine}.{field} drifted from Hermes"


def test_every_adapted_sentence_has_a_reason_and_is_not_a_leftover():
    for (key, field), reason in ADAPTED.items():
        assert reason and key in S.SETTINGS
        assert ours(key, field) or field == "detail", f"{key}.{field} is listed as adapted but missing"


@pytest.mark.parametrize("hermes_key", sorted(TWIN))
def test_kind_numbers_defaults_units_and_switches_are_hermes_ones(hermes_key):
    mine, row = TWIN[hermes_key], HERMES[hermes_key]
    kind, default, low, high, choices, env = S.SETTINGS[mine][:6]
    assert kind == {"number": "number", "flag": "flag", "string": "choice" if row["choice_order"] else "text"}[row["kind"]]
    if row["kind"] == "number":
        assert (float(low), float(high), float(default)) == (row["min"], row["max"], row["default"]), mine
        assert S.UNITS.get(mine, "") == row["units"], mine
        assert (mine in S.INTEGRAL) == (row["step"] == 1), mine
        assert S.OFF_OR_AT_LEAST.get(mine) == row["off_or_at_least"], mine
    else:
        assert default == row["default"], mine
    assert tuple(choices or ()) == tuple(row["choice_order"] or ()), mine
    assert env == row["env"], mine
    assert S.IMPACT[mine] == row["impact"], mine
    assert (mine in S.ADVANCED) == bool(row["advanced"]), mine
    assert (mine in S.CONSEQUENTIAL) == bool(row["consequential"]), mine


def test_the_choice_words_are_hermes_words():
    for key in ("model_switch", "first_token_wait", "reply_notice", "hedge_mode"):
        row = HERMES[key]
        assert S.choice_labels_now(key, 10) == row["choice_labels"], key
        assert S.choice_help_now(key, 10) == row["choice_help"], key
        assert S.CHOICE_FOOTNOTE.get(key, "") == row["choice_footnote"], key


def test_the_shelves_hold_the_hermes_keys_in_hermes_order_and_the_agent_zero_ones_follow_rest_times():
    mine = {g: list(keys) for g, _t, _n, keys in S.GROUPS}
    for group in FIXTURE["groups"]:
        expected = [RENAMED.get(k, k) for k in group["keys"] if k not in HERMES_ONLY]
        assert mine[group["id"]] == expected, group["id"]
    assert [g for g, *_ in S.GROUPS] == ["extra", "speed", "keyring", "tuning", "agentzero", "off"]
    assert mine["agentzero"] == ["kame_log_level", "key_log_style", "kame_log_full_errors", "kame_unusable_response_limit"]
    every = [k for keys in mine.values() for k in keys]
    assert len(every) == len(set(every))
    assert set(every) | {"fallback_models"} == set(S.SETTINGS)


def test_every_setting_has_a_power_a_sentence_and_the_agent_zero_ones_too():
    for name in S.SETTINGS:
        assert S.IMPACT.get(name) in ("high", "medium", "low", "escape"), name
        assert S.SETTINGS[name][7], name
    for name in S.TROUBLESHOOTING:
        assert S.IMPACT[name] == "escape" and name not in S.ADVANCED


# -- the two switches Agent Zero already had under other names -----------------------------------------

def test_the_two_hermes_switches_that_exist_here_are_presented_with_hermes_words_in_troubleshooting():
    assert S.SETTINGS["storm_collapse_disabled"][6] == "Log every failure during an outage"
    assert S.SETTINGS["live_status_disabled"][6] == "Hide the status line"
    off = [k for g, _t, _n, keys in S.GROUPS if g == "off" for k in keys]
    assert off == ["rotation_disabled", "spread_disabled", "carousel_disabled", "storm_collapse_disabled",
                   "live_status_disabled", "stream_stitch_disabled", "refusal_recorder_disabled",
                   "call_timings_disabled", "hedging_disabled"]
    assert S.default("storm_collapse_disabled") is False and S.default("live_status_disabled") is False


def test_the_old_names_still_work_and_mean_the_opposite_switch():
    assert S.canonical("kame_wait_notice", False) == ("live_status_disabled", True)
    assert S.canonical("kame_collapse_storm_logs", "true") == ("storm_collapse_disabled", False)
    assert S.canonical("hedge_mode", "off") == ("hedge_mode", "off")


def test_the_engine_reads_the_new_switches_and_a_config_saved_with_the_old_names(clean):
    from kame_activation import activate  # noqa: F401  (imported to prove it loads)
    stub.reset()
    stub.CONFIG.update({"storm_collapse_disabled": True, "live_status_disabled": True})
    activate(None)
    cur = K.current_settings()
    assert cur["storm_collapse_disabled"] is True and cur["live_status_disabled"] is True
    assert cur["kame_collapse_storm_logs"] is False and cur["kame_wait_notice"] is False
    stub.reset()
    stub.CONFIG.update({"kame_collapse_storm_logs": False, "kame_wait_notice": False})
    activate(None)
    cur = K.current_settings()
    assert cur["storm_collapse_disabled"] is True and cur["live_status_disabled"] is True
    stub.reset()
    activate(None)
    cur = K.current_settings()
    assert cur["storm_collapse_disabled"] is False and cur["live_status_disabled"] is False


# -- "Ignore my fallback list" -----------------------------------------------------------------------------

def test_ignore_my_fallback_list_is_off_by_default_advanced_and_low(clean):
    assert S.default("never_fall_back_to_another_model") is False
    assert "never_fall_back_to_another_model" in S.ADVANCED and S.IMPACT["never_fall_back_to_another_model"] == "low"
    assert S.SETTINGS["never_fall_back_to_another_model"][5] == "KAME_NO_MODEL_FALLBACK"


def test_ignore_my_fallback_list_closes_the_switch_gate(clean):
    K.set_fallback_models("openrouter/meta-llama/llama-4-maverick")
    K.set_model_switch("auto")
    K.set_no_model_fallback(False)
    assert K._kame_switch_on() is True
    K.set_no_model_fallback(True)
    assert K._kame_switch_on() is False
    assert K.current_settings()["never_fall_back_to_another_model"] is True
    K.set_no_model_fallback(False)


def test_the_activation_and_the_environment_reach_the_gate(clean, monkeypatch):
    from kame_activation import activate
    stub.reset()
    stub.CONFIG.update({"never_fall_back_to_another_model": True})
    activate(None)
    assert K._kame_switch_on() is False
    stub.reset()
    monkeypatch.setenv("KAME_NO_MODEL_FALLBACK", "1")
    activate(None)
    assert K.current_settings()["never_fall_back_to_another_model"] is True
    monkeypatch.delenv("KAME_NO_MODEL_FALLBACK")
    stub.reset()
    activate(None)
    assert K.current_settings()["never_fall_back_to_another_model"] is False


# -- the validator speaks Hermes' words ---------------------------------------------------------------------

def test_refusals_use_hermes_wording():
    assert S.parse("max_hold_seconds", "5")[2] == "max_hold_seconds accepts 60 to 86400; 5 is outside that"
    assert S.parse("max_hold_seconds", "abc")[2] == "max_hold_seconds takes a number of seconds; 'abc' is not one"
    assert S.parse("hedge_max_legs", "2.5")[2] == "hedge_max_legs counts whole things; 2.5 is not a whole number"
    assert S.parse("stream_silence_timeout_seconds", "3")[2] == (
        "stream_silence_timeout_seconds is either 0 (off) or at least 5 — anything shorter fires while a healthy "
        "provider is still connecting")
    assert S.parse("rotation_disabled", "maybe")[2] == "rotation_disabled takes true or false; 'maybe' is neither"
    assert S.parse("model_switch", "x")[2] == "model_switch accepts auto, never, timed; 'x' is not one of them"
    assert S.parse("stream_silence_timeout_seconds", "0")[0] and S.parse("stream_silence_timeout_seconds", "60")[0]


# -- what the page is served --------------------------------------------------------------------------------

def test_the_panel_report_carries_everything_the_hermes_row_shows():
    out = stub.call(STATUS.KameStatus, {"panel": True})
    meta = {m["name"]: m for m in out["settings_meta"]}
    row = meta["hedge_max_legs"]
    assert row["impact"] == "medium" and row["advanced"] is True and row["units"] == "legs" and row["integral"] is True
    assert row["when"] == "Lower to 2 to cap a slow call at one backup."
    assert meta["stream_silence_timeout_seconds"]["off_or_at_least"] == 5
    assert meta["rotation_disabled"]["consequential"] is True and meta["rotation_disabled"]["impact"] == "escape"
    assert meta["model_switch"]["detail"] == "" or meta["model_switch"]["detail"] != "x"
    assert meta["failover_after_seconds"]["detail"].startswith("Works only when you have set fallback models")
    order = [m["name"] for m in out["settings_meta"]]
    assert order == list(S.SETTINGS), "served in the order of the table"
    assert [g["id"] for g in out["setting_groups"]] == [g for g, *_ in S.GROUPS]


def test_the_environment_name_is_served_for_every_shared_setting():
    out = stub.call(STATUS.KameStatus, {"panel": True})
    meta = {m["name"]: m for m in out["settings_meta"]}
    assert meta["hedge_max_legs"]["env"] == "KAME_HEDGE_MAX_LEGS"
    assert meta["storm_collapse_disabled"]["env"] == "KAME_STORM_COLLAPSE_DISABLED"
