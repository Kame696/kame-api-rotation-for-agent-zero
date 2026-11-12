"""Chaos across the model ladder on Agent Zero: four models of one provider, KAME's real entry point.

``tools/chaos.py`` drives one model's carousel and cannot race. This one binds KAME to a stand-in
``models.LiteLLMChatWrapper`` exactly as ``apply_kame_patch`` does, so every model of the list runs
its own KAME call (the cross-model race of 1.8.2.8, the widened race and the start probe of 1.8.2.9,
the E8 tail, the stand-in, a cut answer continued on another model). The stand-in model answers each
request in a random shape and, when KAME continues a cut answer, either continues it or starts it
again. After every reply:

  I1  the reply ends (an answer or an exception) within a bound; no hang
  I2  what the page shows (Agent Zero draws ``full``) and the text returned are the canonical answer
      or a beginning of it; never a word twice, never a word out of order. A chunk list that repeats
      a word is a defect unless KAME started the answer again on purpose (``resume_noprogress >= 2``,
      the excuse ``tools/chaos.py`` uses; F5). A returned beginning only ("short" in the outcomes)
      is allowed only when the last request got the empty shape (a model that says it is done after
      a cut is believed, by design); any other beginning is a defect (F6).
  I3  a reserve key is asked only when no other key of that model is ready (E4: the reply's own
      request and the same-model backups)
  I3b a cross-model leg or the start probe never asks a reserve key, whatever else is ready (R1,
      read from the hop's ``"race"`` flag). A widened leg may (F1), so it is not checked here.
  I4  an exception that reaches the caller is a provider refusal or one of KAME's own, never an
      internal error (AttributeError, KeyError, TypeError, ...)
  I5  no probe judge survives its deadline (1.8.4.2, S1e): a start probe that lost the race is heard to its
      first word, its end, its error or 30 s, so thirty-five virtual seconds after the reply none is left.
      The runs also start with random remembered trouble (S1) on some models: the start decision it adds
      must never cost a reply (I1) or change what is shown (I2).

Offline, stand-in modules, virtual clock: no Agent Zero installation, no network, nothing written
outside a temp folder. Mirror of Hermes ``tools/race_chaos.py``.

    python tools/race_chaos.py --seeds 200 [--show]
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import importlib
import logging
import os
import random
import sys
import tempfile
import time
import traceback
import types
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="kame-a0-race-chaos-")
os.chdir(HOME)
os.environ["KAME_RECORDER_DISABLED"] = "1"
os.environ.pop("KAME_NO_REMEMBERED_TROUBLE", None)     # 1.8.4.2 (S1): remembered trouble is part of the run
logging.disable(logging.CRITICAL)

CURRENT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(CURRENT / "tests"), str(CURRENT)]
importlib.import_module("test_v1_8_1_1_reliability")  # stands in for Agent Zero's modules
K = importlib.import_module("kame_engine")
H = importlib.import_module("kame_hedging")
P = importlib.import_module("kame_pacing")


class _Message:
    def __init__(self, content=""):
        self.content = content


class AIMessage(_Message):
    pass


class HumanMessage(_Message):
    pass


if K.AIMessage is None:
    K.AIMessage = AIMessage           # a real Agent Zero has one: cut answers are continued, not restarted
K.HumanMessage = HumanMessage         # the stand-in modules' message takes no content; langchain's does

PROVIDER = "google"
MODELS = ["gemini-race", "gemini-race-two", "gemini-race-three", "gemini-race-four"]
KEYS = [f"AIzaSyCHAOSA0{i}" + "0" * 27 for i in range(5)]
ANSWER = ["the ", "quick ", "brown ", "fox ", "jumps"]
CANON = "".join(ANSWER)

# 1.8.4.1 (K6): answers that repeat, five pieces each like the prose one so a seed draws the same
# shapes (cut points, delays) whatever the text. Seeds divisible by 3 keep the prose answer, so those
# seed numbers in the old records mean the same run; ``--no-patterns`` keeps it for every seed.
PROSE = list(ANSWER)
PATTERN_ANSWERS = [
    ["ha ha ha ha ", "ha ha ha ha ", "ha ha ha ha ", "ha ha ha ha ", "ha ha ha ha"],      # a word repeated
    ["| a | 1 |\n", "| a | 1 |\n", "| a | 1 |\n", "| a | 1 |\n", "| b | 2 |"],        # equal table rows
    ["The rule: ", "----------", "----------", "----------", " and done"],              # a rule of dashes in prose
    ["x = f(1)\n", "x = f(1)\n", "x = f(1)\n", "x = f(1)\n", "return x"],             # equal code lines
    # more of both kinds: text before the pattern (a sentence, a header row, a def line) and a bare rule of dashes.
    # A repeating answer (REPEATS below) is only ever started again by the provider here, never carried
    # on. Carried on after a cut, repeating text cannot be told from a repeat of what was shown, and the
    # stitcher reads it as a repeat: an open point, measured and written down (PLAN_1.8.4.1 K2, withdrawn).
    ["He said: ", "ha ha ha ha ", "ha ha ha ha ", "ha ha ha ha ", "ha ha ha ha"],
    ["| h | n |\n", "| a | 1 |\n", "| a | 1 |\n", "| a | 1 |\n", "| b | 2 |"],
    ["----------", "----------", "----------", "----------", " and done"],               # a bare rule of dashes
    ["def f(x):\n", "x = f(1)\n", "x = f(1)\n", "x = f(1)\n", "return x"],
]
USE_PATTERNS = True
REPEATS = False     # the chosen answer is one of the repeating ones


def choose_answer(seed):
    """The answer this seed asks for: prose, or (seed not divisible by 3) one that repeats."""
    global ANSWER, CANON, REPEATS
    ANSWER = PROSE if (not USE_PATTERNS or seed % 3 == 0) else PATTERN_ANSWERS[seed % len(PATTERN_ANSWERS)]
    CANON = "".join(ANSWER)
    REPEATS = ANSWER is not PROSE


INTERNAL = (AttributeError, KeyError, TypeError, IndexError, NameError, UnboundLocalError,
            AssertionError, ZeroDivisionError, RecursionError)


# --- a virtual clock (the loop of tests/kame_vclock.py, without pytest) -------------------------------

class VirtualLoop(asyncio.SelectorEventLoop):
    def __init__(self):
        super().__init__()
        self._virtual = 0.0

    def time(self):
        return self._virtual

    def _run_once(self):
        if not self._ready and self._scheduled:
            due = self._scheduled[0]._when
            if due > self._virtual:
                self._virtual = due
        super()._run_once()


WALL0, MONO0 = 1_800_000_000.0, 5_000.0
LOOP = {"loop": None}
time.time = lambda: WALL0 + (LOOP["loop"]._virtual if LOOP["loop"] else 0.0)
time.monotonic = lambda: MONO0 + (LOOP["loop"]._virtual if LOOP["loop"] else 0.0)
time.perf_counter = lambda: MONO0 + (LOOP["loop"]._virtual if LOOP["loop"] else 0.0)


# --- the provider -------------------------------------------------------------------------------------

class Refusal(Exception):
    def __init__(self, status, message, details=()):
        super().__init__(message)
        self.status_code = status
        self.body = {"error": {"code": status, "message": message,
                               "status": {429: "RESOURCE_EXHAUSTED", 404: "NOT_FOUND"}.get(status, "UNAVAILABLE"),
                               "details": list(details)}}
        self.response = types.SimpleNamespace(text=str(self.body), status_code=status, headers={})


def per_minute():
    return Refusal(429, "Quota exceeded for metric: generate_content_free_tier_requests", [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "20s"}])


def daily():
    return Refusal(429, "Quota exceeded for metric: generate_content_free_tier_requests", [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "20"}]}])


def busy():
    return Refusal(503, "The model is overloaded. Please try again later.")


def gone(model):
    return Refusal(404, f"models/{model} is not found for API version v1beta, or is not supported for generateContent.")


class World:
    def __init__(self, rng, reserve):
        self.rng = rng
        self.reserve = set(reserve)
        self.asked = []
        self.violations = []
        self.ctxs = []          # the context of every KAME call of this reply (F5 reads resume_noprogress)


WORLD = {"w": None}


def _ordinary_ready(identity, now):
    pool = (K._KAME_KEY_HEALTH.get(identity) or {}).get("keys") or {}
    ready = 0
    for k in KEYS:
        if k in WORLD["w"].reserve:
            continue
        kd = pool.get(k)
        if kd is None:
            ready += 1
            continue
        if kd.get("retired_at"):
            continue
        # 1.8.4.2: ready means resting for LESS than now, as ``_get_best_key`` reads it (``sick_until < now``): a key
        # refused at this very instant with a rest of 0 s is not ready yet (seed 314 of the 1.8.4.2 run).
        if float(kd.get("sick_until", 0) or 0) >= now or float(kd.get("model_until", 0) or 0) >= now:
            continue
        ready += 1
    return ready


def _continued_from(messages):
    """The text KAME asked this request to continue, or ``None``."""
    for m in reversed(list(messages or [])[-2:]):
        if isinstance(m, K.AIMessage):
            return str(m.content or "")
    return None


class LiteLLMChatWrapper:
    def __init__(self, provider, name):
        self.a0_model_conf = types.SimpleNamespace(provider=provider)
        self.model_name = name

    async def unified_call(self, system_message="", user_message="", messages=None, response_callback=None,
                           reasoning_callback=None, tokens_callback=None, rate_limiter_callback=None,
                           explicit_caching=False, **kwargs):
        w = WORLD["w"]
        key = kwargs.get("api_key") or ""
        identity = f"{PROVIDER}:{self.model_name.lower()}"
        if key in w.reserve and len(w.reserve) < len(KEYS):
            ready = _ordinary_ready(identity, time.time())
            if ready:
                w.violations.append(f"I3 reserve key asked on {self.model_name} while {ready} other key(s) ready")
            hop = K._KAME_FALLBACK_HOP.get()
            if hop and hop.get("race"):
                # I3b (R1): a cross leg and the start probe are backup requests; they never take a reserve key
                w.violations.append(f"I3b race leg asked a reserve key on {self.model_name}")
        roll = w.rng.random()
        prefix = _continued_from(messages)
        words = list(ANSWER)
        if prefix and CANON.startswith(prefix.rstrip()) and w.rng.random() < 0.5 and not REPEATS:
            done = len(prefix.rstrip())          # continue where the cut answer stopped
            rest = CANON[done:]
            words = [rest] if rest else []
        if roll < 0.38:
            name, delay, cut = "fast", w.rng.uniform(0.1, 2.0), None
        elif roll < 0.50:
            name, delay, cut = "silent", 90.0, None
        elif roll < 0.62:
            w.asked.append((self.model_name, key[-6:], "503"))
            await asyncio.sleep(0.3)
            raise busy()
        elif roll < 0.70:
            w.asked.append((self.model_name, key[-6:], "per_minute"))
            await asyncio.sleep(0.2)
            raise per_minute()
        elif roll < 0.76:
            w.asked.append((self.model_name, key[-6:], "daily"))
            await asyncio.sleep(0.2)
            raise daily()
        elif roll < 0.80:
            w.asked.append((self.model_name, key[-6:], "gone"))
            await asyncio.sleep(0.2)
            raise gone(self.model_name)
        elif roll < 0.92:
            name, delay, cut = "cut", w.rng.uniform(0.1, 1.0), w.rng.randint(0, max(0, len(words) - 1))
        else:
            name, delay, cut = "empty", w.rng.uniform(0.1, 1.0), 0
            words = []
        w.asked.append((self.model_name, key[-6:], name + ("+cont" if words != list(ANSWER) and words else "")))
        await asyncio.sleep(delay)
        full = ""
        for i, word in enumerate(words):
            if cut is not None and i >= cut and name == "cut":
                raise ConnectionError("peer closed connection without sending complete message body")
            full += word
            if response_callback is not None:
                await response_callback(word, full)
            await asyncio.sleep(0.05)
        if name == "cut":
            raise ConnectionError("peer closed connection without sending complete message body")
        return (full, "")


class ModelType:
    CHAT = "Chat"


class ModelConfig:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def build_kwargs(self):
        return {}


models = types.ModuleType("models")
models.LiteLLMChatWrapper = LiteLLMChatWrapper
models.ModelType, models.ModelConfig = ModelType, ModelConfig
models.get_chat_model = lambda provider, name, model_config=None, **kw: LiteLLMChatWrapper(provider, name)
sys.modules["models"] = models
K._kame_provider_keys = lambda provider: list(KEYS) if str(provider).lower() == PROVIDER else []
assert K._kame_bind_entry_points(LiteLLMChatWrapper) in (1, 2), "KAME did not bind the stand-in model"

_real_carousel = K._kame_carousel


async def _recording_carousel(self, ctx):
    """Keep every call's context: F5 needs to know whether KAME started an answer again on purpose."""
    if WORLD["w"] is not None:
        WORLD["w"].ctxs.append(ctx)
    return await _real_carousel(self, ctx)


K._kame_carousel = _recording_carousel


# --- one reply -------------------------------------------------------------------------------------------

def reset():
    for name in ("_KAME_KEY_HEALTH", "_KAME_ACCOUNT_HOLDS", "_KAME_STATED_RL", "_KAME_NO_ANSWER_SINCE",
                 "_KAME_FALLBACK_SKIP", "_KAME_FALLBACK_MODEL_CACHE", "_KAME_LAST_MODEL_SWITCH", "_KAME_REPLIES",
                 "_KAME_FAILED_ROWS", "_KAME_BUSY_UNTIL", "_KAME_BUSY_WINDOW", "_KAME_PROBED_AT", "_KAME_TURNS"):
        obj = getattr(K, name, None)
        if hasattr(obj, "clear"):
            obj.clear()
    if K._KJ is not None:
        K._KJ._reset_for_tests()
        K._KJ.SHARE_HEALTH_ON = False
        K._KJ.EVENTS.clear()
    H.HEDGE_TRACKER.clear()
    P.PACER.clear()
    K._KAME_PROBE_JUDGES.clear()
    if K._KMH is not None:
        # 1.8.4.2 (S1): no remembered trouble carries over from one seed to the next
        try:
            os.unlink(os.path.join(HOME, K._KMH.FILE_NAME))
        except OSError:
            pass
        K._KMH.HEALTH.configure(directory=HOME, calls_path=os.path.join(HOME, "no-calls.jsonl"))


def setup_state(rng, reserve=()):
    """Random busy windows, tails and resting keys, as the owner's day leaves them."""
    now = time.time()
    if reserve and rng.random() < 0.4:
        # a model whose only ready key is a reserve key: the case R1 (I3b) is about
        ident = f"{PROVIDER}:{rng.choice(MODELS[1:])}"
        K._get_identity_state(ident, KEYS)
        for k in KEYS:
            if k not in reserve:
                K._KAME_KEY_HEALTH[ident]["keys"][k]["sick_until"] = now + 600.0
    for m in MODELS:
        ident = f"{PROVIDER}:{m}"
        K._get_identity_state(ident, KEYS)
        roll = rng.random()
        if roll < 0.2:                                    # a busy window still open
            K._KAME_BUSY_UNTIL[ident] = now + rng.choice([20.0, 60.0])
            K._KAME_BUSY_WINDOW[ident] = 30.0
            K._KAME_PROBED_AT[ident] = now - rng.choice([5.0, 60.0])
        elif roll < 0.35:                                 # the tail of a window (E8)
            K._KAME_BUSY_UNTIL[ident] = now - rng.uniform(1.0, 20.0)
            K._KAME_BUSY_WINDOW[ident] = 30.0
            K._KAME_PROBED_AT[ident] = now - 60.0
        if K._KMH is not None and rng.random() < 0.3:
            # 1.8.4.2 (S1): this model keeps failing - two to four failures lately, the latest one a failure
            for i in range(rng.choice([2, 3, 4])):
                K._KMH.HEALTH.note(ident, False, now=now - 120.0 + i)
        pool = K._KAME_KEY_HEALTH[ident]["keys"]
        for k in KEYS:
            r = rng.random()
            if r < 0.15:
                pool[k]["sick_until"] = now + rng.choice([30.0, 600.0, 7200.0])
            elif r < 0.2:
                pool[k]["model_until"] = now + rng.choice([30.0, 600.0])


async def call_chat_model(owner, on_text):
    """Named as Agent Zero's own turn, so KAME reads this call as the conversation itself."""
    return await owner.unified_call(messages=[K.HumanMessage(content="hi")], response_callback=on_text)


def run_one(seed):
    choose_answer(seed)
    rng = random.Random(seed)
    loop = VirtualLoop()
    LOOP["loop"] = loop
    asyncio.set_event_loop(loop)
    reset()
    reserve = [KEYS[0]] if rng.random() < 0.5 else []
    K.set_key_reserve(", ".join(K._key_short_id(k) for k in reserve))
    K.set_hedge_mode(rng.choice(["adaptive", "adaptive", "max", "off"]))
    K.set_fallback_models("\n".join(f"{PROVIDER}/{m}" for m in MODELS[1:]))
    WORLD["w"] = world = World(rng, reserve)
    setup_state(rng, reserve)
    page = {"full": "", "chunks": ""}

    async def on_text(delta, full):
        page["full"] = full
        page["chunks"] += delta or ""
        return None

    owner = LiteLLMChatWrapper(PROVIDER, MODELS[0])
    problems = list()
    started = loop._virtual
    real0 = time.process_time()
    outcome = "answer"
    try:
        result = loop.run_until_complete(asyncio.wait_for(call_chat_model(owner, on_text), timeout=3600))
        text = result[0] if isinstance(result, tuple) else str(getattr(result, "response", result) or "")
        if not CANON.startswith(text):
            problems.append(f"I2 returned {text!r}")
        elif text != CANON:
            outcome = "short" if text else "empty"
            if text and (not world.asked or world.asked[-1][2] != "empty"):
                # F6: a beginning only is believed after an empty answer (by design); after anything else it is a defect
                problems.append(f"I2 returned only a beginning {text!r} and the last request was not empty: "
                                f"{world.asked[-1] if world.asked else None}")
    except asyncio.TimeoutError:
        problems.append("I1 no end after 3600 virtual seconds")
        outcome = "hang"
    except BaseException as exc:  # noqa: BLE001 - the chaos wants every shape
        outcome = type(exc).__name__
        if isinstance(exc, INTERNAL):
            problems.append(f"I4 internal {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
    if not CANON.startswith(page["full"]):
        problems.append(f"I2 page {page['full']!r}")
    problems.extend(world.violations)
    took = loop._virtual - started
    try:
        loop.run_until_complete(asyncio.sleep(35.0))      # I5: the judges end by their own deadline
    except BaseException:
        pass
    if K._KAME_PROBE_JUDGES:
        problems.append(f"I5 {len(K._KAME_PROBE_JUDGES)} probe judge(s) survived their deadline")
    try:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    except Exception:
        pass
    loop.close()
    LOOP["loop"] = None
    repeated = page["chunks"] != page["full"] and not CANON.startswith(page["chunks"])
    if repeated and not any(int(c.get("resume_noprogress") or 0) >= 2 for c in world.ctxs):
        # F5: a word shown twice in the chunks is a defect unless KAME started the answer again on purpose
        problems.append(f"I2 chunks repeated a word and KAME had not started the answer again: {page['chunks']!r}")
    return problems, took, time.process_time() - real0, world.asked, outcome, repeated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=200)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--show", action="store_true", help="print every seed that did not end in a full answer")
    ap.add_argument("--no-patterns", action="store_true",
                    help="every seed asks for the prose answer (the runs of the records before 1.8.4.1)")
    args = ap.parse_args()
    global USE_PATTERNS
    USE_PATTERNS = not args.no_patterns
    K.set_log_level("silent")
    K.set_wait_notice(False)
    K.set_max_total_wait(300)        # a chat never gives up; here it must, to be checked

    async def no_intervention(*_a, **_k):
        return None
    K._kame_honor_intervention = no_intervention
    outcomes = collections.Counter()
    times = []
    bad = []
    chunk_repeats = []
    reach = collections.Counter()      # how many models of the list one reply asked: the race paths ran
    continued = 0
    for seed in range(args.start, args.start + args.seeds):
        problems, took, cpu, asked, outcome, repeated = run_one(seed)
        outcomes[outcome] += 1
        reach[len({m for m, _k, _s in asked})] += 1
        continued += sum(1 for _m, _k, s in asked if s.endswith("+cont"))
        times.append(took)
        if repeated:
            chunk_repeats.append(seed)
        if args.show and outcome != "answer" and not problems:
            print(f"seed {seed}: {outcome}, {took:.1f}s virtual, asked {asked[-6:]}", flush=True)
        if problems:
            bad.append((seed, problems, asked))
            print(f"seed {seed}: " + " | ".join(p.splitlines()[0] for p in problems), flush=True)
    times.sort()
    print(f"seeds {args.seeds}, defects {len(bad)}, outcomes {dict(outcomes)}, "
          f"virtual p50 {times[len(times) // 2]:.1f}s, max {times[-1]:.1f}s, "
          f"chunks repeated (page clean, answer started again on purpose) {len(chunk_repeats)}")
    print(f"models asked per reply {dict(sorted(reach.items()))}, requests that continued a cut answer {continued}")
    for seed, problems, asked in bad[:5]:
        print(f"--- seed {seed}")
        for p in problems:
            print(p)
        print("asked:", asked[:30])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
