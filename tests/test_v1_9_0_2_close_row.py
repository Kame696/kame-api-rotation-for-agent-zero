"""1.9.0.2 (H5, parity with the Hermes port's audit F6) - one closing row per call without an answer.

calls.jsonl held only per-attempt rows, so a call that was stopped, handed to the next model of the
list or failed ended on its last ``refused`` row and read like one still running. Now it closes with
one ``record_type: close`` row (``cancelled`` / ``handed_over`` / ``raised``), with no key and no failure
kind, so ``kame_model_health`` (which reads only ``ok`` or kind ``server``/``timeout``) never counts it.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

if not any(name in sys.modules for name in ("test_v1_8_1_1_reliability", "tests.test_v1_8_1_1_reliability")):
    import test_v1_8_1_1_reliability  # noqa: F401
import kame_engine as K
import kame_journal as J


@pytest.fixture()
def rows(monkeypatch):
    seen = []

    class _Journal:
        @staticmethod
        def record_call(**kw):
            seen.append(kw)

    monkeypatch.setattr(K, "_KJ", _Journal)
    monkeypatch.setattr(K, "_kame_main", lambda ctx: True)
    return seen


def _ctx(**extra):
    ctx = {"identity": "gemini:gemini-x", "kame_call_id": "abc123"}
    ctx.update(extra)
    return ctx


def test_a_stop_closes_as_cancelled(rows):
    K._kame_close_row(_ctx(), asyncio.CancelledError())
    assert [(r["outcome"], r["record_type"], r["key"], r["kind"]) for r in rows] == [
        ("cancelled", "close", "", "")]
    assert rows[0]["call_id"] == "abc123"


def test_a_switch_to_the_next_model_closes_as_handed_over(rows):
    K._kame_close_row(_ctx(), K._KameSwitchModel("this model cannot answer either"))
    assert [r["outcome"] for r in rows] == ["handed_over"]


def test_anything_else_closes_as_raised(rows):
    K._kame_close_row(_ctx(), ValueError("x"))
    assert [r["outcome"] for r in rows] == ["raised"]


def test_never_two_rows_and_never_without_a_call_id(rows):
    ctx = _ctx()
    K._kame_close_row(ctx, ValueError("x"))
    K._kame_close_row(ctx, ValueError("y"))
    K._kame_close_row({"identity": "gemini:gemini-x"}, ValueError("z"))
    assert len(rows) == 1


def test_no_row_after_an_answer(rows):
    ctx = _ctx()
    K._kame_timing("gemini:gemini-x", "k", 1, "ok", "", None, 0.0, ctx, 0.0, None, None, "", "abc123")
    rows.clear()
    K._kame_close_row(ctx, asyncio.CancelledError())
    assert rows == []


def test_the_journal_writes_the_type_and_no_key(monkeypatch):
    written = []
    monkeypatch.setattr(J, "TIMINGS_ON", True)
    monkeypatch.setattr(J, "_append", lambda path, ceiling, row: written.append(row))
    J.record_call(identity="gemini:gemini-x", key="", outcome="cancelled", call_id="abc", record_type="close")
    assert written and written[0]["record_type"] == "close" and written[0]["key"] == ""
    written.clear()
    J.record_call(identity="gemini:gemini-x", key="", outcome="ok", call_id="abc")
    assert "record_type" not in written[0]
