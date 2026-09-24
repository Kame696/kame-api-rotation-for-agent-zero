"""1.8.1.1 reliability witnesses for persistence, reset, keys and wake-up."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path


def _stub(name):
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


for _name in ("openai", "langchain_core", "helpers"):
    _stub(_name)
_lit = _stub("litellm")
_lit.suppress_debug_info = False
_lit.acompletion = lambda *a, **k: None
_lc = _stub("langchain_core.messages")
_lc.SystemMessage = _lc.HumanMessage = type("_Msg", (), {})
_ps = _stub("helpers.print_style")
_ps.PrintStyle = type(
    "_PrintStyle",
    (),
    {
        "__init__": lambda self, *a, **k: None,
        "print": lambda self, *a, **k: None,
        "warning": staticmethod(lambda *a, **k: None),
        "error": staticmethod(lambda *a, **k: None),
        "success": staticmethod(lambda *a, **k: None),
    },
)
_errs = _stub("helpers.errors")
for _name in ("InterventionException", "RepairableException", "HandledException"):
    setattr(_errs, _name, type(_name, (Exception,), {}))

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
DATA = Path(tempfile.mkdtemp(prefix="kame-1811-a0-"))
os.environ["KAME_DATA_DIR"] = str(DATA)

import kame_engine as K  # noqa: E402
import kame_journal as J  # noqa: E402
import kame_keys as KK  # noqa: E402


def _fresh():
    K._KAME_KEY_HEALTH.clear()
    K._KAME_ACCOUNT_HOLDS.clear()
    K._KAME_STATED_RL.clear()
    K._KAME_NO_ANSWER_SINCE.clear()
    J._reset_for_tests()
    J.SHARE_HEALTH_ON = True
    for item in DATA.glob("*"):
        if item.is_file():
            item.unlink()


def test_redaction_preserves_quota_evidence_and_drops_credentials():
    quota = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    payload = {"api_key": "fixture-private", "refresh_token": "short", "quotaId": quota}
    for value in (payload, json.dumps(payload), "Authorization: Bearer fixture-private"):
        cleaned = J._safe_text(value)
        assert "fixture-private" not in cleaned
        assert '"short"' not in cleaned
    assert quota in J._safe_text(payload)
    result = J._safe_text({"password": ["first-secret", "last-secret"], "token": {"nested": "inner-secret"}})
    assert all(secret not in result for secret in ("first-secret", "last-secret", "inner-secret"))


def test_model_and_account_holds_are_independent_and_survive_restart():
    _fresh()
    now = time.time()
    identity = "provider:model-a"
    key = "key-a"
    J.note_hold(identity, key, now + 90, "server", "")
    J.note_hold(identity, key, now + 300, "daily", "account")
    row = J.hold_for(identity, key)
    assert row["model"]["kind"] == "server"
    assert row["account"]["kind"] == "daily"
    assert row["until"] == row["account"]["until"]
    assert J.hold_for("provider:new-model", key)["account"]["kind"] == "daily"

    J._HEALTH.clear()
    J._ACCOUNT_HEALTH.clear()
    J._HEALTH_LOADED = False
    row = J.hold_for(identity, key)
    assert row["model"]["kind"] == "server" and row["account"]["kind"] == "daily"


def test_schema_one_account_rows_migrate_and_malformed_rows_are_ignored():
    _fresh()
    now = time.time()
    key = "key-a"
    digest = J._long_hash(key)
    document = {
        "schema": 1,
        "holds": {
            "provider:model-a": {
                digest: {"until": now + 120, "kind": "server", "scope": "", "at": now},
                "malformed": "not-a-row",
            },
            "provider:model-b": {
                digest: {"until": now + 240, "kind": "daily", "scope": "account", "at": now}
            },
        },
    }
    (DATA / J.HEALTH_FILE).write_text(json.dumps(document), encoding="utf-8")
    J._HEALTH_LOADED = False
    row = J.hold_for("provider:model-a", key)
    assert row["model"]["kind"] == "server"
    assert row["account"]["kind"] == "daily"
    assert J.hold_for("provider:model-new", key)["account"]["kind"] == "daily"


def test_answer_clears_only_that_model_and_provider_account_hold():
    _fresh()
    now = time.time()
    key = "key-a"
    J.note_hold("p:model-a", key, now + 100, "server", "")
    J.note_hold("p:model-b", key, now + 200, "daily", "")
    J.note_hold("p:model-a", key, now + 300, "account-limit", "account")
    J.note_answer("p:model-a", key)
    assert J.hold_for("p:model-a", key) is None
    remaining = J.hold_for("p:model-b", key)
    assert remaining and remaining["model"]["kind"] == "daily" and remaining["account"] is None


def test_forget_holds_is_int_compatible_and_reports_write_failure(monkeypatch):
    _fresh()
    J.note_hold("p:m", "key", time.time() + 60, "server", "")
    J.SHARE_HEALTH_ON = False
    result = J.forget_holds()
    assert isinstance(result, int) and result.persistence_ok is True
    stored = json.loads((DATA / J.HEALTH_FILE).read_text(encoding="utf-8"))
    assert stored["holds"] == {} and stored["account_holds"] == {}
    J.SHARE_HEALTH_ON = True
    J.note_hold("p:m", "key", time.time() + 60, "server", "")
    monkeypatch.setattr(J, "_flush_health", lambda force=False: (False, "fixture failure"))
    result = J.forget_holds()
    assert isinstance(result, int) and result.persistence_ok is False
    assert "fixture failure" in result.error


def test_engine_reset_surfaces_persistence_failure(monkeypatch):
    _fresh()
    K._get_identity_state("p:m", ["key"])
    failed = J.ForgetHoldsResult(1, False, "disk refused")
    monkeypatch.setattr(J, "forget_holds", lambda: failed)
    try:
        K.reset_pool()
    except RuntimeError as exc:
        assert "persisted holds could not be cleared" in str(exc)
    else:
        raise AssertionError("reset_pool silently reported success after persistence failure")


def test_engine_account_hold_reaches_new_model_and_success_preserves_other_model_hold():
    _fresh()
    key = "key-a"
    K._get_identity_state("p:model-a", [key])
    K._get_identity_state("p:model-b", [key])
    K._mark_key_health("p:model-b", key, False, 180, "daily")
    # 1.8.1.2: a daily label now re-probes early (15s, doubling), so read the
    # model-b hold back instead of assuming the old flat five minutes.
    model_b_until = K._KAME_KEY_HEALTH["p:model-b"]["keys"][key]["model_until"]
    assert model_b_until > time.time()
    K._mark_key_health("p:model-a", key, False, 300, "per_minute", scope="account")
    assert K._get_identity_state("p:model-new", [key])["keys"][key]["sick_until"] > time.time() + 250
    K._mark_key_health("p:model-a", key, True)
    assert K._KAME_KEY_HEALTH["p:model-a"]["keys"][key]["sick_until"] <= time.time()
    # model-a's answer refutes the account hold, never model-b's own hold.
    assert K._KAME_KEY_HEALTH["p:model-b"]["keys"][key]["sick_until"] == model_b_until


def test_server_thaw_shortens_only_model_component_and_persists_it():
    _fresh()
    key = "key-a"
    identity = "p:model-a"
    K._get_identity_state(identity, ["ok", key])
    K._mark_key_health(identity, key, False, 90, "server")
    K._mark_key_health(identity, key, False, 300, "per_minute", scope="account")
    account_before = K._KAME_ACCOUNT_HOLDS[("p", key)]["until"]
    assert K._thaw_server_cooled_keys(identity, "ok", new_cooldown=3) == 1
    kd = K._KAME_KEY_HEALTH[identity]["keys"][key]
    assert kd["model_until"] < time.time() + 10
    assert K._KAME_ACCOUNT_HOLDS[("p", key)]["until"] == account_before
    persisted = J.hold_for(identity, key)
    assert persisted["model"]["until"] < time.time() + 10
    assert persisted["account"]["until"] == account_before


def test_exhaustion_sleep_wakes_after_reset(monkeypatch):
    _fresh()
    identity = "p:model-a"
    keys = ["a", "b"]
    K._get_identity_state(identity, keys)
    for key in keys:
        K._mark_key_health(identity, key, False, 60, "server")
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        K.reset_pool()

    monkeypatch.setattr(K.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(K.random if hasattr(K, "random") else __import__("random"), "uniform", lambda *_: 0.1)
    state = K._KameSleepState()
    asyncio.run(K._kame_sleep_on_exhaustion(identity, keys, "Chat", "model-a", state))
    assert len(sleeps) == 1
    assert K._pool_has_ready_key(identity, keys)


def test_dotenv_import_comments_quotes_and_collision_resistant_backups(tmp_path):
    first = "AIzaSyONE-000000000000000"
    second = "AIzaSyTWO-000000000000000"
    text = (
        "# comment\n"
        f'API_KEY_GOOGLE="{first},{second}" # trailing comment\n'
        "OTHER=value # ignored\n"
    )
    provider, keys = KK.parse_import(text)
    assert provider == "google" and keys == [first, second]
    env = tmp_path / ".env"
    env.write_text(text, encoding="utf-8")
    assert KK.read_env(env)["API_KEY_GOOGLE"] == f"{first},{second}"
    b1 = KK.backup(env)
    b2 = KK.backup(env)
    assert b1 and b2 and b1 != b2
    assert (tmp_path / b1).read_text(encoding="utf-8") == text


def test_existing_env_is_not_changed_when_backup_fails(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("API_KEY_GOOGLE=old\n", encoding="utf-8")
    saved = []
    monkeypatch.setattr(KK, "backup", lambda _path: None)
    message = KK.add(env, "google", ["new"], lambda name, value: saved.append((name, value)))
    assert not saved and "Nothing changed" in message


def test_settings_command_always_uses_the_global_scope(tmp_path, monkeypatch):
    """1.8.1.2 (owner decision): one engine per process, one set of dials.

    1.8.1.1 wrote the agent's project/profile file; a subordinate on another
    profile then flipped the process-global engine back at its next monologue.
    Whatever scope the agent is in, the command reads and writes the global
    config and never asks for a scoped path.
    """
    plugin_helpers = types.ModuleType("helpers.plugins")
    plugin_helpers.CONFIG_FILE_NAME = "config.json"
    saved, asked = [], []
    plugin_helpers.determine_plugin_asset_path = lambda *a: asked.append(a) or str(tmp_path / "x.json")
    plugin_helpers.save_plugin_config = lambda plugin, project, profile, config: saved.append(
        (plugin, project, profile, dict(config))
    )
    plugin_helpers.get_plugin_config = lambda plugin, agent=None, **k: (
        {"global_value": True} if agent is None else {"scoped_value": True})
    project_helpers = types.ModuleType("helpers.projects")
    project_helpers.get_context_project_name = lambda context: "project-x"
    monkeypatch.setitem(sys.modules, "helpers.plugins", plugin_helpers)
    monkeypatch.setitem(sys.modules, "helpers.projects", project_helpers)
    monkeypatch.setattr(sys.modules["helpers"], "projects", project_helpers, raising=False)

    spec = importlib.util.spec_from_file_location("kame_command_1812_scope", HERE / "commands" / "kame_command.py")
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    agent = types.SimpleNamespace(context=object(), config=types.SimpleNamespace(profile="profile-x"))
    assert command._raw_config(agent) == ("", "", {"global_value": True})
    command._save("new_value", 2, agent)
    assert saved == [("api_rotation_by_kame", "", "", {"global_value": True, "new_value": 2})]
    assert asked == []


def test_activation_reads_the_global_config_only():
    source = (HERE / "kame_activation.py").read_text(encoding="utf-8")
    assert 'get_plugin_config("api_rotation_by_kame", agent=None)' in source
    assert 'get_plugin_config("api_rotation_by_kame", agent=agent)' not in source


def test_call_id_source_is_collision_resistant():
    source = (HERE / "kame_engine.py").read_text(encoding="utf-8")
    assert "secrets.token_hex(8)" in source
    assert 'int(time.time() * 1000)' not in source


def test_short_server_failure_cannot_relabel_or_thaw_long_model_hold():
    _fresh()
    identity, key = "p:m", "a"
    K._get_identity_state(identity, [key, "ok"])
    K._mark_key_health(identity, key, False, 300, "per_minute", sized_by="provider")
    before = K._KAME_KEY_HEALTH[identity]["keys"][key]["sick_until"]
    K._mark_key_health(identity, key, False, 1, "server")
    assert K._thaw_server_cooled_keys(identity, "ok") == 0
    kd = K._KAME_KEY_HEALTH[identity]["keys"][key]
    assert kd["sick_until"] == before and kd["hold_kind"] == "per_minute"
    assert J.hold_for(identity, key)["model"]["kind"] == "per_minute"


def test_new_model_inherits_account_hold_but_success_keeps_other_model_hold():
    _fresh()
    K._get_identity_state("p:a", ["key"])
    K._mark_key_health("p:a", "key", False, 3600, "insufficient_quota")
    state = K._get_identity_state("p:b", ["key"])
    assert state["keys"]["key"]["sick_until"] > time.time() + 3590
    K._mark_key_health("p:b", "key", False, 300, "per_minute", sized_by="provider")
    K._mark_key_health("p:a", "key", True)
    assert state["keys"]["key"]["sick_until"] > time.time() + 290
    assert state["keys"]["key"]["hold_kind"] == "per_minute"
