"""1.8.4.3 (A1, A2, A3-A5, A7) - thoughts stay live; a reply that stops mid-thought is asked again (Agent Zero).

Built as 1.8.4.3, released as 1.9.0.0 (PLAN_1.8.4.3.md section 7.4).

The rule (PLAN section 6.1, 6.3): a reply whose model has started to think but shows no sentence yet keeps
showing its thoughts live (the provisional leg). If it then goes quiet past a bound learned per model, a rescue
leg is asked on another key of the same model, then (L5) on the next model of the list. A rescue leg's thoughts
never reach the screen; its first sentence takes over. Nothing of a leg that lost is ever stored by Agent Zero,
so the quiet leg's thoughts can be shown with no replay risk.

A virtual clock (kame_vclock.py). No Agent Zero installation, no network.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, Refusal, clean, make_ctx, streaming,
)
from kame_vclock import vt  # noqa: F401
import kame_engine as K
import kame_hedging as H
import kame_settings as S


# --- A1 - the bound follows the model ------------------------------------------------------------------------

@pytest.mark.parametrize("samples, expected", [
    ([], 20.0),
    ([50.0], 20.0),
    ([40.0, 40.0], 50.0),
    ([2.0] * 30, 8.0),
    ([4.0] * 25 + [12.0] * 5, 20.0),
    ([2.0] * 30 + [124.0], 8.0),
    ([2.0] * 20 + [40.0] * 10, 50.0),
    ([100.0] * 20, 90.0),
])
def test_a1_the_bound_table(samples, expected):
    tracker = H.HedgeTracker()
    for s in samples:
        tracker.record_quiet(ID, s)
    assert tracker.quiet_bound(ID) == pytest.approx(expected)


def test_a1_the_constants():
    assert (H._QUIET_COLD_S, H._QUIET_MIN_S, H._QUIET_MAX_S) == (20.0, 8.0, 20.0)
    assert (H._QUIET_CEILING_S, H._QUIET_FOLLOW, H._QUIET_MIN_SAMPLES) == (90.0, 1.25, 10)


def test_a1_a_model_is_learned_on_its_own_and_garbage_is_ignored():
    tracker = H.HedgeTracker()
    tracker.record_quiet("a:one", 40.0)
    tracker.record_quiet("a:one", 40.0)
    for bad in (-1.0, float("nan"), float("inf"), "x", None):
        tracker.record_quiet("a:one", bad)
    assert tracker.quiet_bound("a:one") == pytest.approx(50.0)
    assert tracker.quiet_bound("a:two") == 20.0


def test_a1_the_history_seeds_an_empty_list_only_and_clear_forgets():
    tracker = H.HedgeTracker()
    assert tracker.seed_quiet(ID, [40.0, 40.0, -3.0, 0.0]) == 2
    assert tracker.quiet_bound(ID) == pytest.approx(50.0)
    assert tracker.seed_quiet(ID, [1.0] * 5) == 0            # live samples always win
    tracker.clear()
    assert tracker.quiet_bound(ID) == 20.0


def test_a1_the_ledger_counts_rescue_as_an_extra_request_and_rescue_won_as_a_count():
    led = H.SpeedLedger()
    led.note("replies", 10, now=1.0)
    led.note("rescue", 2, now=2.0)
    led.note("rescue_won", 1, now=2.0)
    hour = led.view(now=3.0)["hour"]
    assert hour["rescue"] == 2 and hour["rescue_won"] == 1
    assert hour["extra_pct"] == 20.0                          # rescue is inside extra_pct, rescue_won is not
    assert led.view(now=3.0)["since_start"]["rescue"] == 2
    assert led.view(now=3.0)["since_start"]["rescue_won"] == 1
    assert led.view(now=3.0)["since_start"]["extra_pct"] == 20.0


def test_a1_the_ledger_after_an_hour_keeps_the_total_only():
    led = H.SpeedLedger()
    led.note("rescue", 3, now=0.0)
    led.note("rescue_won", 2, now=0.0)
    late = led.view(now=61 * 60)
    assert late["hour"]["rescue"] == 0 and late["hour"]["rescue_won"] == 0
    assert late["since_start"]["rescue"] == 3 and late["since_start"]["rescue_won"] == 2


def test_a1_the_engine_view_has_the_new_fields_when_nothing_was_counted():
    view = K.speed_report()
    for pack in (view["hour"], view["since_start"]):
        assert "rescue" in pack and "rescue_won" in pack


# --- A2 - the setting ----------------------------------------------------------------------------------------

def test_a2_the_setting_is_in_the_table_with_its_environment_name_and_range():
    kind, default, low, high, _choices, env, title, sentence = S.SETTINGS["thought_quiet_s"]
    assert (kind, low, high, env) == ("number", 0, 120, "KAME_THOUGHT_QUIET_S")
    assert default == ""                                      # unset = learned; a reset must not fix a number
    assert "live" in sentence and "another key" in sentence and "next model of your list" in sentence
    assert "when the answer starts" not in sentence
    assert S.parse("thought_quiet_s", "0")[:2] == (True, 0)
    assert S.parse("thought_quiet_s", "7.5")[:2] == (True, 7.5)
    assert S.parse("thought_quiet_s", "121")[0] is False
    assert "thought_quiet_s" in S.GROUPS[1][3]                # the Speed shelf


def test_a2_unset_is_learned_zero_is_off_a_number_is_that_number(clean):
    K.set_thought_quiet(None)
    assert K._kame_quiet_bound(ID, False) == 20.0             # nothing learned yet
    H.HEDGE_TRACKER.record_quiet(ID, 40.0)
    H.HEDGE_TRACKER.record_quiet(ID, 40.0)
    assert K._kame_quiet_bound(ID, False) == pytest.approx(50.0)
    K.set_thought_quiet("")
    assert K._kame_quiet_bound(ID, False) == pytest.approx(50.0)
    K.set_thought_quiet(7)
    assert K._kame_quiet_bound(ID, False) == 7.0
    K.set_thought_quiet("0")
    assert K._kame_quiet_bound(ID, False) is None
    K.set_thought_quiet("garbage")                            # not a number: nothing changes
    assert K._kame_quiet_bound(ID, False) is None
    K.set_thought_quiet(None)


def test_a2_the_environment_reaches_the_setting(clean, monkeypatch):
    monkeypatch.setenv("KAME_THOUGHT_QUIET_S", "9")
    K._kame_apply_env_overrides()
    try:
        assert K._kame_quiet_bound(ID, False) == 9.0
        assert K.current_settings()["thought_quiet_s"] == 9.0
    finally:
        K.set_thought_quiet(None)
    assert K.current_settings()["thought_quiet_s"] == ""


# --- the scene: scripted legs on a virtual clock ----------------------------------------------------------------

NEXT = "google/gemini-3.7-flash"
NEXT_ID = "google:gemini-3.7-flash"
THIRD = "google/gemini-3.6-flash"
THIRD_ID = "google:gemini-3.6-flash"


def overloaded():
    return Refusal("Error code: 503", 503,
                   {"error": {"code": 503, "message": "The model is overloaded. Please try again later.",
                              "status": "UNAVAILABLE"}})


async def play(script, rc, rsc):
    """("sleep", s) / ("think", text) / ("say", text) / ("fail", factory)."""
    for step in script:
        kind = step[0]
        if kind == "sleep":
            await asyncio.sleep(step[1])
        elif kind == "think":
            if rsc is not None:
                await rsc(step[1], step[1])
        elif kind == "say":
            if rc is not None:
                await rc(step[1], step[1])
        elif kind == "fail":
            raise step[1]()


class Scene:
    def __init__(self, vt):
        self.vt = vt
        self.scripts = {}          # list model name -> script
        self.starts = []           # (virtual seconds, "home0" | "home1" ... | list model name)
        self.cancelled = []
        self.hops = []             # the fallback-hop record each list model was asked inside
        self.objs = {}             # who -> the result object it returned
        self.seen = []             # (virtual seconds, "think" | "text", delta) - what reached Agent Zero
        self.home_scripts = [[("say", "x")]]
        self.homes = 0

    # --- legs ------------------------------------------------------------------------------------
    async def home(self, owner, key, ctx):
        n = self.homes
        self.homes += 1
        who = f"home{n}"
        self.starts.append((self.vt.elapsed(), who))
        script = self.home_scripts[min(n, len(self.home_scripts) - 1)]
        try:
            await play(script, ctx.get("response_callback"), ctx.get("reasoning_callback"))
        except asyncio.CancelledError:
            self.cancelled.append(who)
            raise
        obj = (f"answer from {who}", "")
        self.objs[who] = obj
        return obj

    def ctx(self, **extra):
        ctx, _ = make_ctx(self.home)

        async def on_text(delta, full):
            self.seen.append((self.vt.elapsed(), "text", delta))

        async def on_think(delta, full):
            self.seen.append((self.vt.elapsed(), "think", delta))

        ctx["response_callback"], ctx["reasoning_callback"], ctx["tokens_callback"] = \
            K._kame_wrap_callbacks(ctx, on_text, on_think, None)
        ctx.update(extra)
        return ctx

    def reply(self, home_scripts, limit=300, **extra):
        self.home_scripts = home_scripts
        ctx = self.ctx(**extra)
        result = self.vt.run(K._kame_carousel(None, ctx), limit=limit)
        return ctx, result

    # --- reading -------------------------------------------------------------------------------------
    def started(self, who):
        return [t for t, w in self.starts if w == who]

    def kinds(self):
        return [(k, d) for _t, k, d in self.seen]


class ListModel:
    def __init__(self, provider, name, scene):
        self.a0_model_conf = types.SimpleNamespace(provider=provider)
        self.model_name = f"{provider}/{name}"
        self.scene = scene

    async def unified_call(self, **kwargs):
        s = self.scene
        s.starts.append((s.vt.elapsed(), self.model_name))
        s.hops.append(dict(K._KAME_FALLBACK_HOP.get() or {}))
        script = s.scripts.get(self.model_name, [("say", "x")])
        try:
            await play(script, kwargs.get("response_callback"), kwargs.get("reasoning_callback"))
        except asyncio.CancelledError:
            s.cancelled.append(self.model_name)
            raise
        obj = (f"answer from {self.model_name}", "")
        s.objs[self.model_name] = obj
        return obj


@pytest.fixture
def scene(vt, monkeypatch, tmp_path):
    from test_v1_8_2_7_speed_fallback import _first_then_rest
    monkeypatch.setenv("KAME_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(K, "_get_best_key", _first_then_rest(KEYS[0]))
    s = Scene(vt)
    mod = types.ModuleType("models")

    class ModelType:
        CHAT = "Chat"

    class ModelConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def build_kwargs(self):
            return {}

    mod.ModelType, mod.ModelConfig = ModelType, ModelConfig
    mod.get_chat_model = lambda provider, name, model_config=None, **kw: ListModel(provider, name, s)
    monkeypatch.setitem(sys.modules, "models", mod)
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    K._KAME_BUSY_KIND.clear()
    K._KAME_PROBED_AT.clear()
    K._KAME_REPLIES.clear()
    K._KAME_TURNS.clear()
    K._KAME_SEEDED.clear()
    K._KAME_QUIET_SEEDED.clear()
    H.SPEED_LEDGER.clear()
    K.set_fallback_models(f"{NEXT}\n{THIRD}")
    K.set_thought_quiet(2.0)                       # the watch on, with a short limit
    yield s
    K.set_thought_quiet(None)
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    K._KAME_PROBED_AT.clear()
    H.SPEED_LEDGER.clear()


def ledger():
    return H.SPEED_LEDGER.view()["since_start"]


def events():
    return [e["reason"] for e in K._KJ.EVENTS.recent(80)]


# --- A7 1 - no stall ------------------------------------------------------------------------------------

def test_a7_1_a_thinking_reply_shows_its_thoughts_live_and_asks_nothing_more(scene):
    thoughts = []
    for n in range(4):
        thoughts += [("think", f"t{n}"), ("sleep", 0.3)]
    ctx, result = scene.reply([thoughts + [("say", "the answer")]])
    assert result is scene.objs["home0"]
    assert scene.kinds() == [("think", "t0"), ("think", "t1"), ("think", "t2"), ("think", "t3"),
                             ("text", "the answer")]
    times = [t for t, _k, _d in scene.seen]
    assert times == sorted(times) and times[3] - times[0] >= 0.8       # token by token, not all at the end
    assert len(scene.starts) == 1
    assert ledger()["rescue"] == 0 and ledger()["rescue_won"] == 0
    assert not ctx.get("rescued") and not ctx.get("rescues_asked")


# --- A7 2 - thinks once, then hangs --------------------------------------------------------------------

def test_a7_2_a_reply_that_thinks_once_and_hangs_is_rescued_on_another_key(scene):
    ctx, result = scene.reply([[("think", "first thought"), ("sleep", 500)],
                               [("think", "the rescue's own thoughts"), ("sleep", 0.3), ("say", "rescued")]])
    assert result is scene.objs["home1"]                              # the rescue's own result object
    assert scene.kinds() == [("think", "first thought"), ("text", "rescued")]
    assert "home0" in scene.cancelled                                 # the quiet one was cut
    opened = scene.started("home1")[0]
    assert 1.9 <= opened <= 3.0                                       # at the bound (2 s), not before
    assert ctx["rescued"] is True and ctx["rescues_asked"] == 1
    assert ledger()["rescue"] == 1 and ledger()["rescue_won"] == 1
    assert any("stopped mid-thought" in e and "another key races it" in e for e in events()), events()
    assert any("the rescue answered first" in e and "the quiet one was cut" in e for e in events()), events()
    assert not K._KAME_BUSY_UNTIL                                      # a stall is not a busy model


# --- A7 3 - the provisional resumes and wins -----------------------------------------------------------

def test_a7_3_when_the_quiet_leg_speaks_first_it_wins_and_the_rescue_is_cut_and_counted(scene):
    ctx, result = scene.reply([[("think", "a"), ("sleep", 2.6), ("say", "back again")],
                               [("think", "rescue thinking"), ("sleep", 500)]])
    assert result is scene.objs["home0"]
    assert scene.kinds() == [("think", "a"), ("text", "back again")]
    assert "home1" in scene.cancelled
    assert ledger()["rescue"] == 1 and ledger()["rescue_won"] == 0    # the request is counted, it did not win
    assert not ctx.get("rescued") and ctx["rescues_asked"] == 1


# --- A7 4 - rescue 2 -----------------------------------------------------------------------------------

def test_a7_4_a_rescue_that_fails_at_once_does_not_make_rescue_two_wait(scene):
    scene.scripts[NEXT] = [("say", "from the next model")]
    ctx, result = scene.reply([[("think", "a"), ("sleep", 500)], [("fail", overloaded)]])
    assert result is scene.objs[NEXT]
    t2 = scene.started(NEXT)[0]
    assert 1.9 <= t2 <= 3.5, t2                                       # right after the bound, not at 2 x bound after rescue 1
    assert ctx["rescued"] is True and ctx["rescues_asked"] == 2
    assert ledger()["rescue"] == 2 and ledger()["rescue_won"] == 1
    assert any("(your list) races it" in e for e in events()), events()
    assert NEXT_ID not in K._KAME_BUSY_UNTIL and ID not in K._KAME_BUSY_UNTIL


def test_a7_4_a_rescue_silent_past_its_late_point_makes_rescue_two_start_then(scene, monkeypatch):
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 1.0)                 # no first-word history: this is `late`
    scene.scripts[NEXT] = [("say", "from the next model")]
    ctx, result = scene.reply([[("think", "a"), ("sleep", 500)], [("sleep", 500)]])
    assert result is scene.objs[NEXT]
    opened1 = scene.started("home1")[0]
    t2 = scene.started(NEXT)[0]
    assert 0.8 <= t2 - opened1 <= 1.6, (opened1, t2)                  # late = 1 s after rescue 1 began; 2 x bound is 4 s
    assert ctx["rescues_asked"] == 2


def test_a7_4_a_thinking_rescue_keeps_the_next_model_away(scene, monkeypatch):
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 0.5)
    thoughts = []
    for n in range(12):
        thoughts += [("think", f"r{n}"), ("sleep", 0.5)]
    scene.scripts[NEXT] = [("say", "never asked")]
    ctx, result = scene.reply([[("think", "a"), ("sleep", 500)], thoughts + [("say", "rescue text")]])
    assert result is scene.objs["home1"]
    assert not scene.started(NEXT) and not scene.started(THIRD)
    assert ctx["rescues_asked"] == 1


def test_a7_4_never_a_third_rescue(scene, monkeypatch):
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 0.5)
    scene.scripts[NEXT] = [("sleep", 30), ("say", "late")]
    scene.scripts[THIRD] = [("say", "a third rescue would answer")]
    ctx, result = scene.reply([[("think", "a"), ("sleep", 500)], [("sleep", 500)]])
    assert result is scene.objs[NEXT]
    assert not scene.started(THIRD)
    assert len(scene.started("home0") + scene.started("home1") + scene.started("home2")) == 2
    assert ctx["rescues_asked"] == 2 and ledger()["rescue"] == 2


def test_a7_4_with_never_switch_or_no_list_the_next_model_is_never_a_rescue(scene):
    scene.scripts[NEXT] = [("say", "never asked")]
    K.set_model_switch("never")
    ctx, result = scene.reply([[("think", "a"), ("sleep", 9.0), ("say", "back at last")], [("sleep", 500)]])
    assert result is scene.objs["home0"] and not scene.started(NEXT)
    assert ctx["rescues_asked"] == 1                                   # the other key is still asked once
    K.set_model_switch("auto")
    K.set_fallback_models("")
    scene.starts.clear()
    scene.homes = 0
    ctx, result = scene.reply([[("think", "a"), ("sleep", 9.0), ("say", "back at last")], [("sleep", 500)]])
    assert result is scene.objs["home0"] and not scene.started(NEXT) and ctx["rescues_asked"] == 1


# --- A7 5 - which callback is the real claim ---------------------------------------------------------

def shim_race(callbacks, watch=True):
    async def cb(a, b):
        return None
    ctx = {name: cb for name in callbacks}
    race = K._KameRace(ctx)
    race.watch = watch
    progress = {"any": False, "reasoning": False, "first_sign_at": None, "first_text_at": None}
    response, reasoning, tokens = race.shims(0, progress)
    return race, response, reasoning, tokens


def test_a7_5_tokens_callback_after_a_thought_does_not_make_the_real_claim(vt):
    race, response, reasoning, tokens = shim_race(["response_callback", "reasoning_callback", "tokens_callback"])
    vt.run(reasoning("t", "t"))
    vt.run(tokens("t", 1))
    assert race.winner is None and race.provisional == 0 and not race.claimed.is_set()
    vt.run(response("text", "text"))
    assert race.winner == 0 and race.claimed.is_set()


def test_a7_5_a_call_with_only_a_tokens_callback_claims_on_it_as_in_1842(vt):
    race, _response, _reasoning, tokens = shim_race(["tokens_callback"])
    vt.run(tokens("t", 1))
    assert race.winner == 0 and race.claimed.is_set()


def test_a7_5_not_watching_a_thought_claims_at_once_as_in_1842(vt):
    race, _response, reasoning, _tokens = shim_race(["response_callback", "reasoning_callback"], watch=False)
    vt.run(reasoning("t", "t"))
    assert race.winner == 0 and race.provisional is None


def test_a7_5_a_second_leg_that_only_thinks_shows_nothing_and_claims_nothing(vt):
    seen = []

    async def reasoning_cb(a, b):
        seen.append(a)

    async def response_cb(a, b):
        seen.append("TEXT:" + a)

    race = K._KameRace({"response_callback": response_cb, "reasoning_callback": reasoning_cb})
    race.watch = True
    p0 = {"any": False, "reasoning": False, "first_sign_at": None, "first_text_at": None}
    p1 = dict(p0)
    think0 = race.shims(0, p0)[1]
    think1 = race.shims(1, p1)[1]
    vt.run(think0("a", "a"))
    assert race.provisional == 0
    assert vt.run(think1("b", "b")) is None
    assert seen == ["a"] and race.winner is None and race.provisional == 0
    assert race.first_work[1] is not None                              # but it is known to have worked


# --- A7 6 - the guard off ------------------------------------------------------------------------------

def test_a7_6_setting_zero_is_1842_the_quiet_leg_is_waited_for(scene):
    K.set_thought_quiet(0)
    ctx, result = scene.reply([[("think", "a"), ("sleep", 6.0), ("say", "slow answer")], [("say", "never")]])
    assert result is scene.objs["home0"]
    assert len(scene.starts) == 1 and ledger()["rescue"] == 0
    assert scene.kinds() == [("think", "a"), ("text", "slow answer")]


def test_a7_6_hedging_off_is_1842(scene):
    K.set_hedge_mode("off")
    ctx, result = scene.reply([[("think", "a"), ("sleep", 6.0), ("say", "slow answer")], [("say", "never")]])
    assert result is scene.objs["home0"] and len(scene.starts) == 1 and ledger()["rescue"] == 0


def test_a7_6_background_job_and_race_hop_have_no_watch(scene):
    base = scene.ctx()
    reasoning = K._kame_reasoning_call(base)
    assert K._kame_watch_on(base, True, True, None, reasoning) is True
    assert K._kame_watch_on(base, False, True, None, reasoning) is False          # hedging off / background / one key
    assert K._kame_watch_on(base, True, False, None, reasoning) is False          # not streamed
    assert K._kame_watch_on(base, True, True, [KEYS[1]], reasoning) is False       # keys asked together
    token = K._KAME_FALLBACK_HOP.set({"origin": ID, "to": NEXT_ID, "race": True})
    try:
        assert K._kame_watch_on(base, True, True, None, reasoning) is False       # the outer race owns the watch
    finally:
        K._KAME_FALLBACK_HOP.reset(token)
    token = K._KAME_FALLBACK_HOP.set({"origin": ID, "to": NEXT_ID, "race": False})
    try:
        assert K._kame_watch_on(base, True, True, None, reasoning) is True        # a plain fallback hop watches
    finally:
        K._KAME_FALLBACK_HOP.reset(token)
    K.set_thought_quiet(0)
    assert K._kame_watch_on(base, True, True, None, reasoning) is False


# --- A7 7 - a kept start probe -------------------------------------------------------------------------

def busy_and_due():
    import time
    K._KAME_BUSY_UNTIL[ID] = time.time() - 5.0
    K._KAME_BUSY_WINDOW[ID] = 30.0
    K._KAME_PROBED_AT[ID] = time.time() - 35.0


def test_a7_7_the_start_probe_is_kept_and_judged_across_a_provisional_claim(scene):
    K.set_fallback_models(NEXT)
    busy_and_due()
    scene.scripts[NEXT] = [("sleep", 0.1), ("think", "next thinks"), ("sleep", 0.3), ("say", "next answers")]
    ctx, result = scene.reply([[("sleep", 4.0), ("say", "the best model is back")]])
    assert result is scene.objs[NEXT] and ctx.get("probe_primary") is True
    assert scene.kinds() == [("think", "next thinks"), ("text", "next answers")]
    assert "home0" not in scene.cancelled                              # kept, not cut
    scene.vt.advance(6.0)
    assert ID not in K._KAME_BUSY_UNTIL                                # judged ok: the window ended


def test_a7_7_the_start_probe_is_kept_across_a_rescues_claim_too(scene):
    K.set_fallback_models(f"{NEXT}\n{THIRD}")
    busy_and_due()
    scene.scripts[NEXT] = [("sleep", 0.1), ("think", "next thinks"), ("sleep", 500)]
    scene.scripts[THIRD] = [("sleep", 0.2), ("say", "third answers")]
    ctx, result = scene.reply([[("sleep", 6.0), ("say", "the best model is back")]])
    assert result is scene.objs[THIRD] and ctx.get("probe_primary") is True
    assert NEXT in scene.cancelled and "home0" not in scene.cancelled
    scene.vt.advance(8.0)
    assert ID not in K._KAME_BUSY_UNTIL


# --- A7 8 - cancellation while watching ----------------------------------------------------------------

def test_a7_8_stopping_the_reply_while_it_is_watched_cancels_every_leg_and_leaks_nothing(scene):
    scene.scripts[NEXT] = [("sleep", 500)]
    scene.home_scripts = [[("think", "a"), ("sleep", 500)], [("think", "rescue"), ("sleep", 500)]]
    ctx = scene.ctx()
    loop = scene.vt.loop

    async def stop_midway():
        task = asyncio.ensure_future(K._kame_carousel(None, ctx))
        await asyncio.sleep(2.6)                                   # rescue 1 is out, the quiet leg still thinking
        assert len(scene.starts) == 2
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.1)
        return [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task() and not t.done()]

    left = scene.vt.run(stop_midway())
    assert left == [] or all(t in K._KAME_PROBE_JUDGES for t in left), left
    assert {"home0", "home1"} <= set(scene.cancelled)


# --- A7 10 - the 16:15 replay ---------------------------------------------------------------------------

def test_a7_10_the_1615_replay_a_503_a_silent_try_a_backup_that_thinks_then_goes_quiet(scene, monkeypatch):
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (0.5, 1.0))
    K.set_thought_quiet(8.0)
    K.set_fallback_models("")      # no list: the retry after the 503 is not raced at once (A9), the watch is on trial
    ctx, result = scene.reply([[("fail", overloaded)],
                               [("sleep", 30.0), ("say", "silent try 2")],
                               [("sleep", 0.05), ("think", "the backup thinks"), ("sleep", 30.0), ("say", "never")],
                               [("sleep", 0.05), ("say", "rescued answer")]])
    assert ("text", "rescued answer") in scene.kinds()
    assert [d for k, d in scene.kinds() if k == "think"] == ["the backup thinks"]   # shown live before it went quiet
    assert scene.vt.elapsed() < 14.0, "the turn no longer waits for the provider's timeout"
    assert len(scene.starts) == 4


# --- A7 9 - the journal row, the history and the words ----------------------------------------------------

def calls_rows(tmp_path):
    import json
    path = tmp_path / "calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a7_9_a_rescued_row_carries_what_the_user_first_saw_and_how_many_legs_were_asked(scene, tmp_path):
    ctx, result = scene.reply([[("sleep", 0.5), ("think", "first thought"), ("sleep", 500)],
                               [("think", "the rescue thinks"), ("sleep", 0.3), ("say", "rescued")]])
    rows = [r for r in calls_rows(tmp_path) if r["outcome"] == "ok"]
    assert len(rows) == 1
    row = rows[0]
    assert row["rescued"] is True and row["rescues_asked"] == 1
    # the first sign is when the user first saw work (the quiet leg's first thought), not the rescue's
    assert 0 <= row["ms_to_first_sign"] <= 1500 and row["ms_to_first_sign"] <= row["ms_total"]
    assert row["ms_total"] >= 2000                                  # the whole stall is in the total
    assert any(r.get("rescued") for r in K._KAME_REPLIES)           # the panel's reply row says so too


def test_a7_9_a_watched_reply_that_thought_records_its_longest_quiet_and_nothing_about_rescue(scene, tmp_path):
    ctx, _result = scene.reply([[("think", "a"), ("sleep", 1.5), ("think", "b"), ("sleep", 0.4),
                                 ("think", "c"), ("sleep", 0.2), ("say", "done")]])
    row = [r for r in calls_rows(tmp_path) if r["outcome"] == "ok"][0]
    assert 1400 <= row["ms_longest_quiet"] <= 1700 and row["rescued"] is False
    assert "rescues_asked" not in row
    assert ctx.get("quiet_longest") == pytest.approx(1.5, abs=0.2)
    assert H.HEDGE_TRACKER.quiet_bound(ID) == 20.0                  # one sample: not enough to move anything


def test_a7_9_a_reply_with_fewer_than_two_thoughts_teaches_no_quiet(scene, tmp_path):
    scene.reply([[("think", "a"), ("sleep", 1.5), ("say", "done")]])
    row = [r for r in calls_rows(tmp_path) if r["outcome"] == "ok"][0]
    assert "ms_longest_quiet" not in row or row["ms_longest_quiet"] is None


def test_a7_9_a_rescued_row_never_seeds_the_first_word_history(tmp_path, monkeypatch):
    import json
    import kame_journal as J
    monkeypatch.setenv("KAME_DATA_DIR", str(tmp_path))
    rows = [
        {"at": 1.0, "outcome": "ok", "identity": ID, "ms_to_first_sign": 500, "channel": "main"},
        {"at": 2.0, "outcome": "ok", "identity": ID, "ms_to_first_sign": 9000, "channel": "main", "rescued": True},
        {"at": 3.0, "outcome": "ok", "identity": ID, "ms_to_first_sign": 700, "channel": "main", "rescued": False},
    ]
    (tmp_path / J.CALLS_FILE).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert J.first_word_history(now=10.0)[ID] == [0.5, 0.7]


def test_a7_9_the_quiet_history_reads_the_rows_and_seeds_the_bound_after_a_restart(scene, tmp_path):
    import json
    import time
    import kame_journal as J
    t = time.time()                                                 # the virtual clock's "now"
    rows = [{"at": t - 40, "outcome": "ok", "identity": ID, "ms_longest_quiet": 40000, "channel": "main"},
            {"at": t - 30, "outcome": "ok", "identity": ID, "ms_longest_quiet": 40000, "channel": "main"},
            {"at": t - 20, "outcome": "ok", "identity": ID, "ms_longest_quiet": 0, "channel": "main"},
            {"at": t - 10, "outcome": "ok", "identity": ID, "ms_longest_quiet": 5000, "channel": "secondary"}]
    (tmp_path / J.CALLS_FILE).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert J.quiet_history()[ID] == [40.0, 40.0]
    K.set_thought_quiet(None)
    H.HEDGE_TRACKER.clear()
    K._KAME_QUIET_SEEDED.clear()
    scene.reply([[("say", "hi")]])                                  # a reply seeds the history once
    assert H.HEDGE_TRACKER.quiet_bound(ID) == pytest.approx(50.0)


def test_a7_9_the_differential_a_reply_with_no_stall_is_the_same_with_the_watch_on_and_off(scene):
    script = [[("think", "a"), ("sleep", 0.2), ("think", "b"), ("sleep", 0.2), ("say", "x"), ("say", "y")]]
    scene.reply(script)
    on = (scene.kinds(), len(scene.starts))
    scene.seen.clear()
    scene.starts.clear()
    scene.homes = 0
    K.set_thought_quiet(0)
    scene.reply(script)
    assert (scene.kinds(), len(scene.starts)) == on


# --- 6.35 - what the review of the Hermes build found, where it has an equivalent here ---------------

def test_635_a_rescue_leg_that_lost_is_never_noted_busy_by_a_later_cross_win(scene):
    K.set_fallback_models(f"{NEXT}\n{THIRD}")
    busy_and_due()
    scene.scripts[NEXT] = [("sleep", 0.1), ("think", "next thinks"), ("sleep", 2.6), ("say", "next answers")]
    scene.scripts[THIRD] = [("sleep", 500)]                         # the rescue: asked at the bound, silent
    ctx, result = scene.reply([[("sleep", 500)]])
    assert result is scene.objs[NEXT]
    assert THIRD in scene.cancelled and scene.started(THIRD)
    assert THIRD_ID not in K._KAME_BUSY_UNTIL                       # asked late, cut silent: says nothing of its model
    assert not ctx.get("rescued")


def test_635_a_cut_leg_that_had_worked_teaches_its_own_first_sign_uncensored(scene, monkeypatch):
    calls = []
    real = H.HEDGE_TRACKER.record_latency
    monkeypatch.setattr(H.HEDGE_TRACKER, "record_latency",
                        lambda identity, seconds, **kw: (calls.append((identity, round(seconds, 2), kw.get("censored", False))),
                                                         real(identity, seconds, **kw))[1])
    scene.reply([[("sleep", 0.4), ("think", "a"), ("sleep", 500)],
                 [("think", "the rescue thinks"), ("sleep", 0.3), ("say", "rescued")]])
    mine = [c for c in calls if c[0] == ID]
    assert (ID, 0.4, False) in mine                                 # the stalled leg's real first word, not a censored bound
    assert not [c for c in mine if c[2]]                            # nothing censored for a leg that had worked


def test_a_provisional_that_fails_while_a_rescue_runs_leaves_the_rescue_running(scene):
    ctx, result = scene.reply([[("think", "a"), ("sleep", 2.5), ("fail", overloaded)],
                               [("think", "the rescue thinks"), ("sleep", 1.0), ("say", "rescued")]])
    assert result is scene.objs["home1"]
    assert scene.kinds()[-1] == ("text", "rescued")
    assert ctx["rescued"] is True and ledger()["rescue_won"] == 1


def test_a_rescue_that_wins_by_the_next_model_counts_one_rescue_not_a_race(scene):
    scene.scripts[NEXT] = [("say", "from the next model")]
    scene.reply([[("think", "a"), ("sleep", 500)], [("fail", overloaded)]])
    assert ledger()["race"] == 0 and ledger()["rescue"] == 2
    hop = scene.hops[-1]
    assert hop.get("why") == "stopped mid-thought" and hop.get("race") is True and hop.get("origin") == ID


# --- A5 - the panel's cost line ------------------------------------------------------------------------------

@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node is not installed")
def test_a5_the_cost_line_says_how_many_extra_requests_re_asked_a_stalled_reply():
    from test_v1_8_2_9_view_logic import run_js
    speed = {"cap_pct": 10, "hour": {"replies": 12, "extra_pct": 4, "rescue": 3, "rescue_won": 1},
             "since_start": {"replies": 30, "extra_pct": 6}}
    out = run_js("return V.speedCostLine(data);", speed)
    assert out["text"] == ("Extra requests this hour: +4% (12 replies). Your setting allows about +10%. "
                           "3 of them re-asked a reply that stopped mid-thought; 1 answered first. "
                           "Since Agent Zero started: +6%.")
    speed["hour"].update(rescue=0, rescue_won=0)
    quiet = run_js("return V.speedCostLine(data);", speed)
    assert "mid-thought" not in quiet["text"]                           # nothing to say when none was asked
    speed["hour"].pop("rescue_won")
    speed["hour"]["rescue"] = 2
    assert "2 of them re-asked a reply that stopped mid-thought; 0 answered first." in \
        run_js("return V.speedCostLine(data);", speed)["text"]


# --- review round: bugs found by an adversarial read of the build ----------------------------------------------

def run_reply(scene, scripts, limit=2000):
    """('ok', result, virtual seconds) or ('CANCELLED' | the error, None, seconds)."""
    try:
        ctx, result = scene.reply(scripts, limit=limit)
        return "ok", result, scene.vt.elapsed(), ctx
    except asyncio.CancelledError:
        return "CANCELLED", None, scene.vt.elapsed(), None
    except BaseException as exc:  # noqa: BLE001
        return type(exc).__name__ + ":" + str(exc)[:80], None, scene.vt.elapsed(), None


def test_r1_a_raced_in_leg_that_thinks_and_then_fails_does_not_end_the_reply_as_a_stop(scene):
    """A 503, then the next model raced at once thinks and fails; the retry is silent. Was: a bare CancelledError
    that the carousel took for the user's stop."""
    K.set_fallback_models(NEXT)
    scene.scripts[NEXT] = [("sleep", 0.05), ("think", "t"), ("sleep", 0.5), ("fail", overloaded)]
    out = run_reply(scene, [[("fail", overloaded)], [("sleep", 500)], [("say", "third try")]])
    assert out[0] == "ok", out[:3]
    assert out[1] is scene.objs["home2"] and out[2] < 60.0


def test_r1_a_next_model_joined_at_the_timer_that_thinks_and_fails_rotates_the_reply(scene, monkeypatch):
    monkeypatch.setattr(K, "_KAME_CROSS_COLD_S", 1.0)
    monkeypatch.setattr(K, "_KAME_SLOW_S", {False: (4.0, 4.0, 4.0), True: (4.0, 4.0, 4.0)})
    K.set_thought_quiet(30.0)
    K.set_fallback_models(NEXT)
    scene.scripts[NEXT] = [("sleep", 0.05), ("think", "t"), ("sleep", 0.5), ("fail", overloaded)]
    out = run_reply(scene, [[("sleep", 500), ("say", "late")], [("say", "second try")]])
    assert out[0] == "ok", out[:3]
    assert scene.started(NEXT)


def test_r2_a_provisional_that_fails_while_the_rescue_is_silent_raises_at_once(scene):
    K.set_thought_quiet(2.0)
    K.set_fallback_models("")
    out = run_reply(scene, [[("think", "a"), ("sleep", 3.0), ("fail", overloaded)],
                            [("sleep", 500), ("say", "very late")],
                            [("say", "fast")]])
    assert out[0] == "ok", out[:3]
    assert out[1] is scene.objs["home2"], "the silent rescue was waited for"
    assert out[2] < 60.0, f"waited {out[2]:.0f}s for the provider's timeout"
    assert "home1" in scene.cancelled                            # the silent rescue was cut


def test_r2_a_provisional_that_fails_while_another_leg_has_worked_leaves_that_leg_running(scene):
    K.set_thought_quiet(2.0)
    K.set_fallback_models("")
    out = run_reply(scene, [[("think", "a"), ("sleep", 2.5), ("fail", overloaded)],
                            [("think", "the rescue thinks"), ("sleep", 1.0), ("say", "rescued")]])
    assert out[0] == "ok" and out[1] is scene.objs["home1"]


def test_r3_a_leg_cut_at_the_provisional_claim_is_measured_to_that_instant(scene, monkeypatch):
    K.set_thought_quiet(30.0)
    K.set_fallback_models("")
    monkeypatch.setattr(H.HEDGE_TRACKER, "stream_triggers", lambda *a, **k: (1.0, 60.0))
    monkeypatch.setattr(H.HEDGE_TRACKER, "may_start_backup", lambda *a, **k: True)
    calls = []
    real = H.HEDGE_TRACKER.record_latency
    monkeypatch.setattr(H.HEDGE_TRACKER, "record_latency",
                        lambda identity, seconds, **kw: (calls.append((round(seconds, 1), kw.get("censored", False))),
                                                         real(identity, seconds, **kw))[1])
    thoughts = [("sleep", 3.0)] + [x for n in range(6) for x in (("think", f"t{n}"), ("sleep", 2.0))]
    out = run_reply(scene, [thoughts + [("say", "answer")], [("sleep", 500)]])
    assert out[0] == "ok"
    censored = [s for s, c in calls if c]
    assert not censored or max(censored) < 5.0, calls           # started at 1 s, cut at 3 s: 2 s (below the winner: not kept), never 14


def test_r4_a_kept_probe_that_fails_is_judged_once(scene, monkeypatch):
    K.set_thought_quiet(5.0)
    K.set_fallback_models(NEXT)
    busy_and_due()
    judged = []
    real = K._kame_judge_leg
    monkeypatch.setattr(K, "_kame_judge_leg", lambda *a, **k: (judged.append(a[1]), real(*a, **k))[1])
    scene.scripts[NEXT] = [("sleep", 0.1), ("think", "next thinks"), ("sleep", 3.0), ("say", "next answers")]
    boom = lambda: Refusal("Error code: 500", 500, {"error": {"code": 500, "message": "internal"}})  # noqa: E731
    out = run_reply(scene, [[("sleep", 1.0), ("fail", boom)]])
    scene.vt.advance(40.0)
    assert out[0] == "ok" and out[1] is scene.objs[NEXT]
    assert len(judged) == 1, judged


def test_r4_a_kept_probe_still_running_is_not_something_that_can_answer(scene):
    K.set_thought_quiet(30.0)
    K.set_fallback_models(NEXT)
    busy_and_due()
    scene.scripts[NEXT] = [("sleep", 0.1), ("think", "next thinks"), ("sleep", 3.0), ("fail", overloaded)]
    out = run_reply(scene, [[("sleep", 20.0), ("say", "best is back")], [("say", "retry")]])
    assert out[0] == "ok", out[:3]
    assert out[2] < 15.0, "the kept probe was waited for"


def test_r5_when_the_home_model_said_busy_and_a_rescue_of_the_raced_in_leg_wins_the_window_opens(scene):
    K.set_thought_quiet(1.0)
    scene.scripts[NEXT] = [("sleep", 0.05), ("think", "the next model thinks"), ("sleep", 500)]
    scene.scripts[THIRD] = [("sleep", 0.1), ("say", "the rescue answers")]
    out = run_reply(scene, [[("fail", overloaded)], [("sleep", 500)]])
    assert out[0] == "ok" and out[1] is scene.objs[THIRD]
    assert ID in K._KAME_BUSY_UNTIL


def test_r5_a_rescue_of_a_home_leg_still_leaves_the_home_model_alone(scene):
    scene.scripts[NEXT] = [("say", "from the next model")]
    out = run_reply(scene, [[("think", "a"), ("sleep", 500)], [("fail", overloaded)]])
    assert out[0] == "ok" and out[1] is scene.objs[NEXT]
    assert ID not in K._KAME_BUSY_UNTIL


def test_r6_a_model_refused_busy_in_this_reply_is_readmitted_only_after_the_probe_clock(scene):
    ctx = scene.ctx()
    now = __import__("time").time()
    ctx["leg_busy"] = [NEXT_ID, THIRD_ID]
    ctx["leg_busy_at"] = {NEXT_ID: now - 5.0, THIRD_ID: now - 60.0}
    K._kame_readmit_leg_busy(ctx, now)
    assert ctx["leg_busy"] == [NEXT_ID]                              # 5 s ago: still out; 60 s ago: a candidate
    K._kame_readmit_leg_busy(ctx, now + 20.0)
    assert not ctx.get("leg_busy")


def test_r6_the_refusal_is_stamped_per_model(scene):
    K.set_fallback_models(NEXT)
    scene.scripts[NEXT] = [("sleep", 0.05), ("fail", overloaded)]
    ctx, _ = scene.reply([[("sleep", 40.0), ("say", "home")]])   # the next model is raced in at the cold bound
    assert NEXT_ID in ctx["leg_busy"] and NEXT_ID in ctx["leg_busy_at"]
