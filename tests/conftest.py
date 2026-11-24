"""pytest defaults for the Agent Zero suites.

1.8.4.2 (S1): remembered trouble (``kame_model_health``) is switched off for every suite by default, so a
failure one test provokes cannot make a model "risky" for the next one. The 1.8.4.2 suites switch it back on
for themselves (``monkeypatch.delenv``). ``run_all.py`` sets the same variable for the script-style suites.
"""
import os

os.environ.setdefault("KAME_NO_REMEMBERED_TROUBLE", "1")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _kame_payload_pace_starts_empty():
    """1.9.0.6 (Hermes failures/0034): the payload pace remembers refusals per payload, and most suites send the same
    messages: each test starts with no memory of the one before (in a real process that memory is the point)."""
    import sys
    engine = sys.modules.get("kame_engine")
    pace = getattr(engine, "_KAME_PACE", None)
    if pace is not None:
        pace.clear()
    yield
