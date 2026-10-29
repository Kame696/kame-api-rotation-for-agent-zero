"""1.8.2.9 (U6) - the Agent Zero screens say what the Hermes screens say.

The owner's rule: the plugin is one product for two frameworks, identical except where the framework
forces a difference. `fixtures/hermes_1829_ui_strings.json` freezes what the Hermes page says (made from
the Hermes source by `tools/extract_hermes_ui_strings.py`). These tests turn drift into a red test:

  * the tabs come in Hermes' order, with the one Agent Zero tab after Settings;
  * the settings shelves have Hermes' ids, titles, notes and keys, in Hermes' order, with the one Agent Zero
    shelf after "Rest times" and the Hermes-only setting named;
  * every frozen sentence is on the Agent Zero screens word for word (the host's name is the only change);
  * every sentence Agent Zero says in its own words is spelled out, and the Hermes sentences it does not
    say are asserted ABSENT, so porting one means updating the fixture on purpose;
  * the menu and the page are called "API Rotation"; nothing of the old names or the old toast is left.

When the Hermes source is next to this folder, one more test rebuilds the fixture from it and fails if the
frozen copy is stale (the shipped zip carries the tests but not the Hermes source: it skips there).
"""
from __future__ import annotations

import html
import importlib.util
import json
import re
from pathlib import Path

import pytest

import kame_settings as S

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "hermes_1829_ui_strings.json").read_text(encoding="utf-8"))
WEBUI = ROOT / "webui"

SCREEN_FILES = [
    WEBUI / "kame-panel.html", WEBUI / "kame-panel-store.js", WEBUI / "kame-view.js",
    WEBUI / "kame-rotation-store.js", WEBUI / "config.html", WEBUI / "main.html",
    ROOT / "extensions" / "webui" / "model-context-strip-end" / "kame-rotation.html",
    ROOT / "extensions" / "webui" / "sidebar-quick-actions-dropdown-start" / "kame-entry.html",
]


def plain(text: str) -> str:
    """The words a person reads: HTML entities and \\uXXXX escapes decoded, whitespace collapsed."""
    text = html.unescape(text)
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    return re.sub(r"\s+", " ", text)


def read(name: str) -> str:
    return plain((WEBUI / name).read_text(encoding="utf-8"))


EVERYTHING = " ".join(plain(path.read_text(encoding="utf-8")) for path in SCREEN_FILES)


def host(text: str) -> str:
    """A Hermes sentence as it must read in Agent Zero (the host's name is the one allowed change)."""
    for old, new in FIXTURE["substitutions"]:
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text)


# --- tabs --------------------------------------------------------------------------------------

def test_the_tabs_are_the_hermes_tabs_with_one_more_after_settings():
    panel = (WEBUI / "kame-panel.html").read_text(encoding="utf-8")
    listed = re.search(r"x-for=\"t in \[(.*?)\]\" :key", panel, re.S).group(1)
    ours = [label for _id, label in re.findall(r"\['(\w+)','([^']+)'\]", listed)]
    expected = list(FIXTURE["tabs"]["hermes"])
    for extra in FIXTURE["tabs"]["agent_zero_extra"]:
        expected.insert(expected.index(extra["after"]) + 1, extra["label"])
    assert ours == expected


# --- settings shelves --------------------------------------------------------------------------

def test_the_shelves_have_hermes_ids_titles_notes_and_order():
    hermes = FIXTURE["groups"]
    ours = [{"id": g, "title": t, "note": n, "keys": list(k)} for g, t, n, k in S.GROUPS]
    expected = [dict(g) for g in hermes]
    for extra in FIXTURE["groups_agent_zero_only"]:
        at = next(i for i, g in enumerate(expected) if g["id"] == extra["after"]) + 1
        expected.insert(at, {"id": extra["id"]})
    assert [g["id"] for g in ours] == [g["id"] for g in expected]
    only_here = {extra["id"] for extra in FIXTURE["groups_agent_zero_only"]}
    for mine, theirs in zip(ours, expected):
        if mine["id"] in only_here:
            continue
        assert mine["title"] == theirs["title"], mine["id"]
        # the notes are the same words; the host's name is the only change (Hermes -> Agent Zero)
        assert mine["note"] == host(theirs["note"]), mine["id"]


def test_the_keys_on_each_shelf_are_the_hermes_keys_minus_the_hermes_only_ones():
    hermes_only = set(FIXTURE["groups_hermes_only_keys"])
    mine = {g: list(keys) for g, _t, _n, keys in S.GROUPS}
    for group in FIXTURE["groups"]:
        if group["id"] == "off":
            # Hermes lists its `*disable*` flags; Agent Zero's twins are the switches of TROUBLESHOOTING
            assert group["keys"] == ["*disable-flags*"]
            assert set(mine["off"]) == set(S.TROUBLESHOOTING)
            continue
        expected = [k for k in group["keys"] if k not in hermes_only]
        assert mine[group["id"]] == expected, group["id"]
    for key in hermes_only:
        assert key not in S.SETTINGS, f"{key} has no Agent Zero twin; it must not be defined here"


# --- sentences ---------------------------------------------------------------------------------

def test_every_frozen_hermes_sentence_is_on_the_agent_zero_screens_word_for_word():
    assert len(FIXTURE["strings"]) >= 50
    missing = []
    for item in FIXTURE["strings"]:
        wanted = host(item["hermes"])
        for name in item["files"]:
            if wanted not in read(name):
                missing.append(f"{name}: {wanted!r}")
    assert missing == [], "words that drifted from Hermes:\n" + "\n".join(missing)


def test_every_sentence_agent_zero_says_in_its_own_words_is_spelled_out_and_present():
    assert FIXTURE["adapted"], "the adapted list is part of the contract"
    for item in FIXTURE["adapted"]:
        assert item["reason"], item["hermes"]
        assert item["agent_zero"] != host(item["hermes"]), "that is not adapted, move it to strings"
        for name in item["files"]:
            assert item["agent_zero"] in read(name), f"{name}: {item['agent_zero']!r}"


def test_the_hermes_sentences_agent_zero_does_not_say_are_still_not_said():
    assert FIXTURE["not_ported"], "the not-ported list is part of the contract"
    for item in FIXTURE["not_ported"]:
        assert item["reason"], item["hermes"]
        assert host(item["hermes"]) not in EVERYTHING, (
            f"{item['hermes']!r} is on an Agent Zero screen now: move it from not_ported to strings in "
            "tools/extract_hermes_ui_strings.py and rewrite the fixture")


# --- the names that must be gone ---------------------------------------------------------------

def test_the_menu_and_the_page_are_called_api_rotation_and_the_old_names_are_gone():
    entry = (ROOT / "extensions" / "webui" / "sidebar-quick-actions-dropdown-start" / "kame-entry.html").read_text(encoding="utf-8")
    assert "<span>API Rotation</span>" in entry
    assert "<title>API Rotation</title>" in (WEBUI / "main.html").read_text(encoding="utf-8")
    for old in ("Fastest", "kame-rotation-wait", "KAME - API key rotation", "Open the KAME panel"):
        assert old not in EVERYTHING, old


# --- freshness against the Hermes source -------------------------------------------------------

def test_the_frozen_fixture_is_what_the_hermes_source_says_today():
    tool = ROOT / "tools" / "extract_hermes_ui_strings.py"
    if not tool.is_file():
        pytest.skip("the extractor is a development tool; it is not in the plugin zip")
    spec = importlib.util.spec_from_file_location("kame_extract_hermes_ui_strings", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not (module.HERMES / "desktop" / "plugin.js").is_file():
        pytest.skip("no Hermes source next to this folder")
    ui, reads, problems = module.build(module.HERMES)
    assert problems == []
    assert ui == FIXTURE, "the Hermes page changed: run tools/extract_hermes_ui_strings.py --write and port the change"
    frozen_reads = json.loads((ROOT / "tests" / "fixtures" / "hermes_1829_panel_reads.json").read_text(encoding="utf-8"))
    assert reads == frozen_reads
