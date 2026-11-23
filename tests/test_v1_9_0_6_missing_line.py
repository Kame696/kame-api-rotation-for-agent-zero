"""1.9.0.6 (Agent Zero decisions/0019 A6b) - the page says which model of the fallback list was not found.

One line per model that answered 404 "not found" in the last 6 h:
"Model '<as written>' was not found by <provider> - check the name in your fallback list" and, when the provider
suggested one, " (did you mean '<hint>'?)". Run in node against the real status report (no browser, no network).
"""
from __future__ import annotations

from pathlib import Path

from test_v1_8_2_9_page_store import loaded, run_store, fresh  # noqa: F401
from test_v1_8_2_7_speed_fallback import clean  # noqa: F401
import kame_engine as K

PANEL = (Path(__file__).resolve().parent.parent / "webui" / "kame-panel.html").read_text(encoding="utf-8")
BODY = "await store.refresh(); return store.fallbackList;"


def test_no_missing_model_no_line():
    out = run_store(BODY, loaded(fallback_models="google/gemini-3.7-flash"))
    assert out["missing"] == []


def test_a_missing_model_is_one_line_with_the_name_as_written_and_the_hint():
    data = loaded(fallback_models="google/gemini-3.7-flash\nopenai/gpt-5.1")
    K._KAME_MISSING["google:gemini-3.7-flash"] = {"until": 4_000_000_000.0, "hint": "gemini-3.7-flash-001"}
    K._KAME_MISSING["openai:gpt-5.1"] = {"until": 4_000_000_000.0, "hint": ""}
    data = loaded(fallback_models="google/gemini-3.7-flash\nopenai/gpt-5.1")
    try:
        out = run_store(BODY, data)
    finally:
        K._KAME_MISSING.clear()
    assert out["missing"] == [
        "Model 'google/gemini-3.7-flash' was not found by google - check the name in your fallback list"
        " (did you mean 'gemini-3.7-flash-001'?)",
        "Model 'openai/gpt-5.1' was not found by openai - check the name in your fallback list",
    ]


def test_both_shelves_of_the_page_show_the_lines():
    assert PANEL.count("fallbackList.missing") == 2         # the Settings tab and the Fallback tab


def test_the_engine_view_says_the_same_lines_as_the_page():
    """Parity with Hermes ``fallback.missing``: the status view carries the lines the page shows."""
    data = loaded(fallback_models="google/gemini-3.7-flash\nopenai/gpt-5.1")
    K._KAME_MISSING["google:gemini-3.7-flash"] = {"until": 4_000_000_000.0, "hint": "gemini-3.7-flash-001"}
    data = loaded(fallback_models="google/gemini-3.7-flash\nopenai/gpt-5.1")
    try:
        page = run_store(BODY, data)["missing"]
    finally:
        K._KAME_MISSING.clear()
    (report,) = data.values()
    assert page and report["view"]["fallback"]["missing"] == page
