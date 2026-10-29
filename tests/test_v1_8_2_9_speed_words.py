"""1.8.2.9 (E6, decisions/0007) - speed in plain words.

The buttons say what each choice costs, computed from the live backup budget:
"Saver · 1x", "Balanced · up to 1.1x", "Faster · up to 1.3x", with one plain sentence
under the chosen one and one footnote. The texts are Hermes' word for word (the sentences
name no host). The stored values `off` / `adaptive` / `max` do not change. `webui/config.html`
is generated from the same functions and must equal a fresh run of the generator.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kame_settings as S  # noqa: E402


def test_the_labels_at_the_default_budget():
    labels = S.choice_labels_now("hedge_mode", 10)
    assert labels == {"off": "Saver · 1x", "adaptive": "Balanced · up to 1.1x", "max": "Faster · up to 1.3x"}


def test_the_labels_follow_the_budget():
    # Faster is capped at 100% extra: 3 x 50 = 150 is cut to 100, so 2x and not 2.5x.
    labels = S.choice_labels_now("hedge_mode", 50)
    assert labels["adaptive"] == "Balanced · up to 1.5x" and labels["max"] == "Faster · up to 2x"
    assert S.choice_labels_now("hedge_mode", 0) == {
        "off": "Saver · 1x", "adaptive": "Balanced · up to 1x", "max": "Faster · up to 1x"}


def test_the_labels_without_a_budget_use_the_default():
    assert S.choice_labels_now("hedge_mode")["max"] == "Faster · up to 1.3x"


def test_other_choices_keep_their_plain_labels():
    assert S.choice_labels_now("model_switch") == {"auto": "Automatic", "timed": "Set time", "never": "Never"}
    assert S.choice_help_now("model_switch") == {}


def test_one_sentence_per_speed_choice_as_hermes_says_it():
    help_now = S.choice_help_now("hedge_mode", 10)
    assert help_now == {
        "off": "One request per reply, never more: the cheapest, and a slow reply simply waits.",
        "adaptive": ("A second key races only a reply that is slower than twice its usual time, "
                     "so over a day it uses at most about 10% more requests."),
        "max": ("Races a little sooner and more often, at most about 30% more requests, and goes "
                "back to Balanced by itself for 10 minutes whenever one of that model's keys hits a "
                "rate limit."),
    }


def test_the_footnote_is_hermes_footnote():
    assert S.CHOICE_FOOTNOTE["hedge_mode"] == (
        "Balanced and Faster may also race your next model when a reply is already about 3x slower "
        "than usual, and ask several keys at once when a model runs out of quota - the counter below "
        "shows what was really spent.")


def test_the_stored_value_is_still_max_and_still_parses():
    assert S.parse("hedge_mode", "max") == (True, "max", "")
    assert S.parse("hedge_mode", "adaptive")[0] and S.parse("hedge_mode", "off")[0]
    assert S.parse("hedge_mode", "fastest")[0] is False
    assert [v for v, _l in S.CHOICE_LABELS["hedge_mode"]] == ["off", "adaptive", "max"]
    assert dict(S.CHOICE_LABELS["hedge_mode"])["max"] == "Faster"


def test_the_setting_sentence_no_longer_says_every_reply_races():
    sentence = S.SETTINGS["hedge_mode"][7]
    assert "every reply races" not in sentence and "Faster" in sentence


SHIPPED_SUFFIXES = {".py", ".html", ".js", ".yaml", ".yml", ".md", ".json", ".txt"}


def test_the_word_fastest_is_in_no_shipped_file():
    # CHANGELOG.md keeps the history of the releases that did say it; tests are not shipped.
    offenders = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if (not path.is_file() or path.suffix not in SHIPPED_SUFFIXES or "__pycache__" in rel.parts
                or rel.parts[0] == "tests" or rel.as_posix() == "CHANGELOG.md"):
            continue
        if "Fastest" in path.read_text(encoding="utf-8", errors="replace"):
            offenders.append(rel.as_posix())
    assert offenders == []


def test_the_settings_page_has_the_three_labels_and_equals_a_fresh_run():
    page = (ROOT / "webui" / "config.html").read_text(encoding="utf-8")
    for label in ("Saver · 1x", "Balanced · up to 1.1x", "Faster · up to 1.3x"):
        assert label in page
    assert S.CHOICE_FOOTNOTE["hedge_mode"] in page
    for sentence in S.choice_help_now("hedge_mode").values():
        assert sentence in page
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "config.html"
        subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "gen_config_page.py"), str(out)],
                       check=True, capture_output=True)
        # A Windows checkout may turn the committed file's line ends into CRLF: compare the text, not the ends.
        fresh = out.read_bytes().replace(b"\r\n", b"\n")
        assert fresh == (ROOT / "webui" / "config.html").read_bytes().replace(b"\r\n", b"\n")


def test_the_status_api_serves_the_live_labels(monkeypatch):
    import importlib.util
    import types
    import kame_hedging as H
    # api/kame_status.py needs Agent Zero's ApiHandler and the plugin package path: stub just those.
    helpers = types.ModuleType("helpers")
    api = types.ModuleType("helpers.api")
    api.ApiHandler = type("ApiHandler", (), {})
    api.Input = api.Output = api.Request = object
    monkeypatch.setitem(sys.modules, "helpers", helpers)
    monkeypatch.setitem(sys.modules, "helpers.api", api)
    for name in ("usr", "usr.plugins", "usr.plugins.api_rotation_by_kame"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["usr.plugins.api_rotation_by_kame"].kame_settings = S
    spec = importlib.util.spec_from_file_location("kame_status_under_test", ROOT / "api" / "kame_status.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    H.configure(hedge_budget_pct=20.0)
    try:
        rows = {r["name"]: r for r in module._settings_meta()}
    finally:
        H.configure(hedge_budget_pct=10.0)
    assert rows["hedge_mode"]["labels"]["max"] == "Faster · up to 1.6x"
    assert rows["hedge_mode"]["choice_help"]["max"].startswith("Races a little sooner")
    assert rows["hedge_mode"]["choice_footnote"] == S.CHOICE_FOOTNOTE["hedge_mode"]
    assert rows["model_switch"]["labels"]["auto"] == "Automatic"
