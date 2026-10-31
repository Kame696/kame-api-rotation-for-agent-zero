"""1.8.2.9 (U1, U2, U3) - the shape of the API Rotation page and of the chip, checked in the files.

The screens themselves are looked at on the test server (PLAN section 5, item 7). What can be checked
offline is checked here: the menu and the page are called "API Rotation", the tabs and the Overview come
in Hermes' order with its words, nothing of the old toast or the old names is left, the markup is
balanced, and both screens read the one shared view module.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEBUI = ROOT / "webui"
PANEL = (WEBUI / "kame-panel.html").read_text(encoding="utf-8")
STORE = (WEBUI / "kame-panel-store.js").read_text(encoding="utf-8")
CHIP = (ROOT / "extensions" / "webui" / "model-context-strip-end" / "kame-rotation.html").read_text(encoding="utf-8")
CHIP_STORE = (WEBUI / "kame-rotation-store.js").read_text(encoding="utf-8")
ENTRY = (ROOT / "extensions" / "webui" / "sidebar-quick-actions-dropdown-start" / "kame-entry.html").read_text(encoding="utf-8")
MAIN = (WEBUI / "main.html").read_text(encoding="utf-8")

VOID = {"br", "hr", "img", "input", "meta", "link", "col", "area", "base", "source", "wbr"}


class Balance(HTMLParser):
    """Every opened element is closed, in order (void elements and the contents of style/script aside)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closes {self.stack[-1] if self.stack else 'nothing'} (line {self.getpos()[0]})")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def balanced(text: str):
    # the page and the chip are fragments of a document: a missing <html>/<head>/<body> is fine
    parser = Balance()
    parser.feed(text)
    leftover = [t for t in parser.stack if t not in ("html", "head", "body")]
    return parser.errors, leftover


def test_the_markup_of_the_page_and_the_chip_is_balanced():
    for name, text in (("kame-panel.html", PANEL), ("kame-rotation.html", CHIP), ("kame-entry.html", ENTRY), ("main.html", MAIN)):
        errors, leftover = balanced(text)
        assert not errors and not leftover, f"{name}: {errors} {leftover}"


def test_the_menu_entry_and_the_page_are_called_api_rotation():
    assert "<span>API Rotation</span>" in ENTRY and 'title="API Rotation"' in ENTRY
    assert "<title>API Rotation</title>" in MAIN
    assert re.search(r'<div class="kame-title">API Rotation</div>', PANEL)
    assert re.search(r'<div class="kame-sub">KAME <span x-text="\$store.kamePanel.version">', PANEL)
    # the plugin id, the folder and the dialog path do not change
    assert "/plugins/api_rotation_by_kame/webui/main.html" in ENTRY
    assert "<span>KAME</span>" not in ENTRY


def test_nothing_of_the_old_names_or_the_old_toast_is_left_on_a_screen():
    everything = {"panel": PANEL, "store": STORE, "chip": CHIP, "chip store": CHIP_STORE, "entry": ENTRY, "main": MAIN,
                  "config": (WEBUI / "config.html").read_text(encoding="utf-8")}
    for name, text in everything.items():
        for old in ("KAME - API key rotation", "Open the KAME panel", "Fastest", "kame-rotation-wait", "Key rotation\n"):
            assert old not in text, f"{name} still says {old!r}"
    assert "toast" not in PANEL.lower() and "toast" not in CHIP_STORE.lower()


def test_the_tabs_are_hermes_tabs_plus_the_fallback_tab_after_settings():
    tabs = re.search(r"x-for=\"t in \[(.*?)\]\" :key", PANEL, re.S).group(1)
    assert re.findall(r"\['(\w+)','([^']+)'\]", tabs) == [
        ("overview", "Overview"), ("settings", "Settings"), ("fallback", "Fallback models"),
        ("events", "Events"), ("background", "Background jobs")]


def test_the_overview_reads_top_to_bottom_in_hermes_order():
    overview = PANEL.split("<!-- ===== Overview ===== -->", 1)[1].split("<!-- ===== Settings ===== -->", 1)[0]
    order = ["Getting started", 'class="kame-now"', "Your models", "What KAME did", "Quick controls", 'class="kame-details"']
    at = [overview.index(word) for word in order]
    assert at == sorted(at), dict(zip(order, at))
    # the three sentences are the first things of the page that are not first-run help
    assert overview.count('class="kame-now-') >= 3


def test_hermes_words_are_on_the_page():
    words = [
        "The three you may want to change often. Everything else is in Settings.",
        "Nothing to report &mdash; every reply went straight out.",
        "All events &rarr;", "All settings &rarr;",
        "Wake this model", "Try this model now", "Wake it", "Try it",
        "Its keys start again as if never tried; a key that is really spent is refused again. No key is deleted.",
        "Also used by background jobs",
        "Edit your fallback models &rarr;",
        "Background jobs and your quota",
        "Titles, summaries and other side jobs spend the same per-model quota as your chats.",
        "Show advanced settings", "Hide advanced settings", "Reset to defaults", "Clear pool", "Clear events",
        "Reset everything", "Clear the pool",
        "Hide these switches", "Show the ", "Keys are added or removed with",
    ]
    for word in words:
        assert word in PANEL, word
    assert "In the order of your fallback list: KAME starts every reply on 1" in STORE
    assert "One ring per provider and model. A key spent on one model keeps its allowance on another." in STORE
    assert "KAME settings · all at their defaults" in STORE


def test_the_old_cards_are_folded_under_details_and_the_thin_bar_is_gone():
    assert "detailsOpen: false" in STORE
    details = PANEL.split('<section class="kame-details">', 1)[1].split("</section>", 1)[0]
    for card in ("Right now", "Background jobs and your quota", "Your API keys", "Agent Zero, as KAME found it",
                 "This Agent Zero process", "Each model's keys", "processFields"):
        assert card in details, card
    # the counters of this process: Hermes' list, drawn from the store
    for label in ("Fallbacks", "Keys asked together", "Backup keys started"):
        assert label in STORE, label
    assert "kame-meter" not in PANEL and "kame-rot-meter" not in CHIP
    # 1.8.3.0 (A7): as on Hermes, these two live on the Events page
    assert "Last replies" not in details and "Still waiting" not in details
    events = PANEL.split("<!-- ===== Events ===== -->", 1)[1].split("<!-- ===== Background jobs ===== -->", 1)[0]
    assert "Last replies" in events and "Still waiting" in events


def test_models_sharing_one_key_are_one_row_in_the_page_and_the_chip():
    assert "keyGroups" in PANEL and "modelsLine" in PANEL
    # 1.8.3.1 (D3): the per-key blocks left the chip's popover (they stay on the page); a model's row there
    # is a ring, its name and ONE status, and a reply is a sentence that wraps
    assert "view.providers" not in CHIP and "modelsLine" not in CHIP and "Last decisions" not in CHIP
    assert "overflow-wrap: anywhere" in CHIP.split(".kame-rot-rline", 1)[1].split("}", 1)[0], "a reply is never cut"


def test_the_chip_opens_the_page_and_shows_one_count():
    assert "Open API Rotation" in CHIP and "ensureModalOpen('/plugins/api_rotation_by_kame/webui/main.html')" in CHIP
    assert "p.keysLine" in CHIP and "view.tip" in CHIP and "x-html=\"$store.kameRotation.view.ring\"" in CHIP


def test_the_page_and_the_chip_read_the_same_view_module():
    for name, text in (("page store", STORE), ("chip store", CHIP_STORE)):
        assert "/plugins/api_rotation_by_kame/webui/kame-view.js" in text, name
    assert "chipSummary" in CHIP_STORE and "nowSentences" in STORE
    # neither store builds a count of its own
    for name, text in (("page store", STORE), ("chip store", CHIP_STORE)):
        assert "totals.keys" not in text.replace("t.keys", "") or name == "page store"
        assert "ringPercent" not in text, name


def test_the_chip_asks_for_the_chat_it_is_open_on_and_the_page_asks_with_the_panel_flag():
    assert "context_id" in CHIP_STORE
    assert "{ panel: true }" in STORE


def test_the_new_files_are_in_the_integrity_list():
    integrity = (ROOT / "integrity.py").read_text(encoding="utf-8")
    for rel in ("webui/kame-view.js", "api/kame_fallback_set.py", "api/kame_pool_action.py",
                "webui/kame-panel.html", "webui/kame-panel-store.js", "webui/kame-rotation-store.js"):
        assert f'"{rel}"' in integrity, rel
        assert (ROOT / rel).is_file(), rel


def test_the_unit_files_are_not_in_the_plugin_zip_by_accident():
    package = (ROOT / "tools" / "package.py").read_text(encoding="utf-8")
    assert '"webui"' in package and '"api"' in package          # the whole trees go in, so the new files do
