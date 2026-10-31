"""1.8.3.1 (A5 / D5) - `tools/parity_check.py`: the two ports mean the same, by machine.

The tool reads the settings of both ports (title, kind, default, choices, range, advanced, shelf, order) and a
named list of timing constants from both sources, and fails on any difference that is not written in its
allowlist with a reason. These tests pin three things: the tool's readers, that the check is clean today (with
the Hermes tree next to this folder; they skip where it is absent - the public repo, the shipped zip), and that
it has teeth: a changed default, a changed constant, a renamed constant and an allowlist entry that is no longer
needed each fail it.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "parity_check.py"

pytestmark = pytest.mark.skipif(not TOOL.is_file(), reason="the parity check is a development tool; it is not in the plugin zip")


def load():
    spec = importlib.util.spec_from_file_location("kame_parity_check", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = load() if TOOL.is_file() else None
HERMES_AT_HAND = bool(P and (P.HERMES / "settings.py").is_file())
needs_hermes = pytest.mark.skipif(not HERMES_AT_HAND, reason="no Hermes source next to this folder")


# -- the readers ----------------------------------------------------------------------------------------

def test_python_constants_are_read_without_importing_the_module(tmp_path):
    source = tmp_path / "m.py"
    source.write_text("import nothing_that_exists\nA = 1.5\nB: int = 3\nC = (1, 2)\nD = {False: (1.0, 2.0)}\nE = f()\n", encoding="utf-8")
    assert P.read_py_constants(source) == {"A": 1.5, "B": 3, "C": (1, 2), "D": {False: (1.0, 2.0)}}


def test_javascript_constants_are_read_as_numbers(tmp_path):
    source = tmp_path / "m.js"
    source.write_text("const TICK_MS = 1000\nexport const MORE = 6;\nconst NOT_A_NUMBER = foo()\nconst PAUSE = 1_500\n", encoding="utf-8")
    assert [P.read_js_constant(source, n) for n in ("TICK_MS", "MORE", "NOT_A_NUMBER", "PAUSE", "NOPE")] == [1000, 6, None, 1500, None]


def test_the_host_name_is_the_only_change_allowed_in_a_title():
    assert P.host_words("Hermes’ own wait  for Hermes") == P.host_words("Agent Zero’s own wait for Agent Zero")
    assert P.host_words("Hermes' own") == "Agent Zero's own"


def test_every_written_difference_has_a_reason():
    assert P.ALLOWED
    for key, reason in P.ALLOWED.items():
        assert len(reason) > 30, key


def test_the_constants_the_plan_names_are_in_the_list():
    labels = " | ".join(label for label, *_ in P.PY_CONSTANTS + P.JS_CONSTANTS)
    for needed in ("first-token wait, Off", "slow bound", "a busy model, asked again every", "page and chip read the status every",
                   "models the popover lists", "replies the popover lists", "a wait worth telling"):
        assert needed in labels, needed


def test_without_a_hermes_tree_there_is_nothing_to_compare_and_the_run_is_not_a_failure(tmp_path):
    report = P.check(tmp_path / "nowhere")
    assert report["skipped"] is True and report["differences"] == []


# -- the check, against the Hermes tree --------------------------------------------------------------------

@needs_hermes
def test_the_two_ports_are_the_same_except_what_is_written():
    report = P.check()
    assert report["skipped"] is False
    assert report["differences"] == [], report["differences"]
    assert report["stale_allowed"] == [], report["stale_allowed"]
    assert report["constants_checked"] >= 50


@needs_hermes
def test_a_changed_default_is_found(monkeypatch):
    real = P.agent_zero_rows

    def changed():
        rows, order = real()
        rows["hedge_mode"] = {**rows["hedge_mode"], "default": "max"}
        return rows, order

    monkeypatch.setattr(P, "agent_zero_rows", changed)
    found = P.check()["differences"]
    assert any(row["subject"] == "hedge_mode" and row["field"] == "default" for row in found), found


@needs_hermes
def test_a_changed_range_choice_group_or_advanced_flag_is_found(monkeypatch):
    real = P.agent_zero_rows

    def changed():
        rows, order = real()
        rows["hedge_max_legs"] = {**rows["hedge_max_legs"], "hi": 9}
        rows["model_switch"] = {**rows["model_switch"], "choices": ["never", "auto", "timed"]}
        rows["failover_after_seconds"] = {**rows["failover_after_seconds"], "advanced": True}
        rows["reply_notice"] = {**rows["reply_notice"], "group": "speed"}
        return rows, order

    monkeypatch.setattr(P, "agent_zero_rows", changed)
    found = {(row["subject"], row["field"]) for row in P.check()["differences"]}
    assert {("hedge_max_legs", "hi"), ("model_switch", "choices"), ("failover_after_seconds", "advanced"),
            ("reply_notice", "group")} <= found, found


@needs_hermes
def test_a_changed_or_renamed_constant_is_found(monkeypatch):
    real = P.read_py_constants

    def changed(path):
        out = dict(real(path))
        if path.name == "kame_engine.py":
            out["_KAME_PROBE_EVERY_S"] = 25.0
            out.pop("_KAME_WIDEN_MAX", None)
        return out

    monkeypatch.setattr(P, "read_py_constants", changed)
    found = {row["subject"]: row for row in P.check()["differences"]}
    assert found["a busy model, asked again every"]["agent_zero"] == 25.0
    assert found["models added at the slow bound"]["agent_zero"] == "<missing>"       # a renamed constant stops the check


@needs_hermes
def test_a_stale_allowlist_entry_is_found(monkeypatch):
    monkeypatch.setitem(P.ALLOWED, ("constant", "models the popover lists"), "an entry whose difference is gone, to be removed")
    assert P.check()["stale_allowed"] == ["('constant', 'models the popover lists')"]


@needs_hermes
def test_the_command_line_exits_zero_when_clean_and_prints_the_allowlist(capsys):
    assert P.main([]) == 0
    out = capsys.readouterr().out
    assert "allowed" in out and "clean (allowlist only)" in out
