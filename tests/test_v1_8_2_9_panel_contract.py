"""1.8.2.9 (U6) - the names the screens read are names `status_view()` really writes.

The page and the chip read ONE view (`status_view()`, E9). A field renamed on one side and not the other
makes a screen go quiet without an error, which is the failure this file turns into a red test:

  * every field the shared view module and the page store read off `view` exists in the view;
  * every field the Hermes page reads (`PANEL_READS`, frozen from its source) exists in the view too, so
    the two ports keep speaking one set of field names.

No Agent Zero, no network, and no JavaScript engine: the scan is a regular expression over the sources.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import clean  # noqa: F401  (autouse fixture; also stubs Agent Zero)
from test_v1_8_2_9_status_view import seed_owner_case  # noqa: F401
import kame_engine as K

ROOT = Path(__file__).resolve().parent.parent
WEBUI = ROOT / "webui"

#: Fields the page store adds to the view itself (`{ ...d.view, active, build }`), not written by the engine.
ADDED_BY_THE_PAGE = {"active", "build"}
#: Not fields: what a JavaScript value answers to.
JS_MEMBERS = {"length", "map", "filter", "find", "some", "every", "slice", "forEach", "join", "includes", "sort",
              "reduce", "concat", "indexOf", "toFixed", "push", "flatMap"}

READ = re.compile(r"(?<![\w\-/])view\s*\??\.\s*([a-z_][a-z0-9_]*)((?:\s*\??\.\s*[a-z_][a-z0-9_]*)*)")


# 1.8.3.1 (A3): the reply rows carry the facts the popover tells (an answered row with every optional field, and
# a failed one); `back_to` is Hermes-only (its pill puts the selector back; Agent Zero has no per-reply selector)
HERMES_ONLY_PATHS = {"replies[].back_to"}


def SEED_REPLIES():
    K._KAME_REPLIES.append({"at": time.time(), "model": "m", "for": "n", "first_s": 1.0, "total_s": 2.0, "main": {"provider": "p", "model": "m"},
                            "why": "busy", "attempts": 2, "cut": True, "waited_s": 3.0, "backup": True})
    K._KAME_REPLIES.append({"at": time.time(), "model": "m", "for": "", "first_s": None, "total_s": None, "failed": True,
                            "reason": "the provider answered 503"})


@pytest.fixture(autouse=True)
def calm():
    K._KAME_IDENTITY_BASE.clear()
    K._KAME_PRIMARY["identity"] = ""
    K._KAME_IN_FLIGHT.clear()
    yield
    K._KAME_IDENTITY_BASE.clear()
    K._KAME_PRIMARY["identity"] = ""
    K._KAME_IN_FLIGHT.clear()


def full_view():
    """A view with every part filled: pools, a fallback list, events, replies, a call in flight."""
    seed_owner_case()
    K.set_fallback_models("google/gemini-3.7-flash")
    K._KAME_PRIMARY["identity"] = "openai:gpt-5.4-nano"
    K._KJ.EVENTS.add("rotation", identity="openai:gpt-5.4-nano", key="ka5206", reason="out of credit")
    SEED_REPLIES()
    return K.status_view()


def reads_of(path: Path):
    """`{('a',): line, ('a', 'b'): line}` for every `view.a.b` the file reads."""
    found = {}
    text = path.read_text(encoding="utf-8")
    for match in READ.finditer(text):
        chain = [match.group(1)] + re.findall(r"[a-z_][a-z0-9_]*", match.group(2))
        line = text.count("\n", 0, match.start()) + 1
        # stop at the first JavaScript member (`view.pools.length`, `view.replies.map(...)`)
        cut = []
        for name in chain:
            if name in JS_MEMBERS:
                break
            cut.append(name)
        if cut:
            found.setdefault(tuple(cut[:2]), line)
    return found


def test_every_field_the_shared_view_and_the_page_store_read_is_in_the_view():
    view = full_view()
    missing = []
    for name in ("kame-view.js", "kame-panel-store.js"):
        for chain, line in sorted(reads_of(WEBUI / name).items()):
            head = chain[0]
            if head in ADDED_BY_THE_PAGE:
                continue
            if head not in view:
                missing.append(f"{name}:{line} view.{head}")
                continue
            if len(chain) > 1 and isinstance(view[head], dict) and view[head] and chain[1] not in view[head]:
                missing.append(f"{name}:{line} view.{'.'.join(chain)}")
    assert missing == [], "the screens read fields status_view() does not write: " + ", ".join(missing)


def test_the_scan_sees_something():
    # a scan that matches nothing would pass every file; keep it honest
    assert len(reads_of(WEBUI / "kame-view.js")) >= 15
    assert len(reads_of(WEBUI / "kame-panel-store.js")) >= 3   # most of the store hands the view to kame-view.js


def test_the_scan_catches_a_renamed_field(tmp_path):
    source = tmp_path / "x.js"
    source.write_text("const a = view?.totals?.keys; const b = view.nothing_like_this.length;", encoding="utf-8")
    found = reads_of(source)
    assert ("totals", "keys") in found and ("nothing_like_this",) in found
    assert "nothing_like_this" not in full_view()


def path_exists(node, path):
    if not path:
        return True
    head, _, rest = path.partition(".")
    many = head.endswith("[]")
    name = head[:-2] if many else head
    if not isinstance(node, dict) or name not in node:
        return False
    value = node[name]
    if many:
        return isinstance(value, list) and any(path_exists(item, rest) for item in value)
    return path_exists(value, rest)


def test_every_path_the_hermes_page_reads_is_a_path_of_the_view():
    paths = json.loads((ROOT / "tests" / "fixtures" / "hermes_1829_panel_reads.json").read_text(encoding="utf-8"))["paths"]
    assert len(paths) >= 40
    view = full_view()
    assert [p for p in paths if p not in HERMES_ONLY_PATHS and not path_exists(view, p)] == []


def test_the_chip_store_reads_the_view_through_the_shared_module():
    # the chip's own derived object (`view` in the rotation store) is built by chipSummary(), not read raw
    chip = (WEBUI / "kame-rotation-store.js").read_text(encoding="utf-8")
    assert "chipSummary" in chip
    assert "/plugins/api_rotation_by_kame/webui/kame-view.js" in chip
