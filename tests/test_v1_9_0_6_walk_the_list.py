"""1.9.0.6 (owner's live test, 2026-11-23; parity with Hermes tests/test_1_9_0_6_walk_the_list.py) - a reply walks
the list when its models fail.

The owner's Hermes kept a reply on one model of the list for 100 s while the next one was free. The same shapes,
here: a raced model whose leg AND replacement fail lets the next model of the list join while the home model thinks;
a large pool whose keys answer 429 one by one moves the reply instead of rotating on it.

A virtual clock (kame_vclock.py). No Agent Zero installation, no network.
"""
from __future__ import annotations

import pytest

from test_v1_8_2_7_speed_fallback import (  # noqa: F401  (fixtures are used by name)
    ID, KEYS, Refusal, clean, make_ctx, streaming,
)
from kame_vclock import vt  # noqa: F401
from test_v1_9_0_4_replace_failed_leg import (  # noqa: F401
    NEXT, World, asked, main_model_thinks, overloaded, rows_saying, world,
)
import kame_engine as K

THIRD = "google/gemini-3.5-flash"


def test_a_raced_model_that_fails_twice_lets_the_next_model_of_the_list_join(world):
    K.set_fallback_models(f"{NEXT}, {THIRD}")
    world.plan[NEXT] = [(0.5, overloaded), (0.5, overloaded), (0.05, "answer")]
    world.plan[THIRD] = [(0.05, "answer")]
    ctx, _ = make_ctx(main_model_thinks(60.0))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=200)
    assert result[0] == f"answer from {THIRD} (ask 1)", (result, world.starts)
    assert len(asked(world)) == 2, "the failed model got its one replacement, never a third ask"
    # the timed start (3 s) + two 0.5 s refusals + the next model's 0.05 s answer = 4.05 s, against 60 s of thinking
    assert world.vt.elapsed() < 4.5, "the next model answered long before the home model's slow bound"
    assert rows_saying("failed on two keys")


def test_a_raced_model_that_said_busy_is_remembered_so_the_next_reply_skips_it(world):
    """Hermes' fake-provider comparison (storm): the winner left out every leg that FAILED, the 503 ones too, so no
    busy model of a walk was remembered and each reply walked the list again. A 503 leg is noted busy; a 429 is not."""
    K.set_fallback_models(f"{NEXT}, {THIRD}")
    world.plan[NEXT] = [(0.5, overloaded), (0.5, overloaded), (0.05, "answer")]
    world.plan[THIRD] = [(0.05, "answer")]
    ctx, _ = make_ctx(main_model_thinks(60.0))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=200)
    assert result[0] == f"answer from {THIRD} (ask 1)", (result, world.starts)
    model = NEXT.split("/")[-1]
    busy = {i: u for i, u in K._KAME_BUSY_UNTIL.items() if i.endswith(model) and u > world.vt.now()} \
        if hasattr(world.vt, "now") else {i: u for i, u in K._KAME_BUSY_UNTIL.items() if i.endswith(model)}
    assert busy, f"the model that answered 503 is remembered busy: {dict(K._KAME_BUSY_UNTIL)}"


def test_without_a_next_model_the_race_waits_for_the_home_model_as_before(world):
    world.plan[NEXT] = [(0.5, overloaded), (0.5, overloaded), (0.5, overloaded)]
    ctx, _ = make_ctx(main_model_thinks(6.0))
    result = world.vt.run(K._kame_carousel(None, ctx), limit=120)
    assert result[0] == "main answer"
    assert len(asked(world)) == 2


@pytest.mark.parametrize("refused, pool, moves", [
    (1, 14, False), (6, 14, False), (7, 14, True), (3, 4, True), (2, 4, False), (3, 5, True), (2, 2, False),
])
def test_half_of_a_large_pool_refusing_moves_the_reply(refused, pool, moves):
    keys = [f"k{i}" for i in range(pool)]
    assert K._kame_refused_widely(set(keys[:refused]), keys) is moves


def test_three_of_four_moves_and_timeouts_are_not_counted():
    keys = ["a", "b", "c", "d"]
    assert K._kame_refused_widely({"a", "b", "c"}, keys)
    assert "timeout" not in K._KAME_MOVE_ON_KINDS and {"per_minute", "server"} <= K._KAME_MOVE_ON_KINDS
