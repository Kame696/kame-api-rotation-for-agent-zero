"""1.8.1.3 - the upgrade checker must not mistake an unreadable module for a
missing symbol.

Agent Zero v2.12+ writes ``type X = ...`` statements (Python 3.12 syntax) in
``helpers/plugins.py``. Parsed by an older interpreter, the checker used to
swallow the SyntaxError, report the optional ``save_plugin_config`` as "not
present, this Agent Zero predates it, nothing to do", and -- under
``--update-baseline`` -- write a baseline with that fingerprint dropped.

These tests build a tiny fake checkout whose watched module cannot be parsed
by ANY Python (a stray token), so they hold on every interpreter.

    python -m pytest tests/test_v1_8_1_3_tooling.py
"""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _checker():
    spec = importlib.util.spec_from_file_location(
        "kame_a0_upgrade_check_under_test", ROOT / "tools" / "a0_upgrade_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_a0(tmp_path):
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    (helpers / "__init__.py").write_text("", encoding="utf-8")
    # Unparseable everywhere, the way `type X = ...` is unparseable on 3.11.
    (helpers / "plugins.py").write_text(
        "def save_plugin_config(a, b):\n    return a\n\ntype = = broken\n",
        encoding="utf-8",
    )
    return tmp_path


def test_an_unparseable_module_raises_unreadable_not_none(fake_a0):
    checker = _checker()
    with pytest.raises(checker.Unreadable):
        checker._source_of(str(fake_a0), "helpers.plugins", "save_plugin_config")


def test_a_truly_missing_module_is_still_none(fake_a0):
    checker = _checker()
    assert checker._source_of(str(fake_a0), "helpers.nowhere", "anything") is None


def test_fingerprints_reports_unreadable_separately_from_missing(fake_a0):
    checker = _checker()
    watch = [
        {"id": "helpers.plugins.save_plugin_config", "module": "helpers.plugins",
         "symbol": "save_plugin_config", "optional": True},
        {"id": "helpers.nowhere.x", "module": "helpers.nowhere", "symbol": "x",
         "optional": True},
    ]
    hashes, missing, unreadable = checker.fingerprints(str(fake_a0), watch)
    assert hashes == {}
    assert [e["id"] for e in missing] == ["helpers.nowhere.x"]
    assert [e["id"] for e, _ in unreadable] == ["helpers.plugins.save_plugin_config"]


def test_update_baseline_refuses_and_leaves_the_file_alone(fake_a0, tmp_path, monkeypatch, capsys):
    checker = _checker()
    baseline = tmp_path / "a0_compat_copy.json"
    shutil.copyfile(ROOT / "a0_compat.json", baseline)
    before = baseline.read_bytes()
    monkeypatch.setattr(checker, "BASELINE", str(baseline))
    monkeypatch.setattr(checker, "latest_a0_tag", lambda: None, raising=False)
    monkeypatch.setattr(
        sys, "argv",
        ["a0_upgrade_check.py", str(fake_a0), "--skip-tests", "--update-baseline", "v9.99"],
    )
    code = checker.main()
    out = capsys.readouterr().out
    assert code == 2
    assert "UNREADABLE" in out
    assert "not present (optional)" not in out
    assert baseline.read_bytes() == before
    assert json.loads(before)["verified_against"] != "v9.99"
