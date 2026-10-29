"""Freeze what the Hermes version of the plugin says on its screens, so the Agent Zero screens cannot drift.

A development tool (it is not shipped in the plugin zip). It READS the Hermes sources and writes
`tests/fixtures/hermes_1829_ui_strings.json` and `tests/fixtures/hermes_1829_panel_reads.json`; it never
touches a Hermes file.

    python tools/extract_hermes_ui_strings.py            # compare the fixtures with the Hermes sources
    python tools/extract_hermes_ui_strings.py --write    # rewrite both fixtures

What goes in the fixture:

  * `groups`   - the shelves of the settings page, from Hermes' `settings.py` `GROUPS` (id, title, note, keys).
  * `tabs`     - the tab labels in the order Hermes shows them, from `desktop/plugin.js`.
  * `strings`  - sentences and labels the Agent Zero page and chip carry word for word (the only change allowed
                 is the host's name, see `substitutions`). Each one is looked up in the Hermes source while the
                 fixture is written: a sentence Hermes has since changed or dropped stops the extraction.
  * `adapted`  - Hermes sentences that Agent Zero says in its own words because the framework differs; the
                 Agent Zero text is spelled out, with the reason.
  * `not_ported` - Hermes sentences Agent Zero does NOT say (it keeps its own 1.8.2.8 wording on that screen).
                 The parity test asserts they are still absent, so porting one forces this file to be updated.
  * `panel_reads` (second file) - Hermes' `PANEL_READS`, the snapshot paths its page reads.

The list of sentences is chosen by hand below: a screen is not a diff, and a sentence that names Hermes' own
config file has no Agent Zero twin. What the tool guarantees is that every sentence in the list is REAL
(present in the Hermes source today) and that the fixture is rebuilt from the source, not from memory.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                                     # .../agent-zero/current
FIXTURES = ROOT / "tests" / "fixtures"
HERMES = ROOT.parent.parent / "hermes" / "hermes-kame-api-rotation"

#: The host's name is the only difference allowed in a sentence both ports share.
SUBSTITUTIONS = [["Hermes", "Agent Zero"]]

PANEL = "kame-panel.html"
PANEL_STORE = "kame-panel-store.js"
VIEW = "kame-view.js"

#: (Hermes text, Agent Zero files that must carry it). Order is the order of the screens.
SAME = [
    ("Your models", [PANEL]),
    ("What KAME did", [PANEL]),
    ("Quick controls", [PANEL]),
    ("Getting started", [PANEL]),
    ("Nothing to report — every reply went straight out.", [PANEL]),
    ("All events →", [PANEL]),
    ("All settings →", [PANEL]),
    ("The three you may want to change often. Everything else is in Settings.", [PANEL]),
    ("Wake this model", [PANEL]),
    ("Try this model now", [PANEL]),
    ("Wake it", [PANEL]),
    ("Its keys start again as if never tried; a key that is really spent is refused again. No key is deleted.", [PANEL]),
    ("In the order of your fallback list: KAME starts every reply on 1, and goes down the list when a model is busy, slow or out of quota.", [PANEL_STORE]),
    ("One ring per provider and model. A key spent on one model keeps its allowance on another.", [PANEL_STORE]),
    ("Also used by background jobs", [PANEL]),
    ("Background jobs and your quota", [PANEL]),
    ("Titles, summaries and other side jobs spend the same per-model quota as your chats.", [PANEL]),
    ("Nothing wrong.", [VIEW]),
    ("Every model is busy — KAME keeps asking, your best first, until one answers.", [VIEW]),
    ("No model can answer right now — KAME waits for the first key to come back.", [VIEW]),
    ("Nothing is ready right now — see the models below.", [VIEW]),
    ("Answering now", [VIEW]),
    ("Everything works without changing anything here. Changes apply on the next message and survive restarts.", [PANEL]),
    ("KAME settings · all at their defaults", [PANEL_STORE]),
    ("Reset to defaults", [PANEL]),
    ("Clear pool", [PANEL]),
    ("Clear events", [PANEL]),
    ("Hide advanced settings", [PANEL]),
    ("Reset everything", [PANEL]),
    ("Clear the pool", [PANEL]),
    ("Settings that are already tuned or only one unusual setup needs. Hidden unless you changed them.", [PANEL]),
    ("Hide these switches", [PANEL]),
    ("Empties the event list on the Events page. Nothing else changes.", [PANEL]),
    ("Only clears rotation and quarantine; it does not delete any key.", [PANEL]),
    ("Last replies", [PANEL]),
    ("Right now", [PANEL]),
    ("This Hermes process", [PANEL]),
    ("Keys asked together", [PANEL]),
    ("Still waiting", [PANEL]),
    # what each key is called, and why (the ring legend and the key table)
    ("not used yet", [VIEW]),
    ("out for today", [VIEW]),
    ("busy — asked again soon", [VIEW]),
    ("no key used yet", [VIEW]),
    ("out of credit", [VIEW]),
    ("daily quota spent", [VIEW]),
    ("per-minute limit", [VIEW]),
    ("key revoked", [VIEW]),
    ("refused as a credential", [VIEW]),
    ("out of rotation — replace it", [VIEW]),
    ("refused — still being tried", [VIEW]),
    ("resting (thin)", [VIEW]),
    ("out for today (dotted)", [VIEW]),
    # the names of the decisions in "What KAME did"
    ("Not for this model", [VIEW]),
    ("Bad key", [VIEW]),
    ("Key benched", [VIEW]),
    ("Answered on retry", [VIEW]),
    ("Key rests", [VIEW]),
    ("Setting changed", [VIEW]),
    ("Provider outage", [VIEW]),
    ("Answer cut", [VIEW]),
    ("Next key", [VIEW]),
    ("All keys resting", [VIEW]),
    ("Passed to Hermes", [VIEW]),
    ("just now", [VIEW]),
]

#: (Hermes text, Agent Zero text, files, reason) - the framework forces a different sentence.
ADAPTED = [
    ("Keys are added or removed with /kame-keys (or in Hermes’ own key settings); this page never touches one.",
     "Keys are added or removed with", [PANEL],
     "Agent Zero keeps its keys in Settings > API keys, one comma-separated field per provider"),
    ("Titles, summaries and other side jobs spend the same per-model quota as your chats. Counts come from this profile.",
     "Titles, summaries and other side jobs spend the same per-model quota as your chats.", [PANEL],
     "Agent Zero has no profiles; the sentence stops before the Hermes-only clause"),
    ("Open Settings and paste several keys into one provider key field, separated by commas.",
     "paste several keys into one provider key field, separated by commas", [PANEL],
     "same advice, pointing at Agent Zero's own Settings > API keys"),
    ("What KAME can see", "Agent Zero, as KAME found it", [PANEL],
     "the card is the 1.8.2.8 'what the host looks like' card, named for the host"),
]

#: (Hermes text, reason) - sentences Agent Zero does not say. Asserted absent by the parity test.
NOT_PORTED = [
    ("Idle. A key is ready, so the next call goes straight out.",
     "Agent Zero's first sentence is built in kame-view.js nowSentences() with its own words"),
    ("Reset every setting?", "Agent Zero asks in one line with its own confirm text"),
    ("Clear the pool?", "Agent Zero asks in one line with its own confirm text"),
    ("Reading this list", "Events tab: Agent Zero keeps its 1.8.2.8 wording and its own legend"),
    ("Each word on this list, and what it means.", "Events tab: Agent Zero keeps its 1.8.2.8 wording"),
    ("KAME rotating", "Events tab filter names are Agent Zero's own"),
    ("providers refusing", "Events tab filter names are Agent Zero's own"),
    ("What went wrong", "Events tab filter names are Agent Zero's own"),
    ("Auxiliary model calls", "Background jobs tab: Agent Zero names its own jobs (kame_aux), not Hermes' auxiliary router"),
    ("Naming this chat", "Background jobs: Agent Zero names the job from its own call stack"),
    ("Waiting for the first snapshot…", "Agent Zero reads on demand, there is no snapshot file to wait for"),
    ("Reading the pool…", "Agent Zero reads on demand, there is no snapshot file to wait for"),
]

#: A shelf that exists only in the Agent Zero port, and where it sits (index in the final list).
AGENT_ZERO_ONLY_GROUPS = [{"id": "agentzero", "after": "tuning"}]
#: Hermes keys with no Agent Zero twin (framework difference).
HERMES_ONLY_KEYS = ["never_fall_back_to_another_model"]


# --- reading the Hermes sources ---------------------------------------------------------------

def js_literals(text: str):
    """`[(line, value)]` for every string literal of a JavaScript file (comments skipped)."""
    out, i, n, line = [], 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i)
            j = n - 2 if j < 0 else j
            line += text.count("\n", i, j + 2)
            i = j + 2
        elif c in "'\"`":
            start, j, buf = line, i + 1, []
            while j < n and text[j] != c:
                if text[j] == "\\" and j + 1 < n:
                    esc = text[j + 1]
                    if esc == "u" and re.match(r"[0-9a-fA-F]{4}", text[j + 2:j + 6]):
                        buf.append(chr(int(text[j + 2:j + 6], 16)))
                        j += 6
                        continue
                    buf.append({"n": "\n", "t": "\t"}.get(esc, esc))
                    j += 2
                    continue
                if text[j] == "\n":
                    line += 1
                buf.append(text[j])
                j += 1
            out.append((start, i, j + 1, "".join(buf)))
            i = j + 1
        else:
            i += 1
    return [(s, a, b, v) for s, a, b, v in out]


def js_sentences(text: str):
    """String literals, with literals joined by a plain `+` (or nothing) merged into one sentence."""
    literals = js_literals(text)
    merged, current = [], None
    for line, start, end, value in literals:
        if current is not None and re.fullmatch(r"\s*\+?\s*", text[current[2]:start]):
            current = (current[0], current[1], end, current[3] + value)
        else:
            if current is not None:
                merged.append(current)
            current = (line, start, end, value)
    if current is not None:
        merged.append(current)
    return [(line, value) for line, _s, _e, value in merged]


def read_groups(settings_py: Path):
    """Hermes `GROUPS` as `[{id, title, note, keys}]` (the Troubleshooting keys are all `*disable*` flags)."""
    tree = ast.parse(settings_py.read_text(encoding="utf-8"))
    names = {}
    groups_node = None
    for node in tree.body:
        target = value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, value = node.target.id, node.value
        if target is None:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            names[target] = value.value
        elif target == "GROUPS":
            groups_node = value
    if groups_node is None:
        raise SystemExit("settings.py: no GROUPS table found")

    def key_of(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return names.get(node.id, "*" + node.id.lower().replace("_", "-") + "*")
        raise SystemExit("settings.py: a GROUPS entry this tool cannot read")

    out = []
    for entry in groups_node.elts:
        gid, title, note, keys = entry.elts
        listed = [key_of(k) for k in keys.elts] if isinstance(keys, (ast.Tuple, ast.List)) else [key_of(keys)]
        out.append({"id": gid.value, "title": title.value, "note": note.value, "keys": listed})
    return out


def read_tabs(plugin_js: str):
    """The tab labels in the order Hermes lays them out."""
    found = re.findall(r"h\(Tab, \{[^}]*?label: (?:'([^']+)'|`([^`$]+)[`$])", plugin_js, re.S)
    labels = [a or b for a, b in found]
    # 'Events (3)' / 'Background jobs (2)': the count is a template part, the label is what is before it
    return [label.strip() for label in labels]


def read_panel_reads(plugin_js: str):
    match = re.search(r"const PANEL_READS = \[(.*?)\n\]", plugin_js, re.S)
    if not match:
        raise SystemExit("plugin.js: no PANEL_READS found")
    return re.findall(r"'([^']+)'", match.group(1))


# --- building the fixtures --------------------------------------------------------------------

def build(hermes: Path):
    plugin_js = (hermes / "desktop" / "plugin.js").read_text(encoding="utf-8")
    sentences = js_sentences(plugin_js)

    def locate(text):
        for line, value in sentences:
            if text in value:
                return line
        return None

    problems, strings, adapted, not_ported = [], [], [], []
    for text, files in SAME:
        line = locate(text)
        if line is None:
            problems.append(f"SAME: not in Hermes plugin.js any more: {text!r}")
        strings.append({"hermes": text, "files": files, "hermes_line": line})
    for text, a0, files, reason in ADAPTED:
        line = locate(text)
        if line is None:
            problems.append(f"ADAPTED: not in Hermes plugin.js any more: {text!r}")
        adapted.append({"hermes": text, "agent_zero": a0, "files": files, "hermes_line": line, "reason": reason})
    for text, reason in NOT_PORTED:
        line = locate(text)
        if line is None:
            problems.append(f"NOT_PORTED: not in Hermes plugin.js any more: {text!r}")
        not_ported.append({"hermes": text, "hermes_line": line, "reason": reason})

    tabs = read_tabs(plugin_js)
    if len(tabs) < 4:
        problems.append(f"tabs: read {tabs!r}, expected at least four")
    groups = read_groups(hermes / "settings.py")
    reads = sorted(set(read_panel_reads(plugin_js)))

    ui = {
        "source": "Hermes 1.8.2.9: desktop/plugin.js, settings.py (read-only, by tools/extract_hermes_ui_strings.py)",
        "substitutions": SUBSTITUTIONS,
        "tabs": {"hermes": tabs, "agent_zero_extra": [{"label": "Fallback models", "after": "Settings"}]},
        "groups": groups,
        "groups_hermes_only_keys": HERMES_ONLY_KEYS,
        "groups_agent_zero_only": AGENT_ZERO_ONLY_GROUPS,
        "strings": strings,
        "adapted": adapted,
        "not_ported": not_ported,
    }
    panel_reads = {"source": "Hermes 1.8.2.9 desktop/plugin.js PANEL_READS", "paths": reads}
    return ui, panel_reads, problems


def dump(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="rewrite the fixtures")
    parser.add_argument("--hermes", default=str(HERMES), help="folder of the Hermes plugin source")
    args = parser.parse_args(argv)

    hermes = Path(args.hermes)
    if not (hermes / "desktop" / "plugin.js").is_file():
        print(f"no Hermes source at {hermes}")
        return 2
    ui, reads, problems = build(hermes)
    for problem in problems:
        print("PROBLEM:", problem)
    if problems:
        return 1

    targets = {FIXTURES / "hermes_1829_ui_strings.json": dump(ui),
               FIXTURES / "hermes_1829_panel_reads.json": dump(reads)}
    stale = 0
    for path, content in targets.items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current == content:
            print(f"up to date : {path.name}")
        elif args.write:
            FIXTURES.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            print(f"written    : {path.name}")
        else:
            stale += 1
            print(f"DIFFERS    : {path.name} (run with --write to refresh it)")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
