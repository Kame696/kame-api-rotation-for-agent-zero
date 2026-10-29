"""1.8.2.9 (U5, C6) - the "Fallback models" tab edits the list as rows; the merge is done on the server.

A row is a provider and a model. Only complete rows are saved; a saved line that a row matches is kept
as written (a hand-written `@ api_base` stays); a saved line nobody can read as an entry (a comment) is
kept after the list. No Agent Zero, no network: the handler runs against the fakes in `kame_api_stub`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import clean  # noqa: F401  (fixture used by name; also stubs Agent Zero)
import kame_api_stub as stub
import kame_engine as K
import kame_settings as S

ROOT = Path(__file__).resolve().parent.parent
API = stub.load_api("kame_fallback_set", K, S)


@pytest.fixture(autouse=True)
def fresh():
    stub.reset()
    yield
    stub.reset()


def save(rows, saved_text=""):
    stub.CONFIG["fallback_models"] = saved_text
    return stub.call(API.KameFallbackSet, {"rows": rows})


def test_rows_save_as_lines_in_the_order_shown():
    out = save([{"provider": "openai", "name": "gpt-5.4-nano"},
                {"provider": "google", "name": "gemini/gemini-3.5-flash"}])
    assert out["ok"] is True
    assert out["text"].splitlines() == ["openai/gpt-5.4-nano", "google/gemini/gemini-3.5-flash"]
    assert stub.CONFIG["fallback_models"] == out["text"]
    assert [e["identity"] for e in out["entries"]] == ["openai:gpt-5.4-nano", "google:gemini/gemini-3.5-flash"]
    assert stub.ACTIVATED, "the new list must be applied at once"
    assert out["message"] == "2 models saved"


def test_a_saved_line_keeps_its_hand_written_endpoint():
    out = save([{"provider": "openai", "name": "gpt-5.4-nano"}],
               "openai/gpt-5.4-nano @ https://x/v1")
    assert out["text"] == "openai/gpt-5.4-nano @ https://x/v1"
    assert out["entries"][0]["api_base"] == "https://x/v1"


def test_a_row_with_its_own_endpoint_wins_over_the_saved_one():
    out = save([{"provider": "openai", "name": "gpt-5.4-nano", "api_base": "https://y/v1"}],
               "openai/gpt-5.4-nano @ https://x/v1")
    assert out["text"] == "openai/gpt-5.4-nano @ https://y/v1"


def test_a_saved_line_is_kept_whatever_its_casing():
    out = save([{"provider": "OpenAI", "name": "GPT-5.4-Nano"}], "openai/GPT-5.4-Nano @ https://x/v1")
    assert out["text"] == "openai/GPT-5.4-Nano @ https://x/v1"


def test_a_line_nobody_can_read_survives_a_save():
    saved = "# my note about the list\nopenai/gpt-5.4-nano\nthis is not an entry"
    out = save([{"provider": "groq", "name": "llama-3.3-70b-versatile"}], saved)
    lines = out["text"].splitlines()
    assert lines[0] == "groq/llama-3.3-70b-versatile"
    assert "# my note about the list" in lines and "this is not an entry" in lines
    assert [e["identity"] for e in out["entries"]] == ["groq:llama-3.3-70b-versatile"]


def test_a_row_with_an_empty_model_is_not_saved():
    out = save([{"provider": "openai", "name": "gpt-5.4-nano"}, {"provider": "groq", "name": "  "},
                {"provider": "", "name": "orphan"}])
    assert out["text"] == "openai/gpt-5.4-nano"
    assert out["saved"] == 1 and out["incomplete"] == 2
    assert "2 rows left out - choose a provider and a model" in out["message"]


def test_thirteen_rows_save_twelve_and_the_reply_says_one_was_left_out():
    rows = [{"provider": "openai", "name": f"model-{i}"} for i in range(13)]
    out = save(rows)
    assert out["saved"] == 12 and out["left_out"] == 1
    assert len(out["entries"]) == 12
    assert "1 left out - the list holds at most 12" in out["message"]


def test_the_same_model_twice_is_one_entry():
    out = save([{"provider": "openai", "name": "gpt-5.4-nano"}, {"provider": "OPENAI", "name": "GPT-5.4-nano"}])
    assert out["saved"] == 1


def test_an_empty_list_clears_the_saved_models_but_not_a_note():
    out = save([], "openai/gpt-5.4-nano\n# keep me")
    assert out["text"] == "# keep me" and out["entries"] == []
    assert out["message"] == "0 models saved"


def test_rows_must_be_a_list_and_nothing_is_saved_otherwise():
    stub.CONFIG["fallback_models"] = "openai/gpt-5.4-nano"
    out = stub.call(API.KameFallbackSet, {"rows": "openai/x"})
    assert out["ok"] is False and not stub.SAVED


def test_other_settings_in_the_config_are_left_alone():
    stub.CONFIG.update({"hedge_mode": "max", "fallback_models": ""})
    out = stub.call(API.KameFallbackSet, {"rows": [{"provider": "openai", "name": "gpt-5.4-nano"}]})
    assert out["ok"] and stub.CONFIG["hedge_mode"] == "max"


def test_no_key_is_asked_for_or_returned():
    out = save([{"provider": "openai", "name": "gpt-5.4-nano", "api_key": "sk-SECRET-VALUE"}])
    assert "sk-SECRET-VALUE" not in repr(out) and "sk-SECRET-VALUE" not in repr(stub.CONFIG)


# -- the page: a static check (the screen itself is looked at on the server) ------------------------------

def test_the_panel_has_the_rows_the_plus_button_the_x_and_the_guarded_import():
    html = (ROOT / "webui" / "kame-panel.html").read_text(encoding="utf-8")
    store = (ROOT / "webui" / "kame-panel-store.js").read_text(encoding="utf-8")
    assert "+ Add fallback model" in html
    assert 'aria-label="Remove this model"' in html and "&times;" in html
    assert "not saved &mdash; choose a model" in html
    assert "No fallback models yet." in html
    assert "Up to 12 models" in html
    # reorder: up and down arrows (Q1), not a drag library
    assert 'aria-label="Move up"' in html and 'aria-label="Move down"' in html
    # Agent Zero's own model search is optional: the import is guarded, and without it the inputs are plain
    assert "/plugins/_model_config/webui/model-config-store.js" in store
    assert "try {" in store.split("async function loadModelConfig", 1)[1].split("}\n\nlet rowSeq", 1)[0]
    assert "catch" in store.split("async function loadModelConfig", 1)[1][:600]
    assert "$store.kamePanel.hasSearch" in html and "searchModelsDetailed" in store and "groupResults" in store
    assert "Catalog suggestions" in html and "No models found" in html
    assert "kame_fallback_set" in store and "rows" in store
