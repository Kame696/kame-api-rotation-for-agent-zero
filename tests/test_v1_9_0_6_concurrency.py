"""1.9.0.6 (Agent Zero decisions/0019 A1, A2) - many sessions never block each other.

Agent Zero runs every chat and every subordinate agent as its own task (often its own thread, its own event
loop), all through the same patched model methods. KAME's shared state is guarded by `threading` locks; the rule
(0019 A1) is that such a lock guards pure in-memory work only: never held across an `await`, a sleep, a network
call, a host call, or a disk write.

The probe: the carousel runs on ONE event-loop thread here. Whenever a fake request resumes, no KAME lock may be
held by anybody - a held lock at that instant is a lock held across a suspension (and in the real host, a
context frozen behind it). Run on a virtual clock (kame_vclock.py): 2 s of provider time cost nothing.

No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import types

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, Refusal, clean, make_ctx,
)
from test_v1_8_4_3_quiet_guard import (  # noqa: F401
    NEXT, NEXT_ID, THIRD, THIRD_ID, overloaded,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K
import kame_hedging as H
import kame_journal as J

REQUEST_S = 0.2          # what every fake provider request takes (small, per the designer)
CHATS, SUBS = 8, 4       # 8 chat calls + 4 subordinate-agent calls at once
HARD_LIMIT_S = 30.0      # a real second count: a deadlock must fail the test, not hang it


def kame_locks():
    """Every plain `threading.Lock` KAME keeps (an RLock cannot say whether it is held)."""
    found = {
        "_KAME_LOCK": K._KAME_LOCK,
        "_KAME_NO_CACHE_LOCK": K._KAME_NO_CACHE_LOCK,
        "journal._APPEND_LOCK": J._APPEND_LOCK,
        "journal._HEALTH_LOCK": J._HEALTH_LOCK,
        "journal.EVENTS": getattr(J.EVENTS, "_lock", None),
        "hedge.tracker": getattr(H.HEDGE_TRACKER, "_lock", None),
        "hedge.ledger": getattr(H.SPEED_LEDGER, "_lock", None),
    }
    pacer = getattr(K, "_KAME_PACER", None) or getattr(K, "PACER", None)
    for name, owner in (("pacer", pacer), ("latency", getattr(K, "_KAME_LATENCY", None) or getattr(K, "LATENCY", None))):
        lock = getattr(owner, "_lock", None)
        if lock is not None:
            found[name] = lock
    return {name: lock for name, lock in found.items()
            if lock is not None and hasattr(lock, "locked")}


class Fleet:
    """The provider: every request awaits REQUEST_S, may be told to be busy first, and checks the locks."""

    def __init__(self, vt):
        self.vt = vt
        self.held = []                  # (who, lock name) seen held at a suspension point
        self.starts = []                # (virtual s, who)
        self.requests = {}              # who -> how many requests it has seen
        self.busy_first = {}            # who -> how many first requests say "busy"
        self.limiter = None             # a rate limiter to wait on, when the case has one

    def probe(self, who):
        for name, lock in kame_locks().items():
            if lock.locked():
                self.held.append((who, name))

    async def serve(self, who, ctx):
        self.probe(who + ":start")
        seen = self.requests.get(who, 0)
        self.requests[who] = seen + 1
        self.starts.append((self.vt.elapsed(), who))
        if seen < self.busy_first.get(who, 0):
            await asyncio.sleep(0.01)
            self.probe(who + ":busy")
            raise overloaded()
        if self.limiter is not None:
            await self.limiter.wait()
            self.limiter.add(requests=1)
            self.probe(who + ":limiter")
        await asyncio.sleep(REQUEST_S)
        self.probe(who + ":answer")
        callback = ctx.get("response_callback")
        if callback is not None:
            await callback(who, who)
        return (f"answer from {who}", "")

    async def home(self, owner, key, ctx):
        return await self.serve("home", ctx)


class ListModel:
    def __init__(self, provider, name, fleet):
        self.a0_model_conf = types.SimpleNamespace(provider=provider)
        self.model_name = f"{provider}/{name}"
        self.fleet = fleet

    async def unified_call(self, **kwargs):
        return await self.fleet.serve(self.model_name, kwargs)


@pytest.fixture
def fleet(vt, monkeypatch, tmp_path):
    monkeypatch.setenv("KAME_DATA_DIR", str(tmp_path))
    f = Fleet(vt)
    mod = types.ModuleType("models")

    class ModelType:
        CHAT = "Chat"

    class ModelConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def build_kwargs(self):
            return {}

    mod.ModelType, mod.ModelConfig = ModelType, ModelConfig
    mod.get_chat_model = lambda provider, name, model_config=None, **kw: ListModel(provider, name, f)
    mod.rate_limiters = {}
    monkeypatch.setitem(sys.modules, "models", mod)
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_WINDOW, K._KAME_BUSY_KIND, K._KAME_PROBED_AT, K._KAME_REPLIES,
                  K._KAME_TURNS, K._KAME_SEEDED, K._KAME_QUIET_SEEDED):
        table.clear()
    H.SPEED_LEDGER.clear()
    K.set_fallback_models(f"{NEXT}\n{THIRD}")
    K.set_thought_quiet(0)
    yield f
    K.set_thought_quiet(None)
    for table in (K._KAME_BUSY_UNTIL, K._KAME_BUSY_WINDOW, K._KAME_PROBED_AT):
        table.clear()
    H.SPEED_LEDGER.clear()


def run_all(vt, fleet, coroutines):
    """gather everything on the virtual loop, in a worker thread that a real 30 s timer can give up on."""
    box = {}

    def go():
        async def main():
            return await asyncio.gather(*coroutines, return_exceptions=True)

        try:
            asyncio.set_event_loop(vt.loop)
            box["result"] = vt.run(main(), limit=3600)
        except BaseException as exc:             # noqa: BLE001 - reported below
            box["error"] = exc

    worker = threading.Thread(target=go, daemon=True)
    worker.start()
    worker.join(HARD_LIMIT_S)
    assert not worker.is_alive(), "deadlock: the contexts did not finish in 30 real seconds"
    assert "error" not in box, box.get("error")
    return box["result"]


def twelve(fleet, keys=KEYS):
    """8 chat calls + 4 subordinate-agent calls: 12 contexts, each with its own ctx dict and its own chat line."""
    jobs, lines = [], []
    for n in range(CHATS + SUBS):
        ctx, forwarded = make_ctx(fleet.home, keys=keys)
        ctx["who_am_i"] = ("chat" if n < CHATS else "subordinate") + str(n)

        async def one(ctx=ctx, n=n):
            # a subordinate agent is another task with its own agent; the contextvar is per task
            K._KAME_CURRENT_AGENT.set(types.SimpleNamespace(number=0 if n < CHATS else n, context=None))
            return await K._kame_carousel(None, ctx)

        jobs.append(one())
        lines.append(forwarded)
    return jobs, lines


def assert_all_answered(results):
    assert len(results) == CHATS + SUBS
    failures = [r for r in results if isinstance(r, BaseException)]
    assert not failures, failures
    assert all(isinstance(r, tuple) and r[0].startswith("answer from") for r in results), results


# --- A1: twelve contexts at once -----------------------------------------------------------------------------

def test_a1_twelve_contexts_all_answer_and_nobody_waits_for_anybody(vt, fleet):
    jobs, lines = twelve(fleet)
    results = run_all(vt, fleet, jobs)
    assert_all_answered(results)
    assert vt.elapsed() < 2 * REQUEST_S, f"serialized: {vt.elapsed():.1f} s for 12 parallel requests of {REQUEST_S} s"
    assert fleet.held == [], f"a KAME lock was held while a request was suspended: {fleet.held[:5]}"
    assert all(line for line in lines), "every context's own chat line got its words"


def test_a1_twelve_contexts_with_the_host_rate_limiter_active(vt, fleet, monkeypatch):
    class RateLimiter:                       # the host's helpers.rate_limiter.RateLimiter, as it is today
        def __init__(self, seconds=60, **limits):
            self.timeframe = seconds
            self.limits = {k: v for k, v in limits.items()}
            self.values = {k: [] for k in self.limits}
            self._lock = asyncio.Lock()

        def add(self, **kwargs):
            for key, value in kwargs.items():
                self.values.setdefault(key, []).append((time.time(), value))

        async def cleanup(self):
            async with self._lock:
                cutoff = time.time() - self.timeframe
                for key in self.values:
                    self.values[key] = [(t, v) for t, v in self.values[key] if t > cutoff]

        async def get_total(self, key):
            async with self._lock:
                return sum(v for _, v in self.values.get(key, []))

        async def wait(self):
            while True:
                await self.cleanup()
                over = False
                for key, limit in self.limits.items():
                    if limit and await self.get_total(key) >= limit:
                        over = True
                if not over:
                    return
                await asyncio.sleep(1)

    rate = types.ModuleType("helpers.rate_limiter")
    rate.RateLimiter = RateLimiter
    helpers = types.ModuleType("helpers")
    monkeypatch.setitem(sys.modules, "helpers", sys.modules.get("helpers", helpers))
    monkeypatch.setitem(sys.modules, "helpers.rate_limiter", rate)
    limiter = RateLimiter(requests=100)
    sys.modules["models"].rate_limiters = {"existing": limiter}
    try:
        assert K._patch_rate_limiters()
        assert isinstance(limiter._lock, type(threading.Lock()))     # the conversion is on
        fleet.limiter = limiter
        jobs, _lines = twelve(fleet)
        results = run_all(vt, fleet, jobs)
    finally:
        K._unpatch_rate_limiters()
    assert_all_answered(results)
    assert vt.elapsed() < 2 * REQUEST_S
    assert fleet.held == [], fleet.held[:5]
    assert not limiter._lock.locked() if hasattr(limiter._lock, "locked") else True


def test_a1_every_model_of_a_one_provider_list_busy_twice_then_answering(vt, fleet):
    fleet.busy_first = {"home": 2, NEXT: 2, THIRD: 2}
    jobs, lines = twelve(fleet)
    results = run_all(vt, fleet, jobs)
    assert_all_answered(results)
    assert fleet.held == [], f"a KAME lock was held while a request was suspended: {fleet.held[:5]}"
    assert all(line for line in lines)
    assert fleet.requests.get("home", 0) >= 3        # the busy ones were asked again, and answered


# --- A1: the disk is never touched with the shared lock held ---------------------------------------------------

def test_a1_the_journal_is_never_written_while_the_shared_lock_is_held(monkeypatch, tmp_path):
    """A refusal, an answer and a thaw write pool-health.json. None of those writes may run inside _KAME_LOCK:
    a slow disk would freeze every context at the lock."""
    monkeypatch.setenv("KAME_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(J, "SHARE_HEALTH_ON", True)
    J._reset_for_tests()
    K._KAME_KEY_HEALTH.clear()
    K._get_identity_state(ID, KEYS[:3])
    inside = []
    real = J._flush_health

    def spy(force=False):
        if K._KAME_LOCK.locked():
            inside.append("flush")
        return real(force)

    monkeypatch.setattr(J, "_flush_health", spy)
    # a refusal that rests a key (writes a hold), another key of the same pool answering (clears one), the thaw,
    # and a closed door resting the whole pool
    K._mark_key_health(ID, KEYS[0], success=False, delay=30.0, kind="server")
    K._mark_key_health(ID, KEYS[0], success=True)
    K._mark_key_health(ID, KEYS[1], success=False, delay=60.0, kind="server")
    K._thaw_server_cooled_keys(ID, KEYS[2])
    K._kame_rest_pool_for_door(ID, KEYS[:3], 45.0)
    assert inside == [], "pool-health.json was written inside _KAME_LOCK"
    document = (tmp_path / J.HEALTH_FILE).read_text(encoding="utf-8") if (tmp_path / J.HEALTH_FILE).exists() else ""
    assert document, "the holds still reach the disk (after the lock is released)"


def test_a1_a_slow_disk_write_does_not_stop_another_context_reading_a_hold(monkeypatch, tmp_path):
    """While one context's hold is being written, another context asking `hold_for` (it does, under the shared
    lock, when a key is first seen) must not wait for the disk."""
    monkeypatch.setenv("KAME_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(J, "SHARE_HEALTH_ON", True)
    J._reset_for_tests()
    J.note_hold(ID, KEYS[0], time.time() + 60, "server", "")
    gate_in, gate_out = threading.Event(), threading.Event()
    real_replace = J.os.replace

    def slow_replace(src, dst):
        gate_in.set()
        gate_out.wait(10)
        return real_replace(src, dst)

    monkeypatch.setattr(J.os, "replace", slow_replace)
    writer = threading.Thread(target=lambda: J.note_hold(ID, KEYS[1], time.time() + 60, "server", ""), daemon=True)
    writer.start()
    assert gate_in.wait(5), "the writer never reached the disk"
    answer = {}
    reader = threading.Thread(target=lambda: answer.update(held=J.hold_for(ID, KEYS[0])), daemon=True)
    reader.start()
    reader.join(2.0)
    finished = not reader.is_alive()
    gate_out.set()
    writer.join(5)
    reader.join(5)
    assert finished, "hold_for waited for another context's disk write"
    assert answer["held"] is not None


def test_a1_the_prompt_cache_table_is_read_outside_its_lock(monkeypatch):
    seen = []
    real = J.cache_refusals_load

    def spy():
        seen.append(K._KAME_NO_CACHE_LOCK.locked())
        return real()

    monkeypatch.setattr(J, "cache_refusals_load", spy)
    K._kame_cache_forget(keep_file=True)
    K._kame_cache_set()
    assert seen == [False], "the cache-refusal file was read with _KAME_NO_CACHE_LOCK held"
    assert K._kame_cache_set() is K._kame_cache_set()          # still one table, read once
    assert len(seen) == 1


# --- A2: what belongs to one conversation stays with it ----------------------------------------------------------

def test_a2_environment_reads_are_process_settings_never_conversation_state():
    """0019 A2: `os.environ` is read in four places only, and none holds anything of one conversation: the
    settings loaded at start, the data directory, the remembered-trouble kill switch, and the settings page's
    "which variable overrides this" hint."""
    import ast
    from pathlib import Path

    allowed = {("kame_engine.py", "_kame_apply_env_overrides"), ("kame_journal.py", "data_dir"),
               ("kame_model_health.py", "disabled"), ("kame_settings.py", "env_override")}
    found = set()
    for path in sorted(Path(K.__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    text = ast.unparse(node) if isinstance(node, (ast.Attribute, ast.Call)) else ""
                    if text.startswith(("os.environ", "os.getenv")) or text == "os.environ":
                        found.add((path.name, fn.name))
    assert found <= allowed, f"a new environment read on a call path: {sorted(found - allowed)}"


def test_a2_two_interleaved_conversations_keep_their_own_walk_chat_line_and_hop(vt, fleet, monkeypatch):
    """A's home model is busy and A walks the list; B's home answers. Every request yields the loop many times,
    so the two replies are interleaved at every await point. Each keeps its own `left` walk (the hop it hands
    to the model that answers instead), its own chat line and its own words."""
    other = "google:gemini/gemini-3.9-flash"
    seen = []                                   # (tag, hop origin, hop visited) of every list-model request

    async def many_yields(n):
        for _ in range(n):
            await asyncio.sleep(0)

    class Tagged(ListModel):
        async def unified_call(self, **kwargs):
            hop = K._KAME_FALLBACK_HOP.get() or {}
            tag = (kwargs.get("messages") or ["?"])[0]
            seen.append((tag, hop.get("origin"), list(hop.get("visited") or [])))
            await many_yields(7)
            callback = kwargs.get("response_callback")
            if callback is not None:
                await callback(f"{tag} via {self.model_name}", f"{tag} via {self.model_name}")
            await many_yields(5)
            return (f"{tag} via {self.model_name}", "")

    sys.modules["models"].get_chat_model = lambda provider, name, model_config=None, **kw: Tagged(provider, name, fleet)

    async def home_a(owner, key, ctx):
        await many_yields(3)
        raise overloaded()

    async def home_b(owner, key, ctx):
        await many_yields(9)
        callback = ctx.get("response_callback")
        await callback("B home", "B home")
        return ("B answered at home", "")

    ctx_a, lines_a = make_ctx(home_a)
    ctx_a["messages"] = ["A"]
    K._get_identity_state(other, KEYS)
    ctx_b, lines_b = make_ctx(home_b)
    ctx_b.update(identity=other, messages=["B"])
    ctx_b["flight"] = {"job": "chat", "identity": other, "started": time.time(), "refusals": 0, "last": "",
                       "fallback": False}
    ctx_a["flight"] = {"job": "chat", "identity": ID, "started": time.time(), "refusals": 0, "last": "",
                       "fallback": False}
    results = run_all(vt, fleet, [K._kame_carousel(None, ctx_a), K._kame_carousel(None, ctx_b)])
    assert not any(isinstance(r, BaseException) for r in results), results
    result_a, result_b = results
    assert result_a[0].startswith("A via google/") and result_b[0] == "B answered at home"
    assert lines_a and all(w.startswith("A via") for w in lines_a), lines_a       # A's line has only A's words
    assert lines_b == ["B home"], lines_b                                          # B's line has only B's words
    assert seen and all(tag == "A" for tag, _o, _v in seen), seen
    assert all(origin == ID and set(visited) <= {ID, NEXT_ID, THIRD_ID} for _t, origin, visited in seen), seen
    assert not any(other in visited for _t, _o, visited in seen), seen            # never B's model
    assert ctx_b.get("flight", {}).get("fallback") is False and not ctx_b.get("answered_by")
    assert ctx_a.get("kame_call_id") != ctx_b.get("kame_call_id")
