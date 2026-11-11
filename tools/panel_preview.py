"""See the Agent Zero rotation page the way a person does, without running Agent Zero.

The page is the real `webui/kame-panel.html` and the real stores, under Agent Zero's own Alpine and
`AlpineStore.js` taken from a checkout. Only `/js/api.js` is replaced: it answers from a fixture built by
the real status handler over the real engine, so what is on screen is what that state would show.

    python tools/panel_preview.py --a0 <agent-zero checkout> --scenario owner --out panel.png
    python tools/panel_preview.py --a0 <checkout> --scenario all --out-dir shots --width 1100 --tab overview

Scenarios: owner, first-run, one-key, all-resting, dead-keys. `--tab` opens overview, settings, fallback or events.
Writes nothing inside the plugin. Needs Google Chrome for the screenshot.
"""
from __future__ import annotations

import importlib
import argparse
import http.server
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
TESTS = PLUGIN / "tests"
CHROME = [r"C:\Program Files\Google\Chrome\Application\chrome.exe", "/usr/bin/google-chrome", "/usr/bin/chromium"]

PAGE = """<!doctype html><html class="dark-mode"><head><meta charset="utf-8"><title>KAME panel preview</title>
<link rel="stylesheet" href="/index.css"><style>body{{background:#131313;color:#fff;padding:18px;overflow:auto}}</style>
<script type="module">
import "/plugins/api_rotation_by_kame/webui/kame-panel-store.js";
const start = async () => {{
  const html = await (await fetch("/plugins/api_rotation_by_kame/webui/kame-panel.html")).text();
  const doc = new DOMParser().parseFromString(html, "text/html");
  const host = document.getElementById("host");
  for (const s of doc.querySelectorAll("style")) document.head.appendChild(s);
  host.innerHTML = doc.body.innerHTML;
  await import("/vendor/alpine/alpine.min.js");
  const A = globalThis.Alpine;
  A.directive("create", (el, {{expression}}, {{evaluateLater}}) => evaluateLater(expression)());
  A.directive("destroy", () => {{}});
  A.start();
  setTimeout(async () => {{
    const s = A.store("kamePanel");
    if (!s) return;
    if (!s.data) await s.onOpen();
    s.tab = "{tab}";
  }}, 300);
}};
start();
</script></head><body><div id="host"></div></body></html>"""

API = """const fixture = {fixture};
export async function callJsonApi(path, body) {{
  if (path.endsWith("kame_status")) return fixture;
  return {{ ok: true }};
}}
export async function fetchApi() {{ return new Response("{{}}"); }}
"""


def build_fixture(scenario: str) -> dict:
    """The report the page fetches, from the real handler over a seeded engine."""
    sys.path[:0] = [str(TESTS), str(PLUGIN)]
    os.environ.setdefault("KAME_RECORDER_DISABLED", "1")
    importlib.import_module("test_v1_8_1_1_reliability")  # stands in for Agent Zero's modules
    stub = importlib.import_module("kame_api_stub")
    import kame_engine as K
    import kame_settings as S

    K.set_log_level("silent")
    gemini = [f"AIzaSyPREVIEW-gem-{n:02d}-000000000000" for n in range(14)]
    models = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-pro", "gemini-2.5-flash"]
    if scenario == "owner":
        for model in models:
            K._get_identity_state(f"gemini:{model}", gemini)
        for key in gemini[:6]:
            K._mark_key_health("gemini:gemini-3.6-flash", key, False, 3600, "daily quota")
        K._KAME_PRIMARY["identity"] = "gemini:gemini-3.6-flash"
    elif scenario == "one-key":
        K._get_identity_state("anthropic:claude-opus-5-5", ["sk-ant-PREVIEW-one-000000000"])
        K._KAME_PRIMARY["identity"] = "anthropic:claude-opus-5-5"
    elif scenario == "all-resting":
        for model in models:
            K._get_identity_state(f"gemini:{model}", gemini)
            for key in gemini:
                K._mark_key_health(f"gemini:{model}", key, False, 70, "rate limit")
        K._KAME_PRIMARY["identity"] = "gemini:gemini-3.6-flash"
    elif scenario == "dead-keys":
        K._get_identity_state("openai:gpt-6", ["sk-PREVIEW-dead-0000000000", "sk-PREVIEW-good-0000000000"])
        K._mark_key_health("openai:gpt-6", "sk-PREVIEW-dead-0000000000", False, 86400, "insufficient_quota")
        K._KAME_PRIMARY["identity"] = "openai:gpt-6"
    status = stub.load_api("kame_status", K, S)
    out = stub.call(status.KameStatus, {"panel": True})
    out["settings"] = {name: S.default(name) for name in S.SETTINGS}
    out["active"] = True
    return json.loads(json.dumps(out, default=str))


def serve(a0: Path, fixture: dict, tab: str) -> http.server.ThreadingHTTPServer:
    api = API.format(fixture=json.dumps(fixture))
    page = PAGE.format(tab=tab)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            body = None
            if path in ("/", "/index.html"):
                body, kind = page.encode(), "text/html"
            elif path == "/js/api.js":
                body, kind = api.encode(), "text/javascript"
            else:
                if path.startswith("/plugins/api_rotation_by_kame/"):
                    file = PLUGIN / path[len("/plugins/api_rotation_by_kame/"):]
                else:
                    file = a0 / "webui" / path.lstrip("/")
                if file.is_file():
                    body = file.read_bytes()
                    kind = "text/javascript" if file.suffix in (".js", ".mjs") else (mimetypes.guess_type(file.name)[0] or "text/plain")
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def shoot(url: str, out: Path, width: int, height: int) -> None:
    chrome = next((c for c in CHROME if Path(c).exists()), shutil.which("chrome"))
    if not chrome:
        raise SystemExit("Google Chrome was not found")
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={profile}",
                        f"--window-size={width},{height}", "--virtual-time-budget=6000", "--hide-scrollbars",
                        f"--screenshot={out}", url], capture_output=True, timeout=90)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--a0", required=True, type=Path, help="Agent Zero checkout, for its webui")
    ap.add_argument("--scenario", default="owner")
    ap.add_argument("--tab", default="overview")
    ap.add_argument("--out", type=Path, default=Path("panel.png"))
    ap.add_argument("--width", type=int, default=1100)
    ap.add_argument("--height", type=int, default=1500)
    ap.add_argument("--dump", type=Path, help="also write the fixture as JSON")
    a = ap.parse_args()
    fixture = build_fixture(a.scenario)
    if a.dump:
        a.dump.write_text(json.dumps(fixture, indent=1), encoding="utf-8")
    server = serve(a.a0, fixture, a.tab)
    try:
        shoot(f"http://127.0.0.1:{server.server_address[1]}/", a.out.resolve(), a.width, a.height)
    finally:
        server.shutdown()
    print(a.out)


if __name__ == "__main__":
    main()
