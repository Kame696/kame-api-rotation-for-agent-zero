"""A virtual clock for the 1.8.2.9 suites (not a test itself).

The new behaviour is made of waits (a busy model asked again every 20 s, a background job
given 30 s, a model silent for 45 s). Testing it in real time would take minutes, so these
suites run the carousel on an event loop whose clock jumps to the next timer whenever
nothing is ready, and `time.time()`, `time.monotonic()` and `time.perf_counter()` read that
clock. 60 s of a conversation take a few milliseconds and the numbers are exact.

    def test_something(vt):
        result = vt.run(some_coroutine())      # vt.elapsed() is the virtual seconds so far
"""
from __future__ import annotations

import asyncio
import time

import pytest

_REAL_TIME = time.time


class VirtualLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock is a number: it moves only when every task is waiting."""

    def __init__(self) -> None:
        super().__init__()
        self._virtual = 0.0

    def time(self) -> float:
        return self._virtual

    def _run_once(self) -> None:
        if not self._ready and self._scheduled:
            due = self._scheduled[0]._when
            if due > self._virtual:
                self._virtual = due
        super()._run_once()


class VClock:
    WALL0 = 1_800_000_000.0     # an arbitrary "now": 2027-01-15
    MONO0 = 5_000.0

    def __init__(self, monkeypatch) -> None:
        self.loop = VirtualLoop()
        monkeypatch.setattr(time, "time", lambda: self.WALL0 + self.loop._virtual)
        monkeypatch.setattr(time, "monotonic", lambda: self.MONO0 + self.loop._virtual)
        monkeypatch.setattr(time, "perf_counter", lambda: self.MONO0 + self.loop._virtual)

    def elapsed(self) -> float:
        return self.loop._virtual

    def advance(self, seconds: float) -> None:
        """Let virtual time pass with nothing running."""
        async def idle():
            await asyncio.sleep(seconds)
        self.run(idle())

    def run(self, coro, limit: float | None = None):
        """Run a coroutine to the end; with ``limit`` it is cancelled after that many virtual
        seconds and ``TimeoutError`` is raised (a carousel that waits for ever stops there)."""
        if limit is not None:
            async def bounded():
                return await asyncio.wait_for(coro, timeout=limit)
            return self.loop.run_until_complete(bounded())
        return self.loop.run_until_complete(coro)

    def close(self) -> None:
        try:
            pending = [t for t in asyncio.all_tasks(self.loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        self.loop.close()


@pytest.fixture
def vt(monkeypatch):
    clock = VClock(monkeypatch)
    yield clock
    clock.close()
