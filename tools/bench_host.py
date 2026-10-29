"""A stubbed Agent Zero for the two bench tools (``bench_reply_path.py``, ``bench_status.py``).

Instruments, not features: they run KAME's real engine with just enough of Agent Zero
replaced to import it - the same stubs the offline test suites use - so they work on any
machine with a Python, with no Agent Zero, no network and no key. What they measure is
KAME's own code.

``load_engine()`` installs the stubs, points the journal at a throw-away folder
(``KAME_DATA_DIR``) and returns the engine module; ``keys`` for a provider come from a stub of
Agent Zero's ``.env`` reader.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: provider name (as Agent Zero writes it) -> comma-separated keys, read by the stub below.
KEYS: dict = {}


def _stub(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def install_stubs() -> None:
    """Replace the Agent Zero modules the engine imports. Safe to call twice."""
    if getattr(sys.modules.get("helpers"), "_kame_bench_stub", False):
        return
    for name in ("openai", "langchain_core"):
        _stub(name)
    helpers = _stub("helpers")
    helpers._kame_bench_stub = True
    lit = _stub("litellm")
    lit.suppress_debug_info = False
    lit.acompletion = lambda *a, **k: None
    messages = _stub("langchain_core.messages")
    messages.SystemMessage = messages.HumanMessage = type("_Msg", (), {"__init__": lambda self, content="": None})
    print_style = _stub("helpers.print_style")
    print_style.PrintStyle = type("_PrintStyle", (), {
        "__init__": lambda self, *a, **k: None,
        "print": lambda self, *a, **k: None,
        "warning": staticmethod(lambda *a, **k: None),
        "error": staticmethod(lambda *a, **k: None),
        "success": staticmethod(lambda *a, **k: None),
    })
    errors = _stub("helpers.errors")
    for name in ("InterventionException", "RepairableException", "HandledException"):
        setattr(errors, name, type(name, (Exception,), {}))
    dotenv = _stub("helpers.dotenv")
    dotenv.get_dotenv_value = lambda name, default=None: _read_key(name, default)


def _read_key(name: str, default=None):
    for provider, keys in KEYS.items():
        if name == f"API_KEY_{provider.upper()}":
            return keys
    return default


def set_keys(provider: str, keys) -> None:
    KEYS[provider] = ",".join(keys)


def new_data_dir(prefix: str = "kame-bench-") -> Path:
    """A fresh folder for the journal; the engine reads ``KAME_DATA_DIR`` on every write."""
    folder = Path(tempfile.mkdtemp(prefix=prefix))
    os.environ["KAME_DATA_DIR"] = str(folder)
    return folder


def drop(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


def load_engine():
    """Install the stubs, put the plugin folder on ``sys.path`` and import the engine."""
    install_stubs()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if not os.environ.get("KAME_DATA_DIR"):
        atexit.register(drop, new_data_dir())
    import kame_engine  # noqa: E402

    kame_engine.set_log_level("silent")
    kame_engine.set_wait_notice(False)
    return kame_engine


def percentiles(values_s) -> dict:
    """``{"p50_ms", "p95_ms", "p99_ms"}`` (nearest rank) of a list of seconds."""
    values = sorted(values_s)
    n = len(values)
    if not n:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0}

    def at(q: float) -> float:
        return round(values[min(n - 1, int(q * n))] * 1000.0, 4)

    return {"p50_ms": at(0.50), "p95_ms": at(0.95), "p99_ms": at(0.99)}


def profile_share(profile, filename_suffix: str, names) -> float:
    """Cumulative seconds the profile spent in the named functions of one file."""
    import pstats

    total = 0.0
    for (path, _line, func), row in pstats.Stats(profile).stats.items():
        if path.replace("\\", "/").endswith(filename_suffix) and func in names:
            total += row[3]
    return total
