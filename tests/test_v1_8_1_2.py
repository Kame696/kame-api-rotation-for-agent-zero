"""1.8.1.2 witnesses (Agent Zero port): key import ids and the resting wake-up."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

import sys

# Install the host stubs by reusing the reliability module, under whichever name
# pytest already imported it. Importing it a second time under another name
# re-runs its setup and swaps KAME_DATA_DIR under the tests already collected.
if not any(name in sys.modules for name in ("test_v1_8_1_1_reliability", "tests.test_v1_8_1_1_reliability")):
    import test_v1_8_1_1_reliability  # noqa: F401
import kame_engine as engine
import kame_keys as keys

GEMINI = "AIzaSyA1234567890abcdefghijklmnopqrstu"
NVIDIA = "nvapi-abcdefghijklmnopqrstuvwxyz0123456789"


def _import(text, provider=""):
    chosen, found = keys.parse_import(text, provider)
    env = Path(tempfile.mkdtemp()) / ".env"
    env.write_text("OTHER=1\n", encoding="utf-8")
    saved = {}
    keys.add(env, chosen, found, lambda name, value: saved.__setitem__(name, value))
    return chosen, saved


@pytest.mark.parametrize("text,provider,variable", [
    (f"GEMINI_API_KEY={GEMINI}\n", "", "API_KEY_GOOGLE"),
    (f"NVIDIA_API_KEY={NVIDIA}\n", "", "API_KEY_NVIDIA_NIM"),
    (f"GEMINI_API_KEY={GEMINI}\n", "gemini", "API_KEY_GOOGLE"),
    (f"# my keys\nexport GOOGLE_API_KEY=\"{GEMINI}\"  # main\n", "", "API_KEY_GOOGLE"),
])
def test_dotenv_import_lands_where_agent_zero_reads(text, provider, variable):
    """1.8.1.1 wrote API_KEY_GEMINI / API_KEY_NVIDIA and reported success."""
    _chosen, saved = _import(text, provider)
    assert list(saved) == [variable]


def test_add_with_a_familiar_name_uses_the_agent_zero_id():
    env = Path(tempfile.mkdtemp()) / ".env"
    saved = {}
    keys.add(env, "gemini", [GEMINI], lambda name, value: saved.__setitem__(name, value))
    assert list(saved) == ["API_KEY_GOOGLE"]


def test_mixed_file_is_reported_as_mixed_and_empty_file_as_empty():
    assert keys.import_providers(f"GEMINI_API_KEY={GEMINI}\nOPENAI_API_KEY=sk-x\n") == {"google", "openai"}
    assert keys.import_providers("# nothing here\nFOO=bar\n") == set()


def test_resting_wait_keeps_its_padding_on_ordinary_expiry(monkeypatch):
    """Wake early only when a key recovered BEFORE the deadline (Hermes: slept < eta)."""
    slices = []

    async def fast_sleep(seconds):
        slices.append(seconds)

    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(engine.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(engine, "_next_recovery_seconds", lambda *_a: 10.0)
    monkeypatch.setattr(engine, "_kame_honor_intervention", nothing)
    monkeypatch.setattr(engine, "_kame_wait_notice_tick", lambda *a, **k: None)
    # The key becomes ready exactly at its deadline, i.e. ordinary expiry.
    monkeypatch.setattr(engine, "_pool_has_ready_key", lambda *_a: sum(slices) >= 10.0)
    asyncio.run(engine._kame_sleep_on_exhaustion("p:m", ["k"], "chat", "m", engine._KameSleepState()))
    assert sum(slices) > 10.5  # the +0.5s padding and the jitter were kept


def test_resting_wait_still_wakes_on_early_recovery(monkeypatch):
    slices = []

    async def fast_sleep(seconds):
        slices.append(seconds)

    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(engine.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(engine, "_next_recovery_seconds", lambda *_a: 30.0)
    monkeypatch.setattr(engine, "_kame_honor_intervention", nothing)
    monkeypatch.setattr(engine, "_kame_wait_notice_tick", lambda *a, **k: None)
    monkeypatch.setattr(engine, "_pool_has_ready_key", lambda *_a: sum(slices) >= 3.0)
    asyncio.run(engine._kame_sleep_on_exhaustion("p:m", ["k"], "chat", "m", engine._KameSleepState()))
    assert sum(slices) == pytest.approx(3.0)


# --- H4 parity: a billing refusal no wait fixes goes back to Agent Zero -----

_KEYS = ["AIzaSyH4TESTKEY-aaaaaaaaaa", "AIzaSyH4TESTKEY-bbbbbbbbbb"]
_ID = "google:gemini-3.8-flash"


class _Refused(Exception):
    def __init__(self, message, status, body):
        super().__init__(message)
        self.status_code, self.body = status, body


def _carousel(make_error, monkeypatch):
    engine._KAME_KEY_HEALTH.clear()
    engine._KAME_ACCOUNT_HOLDS.clear()
    engine.set_log_level("silent")
    engine.set_wait_notice(False)
    engine._get_identity_state(_ID, _KEYS)
    calls = []

    async def attempt(self, key, ctx):
        calls.append(key)
        raise make_error()

    async def no_wait(*_a, **_k):
        raise AssertionError("must not wait")

    monkeypatch.setattr(engine, "_kame_sleep_on_exhaustion", no_wait)
    ctx = {"identity": _ID, "all_keys": list(_KEYS), "call_type": "Chat", "model_short": "m",
           "attempt": attempt, "progress": {"any": False, "reasoning": False}}

    async def _cb(delta, full):
        return None

    ctx["response_callback"], ctx["reasoning_callback"], ctx["tokens_callback"] = \
        engine._kame_wrap_callbacks(ctx, _cb, None, None)
    return calls, lambda: asyncio.new_event_loop().run_until_complete(engine._kame_carousel(None, ctx))


def test_a0_plan_without_the_service_is_handed_back_once_every_key_said_so(monkeypatch):
    # The OpenAI SDK / litellm hand over the inner `error` object as `.body`.
    body = {"type": "usage_not_included", "message": "Upgrade to Plus."}
    calls, go = _carousel(lambda: _Refused("429 To use Codex with your ChatGPT plan, upgrade to Plus.", 429, body), monkeypatch)
    with pytest.raises(_Refused):
        go()
    assert sorted(calls) == sorted(_KEYS)


def test_a0_empty_balance_still_waits(monkeypatch):
    body = {"type": "insufficient_quota", "message": "You exceeded your current quota, please check your plan and billing details."}
    calls, go = _carousel(lambda: _Refused("429 You exceeded your current quota", 429, body), monkeypatch)
    with pytest.raises(AssertionError, match="must not wait"):
        go()
