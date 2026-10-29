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
SUBSTITUTIONS = [["Hermes’", "Agent Zero’s"], ["Hermes'", "Agent Zero's"], ["Hermes", "Agent Zero"]]

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
    ("Keys asked together", [PANEL_STORE]),
    ("Still waiting", [PANEL]),
    # 1.8.3.0 (A6): the settings row, word for word
    ("Power: how much this changes what you notice. High = every conversation, Medium = one situation, Low = fine-tuning.", [PANEL]),
    ("When to change it: ", [PANEL]),
    ("Reset every setting?", [PANEL]),
    ("Clear the pool?", [PANEL]),
    ("Every key starts again as if it had never been tried: cooldowns, quarantines and the request history are forgotten. This does not delete any key, and a key that is genuinely spent will simply be refused again.", [PANEL]),
    ("Turn it on", [PANEL]),
    ("This KAME reports no settings.", [PANEL]),
    ("Not sorted into any of the groups above.", [PANEL_STORE]),
    ("empty = not used", [PANEL]),
    ("Saving…", [PANEL]),
    ("Nothing anywhere mentions this one, so the built-in default is in force.", [PANEL_STORE]),
    ("is set in the environment, which outranks everything else.", [PANEL_STORE]),
    ("This one counts whole things, so it takes a whole number.", [PANEL_STORE]),
    ("anything shorter fires while a healthy provider is still connecting.", [PANEL_STORE]),
    ("This is the switch people reach for when they suspect KAME of breaking something — it is reversible, and nothing is uninstalled.", [PANEL_STORE]),
    ("Show the ", [PANEL]),
    # 1.8.3.0 (A7, A8): the Events and Background jobs pages
    ('Reading this list', [PANEL]),
    ('The background calls Hermes makes on its own', [PANEL]),
    ('Each word on this list, and what it means.', [PANEL]),
    ('KAME rotating', [PANEL_STORE]),
    ('providers refusing', [PANEL_STORE]),
    ('Rotations, recoveries, continued answers and waits — the plugin doing its job.', [PANEL_STORE]),
    ('What the providers said. A refusal here is not a fault in KAME; it is what KAME is for.', [PANEL_STORE]),
    ('Everything in the buffer, newest first.', [PANEL_STORE]),
    ('Every background call in the buffer, newest first.', [PANEL]),
    ('all tasks', [PANEL]),
    ('What went wrong', [PANEL]),
    ('What the provider actually sent, with anything key-shaped removed before it was stored.', [PANEL]),
    ('Click to see what the provider actually said.', [PANEL_STORE]),
    ('see the error', [PANEL]),
    ('Keys appear as fingerprints: a hash, never a prefix of the key itself. Provider error text is scrubbed before it is written down, because a provider can quote your own prompt back inside an error.', [PANEL]),
    ('The model that wrote each of the last replies of your conversation (each step of a reply that uses tools is a reply of its own), the time to its first word and in total. A model from your fallback list says which model it answered for.', [PANEL]),
    ('Nothing of that kind yet.', [PANEL_STORE]),
    ('Nothing recorded yet. Every rotation, refusal, cut answer and continuation appears here as it happens.', [PANEL_STORE]),
    ('KAME is running and has not been asked to route a call yet in this process.', [PANEL_STORE]),
    ('An empty list above means nothing failed.', [PANEL_STORE]),
    ('Everything below is what it last knew, not what is happening now.', [PANEL_STORE]),
    ('KAME keeps trying, as configured.', [PANEL_STORE]),
    ('Auxiliary model calls', [PANEL]),
    ('Click a task to see only its calls.', [PANEL]),
    ('They share the same keys and the same pools as the chat (one pool per provider and model), so this is only a separate view, never a separate rotation.', [PANEL]),
    ('Naming this chat', [PANEL_STORE]),
    ('Shrinking a long conversation', [PANEL_STORE]),
    ('The key this call went out on answered, first time.', [PANEL_STORE]),
    ('The provider refused this key for THIS model only', [PANEL_STORE]),
    ('The provider said this is not a valid credential. Replace it — waiting will not repair one.', [PANEL_STORE]),
    ('This key was rested for a minute or more before it will be offered again.', [PANEL_STORE]),
    ('A key answered after KAME had already rotated at least once. The rotation worked.', [PANEL_STORE]),
    ('This key was rested briefly and the next call went somewhere else.', [PANEL_STORE]),
    ('An answer that was cut short was continued on another key and joined back up.', [PANEL_STORE]),
    ('A lot of keys refused at once, which is usually the provider rather than your keys.', [PANEL_STORE]),
    ('The provider stopped in the middle of an answer.', [PANEL_STORE]),
    ('Your model could not answer now — its provider said it is busy (overloaded, or timing out on several keys), it was far slower than usual, or every key was resting — so this reply fell back to the next model of your fallback list. The next reply starts on your best model again.', [PANEL_STORE]),
    ('The previous key did not answer, so KAME asked this one instead — or a slow reply got a backup key racing it.', [PANEL_STORE]),
    ('Every key was resting, so KAME waited instead of spending a request it knew would be refused.', [PANEL_STORE]),
    ('the next request uses another key', [PANEL_STORE]),
    ('the key sits out, the others carry on', [PANEL_STORE]),
    ('out of rotation until you replace it (/kame-keys)', [PANEL_STORE]),
    ('keys keep rotating; repeats are counted, not listed', [PANEL_STORE]),
    ('continuing on another key from where it stopped', [PANEL_STORE]),
    ('shown to you as one answer', [PANEL_STORE]),
    ('no request is sent until a key is back', [PANEL_STORE]),
    ('the next model of your list answers this reply', [PANEL_STORE]),
    ('the provider rejected the request itself', [PANEL_STORE]),
    ('a rate limit with no quota number', [PANEL_STORE]),
    ('The provider said how long. This wait is its number, not ours.', [PANEL_STORE]),
    ('Nothing in the response said how long. This wait is a fallback.', [PANEL_STORE]),
    # 1.8.3.0 (A9): the header, the notes, Quick controls, the process card, the footer and the credit
    ('Fingerprint of the source actually installed. Compare it with the build you deployed.', [PANEL]),
    ('to replace', [PANEL_STORE]),
    ('not rotating', [PANEL_STORE]),
    ('reading ', [PANEL_STORE]),
    ('live', [PANEL_STORE]),
    ('Reading the pool…', [PANEL]),
    ('This install is incomplete — KAME is not rotating.', [PANEL_STORE]),
    ('Re-deploy the whole plugin directory. A copy that does not recurse drops core/ ', [PANEL_STORE]),
    ('and leaves a plugin that registers, reports itself active, and does nothing.', [PANEL_STORE]),
    ('KAME is loaded but every call keeps the key Hermes resolved: ', [PANEL_STORE]),
    ('The numbers below are the last reading, not the current one — ', [PANEL_STORE]),
    ('the backend is probably restarting.', [PANEL_STORE]),
    (' left rotation — the provider refused ', [PANEL_STORE]),
    ('so KAME no longer offers ', [PANEL_STORE]),
    ('Nothing was deleted: paste the replacement over ', [PANEL_STORE]),
    ('back by itself. Your other keys are carrying every turn meanwhile.', [PANEL_STORE]),
    (' just refused and ', [PANEL_STORE]),
    (' still being tried. ', [PANEL_STORE]),
    ('One refusal is not proof — an expired token a second from refreshing sends the same thing — ', [PANEL_STORE]),
    (' in a row, with nothing working in between.', [PANEL_STORE]),
    ('Ready — your next reply goes to ', [VIEW]),
    (' old — Hermes may be restarting.', [VIEW]),
    ('KAME is loaded but not rotating — see the note below.', [VIEW]),
    ('Idle. A key is ready, so the next call goes straight out.', [PANEL_STORE]),
    ('Idle, and every key is resting. The first one comes back in ', [PANEL_STORE]),
    ('Rotated. Attempt ', [PANEL_STORE]),
    (' rather than failing the turn — press Esc to stop.', [PANEL_STORE]),
    ('Recovered', [PANEL_STORE]),
    ('answered after a rotation', [PANEL_STORE]),
    ('Surfaced', [PANEL_STORE]),
    ('errors KAME let through', [PANEL_STORE]),
    ('Rotations', [PANEL_STORE]),
    ('Fallbacks', [PANEL_STORE]),
    ('replies your fallback list answered: the model was busy, resting or too slow', [PANEL_STORE]),
    ('quota ran out, so several keys were tried at once', [PANEL_STORE]),
    ('Waited', [PANEL_STORE]),
    ('Answers cut', [PANEL_STORE]),
    ('the provider stopped mid-sentence', [PANEL_STORE]),
    ('Continued', [PANEL_STORE]),
    ('finished on another key, as one reply', [PANEL_STORE]),
    ('Backend process ', [PANEL]),
    ('/kame for the same picture in the chat, /kame-quota per key and model, /kame-keys to add keys in bulk', [PANEL]),
    ('KAME API Rotation is made by ', [PANEL]),
    ('github.com/Kame696', [PANEL]),
    ('⭐ Star it on GitHub', [PANEL]),
    ('Made by KAME', [PANEL]),
    ('KAME API Rotation is designed and built by ', [PANEL]),
    ('. Free and MIT-licensed. If it saves your conversations from rate limits, a star on GitHub helps other people find it — it costs nothing.', [PANEL]),
    ('⭐ Star on GitHub', [PANEL]),
    ('KAME on GitHub (Kame696)', [PANEL]),
    ('Report a problem', [PANEL]),
    ("KAME has not served this model yet", [PANEL_STORE]),
    ("(your model)", [PANEL]),
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
    ("Set in config.yaml under this plugin. The environment would outrank it.",
     "Set in this plugin's settings. The environment would outrank it.", [PANEL_STORE],
     "Agent Zero keeps its plugin settings in its own settings store, not in config.yaml"),
    ("set in config.yaml", "set in the settings", [PANEL_STORE],
     "the Source badge names the place the value was stored"),
    ("Reads Hermes' .env again and rebuilds this page on the spot.",
     "Reads Agent Zero's settings again and rebuilds this page on the spot.", [PANEL],
     "Agent Zero has no .env of its own for this: the sentence names the settings it reads"),
    ("Removes every KAME_ line from the .env and forgets the environment value, so every setting falls back to its built-in default.",
     "Every KAME setting goes back to its built-in default.", [PANEL],
     "Hermes stores the settings in .env lines; Agent Zero stores them in its plugin settings"),
    ("Every KAME setting goes back to its built-in default, and the KAME_ lines are removed from the .env. Nothing else in that file is touched, and no key is affected.",
     "Every KAME setting goes back to its built-in default. Your fallback list and your keys are not touched.", [PANEL],
     "same promise without the .env clause; Agent Zero's fallback list is part of its settings and is kept"),
    ('Every call your conversation sent and every decision it made about your keys, newest first',
     'Every decision your conversation made about your keys, newest first', [PANEL],
     "Agent Zero's list records the decisions and the failures, not one row per call (the Last replies card carries the answers)"),
    ('No auxiliary model calls yet. Titles, compression, vision and the other auxiliary tasks appear here as Hermes runs them.',
     'No auxiliary model calls yet. Titles, compression, memory and the other background jobs appear here as Agent Zero runs them.', [PANEL],
     "Agent Zero's jobs are not Hermes' auxiliary tasks"),
    ('auxiliary models →',
     'background jobs →', [PANEL_STORE],
     "Agent Zero's tab is called Background jobs"),
    ('Nothing has been written for ',
     'Nothing has been read for ', [PANEL_STORE],
     "Agent Zero's page reads the status on demand; Hermes' reads a file the backend writes"),
    ('This page reads a file the backend half of KAME writes. If Hermes has just started, give it a moment.',
     'This page reads Agent Zero’s status. If Agent Zero has just started, give it a moment.', [PANEL],
     'there is no snapshot file on Agent Zero: the page asks the plugin directly'),
    ('in Hermes’ own provider/key settings and ',
     'in Agent Zero’s Settings > API keys and ', [PANEL_STORE],
     'Agent Zero keeps the keys in Settings > API keys, one field per provider'),
    ('Across every conversation it is serving, including subagents and the auxiliary lane.',
     'Across every conversation it is serving, including subagents and the background jobs.', [PANEL],
     "Agent Zero's side calls are its background jobs, not an auxiliary lane"),
    (' · reading refreshed every second',
     ' · reading refreshed every ', [PANEL],
     'Agent Zero reads its status every few seconds (the footer says how many), Hermes reads a local file every second'),
    ("Change this list in Hermes: hermes fallback add / remove. Background jobs never switch model.",
     "Change this list in the Fallback models tab. Background jobs never switch model.", [PANEL_STORE],
     "Agent Zero edits the list on its own tab; Hermes uses its command line"),
    ("What KAME can see", "Agent Zero, as KAME found it", [PANEL],
     "the card is the 1.8.2.8 'what the host looks like' card, named for the host"),
]

#: (Hermes text, reason) - sentences Agent Zero does not say. Asserted absent by the parity test.
NOT_PORTED = [
    ("Waiting for the first snapshot…",
     "Hermes' second entry of the form, under Settings > Plugins, has no Agent Zero twin: the form lives only on this page"),
]

#: A shelf that exists only in the Agent Zero port, and where it sits (index in the final list).
AGENT_ZERO_ONLY_GROUPS = [{"id": "agentzero", "after": "tuning"}]
#: Hermes keys with no Agent Zero twin: no function behind them here (decisions/0015).
HERMES_ONLY_KEYS = ["resolver_disabled", "quota_id_disabled"]
#: Hermes keys that Agent Zero stores under another name.
RENAMED_KEYS = {"disabled": "rotation_disabled"}


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


def load_hermes_settings(settings_py: Path):
    """The Hermes `settings.py` module, loaded by path (read-only; no Hermes install is involved)."""
    import importlib.util

    sys.path.insert(0, str(settings_py.parent))
    try:
        spec = importlib.util.spec_from_file_location("kame_hermes_settings_for_extract", settings_py)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(settings_py.parent))
    return module


def read_settings(settings_py: Path):
    """`describe_all()` of Hermes, field by field, in Hermes' order (what a settings page shows)."""
    module = load_hermes_settings(settings_py)
    fields = ("key", "kind", "title", "help", "when", "detail", "impact", "advanced", "units", "min", "max", "step",
              "off_or_at_least", "consequential", "default", "env", "group", "choice_order", "choice_labels",
              "choice_help", "choice_footnote")
    rows = []
    for item in module.describe_all():
        row = {name: item.get(name) for name in fields}
        if not row["detail"]:
            row["detail"] = ""
        rows.append(row)
    return rows, [dict(g) for g in module.groups()]


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
    settings, shelves = read_settings(hermes / "settings.py")
    groups = [{"id": g["id"], "title": g["title"], "note": g["note"], "keys": g["keys"]} for g in shelves]
    reads = sorted(set(read_panel_reads(plugin_js)))

    ui = {
        "source": "Hermes 1.8.2.9: desktop/plugin.js, settings.py (read-only, by tools/extract_hermes_ui_strings.py)",
        "substitutions": SUBSTITUTIONS,
        "tabs": {"hermes": tabs, "agent_zero_extra": [{"label": "Fallback models", "after": "Settings"}]},
        "groups": groups,
        "groups_hermes_only_keys": HERMES_ONLY_KEYS,
        "groups_renamed_keys": RENAMED_KEYS,
        "settings": settings,
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
