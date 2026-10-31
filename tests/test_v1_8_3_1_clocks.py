"""1.8.3.1 (A4 / D4) - the same clocks as the Hermes panel.

The API Rotation page reads the status every second (it was 3 s); the chip reads it every second while its
popover is open or a chat is running and every 5 s otherwise (plus the push Agent Zero sends with every
snapshot); and "Wait for the first token: Off" is 120 s of silence, the wait Hermes means by it, written as a
named constant beside the setting. The chip store runs in node with the host's timers replaced by a recorder.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS, clean, make_ctx, streaming  # noqa: F401
import kame_engine as K

ROOT = Path(__file__).resolve().parent.parent
WEBUI = ROOT / "webui"
REGISTER = (ROOT / "tests" / "js" / "register.mjs").as_uri()
NODE = shutil.which("node")


# -- the page -----------------------------------------------------------------------------------------

def test_the_page_reads_the_status_every_second():
    source = (WEBUI / "kame-panel-store.js").read_text(encoding="utf-8")
    assert re.search(r"const REFRESH_MS = 1000;", source)
    assert "const REFRESH_MS = 3000" not in source


# -- the chip -----------------------------------------------------------------------------------------

def run_chip(body: str, running: bool = False):
    program = (
        "globalThis.__calls = []; globalThis.__intervals = [];\n"
        "globalThis.setInterval = (fn, ms) => { globalThis.__intervals.push({fn, ms}); return globalThis.__intervals.length; };\n"
        "globalThis.clearInterval = () => {};\n"
        f"const running = {json.dumps(running)};\n"
        "globalThis.Alpine = { store: (name) => name === 'chats' ? { selectedContext: { id: 'c1', running } } : undefined };\n"
        "globalThis.__responses = { '/plugins/api_rotation_by_kame/kame_status': { available: true, active: true, view: {} } };\n"
        'const { store } = await import("/plugins/api_rotation_by_kame/webui/kame-rotation-store.js");\n'
        "const intervals = globalThis.__intervals;\n"
        "const reads = () => globalThis.__calls.filter(c => c[0].endsWith('kame_status')).length;\n"
        f"const out = await (async () => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out)); process.exit(0);\n"
    )
    done = subprocess.run([NODE, "--import", REGISTER, "--input-type=module", "-e", program],
                          input="", capture_output=True, text=True, encoding="utf-8", timeout=60, env=dict(os.environ))
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


TICKS = """
await store.onMount(() => {});
const tick = intervals.find(i => i.ms === 1000);
const start = reads();
%s
return { reads: reads() - start, ms: intervals.map(i => i.ms) };
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_idle_chip_reads_every_fifth_second():
    out = run_chip(TICKS % "for (let i = 0; i < 10; i++) { tick.fn(); await new Promise(r => setTimeout(r, 0)); }")
    assert out["reads"] == 2 and out["ms"] == [1000]            # one clock only: no 15 s safety net any more


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_chip_with_its_popover_open_reads_every_second():
    out = run_chip(TICKS % "store.open = true; for (let i = 0; i < 4; i++) { tick.fn(); await new Promise(r => setTimeout(r, 0)); }")
    assert out["reads"] == 4


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_chip_beside_a_running_chat_reads_every_second():
    out = run_chip(TICKS % "for (let i = 0; i < 4; i++) { tick.fn(); await new Promise(r => setTimeout(r, 0)); }", running=True)
    assert out["reads"] == 4


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_clock_of_the_popover_moves_every_second():
    out = run_chip(TICKS.replace("return {", "const first = store.now; await new Promise(r => setTimeout(r, 5)); tick.fn(); return { moved: store.now > first,") % "")
    assert out["moved"] is True


def test_the_push_of_every_snapshot_is_still_there():
    push = (ROOT / "extensions" / "webui" / "apply_snapshot_before" / "refresh-kame-rotation.js").read_text(encoding="utf-8")
    assert "kameStore.refresh()" in push


# -- the first-token wait ------------------------------------------------------------------------------

def test_off_is_120_seconds_named_beside_the_setting():
    assert K._KAME_FIRST_TOKEN_OFF_S == 120.0
    K.set_first_token_wait("off")
    ctx, _ = make_ctx(streaming({}))
    assert K._kame_silence_for(None, ctx, 1) == 120.0
    assert K._kame_silence_for(None, ctx, 3) == 120.0           # flat: Off does not tighten after a failure


def test_off_leaves_a_local_model_and_a_background_stream_alone():
    K.set_first_token_wait("off")
    ctx, _ = make_ctx(streaming({}))
    ctx["kwargs"] = {"api_base": "http://localhost:11434"}
    assert K._kame_silence_for(None, ctx, 1) == 0.0
    background, _ = make_ctx(streaming({}))
    background["main_chat"] = False
    assert K._kame_silence_for(None, background, 1) == 0.0
    utility, _ = make_ctx(streaming({}), call_type="Utility")
    assert K._kame_silence_for(None, utility, 1) == 0.0


def test_a_timed_wait_of_zero_is_off_and_means_the_same():
    K.set_first_token_wait("timed")
    K.set_stream_silence_timeout(0)
    ctx, _ = make_ctx(streaming({}))
    assert K._kame_silence_for(None, ctx, 1) == 120.0


def test_the_words_of_the_setting_say_120_seconds_everywhere_they_are_written():
    words = {
        "settings": (ROOT / "kame_settings.py").read_text(encoding="utf-8"),
        "screen": (WEBUI / "config.html").read_text(encoding="utf-8"),
        "yaml": (ROOT / "default_config.yaml").read_text(encoding="utf-8"),
    }
    for name, text in words.items():
        assert "Off: no limit" not in text and "off: no limit" not in text, name
        assert "120 s" in text, name
