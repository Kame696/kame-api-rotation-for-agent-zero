"""1.8.2.9 (E9, decisions/0009) - ONE status view for every surface.

The status line, the chip, the menu and the page read their numbers from `status_view()`.
Counts are over PHYSICAL keys (provider + key + endpoint): four models that share one key are one
key; a key that rests for every model is one resting key. Field names are Hermes' panel snapshot
names. No Agent Zero installation, no network.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import clean  # noqa: F401  (fixture used by name)
import kame_engine as K

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SHARED = "sk-SHARED-openai-key-ka5206-0000"
GEM = ["AIzaSyVIEW-gem-one-0000000000", "AIzaSyVIEW-gem-two-0000000000"]
OPENAI_MODELS = ["claude-sonnet-5", "deepseek-v4-pro", "gpt-5.4-nano", "kimi-k3"]


@pytest.fixture(autouse=True)
def calm():
    K._KAME_IDENTITY_BASE.clear()
    K._KAME_PRIMARY["identity"] = ""
    K._KAME_IN_FLIGHT.clear()
    yield
    K._KAME_IDENTITY_BASE.clear()
    K._KAME_PRIMARY["identity"] = ""
    K._KAME_IN_FLIGHT.clear()


def seed_owner_case():
    """Four models on one dead openai key, and a gemini model with two healthy keys."""
    for name in OPENAI_MODELS:
        ident = f"openai:{name}"
        K._get_identity_state(ident, [SHARED])
        K._mark_key_health(ident, SHARED, False, 3600, "insufficient_quota")
    K._get_identity_state("gemini:gemini-3.5-flash", GEM)


def test_models_sharing_one_key_are_one_key_row_and_the_count_is_over_keys():
    seed_owner_case()
    view = K.status_view()
    groups = view["key_groups"]
    assert len(groups) == 3
    dead = [g for g in groups if g["provider"] == "openai"]
    assert len(dead) == 1
    assert dead[0]["models"] == sorted(OPENAI_MODELS)
    assert dead[0]["state"] == "out_today" and dead[0]["code"] == "d"
    assert dead[0]["until_epoch"] and dead[0]["until_epoch"] > time.time() + 3000
    assert [g["models"] for g in groups if g["provider"] == "gemini"] == [["gemini-3.5-flash"]] * 2
    t = view["totals"]
    assert (t["keys"], t["ready"], t["resting"], t["out_today"]) == (3, 2, 1, 1)


def test_a_key_is_never_in_the_view():
    seed_owner_case()
    text = json.dumps(K.status_view(), default=str)
    for secret in [SHARED] + GEM:
        assert secret not in text


def test_every_surface_says_the_same_count():
    seed_owner_case()
    view = K.status_view()
    t = view["totals"]
    assert t["phrase"] == "2 of 3 keys ready"
    # the status line is built from the same totals
    assert t["phrase"] in K._kame_wait_line(t, 1800.0)
    assert K._kame_totals_phrase(t) == t["phrase"]
    # and the old per-model sums are not what any line is built from
    assert sum(p["keys"] for p in view["pools"]) == 6 and t["keys"] == 3


def test_key_state_codes_are_hermes_codes():
    ident = "gemini:gemini-3.5-flash"
    keys = [f"AIzaSyCODES-{c}-000000000000" for c in "abcde"]
    K._get_identity_state(ident, keys)
    retired, daily, rejected, resting, ready = keys
    with K._KAME_LOCK:
        K._KAME_KEY_HEALTH[ident]["keys"][retired]["retired_at"] = time.time()
    K._mark_key_health(ident, daily, False, 3600, "daily")
    K._mark_key_health(ident, rejected, False, 600, "auth")
    K._mark_key_health(ident, resting, False, 40, "per_minute")
    pool = next(p for p in K.status_view()["pools"] if p["identity"] == ident)
    code = {fp: c for fp, c, _until, _kind in pool["key_states"]}
    assert code[K._key_short_id(retired)] == "o"
    assert code[K._key_short_id(daily)] == "d"
    assert code[K._key_short_id(rejected)] == "x"
    assert code[K._key_short_id(resting)] == "s"
    assert code[K._key_short_id(ready)] == "r"
    by_fp = {fp: until for fp, _c, until, _k in pool["key_states"]}
    assert by_fp[K._key_short_id(ready)] is None and by_fp[K._key_short_id(resting)] > time.time()
    assert pool["healthy"] == 1 and pool["keys"] == 5 and pool["soonest_s"] is None


def test_a_key_answering_now_is_f_and_counts_as_ready():
    ident = "gemini:gemini-3.5-flash"
    K._get_identity_state(ident, GEM)
    K._KAME_IN_FLIGHT["x"] = {"job": "chat", "identity": ident, "started": time.time(), "key": K._key_short_id(GEM[0])}
    pool = next(p for p in K.status_view()["pools"] if p["identity"] == ident)
    assert [c for _fp, c, _u, _k in pool["key_states"]].count("f") == 1 and pool["in_flight"] == 1
    assert K.status_view()["totals"]["ready"] == 2


def test_the_same_secret_at_two_providers_is_two_keys():
    for ident in ("openai:gpt-5.4-nano", "openrouter:gpt-5.4-nano"):
        K._get_identity_state(ident, [SHARED])
    assert K.status_view()["totals"]["keys"] == 2


def test_the_same_key_at_two_endpoints_is_two_keys():
    K._get_identity_state("openai:gpt-a", [SHARED])
    K._get_identity_state("openai:gpt-b", [SHARED])
    K._KAME_IDENTITY_BASE["openai:gpt-a"] = "https://one.example/v1"
    K._KAME_IDENTITY_BASE["openai:gpt-b"] = "https://two.example/v1"
    assert K.status_view()["totals"]["keys"] == 2


def test_models_come_in_the_users_order_and_a_list_model_without_keys_still_has_a_ring():
    K._get_identity_state("zzz:last", GEM[:1])
    K._get_identity_state("aaa:first", GEM[:1])
    K._KAME_PRIMARY["identity"] = "zzz:last"
    K.set_fallback_models("mmm/middle\naaa/first")
    order = [p["identity"] for p in K.status_view()["pools"]]
    assert order == ["zzz:last", "mmm:middle", "aaa:first"]
    middle = next(p for p in K.status_view()["pools"] if p["identity"] == "mmm:middle")
    assert middle["keys"] == 0 and middle["key_states"] == []


def test_repeated_decisions_are_one_row():
    now = time.time()
    for i in range(6):
        K._KJ.EVENTS.add("rotation", identity="openai:gpt-5.4-nano", key="ka5206",
                         reason="out of credit", at=now - (5 - i) * 3600)
    K._KJ.EVENTS.add("switch", identity="gemini:gemini-3.5-flash", key="zz9", reason="took over", at=now)
    moves = K.status_view()["moves"]
    assert [m["count"] for m in moves] == [1, 6]
    row = moves[1]
    assert row["text"] == "rotation ka5206 out of credit"
    assert abs(row["every_s"] - 3600) < 1 and row["repeat"] == "6 times, every hour"
    assert row["last_at"] > row["first_at"]


def test_a_decision_that_is_not_repeated_stays_a_single_line():
    K._KJ.EVENTS.add("rotation", identity="gemini:g", key="k1", reason="throttled")
    K._KJ.EVENTS.add("rotation", identity="gemini:g", key="k2", reason="throttled")
    moves = K.status_view()["moves"]
    assert [m["count"] for m in moves] == [1, 1] and moves[0]["repeat"] == ""


def test_activity_is_the_selected_chat_else_the_newest_conversation_call():
    ident = "gemini:gemini-3.5-flash"
    K._get_identity_state(ident, GEM)
    now = time.time()
    K._KAME_IN_FLIGHT["a"] = {"job": "chat", "identity": ident, "started": now - 30, "context": "chat-1",
                              "key": "k1", "attempt": 2, "waiting": True, "waiting_since": now - 20,
                              "eta_until": now + 45, "status": "waiting on a key"}
    K._KAME_IN_FLIGHT["b"] = {"job": "chat", "identity": ident, "started": now - 5, "context": "chat-2",
                              "racing": ["other-model"], "probe": "best-model"}
    K._KAME_IN_FLIGHT["c"] = {"job": "utility", "identity": ident, "started": now, "context": "chat-1"}
    one = K.status_view("chat-1")["activity"]
    assert one["kind"] == "waiting" and one["attempt"] == 2 and one["key"] == "k1"
    assert 40 <= one["eta_s"] <= 46 and one["waiting_since"] == pytest.approx(now - 20)
    assert one["model"] == "gemini-3.5-flash" and one["keys"] == 2 and one["healthy"] == 2
    newest = K.status_view()["activity"]
    assert newest["kind"] == "calling" and newest["racing"] == ["other-model"] and newest["probe"] == "best-model"
    assert K.status_view("nobody")["activity"]["kind"] == "idle"


def test_idle_activity_still_has_every_field():
    activity = K.status_view()["activity"]
    assert activity["kind"] == "idle"
    for field in ("identity", "model", "attempt", "healthy", "keys", "key", "started_at", "eta_s", "racing", "probe"):
        assert field in activity


def test_the_fallback_view_names_busy_and_slow_models_and_when_they_are_asked_again():
    K.set_fallback_models("google/gemini-3.7-flash\ngoogle/gemini-3.6-flash")
    K._KAME_PRIMARY["identity"] = "gemini:gemini-3.5-flash"
    K._kame_note_busy("google:gemini-3.7-flash")
    K._kame_note_busy("google:gemini-3.6-flash", first=K._KAME_SLOW_FIRST_S, ceiling=K._KAME_SLOW_MAX_S, kind="slow")
    fb = K.status_view()["fallback"]
    by = {e["identity"]: e for e in fb["entries"]}
    assert fb["state"] == "on" and [e["role"] for e in fb["entries"]] == ["primary", "fallback", "fallback"]
    assert by["google:gemini-3.7-flash"]["busy_kind"] == "busy" and by["google:gemini-3.6-flash"]["busy_kind"] == "slow"
    assert by["google:gemini-3.7-flash"]["probe_at"] == pytest.approx(time.time() + K._KAME_PROBE_EVERY_S, abs=2)
    assert by["gemini:gemini-3.5-flash"]["busy_kind"] is None and by["gemini:gemini-3.5-flash"]["busy_until"] is None
    K.set_fallback_models("")
    assert K.status_view()["fallback"]["state"] == "no_chain"


def test_every_path_hermes_reads_exists_in_the_view():
    seed_owner_case()
    K.set_fallback_models("google/gemini-3.7-flash")
    K._KAME_PRIMARY["identity"] = "openai:gpt-5.4-nano"
    K._KJ.EVENTS.add("rotation", identity="openai:gpt-5.4-nano", key="ka5206", reason="out of credit")
    K._KAME_REPLIES.append({"at": time.time(), "model": "m", "for": "", "first_s": 1.0, "total_s": 2.0, "main": {"provider": "p", "model": "m"}})
    view = K.status_view()
    paths = json.loads((FIXTURES / "hermes_1829_panel_reads.json").read_text(encoding="utf-8"))["paths"]
    assert len(paths) >= 40

    def exists(node, path):
        if not path:
            return True
        head, _, rest = path.partition(".")
        many = head.endswith("[]")
        name = head[:-2] if many else head
        if not isinstance(node, dict) or name not in node:
            return False
        value = node[name]
        if many:
            return isinstance(value, list) and any(exists(item, rest) for item in value)
        return exists(value, rest)

    missing = [p for p in paths if not exists(view, p)]
    assert missing == []


def test_pool_report_keeps_every_1_8_2_8_field_and_adds_the_view():
    seed_owner_case()
    report = K.pool_report()
    for field in ("version", "build", "active", "layer", "bound", "pools", "totals", "stats", "tally", "events",
                  "settings", "speed", "in_flight", "host", "replies", "quota_use", "data_dir", "generated_at"):
        assert field in report, field
    assert set(report["totals"]) >= {"keys", "ready", "resting", "retired"}
    assert report["view"]["totals"]["keys"] == 3
    assert K.pool_report("chat-9")["view"]["activity"]["kind"] == "idle"


def test_the_status_api_passes_the_chat_to_the_view(monkeypatch):
    import asyncio
    import importlib
    import importlib.util
    import sys
    import types

    api = types.ModuleType("helpers.api")

    class ApiHandler:
        pass

    api.ApiHandler, api.Input, api.Output, api.Request = ApiHandler, dict, dict, object
    monkeypatch.setitem(sys.modules, "helpers.api", api)
    plugin = types.ModuleType("usr.plugins.api_rotation_by_kame.kame_engine")
    seen = []
    plugin.pool_report = lambda context_id=None: seen.append(context_id) or {"pools": []}
    monkeypatch.setitem(sys.modules, "usr", types.ModuleType("usr"))
    monkeypatch.setitem(sys.modules, "usr.plugins", types.ModuleType("usr.plugins"))
    monkeypatch.setitem(sys.modules, "usr.plugins.api_rotation_by_kame", types.ModuleType("usr.plugins.api_rotation_by_kame"))
    monkeypatch.setitem(sys.modules, "usr.plugins.api_rotation_by_kame.kame_engine", plugin)
    spec = importlib.util.spec_from_file_location("kame_status_for_test", Path(__file__).resolve().parents[1] / "api" / "kame_status.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = asyncio.run(module.KameStatus().process({"context": "chat-7"}, None))
    assert seen == ["chat-7"] and out["available"] is True
    asyncio.run(module.KameStatus().process({}, None))
    assert seen == ["chat-7", None]
