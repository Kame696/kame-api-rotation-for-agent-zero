"""1.8.2.9 (G1-G3, decisions/0013) - three Hermes items that do not apply to Agent Zero.

Each is kept honest by a test, so that if Agent Zero ever changes, the test fails and the row
becomes a task again:

* G1 (A13): on Agent Zero's Responses transport with native function tools, no model of the
  list is raced beside the reply (a Gemini function-call turn needs its signature); the
  sequential switch to the next model stays as it is.
* G2 (A11): no shipped file imports `yaml`, and an optional library is never imported
  outside `try/except ImportError`.
* G3 (A10): nothing between KAME and its list cuts the walk - five entries, four failing,
  the fifth answers inside one call.

No Agent Zero installation, no network.
"""
from __future__ import annotations

import ast
import sys
import time
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    FALLBACK_BEHAVIOUR, ID, KEYS, clean, fake_models, make_ctx, run, streaming, _rest_every_key,
)
import kame_engine as K

ROOT = Path(__file__).resolve().parents[1]
QUICK_SLOW = {False: (0.5, 0.5, 0.5), True: (0.5, 0.5, 0.5)}


@pytest.fixture(autouse=True)
def calm():
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    getattr(K, "_KAME_PROBED_AT", {}).clear()
    yield
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()


# --- G1: Responses-mode native tools -------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"a0_api_mode": "responses"},
    {"a0_responses_function_tools": [{"type": "function", "name": "t"}]},
    {"previous_response_id": "resp_1"},
    {"responses_input_items": [{"role": "user", "content": "hi"}]},
])
def test_responses_mode_with_native_tools_forbids_cross_model_legs(kwargs):
    ctx, _ = make_ctx(streaming({}))
    ctx["kwargs"] = dict(kwargs)
    assert K._kame_cross_allowed(ctx) is False


@pytest.mark.parametrize("kwargs", [{}, {"temperature": 0.2}, {"a0_api_mode": "chat_completions"}])
def test_chat_completions_allows_cross_model_legs(kwargs):
    ctx, _ = make_ctx(streaming({}))
    ctx["kwargs"] = dict(kwargs)
    assert K._kame_cross_allowed(ctx) is True


def test_no_model_of_the_list_races_a_reply_on_the_responses_transport(fake_models, monkeypatch):
    monkeypatch.setattr(K, "_KAME_SLOW_S", QUICK_SLOW)
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 0.1)
    K.set_fallback_models("google/gemini-3.7-flash")
    ctx, _ = make_ctx(streaming({k: 3.0 for k in KEYS}))
    ctx["kwargs"] = {"a0_api_mode": "responses", "previous_response_id": "resp_1"}
    assert K._kame_cross_entry(ctx, ID, False, 30.0) is None
    started = time.monotonic()
    result = run(ctx)
    # the slow bound still hands the reply to the next model - one after the other, never beside
    assert result[0] == "fallback answer from google/gemini-3.7-flash"
    assert ctx.get("answered_by") is None
    assert time.monotonic() - started >= 0.45        # it waited for the slow bound, no race at 0.1 s
    _model, kwargs = fake_models[0]
    assert "previous_response_id" not in kwargs        # the sequential switch strips the host's state


def test_chat_completions_still_races_the_next_model(fake_models, monkeypatch):
    monkeypatch.setattr(K, "_KAME_SLOW_S", QUICK_SLOW)
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 0.1)
    K.set_fallback_models("google/gemini-3.7-flash")
    ctx, _ = make_ctx(streaming({k: 3.0 for k in KEYS}))
    started = time.monotonic()
    assert run(ctx)[0] == "fallback answer from google/gemini-3.7-flash"
    assert ctx.get("answered_by") == "google:gemini-3.7-flash"
    assert time.monotonic() - started < 0.45


# --- G2: no silent optional import ---------------------------------------------------

#: Agent Zero's own modules and the libraries it ships: importing them is the plugin's job.
HOST_MODULES = {"helpers", "agent", "models", "plugins", "usr", "python", "initialize", "langchain_core",
                "langchain", "litellm"}


def _shipped_python():
    for path in sorted(ROOT.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        yield path


def _local_modules():
    names = set()
    for path in _shipped_python():
        names.add(path.stem)
    return names


def _imports(tree):
    """(top-level module, node, guarded?) for every absolute import."""
    out = []

    def walk(node, guarded):
        for child in ast.iter_child_nodes(node):
            now = guarded
            if isinstance(child, ast.Try):
                catches = any(
                    h.type is None or any(n in ast.dump(h.type) for n in ("ImportError", "ModuleNotFoundError", "Exception"))
                    for h in child.handlers)
                for sub in child.body:
                    walk_one(sub, guarded or catches)
                for part in (child.handlers, child.orelse, child.finalbody):
                    for sub in part:
                        walk_one(sub, guarded)
                continue
            walk_one(child, now)

    def walk_one(node, guarded):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name.split(".")[0], node, guarded))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.append((node.module.split(".")[0], node, guarded))
        else:
            walk(node, guarded)

    walk_one(tree, False)
    return out


def test_no_shipped_file_imports_yaml():
    offenders = []
    for path in _shipped_python():
        for name, node, _g in _imports(ast.parse(path.read_text(encoding="utf-8"))):
            if name in ("yaml", "ruamel", "hermes_yaml"):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


def test_every_optional_library_is_imported_inside_try_except_importerror():
    local = _local_modules()
    allowed = set(sys.stdlib_module_names) | HOST_MODULES | local
    offenders = []
    for path in _shipped_python():
        for name, node, guarded in _imports(ast.parse(path.read_text(encoding="utf-8"))):
            if name not in allowed and not guarded:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} import {name}")
    assert offenders == []


# --- G3: nothing cuts KAME's list ------------------------------------------------------

def test_five_entries_four_failing_the_fifth_answers_within_one_call(fake_models):
    K.set_fallback_models("p1/a, p2/b, p3/c, p4/d, p5/e")
    for name in ("p1/a", "p2/b", "p3/c", "p4/d"):
        FALLBACK_BEHAVIOUR[name] = "error"
    ctx, _ = make_ctx(streaming({}))
    _rest_every_key()
    assert run(ctx)[0] == "fallback answer from p5/e"
    assert [m for m, _k in fake_models] == ["p1/a", "p2/b", "p3/c", "p4/d", "p5/e"]
