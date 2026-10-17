"""Remote-only native Agent Zero acceptance for 1.8.2.7; no provider keys.

What 1.8.2.7 adds, on a real Agent Zero checkout, through the real LiteLLM /
OpenAI SDK against a local TCP server (the same shape as native_1818.py):

* a silent first word gets a backup key and the first key to speak wins;
* KAME's fallback list builds the next model with Agent Zero's own
  `models.get_chat_model` and answers with it when every key of the configured
  model rests, and when the provider has no server for it (a closed door);
* parity with Hermes: a key that accepts and then says nothing is dropped
  after the first-token wait; an answer cut mid-stream is continued on the
  next key and shown once; an error handed back carries the line that tells
  the agent whether retrying helps, in the text Agent Zero shows it;
* the panel's two API handlers run in the host's real package layout
  (`usr/plugins/api_rotation_by_kame/api/...`, `helpers.api`, `helpers.plugins`).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if os.environ.get("GITHUB_ACTIONS") != "true":
    raise SystemExit("Native acceptance runs only on the remote GitHub runner.")

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
ROOT = Path(__file__).resolve().parents[1]
A0 = Path(sys.argv[1]).resolve()
REPORT = Path(sys.argv[2]).resolve()
os.environ["KAME_DATA_DIR"] = tempfile.mkdtemp(prefix="kame-native-1827-")
# The optional modules (quirks, hedging, journal) must load, as they do installed.
sys.path.append(str(ROOT))
g = runpy.run_path(str(ROOT / "tests" / "test_a0_compat.py"))
K, models = g["kame"], g["models"]
K.remove_kame_patch()
checks, requests = [], []
lock = threading.Lock()


def check(name, condition, detail=""):
    checks.append({"name": name, "pass": bool(condition), "detail": str(detail)[:500]})
    print(("PASS " if condition else "FAIL ") + name, flush=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        key = self.headers.get("Authorization", "")
        model = body.get("model", "")
        case = next((m["content"] for m in reversed(body["messages"]) if m.get("role") == "user"), "")
        with lock:
            requests.append({"case": case, "key": key, "model": model, "at": time.monotonic(),
                             "last_role": (body["messages"][-1] or {}).get("role"),
                             "last_text": str((body["messages"][-1] or {}).get("content") or "")})
        if case == "busy" and model == "gpt-4o-mini":
            return self._json(503, {"error": {"message": "The model is overloaded. Please try again later.",
                                              "type": "service_unavailable", "code": 503}})
        if case == "door" and model == "gpt-4o-mini":
            return self._json(503, {"error": {"message": "No instances available for this model",
                                              "type": "service_unavailable"}})
        if case == "slow" and key.endswith("FIXTURE-A"):
            time.sleep(4)
        if case == "deny":
            # a refused model on every key: what no wait can fix, so it is handed back
            # (a bad key is retired and waited on instead, as in 1.8.1.8)
            return self._json(403, {"error": {"message": "Your plan does not include this model",
                                              "type": "permission_error"}})
        if case == "cut" and key.endswith("FIXTURE-A"):
            return self._cut(model)
        answer = f"Hello {case} from {model}"
        if case == "cut" and body["messages"][-1].get("role") == "assistant":
            answer = f"from {model}"  # the continuation of what was shown
        chunk = {"id": "chatcmpl-fixture", "object": "chat.completion.chunk", "created": 1,
                 "model": model, "choices": [{"index": 0, "delta": {"content": answer},
                                              "finish_reason": "stop"}]}
        payload = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if case == "mute" and key.endswith("FIXTURE-A"):
            time.sleep(5)  # accepted, then nothing: the first-token wait drops it
        try:
            self.wfile.write(payload)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


    def _cut(self, model):
        """Half an answer, then the connection drops (declared length not met)."""
        chunk = {"id": "chatcmpl-fixture", "object": "chat.completion.chunk", "created": 1,
                 "model": model, "choices": [{"index": 0, "delta": {"content": "Hello cut "},
                                              "finish_reason": None}]}
        part = ("data: " + json.dumps(chunk) + "\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(part) + 400))
        self.end_headers()
        try:
            self.wfile.write(part)
            self.wfile.flush()
            time.sleep(0.3)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


server = Server(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}/v1"


def reset():
    for name in ("_KAME_KEY_HEALTH", "_KAME_ACCOUNT_HOLDS", "_KAME_STATED_RL",
                 "_KAME_NO_ANSWER_SINCE", "_KAME_STORM", "_KAME_FALLBACK_SKIP",
                 "_KAME_FALLBACK_MODEL_CACHE", "_KAME_LAST_MODEL_SWITCH"):
        getattr(K, name).clear()
    K.set_log_level("silent")
    K.set_rotation_disabled(False)
    K.set_fallback_models("")
    K.set_hedge_mode("adaptive")
    K.set_stream_silence_timeout(0)
    K.set_model_switch("auto")
    K.set_first_token_wait("auto")
    K.set_stream_resume_limit(-1)
    K.set_stream_stitch_disabled(False)
    K.set_max_total_wait(0)
    K.set_model_switch("auto")
    K.set_first_token_wait("auto")
    K._KAME_BUSY_UNTIL.clear()
    K._KAME_BUSY_WINDOW.clear()
    K._get_all_api_keys = lambda self: ["FIXTURE-A", "FIXTURE-B"]
    if K._KJ:
        K._KJ._reset_for_tests()
        K._KJ.SHARE_HEALTH_ON = False
    if K._KH:
        K._KH.HEDGE_TRACKER.clear()
    requests.clear()


async def call(case, callback=None):
    """As the conversation calls the model: through a frame named like Agent
    Zero's `Agent.call_chat_model_turn` (agent.py's message loop). KAME's
    fallback list and keys-asked-together apply to the conversation only."""
    return await call_chat_model_turn(case, callback)


async def call_chat_model_turn(case, callback=None):
    return await direct_call(case, callback)


async def direct_call(case, callback=None, wait=30):
    """A plugin calling the model itself (not the conversation)."""
    wrapper = models.LiteLLMChatWrapper(model="gpt-4o-mini", provider="openai",
                                        model_config=models.ModelConfig(
                                            type=models.ModelType.CHAT, provider="openai",
                                            name="gpt-4o-mini", api_base=base))
    return await asyncio.wait_for(wrapper.unified_call(
        system_message="Keep this instruction exactly", user_message=case,
        response_callback=callback or (lambda delta, full: asyncio.sleep(0)),
        api_base=base, api_key="FIXTURE-A", temperature=0,
        a0_api_mode="chat_completions", a0_retry_attempts=0,
        a0_retry_delay=0, num_retries=0, timeout=10), wait)


async def checks_1827():
    check("the provider quirks loaded", K._KQ is not None)
    check("the speed boost loaded", K._KH is not None)

    # 1. a silent first word gets a backup; the first key to speak wins
    reset()
    original_triggers = K._KH.HEDGE_TRACKER.stream_triggers
    K._KH.HEDGE_TRACKER.stream_triggers = lambda *a, **k: (0.5, 1.0)
    original_pick = K._get_best_key

    def a_first(identity, keys):
        if "FIXTURE-A" in keys and not any(r["key"].endswith("FIXTURE-A") for r in requests):
            return "FIXTURE-A", "SUCCESS"
        return original_pick(identity, [k for k in keys if k != "FIXTURE-A"] or keys)

    K._get_best_key = a_first
    shown = []

    async def on_text(delta, full):
        shown.append(full)

    try:
        started = time.monotonic()
        result = await call("slow", on_text)
        took = time.monotonic() - started
    finally:
        K._get_best_key = original_pick
        K._KH.HEDGE_TRACKER.stream_triggers = original_triggers
    check("backup: the reply came well before the slow key's 4 s", took < 3.5, f"{took:.2f}s")
    check("backup: both keys were asked", {r["key"] for r in requests} ==
          {"Bearer FIXTURE-A", "Bearer FIXTURE-B"}, requests)
    check("backup: the answer is whole and native", result[0] == "Hello slow from gpt-4o-mini", result)
    check("backup: only the winner reached the screen", shown and all(s == result[0] for s in shown), shown)
    check("backup: counted", K._KAME_SPEED_STATS["backups"] >= 1)

    # 2. every key resting: the next model of KAME's list answers (real factory)
    reset()
    K.set_fallback_models(f"openai/gpt-4o-fallback @ {base}")
    ident = "openai:openai/gpt-4o-mini"
    K._get_identity_state(ident, ["FIXTURE-A", "FIXTURE-B"])
    for key in ("FIXTURE-A", "FIXTURE-B"):
        # per model: an out-of-credit hold is account-wide and would rightly
        # hold the fallback model of the same provider and keys too
        K._mark_key_health(ident, key, False, 3600, "daily")
    result = await call("resting")
    check("fallback: answered by the list's model", result[0] == "Hello resting from gpt-4o-fallback", result)
    check("fallback: the configured model was not asked", all(r["model"] == "gpt-4o-fallback" for r in requests),
          requests)
    check("fallback: recorded for the panel", K._KAME_LAST_MODEL_SWITCH.get("to") == "openai/gpt-4o-fallback",
          K._KAME_LAST_MODEL_SWITCH)
    check("fallback: the model was built by Agent Zero's factory",
          any(type(m).__name__ == "LiteLLMChatWrapper" for m in K._KAME_FALLBACK_MODEL_CACHE.values()))
    # a plugin's own streaming call is not the conversation: it never switches
    reset()
    K.set_fallback_models(f"openai/gpt-4o-fallback @ {base}")
    K._get_identity_state(ident, ["FIXTURE-A", "FIXTURE-B"])
    for key in ("FIXTURE-A", "FIXTURE-B"):
        K._mark_key_health(ident, key, False, 3600, "daily")
    try:
        await direct_call("plugin", wait=3)
        switched = True
    except (asyncio.TimeoutError, TimeoutError):
        switched = False
    check("fallback: a call outside the conversation never switches model",
          not switched and not requests, requests)
    reset()
    result = await call("back")
    check("fallback: the next call starts on the configured model",
          result[0] == "Hello back from gpt-4o-mini", result)

    # 3. a closed door goes to the list at once
    reset()
    K.set_fallback_models(f"openai/gpt-4o-fallback @ {base}")
    result = await call("door")
    check("closed door: one knock, then the list's model", result[0] == "Hello door from gpt-4o-fallback"
          and [r["model"] for r in requests] == ["gpt-4o-mini", "gpt-4o-fallback"], requests)

    # 4. without a list, a closed door rests the whole pool (no knocking per key)
    reset()
    task = asyncio.create_task(call("door"))
    await asyncio.sleep(2.0)
    task.cancel()
    try:
        await task
    except BaseException:
        pass
    check("closed door, no list: one request, not one per key", len(requests) == 1, requests)
    pool = K._KAME_KEY_HEALTH.get(ident, {}).get("keys", {})
    check("closed door, no list: every key waits for the door",
          pool and all(float(v.get("sick_until") or 0) > time.time() + 20 for v in pool.values()))


def a_first_picker():
    original_pick = K._get_best_key

    def a_first(identity, keys):
        if "FIXTURE-A" in keys and not any(r["key"].endswith("FIXTURE-A") for r in requests):
            return "FIXTURE-A", "SUCCESS"
        return original_pick(identity, [k for k in keys if k != "FIXTURE-A"] or keys)

    return original_pick, a_first


async def checks_parity():
    check("parity: the stitcher, pacer, latency and region modules loaded",
          all(m is not None for m in (K._KS, K._KP, K._KL, K._KR)))

    # 5. a key that accepts and says nothing is dropped after the first-token wait
    reset()
    K.set_hedge_mode("off")  # the wait alone, no backup racing it
    K.set_stream_silence_timeout(1)
    original_local = K._kame_looks_local
    K._kame_looks_local = lambda base: False  # the fixture is local; a provider is not
    original_pick, a_first = a_first_picker()
    K._get_best_key = a_first
    try:
        started = time.monotonic()
        result = await call("mute")
        took = time.monotonic() - started
    finally:
        K._get_best_key, K._kame_looks_local = original_pick, original_local
    check("first-token wait: the silent key was dropped, the next one answered",
          result[0] == "Hello mute from gpt-4o-mini" and took < 4.0, f"{took:.2f}s {result!r}")
    check("first-token wait: the silent key was asked first",
          [r["key"] for r in requests][:2] == ["Bearer FIXTURE-A", "Bearer FIXTURE-B"], requests)

    # 6. an answer cut mid-stream is continued on the next key, shown once
    reset()
    shown = []

    async def on_text(delta, full):
        shown.append(full)

    original_pick, a_first = a_first_picker()
    K._get_best_key = a_first
    try:
        result = await call("cut", on_text)
    finally:
        K._get_best_key = original_pick
    check("stitching: the whole answer came back", result[0] == "Hello cut from gpt-4o-mini", result)
    check("stitching: the next key was sent what was shown",
          len(requests) >= 2 and requests[-1]["last_role"] == "assistant"
          and requests[-1]["last_text"].strip() == "Hello cut", requests)
    check("stitching: nothing was shown twice",
          shown and shown[-1] == "Hello cut from gpt-4o-mini"
          and not any("Hello cut Hello" in t for t in shown), shown)

    # 7. an error handed back tells the agent whether retrying helps, in the
    #    text Agent Zero's _error_retry shows it (format_error -> str())
    reset()
    try:
        await direct_call("deny", wait=20)
        raised = None
    except Exception as exc:
        raised = exc
    from helpers import errors as a0_errors
    text = a0_errors.format_error(raised) if raised is not None else ""
    check("retry hint: a refusal from every key says retrying will not help",
          "Retrying will not help" in text, f"{type(raised).__name__}: {text[-300:]}")
    check("retry hint: the error keeps its LiteLLM type",
          raised is not None and type(raised).__module__.startswith("litellm"), type(raised))


async def checks_1828():
    # 8. the owner's case: the model is overloaded on two keys -> the next model, at once
    reset()
    K.set_fallback_models(f"openai/gpt-4o-fallback @ {base}")
    started = time.monotonic()
    result = await call("busy")
    took = time.monotonic() - started
    check("busy provider: the list's model answered", result[0] == "Hello busy from gpt-4o-fallback", result)
    check("busy provider: two knocks, not the whole pool",
          [r["model"] for r in requests].count("gpt-4o-mini") == 2, requests)
    check("busy provider: switched within seconds", took < 8.0, f"{took:.2f}s")
    check("busy provider: remembered for the next calls", K._KAME_BUSY_UNTIL.get("openai:openai/gpt-4o-mini", 0) > time.time())

    # 9. without a list an overloaded model keeps rotating one key at a time
    reset()
    task = asyncio.create_task(call("busy"))
    await asyncio.sleep(2.5)
    task.cancel()
    try:
        await task
    except BaseException:
        pass
    check("busy provider, no list: the configured model keeps being asked, no other model",
          len(requests) >= 2 and all(r["model"] == "gpt-4o-mini" for r in requests), requests)

    # 10. the reply line names the model of the turn
    reset()
    agent = types.SimpleNamespace(number=0, context=types.SimpleNamespace())
    K.turn_started(agent)
    token = K._KAME_CURRENT_AGENT.set(agent)
    try:
        await call("named")
    finally:
        K._KAME_CURRENT_AGENT.reset(token)
    line = K.reply_line(agent) or ""
    check("reply line: names the model and the time", line.startswith("⏱ ") and "gpt-4o-mini" in line, line)


def panel_checks():
    # The host's real plugin layout: usr/plugins/api_rotation_by_kame/...
    target = A0 / "usr" / "plugins" / "api_rotation_by_kame"
    if target.exists():
        shutil.rmtree(target)
    # The host checkout sits inside the plugin checkout on the runner: never copy it into itself.
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns("tests", "__pycache__", ".git", ".github", "host"))
    sys.path.insert(0, str(A0))
    status_mod = importlib.import_module("usr.plugins.api_rotation_by_kame.api.kame_status")
    set_mod = importlib.import_module("usr.plugins.api_rotation_by_kame.api.kame_settings_set")
    report = asyncio.run(status_mod.KameStatus.process(None, {"panel": True}, None))
    check("panel: status answers in the host layout", report.get("available") is True, report.get("reason"))
    names = {m["name"] for m in report.get("settings_meta") or []}
    check("panel: the settings table is served", {"fallback_models", "hedge_mode"} <= names, sorted(names))
    check("panel: no key anywhere in the report", "FIXTURE-" not in json.dumps(report, default=str))
    saved = asyncio.run(set_mod.KameSettingsSet.process(None, {"name": "failover_after_seconds", "value": "90"}, None))
    check("panel: a setting is saved and applied", saved.get("ok") is True, saved)
    bad = asyncio.run(set_mod.KameSettingsSet.process(None, {"name": "hedge_mode", "value": "turbo"}, None))
    check("panel: a bad value is refused", bad.get("ok") is False, bad)
    engine = importlib.import_module("usr.plugins.api_rotation_by_kame.kame_engine")
    check("panel: the saved value is in force", engine._KAME_FAILOVER_AFTER_S == 90, engine._KAME_FAILOVER_AFTER_S)
    for rel in ("webui/main.html", "webui/kame-panel.html", "webui/kame-panel-store.js",
                "extensions/webui/sidebar-quick-actions-dropdown-start/kame-entry.html"):
        check(f"panel: {rel} installed", (target / rel).is_file())
    check("panel: the central menu slot exists in this host",
          "sidebar-quick-actions-dropdown-start" in (A0 / "webui" / "components" / "sidebar" / "top-section"
                                                     / "quick-actions.html").read_text(encoding="utf-8"))


report = {"failed": 1}
try:
    K.apply_kame_patch()
    asyncio.run(checks_1827())
    asyncio.run(checks_parity())
    asyncio.run(checks_1828())
    K.remove_kame_patch()
    panel_checks()
except Exception as exc:
    check("native 1.8.2.7 harness completed", False, f"{type(exc).__name__}: {exc}")
finally:
    try:
        K.remove_kame_patch()
    except Exception:
        pass
    server.shutdown()
    server.server_close()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    report = {"schema": 1, "host": str(A0), "checks": checks,
              "passed": sum(c["pass"] for c in checks),
              "failed": sum(not c["pass"] for c in checks),
              "transport": "real LiteLLM/OpenAI SDK over local TCP on remote runner",
              "real_provider_calls": 0, "owner_installation": False}
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
if report["failed"]:
    raise SystemExit(1)
