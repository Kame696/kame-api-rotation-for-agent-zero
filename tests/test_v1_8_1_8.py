"""Lifecycle and intervention regressions. No Agent Zero installation needed."""
from __future__ import annotations

import asyncio
import functools
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "a0_1818_support", ROOT / "tests" / "test_v1_8_1_1_reliability.py")
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
K = support.K


@pytest.fixture
def host(monkeypatch):
    class Model:
        async def unified_call(self, system_message="", user_message="", messages=None,
                               response_callback=None, reasoning_callback=None,
                               tokens_callback=None, **kwargs):
            return self.result

    class Topic:
        async def summarize_messages(self, messages):
            return "native topic"

    class Bulk:
        async def summarize(self):
            return "native bulk"

    class RateLimiter:
        def __init__(self, seconds=60, **limits):
            self.timeframe, self.limits = seconds, limits
            self.values = {key: [] for key in limits}
            self._lock = asyncio.Lock()

        async def cleanup(self):
            async with self._lock:
                return None

        async def get_total(self, key):
            async with self._lock:
                return sum(v for _, v in self.values.get(key, []))

    models = types.ModuleType("models")
    models.LiteLLMChatWrapper = Model
    models.rate_limiters = {"existing": RateLimiter(requests=10)}
    history = types.ModuleType("helpers.history")
    history.Topic, history.Bulk = Topic, Bulk
    rate = types.ModuleType("helpers.rate_limiter")
    rate.RateLimiter = RateLimiter
    for name, module in (("models", models), ("helpers.history", history),
                         ("helpers.rate_limiter", rate)):
        monkeypatch.setitem(sys.modules, name, module)
    K._KAME_PATCHED = False
    K._KAME_BOUND_ENTRY_POINTS = []
    K.set_log_level("silent")
    monkeypatch.setattr(K, "_get_all_api_keys", lambda self: [])
    yield types.SimpleNamespace(Model=Model, Topic=Topic, Bulk=Bulk,
                                RateLimiter=RateLimiter, models=models)
    K.remove_kame_patch()


@pytest.mark.parametrize("bulk", [False, True])
@pytest.mark.parametrize("error", K._KAME_PASSTHROUGH_EXC)
def test_compression_preserves_native_control_flow(error, bulk):
    sentinel = error("stop this turn")

    async def stopped(**kwargs):
        raise sentinel

    agent = types.SimpleNamespace(call_utility_model=stopped, read_prompt=lambda *a, **k: "")
    obj = types.SimpleNamespace(history=types.SimpleNamespace(agent=agent),
                                summary="previous summary", output_text=lambda: "original")
    before = K._KAME_CALL_CONTEXT.get()
    with pytest.raises(error) as raised:
        asyncio.run(K._kame_bulk_summarize(obj) if bulk else K._kame_summarize_messages(obj, []))
    assert raised.value is sentinel
    assert obj.summary == "previous summary"
    assert K._KAME_CALL_CONTEXT.get() == before


def test_rate_limiter_patch_is_idempotent(host):
    assert K._patch_rate_limiters()
    methods = tuple(getattr(host.RateLimiter, n) for n in ("__init__", "cleanup", "get_total"))
    for _ in range(20):
        assert K._patch_rate_limiters()
    assert methods == tuple(getattr(host.RateLimiter, n) for n in ("__init__", "cleanup", "get_total"))


def test_uninstall_restores_rate_methods_and_existing_instance_lock(host):
    methods = tuple(getattr(host.RateLimiter, n) for n in ("__init__", "cleanup", "get_total"))
    existing = host.models.rate_limiters["existing"]
    original_lock = existing._lock
    assert K.apply_kame_patch()
    created = host.RateLimiter(requests=10)
    created.values["requests"] = [(0, 7)]
    assert K.remove_kame_patch()
    assert methods == tuple(getattr(host.RateLimiter, n) for n in ("__init__", "cleanup", "get_total"))
    assert existing._lock is original_lock
    assert isinstance(created._lock, asyncio.Lock)
    assert asyncio.run(created.get_total("requests")) == 7


def test_uninstall_preserves_a_foreign_outer_model_wrapper(host, monkeypatch):
    assert K.apply_kame_patch()
    captured = host.Model.unified_call

    @functools.wraps(captured)
    async def foreign(self, *args, **kwargs):
        return await captured(self, *args, **kwargs)

    host.Model.unified_call = foreign
    assert K.remove_kame_patch()
    assert host.Model.unified_call is foreign

    def forbidden_selection(self):
        raise AssertionError("unloaded KAME selected a key")

    monkeypatch.setattr(K, "_get_all_api_keys", forbidden_selection)
    obj = host.Model()
    obj.result = object()
    assert asyncio.run(obj.unified_call()) is obj.result


def test_reinstall_uses_the_current_host_method_not_a_stale_stash(host):
    assert K.apply_kame_patch()
    assert K.remove_kame_patch()

    async def updated(self, system_message="", user_message="", messages=None,
                      response_callback=None, reasoning_callback=None,
                      tokens_callback=None, **kwargs):
        return "updated host"

    host.Model.unified_call = updated
    assert K.apply_kame_patch()
    obj = host.Model()
    obj.result = "old host"
    assert asyncio.run(obj.unified_call()) == "updated host"
    assert K.remove_kame_patch()
    assert host.Model.unified_call is updated


def test_inherited_method_is_not_left_as_a_local_override(host):
    class Child(host.Model):
        pass

    assert "unified_call" not in vars(Child)
    assert K._kame_bind_entry_points(Child) == 1
    K._kame_unbind_entry_points(Child)
    assert "unified_call" not in vars(Child)
    assert "_kame_original_unified_call" not in vars(Child)


@pytest.mark.parametrize("name", ["topic", "bulk"])
def test_uninstall_preserves_foreign_compression_wrapper(host, name):
    cls, method = ((host.Topic, "summarize_messages") if name == "topic"
                   else (host.Bulk, "summarize"))
    assert K.apply_kame_patch()
    captured = getattr(cls, method)

    @functools.wraps(captured)
    async def foreign(self, *args, **kwargs):
        return await captured(self, *args, **kwargs)

    setattr(cls, method, foreign)
    assert K.remove_kame_patch()
    assert getattr(cls, method) is foreign
    assert asyncio.run(getattr(cls(), method)(*([[]] if name == "topic" else []))) == "native " + name


def test_repeated_lifecycle_does_not_accumulate_wrappers(host):
    original = host.Model.unified_call
    initializer = host.RateLimiter.__init__
    for _ in range(30):
        assert K.apply_kame_patch()
        assert not K.apply_kame_patch()
        assert K.remove_kame_patch()
        assert host.Model.unified_call is original
        assert host.RateLimiter.__init__ is initializer


def test_child_binding_does_not_rotate_twice_or_deactivate_parent(host, monkeypatch):
    assert K.apply_kame_patch()
    parent = host.Model.unified_call
    class Child(host.Model):
        pass
    assert K._kame_bind_entry_points(Child) == 1
    count = []
    monkeypatch.setattr(K, "_get_all_api_keys", lambda self: count.append(self) or [])
    obj = Child()
    obj.result = "native"
    assert asyncio.run(obj.unified_call()) == "native"
    assert len(count) == 1
    K._kame_unbind_entry_points(Child)
    assert parent._kame_binding["active"]
    assert "unified_call" not in vars(Child)


def test_failed_rebind_factory_keeps_previous_binding_active(host):
    assert K.apply_kame_patch()
    previous = host.Model.unified_call
    def broken(original, state):
        raise ValueError("fixture factory failure")
    with pytest.raises(ValueError):
        K._kame_bind_owned(host.Model, "unified_call", broken)
    assert host.Model.unified_call is previous
    assert previous._kame_binding["active"]


@pytest.mark.parametrize("bulk", [False, True])
def test_compression_task_cancellation_keeps_context_and_summary(bulk):
    async def cancelled(**kwargs):
        raise asyncio.CancelledError()

    agent = types.SimpleNamespace(call_utility_model=cancelled, read_prompt=lambda *a, **k: "")
    obj = types.SimpleNamespace(history=types.SimpleNamespace(agent=agent),
                                summary="old", output_text=lambda: "body")
    before = K._KAME_CALL_CONTEXT.get()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(K._kame_bulk_summarize(obj) if bulk else K._kame_summarize_messages(obj, []))
    assert obj.summary == "old" and K._KAME_CALL_CONTEXT.get() == before
