"""1.9.0.6 - the panel's round "i" opens "How to install or configure" (parity with the Hermes page)."""
from __future__ import annotations

import re
from pathlib import Path

PANEL = (Path(__file__).resolve().parents[1] / "webui" / "kame-panel.html").read_text(encoding="utf-8")


def test_the_round_i_is_in_the_tab_bar_and_opens_the_guide():
    tabs = PANEL[PANEL.index('<div class="kame-tabs">'):]
    tabs = tabs[:tabs.index("</div>")]
    assert re.search(r'class="kame-info-tab".*?aria-label="How to install or configure".*?>i</button>', tabs, re.S)
    assert "$store.kamePanel.tab = 'guide'" in tabs
    assert "x-show=\"$store.kamePanel.tab === 'guide'\"" in PANEL
    assert ".kame-info-tab {" in PANEL and "border-radius: 50%" in PANEL


def test_the_guide_says_the_five_steps_and_never_try_again():
    start = PANEL.index("<!-- ===== How to install or configure (1.9.0.6) ===== -->")
    guide = PANEL[start:PANEL.index("<!-- ===== Overview ===== -->")]
    for want in ("1 · Install", "2 · Add your keys", "separated by commas", "/kame-keys add",
                 "3 · Add more models (optional)", "it waits until one is back and answers",
                 "4 · Watch it (optional)", "5 · Choose how it behaves (optional)", "/kame doctor"):
        assert want in guide, want
    assert "try again" not in guide.lower()
    assert "KAME API Rotation" not in guide
