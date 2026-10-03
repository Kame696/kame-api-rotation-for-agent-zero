"""Remote-only native Agent Zero + real SDK/TCP acceptance; no provider keys."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if os.environ.get("GITHUB_ACTIONS") != "true":
    raise SystemExit("Native acceptance runs only on the remote GitHub runner.")

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
ROOT = Path(__file__).resolve().parents[1]
A0 = Path(sys.argv[1]).resolve()
REPORT = Path(sys.argv[2]).resolve()
os.environ["KAME_DATA_DIR"] = tempfile.mkdtemp(prefix="kame-native-1818-")
# Reuse the full existing host suite. A failing old assertion stops this run.
g = runpy.run_path(str(ROOT / "tests" / "test_a0_compat.py"))
K, models = g["kame"], g["models"]
K.remove_kame_patch()
checks, requests = [], []
request_lock = threading.Lock()
cancel_seen = threading.Event()


def check(name, condition, detail=""):
    checks.append({"name": name, "pass": bool(condition), "detail": str(detail)[:500]})
    print(("PASS " if condition else "FAIL ") + name, flush=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        key = self.headers.get("Authorization", "")
        case = next((m["content"] for m in reversed(body["messages"])
                     if m.get("role") == "user"), "")
        with request_lock:
            requests.append({"case": case, "key": key, "body": body})
        if case == "cancel":
            cancel_seen.set()
            time.sleep(1)
        if case == "quota" and key.endswith("FIXTURE-A"):
            data = json.dumps({"error": {"message": "fixture rate limit", "type": "rate_limit_error",
                                         "code": "rate_limit_exceeded"}}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "1")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        tool = '{"tool_name":"response","tool_args":{"text":"complete answer"}}'
        answer = tool if case == "tool" else "Hello " + case
        cut = case in ("partial", "tool") and key.endswith("FIXTURE-A")
        content = answer[:19] if cut else answer
        chunk = {"id": "chatcmpl-fixture", "object": "chat.completion.chunk", "created": 1,
                 "model": "gpt-4o-mini", "choices": [{"index": 0, "delta": {"content": content},
                 "finish_reason": None if cut else "stop"}]}
        payload = ("data: " + json.dumps(chunk) + "\n\n").encode()
        if not cut:
            payload += b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        # Truncated HTTP framing forces a real SDK read failure, not a mock exception.
        self.send_header("Content-Length", str(len(payload) + (4096 if cut else 0)))
        self.end_headers()
        try:
            self.wfile.write(payload)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


server = Server(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = f"http://127.0.0.1:{server.server_port}/v1"


def reset():
    for name in ("_KAME_KEY_HEALTH", "_KAME_ACCOUNT_HOLDS", "_KAME_STATED_RL",
                 "_KAME_NO_ANSWER_SINCE", "_KAME_STORM"):
        getattr(K, name).clear()
    K.set_log_level("silent")
    K.set_rotation_disabled(False)
    K._get_all_api_keys = lambda self: ["FIXTURE-A", "FIXTURE-B"]
    if K._KJ:
        K._KJ._reset_for_tests()
        K._KJ.SHARE_HEALTH_ON = False
    requests.clear()


async def call(case, callback=None):
    wrapper = models.LiteLLMChatWrapper(model="gpt-4o-mini", provider="openai")
    return await asyncio.wait_for(wrapper.unified_call(
        system_message="Keep this instruction exactly", user_message=case,
        response_callback=callback or (lambda delta, full: asyncio.sleep(0)),
        api_base=base, api_key="FIXTURE-A", temperature=0,
        a0_api_mode="chat_completions", a0_retry_attempts=0,
        a0_retry_delay=0, num_retries=0, timeout=5), 20)


async def wire_checks():
    for case in ("quota", "partial", "tool"):
        reset()
        snapshots, parsed = [], []
        async def on_text(delta, full):
            snapshots.append(full)
            if case == "tool":
                try:
                    item = json.loads(full)
                except (ValueError, TypeError):
                    return None
                parsed.append(item)
                return True  # Native early-stop; no fabricated completion.
        result = await call(case, on_text)
        check(case + ": two wire attempts, one rotation", len(requests) == 2, requests)
        check(case + ": different key selected", [r["key"] for r in requests] ==
              ["Bearer FIXTURE-A", "Bearer FIXTURE-B"])
        check(case + ": original messages preserved", len(requests) == 2 and
              requests[0]["body"]["messages"] == requests[1]["body"]["messages"])
        check(case + ": model and quality retained", all(r["body"]["model"] == "gpt-4o-mini"
              and r["body"].get("temperature") == 0 for r in requests))
        if case == "tool":
            expected = {"tool_name": "response", "tool_args": {"text": "complete answer"}}
            check("tool: one complete original tool request", parsed == [expected], parsed)
            # The existing suite invokes the genuine host ResponseTool with its
            # minimal context. Only the complete request is allowed to reach it.
            executed = []
            for item in parsed:
                tool = g["ResponseTool"](agent=None, name="response", method=None,
                    args=item["tool_args"], message="", loop_data=None)
                response = await tool.execute()
                check("tool: genuine host ResponseTool returns the answer",
                      response.message == "complete answer")
                executed.append(item)
            check("tool: incomplete request never executes", executed == [expected])
        else:
            check(case + ": native final answer exact", result[0] == "Hello " + case, result)
            check(case + ": final displayed snapshot exact", snapshots[-1] == result[0], snapshots)

    reset()
    K.set_rotation_disabled(True)
    try:
        await call("quota")
    except Exception as exc:
        check("negative control: disabling rotation exposes 429", "429" in str(exc) or
              getattr(exc, "status_code", None) == 429, type(exc).__name__)
    else:
        check("negative control: disabling rotation exposes 429", False)
    finally:
        K.set_rotation_disabled(False)

    reset()
    cancel_seen.clear()
    task = asyncio.create_task(call("cancel"))
    for _ in range(200):
        if cancel_seen.is_set():
            break
        await asyncio.sleep(.01)
    check("cancel: request reached the actual SDK/server", cancel_seen.is_set())
    started = time.monotonic()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        check("cancel: propagates promptly without rotating", time.monotonic() - started < 2
              and len(requests) == 1)
    else:
        check("cancel: propagates promptly without rotating", False)


try:
    originals = tuple(getattr(g["RateLimiter"], n) for n in ("__init__", "cleanup", "get_total"))
    for _ in range(20):
        assert K.apply_kame_patch()
        assert K.remove_kame_patch()
    check("native lifecycle: 20 cycles restore limiter methods",
          originals == tuple(getattr(g["RateLimiter"], n) for n in ("__init__", "cleanup", "get_total")))
    K.apply_kame_patch()
    asyncio.run(wire_checks())
except Exception as exc:
    check("native harness completed", False, f"{type(exc).__name__}: {exc}")
finally:
    K.remove_kame_patch()
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
