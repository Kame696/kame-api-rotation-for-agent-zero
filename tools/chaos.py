"""Chaos run against the Agent Zero carousel: a fake provider that fails in every shape.

Offline, stubs only, virtual clock. A defect is a crash from inside KAME, a runaway loop,
or a broken invariant, reported with the seed that reproduces it.

    python tools/chaos.py --seeds 100 --calls 12 [--fallback] [--hedge]
"""
from __future__ import annotations

import importlib
import argparse
import asyncio
import collections
import faulthandler
import logging
import os
import random
import sys
import tempfile
import time as real_time
import traceback
import types
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="kame-a0-chaos-")
os.chdir(HOME)
os.environ["KAME_RECORDER_DISABLED"] = "1"
logging.disable(logging.CRITICAL)

CURRENT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(CURRENT / "tests"), str(CURRENT)]
importlib.import_module("test_v1_8_1_1_reliability")  # stands in for Agent Zero's modules
K = importlib.import_module("kame_engine")
H = importlib.import_module("kame_hedging")
if K.AIMessage is None:
    # The stand-in modules have no assistant message type, which turns stitching off and sends every cut answer
    # down the start-again path (Agent Zero's page then shows ``full``, the new answer, so nothing repeats there).
    # A real Agent Zero has one, so the chaos runs the path the owner gets: the cut answer is continued.
    class _AIMessage:
        def __init__(self, content=""):
            self.content = content
    K.AIMessage = _AIMessage

ID = "google:gemini/gemini-test"
FATAL_TYPES = (TypeError, KeyError, AttributeError, IndexError, UnboundLocalError, NameError, RecursionError,
               AssertionError, ZeroDivisionError, OverflowError)


class Runaway(BaseException):
    pass


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0
        self.sleeps = 0

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def perf_counter(self):
        return self.now

    def time_ns(self):
        return int(self.now * 1e9)


class Refusal(Exception):
    def __init__(self, status, message, details=(), body="default", headers=None):
        super().__init__(message)
        self.status_code = status
        self.body = ({"error": {"code": status, "message": message,
                                "status": "RESOURCE_EXHAUSTED" if status == 429 else "UNAVAILABLE",
                                "details": list(details)}} if body == "default" else body)
        self.response = types.SimpleNamespace(text=str(self.body or message), status_code=status,
                                              headers=headers or {})


def quota(quota_id, retry=None):
    d = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]}]
    if retry is not None:
        d.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    return d


FAULTS = {
    "ok": lambda r: ("text", ["Hello ", "world"]),
    "rpm": lambda r: Refusal(429, "Quota exceeded for metric", quota("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", f"{r.randint(1, 59)}s")),
    "rpd": lambda r: Refusal(429, "Quota exceeded for metric", quota("GenerateRequestsPerDayPerProjectPerModel-FreeTier", f"{r.randint(1, 59)}s")),
    "bare429": lambda r: Refusal(429, "Resource has been exhausted (e.g. check quota)."),
    "503": lambda r: Refusal(503, "The model is overloaded. Please try again later."),
    "500": lambda r: Refusal(500, "Internal error encountered."),
    "401": lambda r: Refusal(401, "API key not valid. Please pass a valid API key."),
    "403": lambda r: Refusal(403, "Permission denied"),
    "400": lambda r: Refusal(400, "Invalid request: unsupported parameter"),
    "ctx400": lambda r: Refusal(400, "prompt is too long: 214290 tokens > 200000 maximum"),
    "timeout": lambda r: TimeoutError("The read operation timed out"),
    "conn": lambda r: ConnectionError("Connection reset by peer"),
    "cut": lambda r: ("cut", ["Hello "], ConnectionError("peer closed connection without sending complete message body")),
    "empty": lambda r: ("text", []),
    "reason_only": lambda r: ("reason", "thinking"),
    "none": lambda r: ("none",),
    "weird_status": lambda r: Refusal("429", "Too many requests", body=None),
    "body_str": lambda r: Refusal(429, "x", body="not json at all"),
    "body_list": lambda r: Refusal(503, "x", body=[{"error": {"code": 503}}]),
    "bad_delay": lambda r: Refusal(429, "Quota", quota("X", r.choice(["abc", "-5s", "1e9s", "", "0s", "999999999999s", "NaNs", "12"]))),
    "bad_details": lambda r: Refusal(429, "Quota", [None, 5, "x", {"@type": None}, {"violations": "no"}, {"violations": [None]}]),
    "header_retry": lambda r: Refusal(429, "Slow down", body=None, headers={"retry-after": r.choice(["7", "Wed, 21 Oct 2015 07:28:00 GMT", "soon", "-1", "1e400"])}),
    "bare_exc": lambda r: Exception("something odd"),
    "value_err": lambda r: ValueError("Expecting value: line 1 column 1 (char 0)"),
    "unicode": lambda r: ("text", ["olá \ud83d", "!"]),
    "huge_text": lambda r: ("text", ["x" * 200000]),
}


def scenario(rng):
    kind = rng.choice(["healthy", "bad", "storm", "single", "dead"])
    names = list(FAULTS)
    if kind == "healthy":
        w = {n: 1 for n in names}
        w["ok"] = 60
    elif kind == "bad":
        w = {n: rng.randint(1, 8) for n in names}
        w["ok"] = 15
    elif kind == "storm":
        w = {n: rng.randint(0, 5) for n in names}
        w["ok"] = 3
        w[rng.choice(["rpm", "rpd", "503", "bare429"])] = 40
    elif kind == "single":
        w = {"ok": 5, rng.choice(names): 20}
    else:
        w = {n: rng.randint(1, 5) for n in names if n != "ok"}
        w["ok"] = 1
    return kind, w


class Script:
    def __init__(self, rng, weights, limit=400):
        self.rng, self.limit = rng, limit
        self.names = list(weights)
        self.w = [weights[n] for n in self.names]
        self.asked = []
        self.history = []

    async def attempt(self, owner, key, ctx):
        self.asked.append(key)
        if len(self.asked) > self.limit:
            raise Runaway(f"{len(self.asked)} requests in one call")
        name = self.rng.choices(self.names, self.w)[0]
        self.history.append(name)
        step = FAULTS[name](self.rng)
        if isinstance(step, BaseException):
            raise step
        rc = ctx.get("response_callback")
        full = ""
        if step[0] == "text":
            for part in step[1]:
                full += part
                if rc is not None:
                    await rc(part, full)
            return (full, "")
        if step[0] == "cut":
            for part in step[1]:
                full += part
                if rc is not None:
                    await rc(part, full)
            raise step[2]
        if step[0] == "reason":
            cb = ctx.get("reasoning_callback")
            if cb is not None:
                await cb(step[1], step[1])
            return ("", step[1])
        return None


def make_ctx(script, keys, chat):
    K._get_identity_state(ID, keys)
    ctx = {"identity": ID, "all_keys": list(keys), "call_type": "Chat" if chat else "Util", "model_short": "m",
           "attempt": script.attempt, "progress": {"any": False, "reasoning": False},
           "messages": ["hello"], "kwargs": {}, "entry_name": "unified_call", "rate_limiter_callback": None}
    seen = []

    async def on_text(delta, full):
        seen.append(delta)
        ctx["_chaos_page"] = full       # what Agent Zero's page shows: it draws ``full``, not the chunks
        return None

    if chat:
        ctx["response_callback"], ctx["reasoning_callback"], ctx["tokens_callback"] = \
            K._kame_wrap_callbacks(ctx, on_text, None, None)
    else:
        ctx["response_callback"] = ctx["reasoning_callback"] = ctx["tokens_callback"] = None
    return ctx, seen


def reset():
    for name in ("_KAME_KEY_HEALTH", "_KAME_ACCOUNT_HOLDS", "_KAME_STATED_RL", "_KAME_NO_ANSWER_SINCE",
                 "_KAME_FALLBACK_SKIP", "_KAME_FALLBACK_MODEL_CACHE", "_KAME_LAST_MODEL_SWITCH", "_KAME_REPLIES",
                 "_KAME_FAILED_ROWS"):
        getattr(K, name).clear()
    if K._KJ is not None:
        K._KJ._reset_for_tests()
        K._KJ.SHARE_HEALTH_ON = False
        K._KJ.EVENTS.clear()
    H.HEDGE_TRACKER.clear()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--calls", type=int, default=12)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--hedge", action="store_true")
    a = ap.parse_args()

    async def no_intervention(*_a, **_k):
        return None
    K._kame_honor_intervention = no_intervention
    K.set_log_level("silent")
    K.set_wait_notice(False)
    K.set_hedge_mode("adaptive" if a.hedge else "off")
    K.set_max_total_wait(0)

    outcomes = collections.Counter()
    defects = collections.Counter()
    examples = {}
    waits = []
    real_sleep = asyncio.sleep
    for seed in range(a.start, a.start + a.seeds):
        rng = random.Random(seed)
        clock = Clock()
        fake_time = types.SimpleNamespace(time=clock.time, monotonic=clock.monotonic, perf_counter=clock.perf_counter,
                                          time_ns=clock.time_ns, sleep=lambda s: None, strftime=real_time.strftime,
                                          localtime=real_time.localtime, gmtime=real_time.gmtime)

        async def fake_sleep(s=0, result=None):
            clock.now += max(0.0, float(s or 0))
            clock.sleeps += 1
            if clock.sleeps > 200000 or clock.now > 1_800_000_000.0 + 40 * 86400:
                raise Runaway(f"{clock.sleeps} sleeps, {clock.now - 1_800_000_000.0:.0f} s virtual")
            await real_sleep(0)
            return result

        K.time = fake_time
        H.time = fake_time
        asyncio.sleep = fake_sleep
        reset()
        kind, weights = scenario(rng)
        keys = [f"AIzaSyKEY{seed}x{i}" + "0" * 29 for i in range(rng.choice([1, 2, 3, 5, 14]))]
        for n in range(a.calls):
            script = Script(rng, weights)
            ctx, seen = make_ctx(script, keys, rng.random() < 0.7)
            started = clock.now
            clock.sleeps = 0
            faulthandler.dump_traceback_later(120, exit=True)
            try:
                result = asyncio.run(K._kame_carousel(None, ctx))
                outcomes["answered" if result else "empty result"] += 1
            except Runaway as e:
                defects["RUNAWAY"] += 1
                examples.setdefault("RUNAWAY", (seed, n, kind, len(keys), collections.Counter(script.history).most_common(4), str(e)))
            except FATAL_TYPES as e:
                tb = traceback.extract_tb(e.__traceback__)
                where = next((f"{Path(f.filename).name}:{f.lineno}" for f in reversed(tb) if "kame" in Path(f.filename).name), "?")
                sig = f"{type(e).__name__} at {where}"
                defects[sig] += 1
                examples.setdefault(sig, (seed, n, kind, len(keys), script.history[-3:], str(e)[:120]))
            except BaseException as e:
                outcomes[type(e).__name__] += 1
            faulthandler.cancel_dump_traceback_later()
            # 1.8.4.0 round 2: what the user read must never hold a word twice. Every text shape answers
            # "Hello world" or something with neither word, so a second "Hello" or "world" is a seam the
            # stitcher let through (Hermes tools/race_chaos.py found one, R2; this run found R4).
            # The page (``full``) must always be clean. The chunks (console, extensions) may repeat only when
            # KAME started the answer again on purpose: two continuations in a row that added nothing.
            def doubled(text):
                return text.count("Hello") > 1 or text.count("world") > 1
            page = str(ctx.get("_chaos_page") or "")
            chunks = "".join(str(d) for d in seen if d)
            for label, text, allowed in (("DUPLICATE page", page, False),
                                         ("DUPLICATE chunks", chunks, int(ctx.get("resume_noprogress") or 0) >= 2)):
                if doubled(text) and not allowed:
                    defects[label] += 1
                    examples.setdefault(label, (seed, n, kind, len(keys), script.history[-12:], text[:80]))
            if doubled(chunks) and int(ctx.get("resume_noprogress") or 0) >= 2:
                outcomes["started again on purpose"] += 1
            waits.append(clock.now - started)
    asyncio.sleep = real_sleep
    print("outcomes", outcomes.most_common())
    waits.sort()
    print("virtual wait per call: p50", round(waits[len(waits) // 2], 1), "p99", round(waits[int(len(waits) * .99)], 1),
          "max", round(waits[-1], 1))
    print("defects", sum(defects.values()), "distinct", len(defects))
    for sig, count in defects.most_common(25):
        print(f"  {count:5} {sig}  e.g. {examples.get(sig)}")


if __name__ == "__main__":
    main()
