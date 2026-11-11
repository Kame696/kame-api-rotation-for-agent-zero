"""Who the published files say made this plugin: KAME, with a link to the repository, and nobody else.

A release gate, the same file in both ports. It walks the files that are published and fails on
a line that credits anything other than the author for the work: an attribution trailer, a tool's
"generated with" line, a sentence saying a model or an assistant wrote, reviewed, designed or
tested it, or the name of a private note. It then checks that the manifests name the author and
the repository.

A provider or a model named as something the plugin talks to is not a hit: ``anthropic`` as a
provider, ``claude-...`` as a model id, ``openai-codex`` as a wire. Those are the product.

    python tools/authorship_check.py            # exit 0 = clean, 1 = something to fix
    python tools/authorship_check.py --list     # also print every line that names a model family

Reads only. Prints ``file:line: reason`` and the line.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

HERE = Path(__file__).resolve()

AUTHOR = "Kame696"

#: Binary or generated content that holds no authorship statement.
SKIP_DIRS = {"__pycache__", "dist", "node_modules", ".git", "releases", "_backups", "host_baselines"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".zip", ".pyc", ".woff", ".woff2",
                 ".ttf", ".wav", ".mp3", ".ogg", ".jsonl", ".lock"}

_WORKER = (r"(?:an?\s+|the\s+|a\s+second\s+|another\s+)?"
           r"(?:ai|llm|language\s+model|model|assistant|agent(?!\s+zero)|sub-?agent|bot|"
           r"claude|opus|sonnet|haiku|fable|chatgpt|gpt-?\d[\w.]*|gemini|copilot|cursor)")
_DID = r"(?:written|authored|built|designed|reviewed|audited|tested|refactored|generated|coded|implemented)"
_DOES = r"(?:wrote|reviewed|designed|built|audited|tested|refactored|implemented|was\s+told\s+to|told\s+to)"

#: ``(reason, pattern)``. Case is ignored unless the pattern says otherwise.
RULES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    # Written in two halves so this file does not hold the phrase it looks for.
    ("attribution trailer", re.compile("co-" + "authored-by", re.I)),
    ("a tool's signature", re.compile(r"generated\s+(?:with|by)\s+\[?(?:an?\s+)?(?:ai|llm|claude|chatgpt|copilot|cursor|"
                                      r"an?\s+assistant)\b|claude\s+code|noreply@anthropic", re.I)),
    ("work credited to a model or an assistant",
     re.compile(r"\b" + _DID + r"\s+by\s+" + _WORKER + r"\b", re.I)),
    ("work credited to a model or an assistant",
     re.compile(r"\b(?:an?\s+|the\s+|a\s+second\s+|another\s+)(?:ai|llm|language\s+model|assistant|sub-?agent|"
                r"second\s+model)\s+" + _DOES + r"\b|\b(?:second|another)\s+model\s+(?:told|asked)\b", re.I)),
    ("work described as machine-made",
     re.compile(r"\bai[- ](?:written|assisted|authored)\b|\b(?:ai|llm)[- ]generated\s+"
                r"(?:code|plugin|tests?|commits?|comments?|files?|docs?)\b", re.I)),
    # A model family written as a name (capital letter, standing alone) is a worker, not an id.
    ("a model family named as a worker",
     re.compile(r"(?<![\w/:\"'`-])(?:Claude|Opus|Sonnet|Haiku|Fable)(?![\w-])(?!\s*(?:codec|audio))")),
    ("the name of a private note", re.compile(r"``?kame-[a-z0-9]+(?:-[a-z0-9]+){2,}``?")),
    ("an assistant's own file", re.compile(r"\bCLAUDE\.md\b|\bMEMORY\.md\b|\.claude[/\\]|\bAGENTS\.md\b")),
)

#: A line that matches a rule and is still fine: it names the thing the plugin talks to (a provider,
#: a model id, a wire, a package of this plugin).
ALLOW = re.compile(
    r"claude-[a-z0-9]|\"claude\"\s*:|'claude'\s*:|anthropic|messages\s+wire|format\s*=\s*[\"']opus|"
    r"\bopus\b.{0,20}\b(?:codec|audio|ogg)|kame-api-rotation|kame-gemini|kame-private|kame-tests-home|hermes-kame-",
    re.I)

#: Reasons no provider name excuses.
STRICT = {"attribution trailer", "a tool's signature", "an assistant's own file"}

#: A line that is a scanner's own pattern (this gate, or a test that looks for the same words).
PATTERN_LINE = re.compile(r"re\.compile\(")

FAMILY = re.compile(r"\b(?:claude|opus|sonnet|haiku|fable)\b", re.I)


def published_roots() -> Tuple[str, List[Path], List[Path]]:
    """``(port, roots to walk, manifests)`` for the port this file sits in."""
    hermes = HERE.parents[1]
    if (hermes / "hermes-kame-api-rotation").is_dir():
        roots = [hermes / name for name in (
            "hermes-kame-api-rotation", "hermes-kame-provider", "hermes-kame-bridge", "tests", "tools",
            "README.md", "CHANGELOG.md", "VALIDATION.md", "validation", "assets", ".github")]
        manifests = [hermes / name / "plugin.yaml" for name in (
            "hermes-kame-api-rotation", "hermes-kame-provider", "hermes-kame-bridge")]
        return "hermes", [r for r in roots if r.exists()], manifests
    current = HERE.parents[1]
    return "agent-zero", [current], [current / "plugin.yaml"]


def text_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file():
            yield root
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES or path.name == HERE.name:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            yield path


def scan(path: Path, listing: bool) -> Tuple[List[str], List[str]]:
    hits: List[str] = []
    named: List[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return hits, named
    for number, line in enumerate(text.splitlines(), 1):
        if len(line) > 4000:        # a minified bundle or a data row, not a sentence
            continue
        if listing and FAMILY.search(line):
            named.append(f"{path}:{number}: {line.strip()[:160]}")
        for reason, pattern in RULES:
            found = pattern.search(line)
            if found is None:
                continue
            if PATTERN_LINE.search(line) or (reason not in STRICT and ALLOW.search(line)):
                continue
            hits.append(f"{path}:{number}: {reason}: {line.strip()[:200]}")
            break
    return hits, named


def manifest_problems(port: str, manifests: Iterable[Path]) -> List[str]:
    problems: List[str] = []
    for manifest in manifests:
        try:
            text = manifest.read_text(encoding="utf-8")
        except OSError:
            problems.append(f"{manifest}: cannot be read")
            continue
        if port == "hermes":
            author = re.search(r"^author:\s*[\"']?([^\"'\n]+)", text, re.M)
            home = re.search(r"^homepage:\s*(\S+)", text, re.M)
            if author is None or author.group(1).strip() != AUTHOR:
                problems.append(f"{manifest}: author is not {AUTHOR}")
            if home is None or f"github.com/{AUTHOR}/" not in home.group(1):
                problems.append(f"{manifest}: homepage is not a repository of {AUTHOR}")
            print(f"  {manifest.parent.name}: author {author.group(1).strip() if author else '-'}, "
                  f"homepage {home.group(1) if home else '-'}")
    if port == "agent-zero":
        # Agent Zero's manifest has no author field; the credit is on the plugin's own page.
        root = HERE.parents[1]
        page = root / "webui" / "kame-panel.html"
        licence = root / "LICENSE"
        for path, needle in ((page, f"github.com/{AUTHOR}/kame-api-rotation-for-agent-zero"),
                             (licence, f"github.com/{AUTHOR}")):
            try:
                ok = needle in path.read_text(encoding="utf-8")
            except OSError:
                ok = False
            print(f"  {path.name}: {'names' if ok else 'DOES NOT name'} {needle}")
            if not ok:
                problems.append(f"{path}: does not name {needle}")
    return problems


def main(argv: List[str]) -> int:
    listing = "--list" in argv
    port, roots, manifests = published_roots()
    hits: List[str] = []
    named: List[str] = []
    count = 0
    for path in text_files(roots):
        count += 1
        found, families = scan(path, listing)
        hits.extend(found)
        named.extend(families)
    print(f"authorship check, {port}: {count} files read")
    problems = manifest_problems(port, manifests)
    if listing:
        print(f"\nlines that name a model family ({len(named)}), for a reader to judge:")
        for line in named:
            print("  " + line)
    for line in hits + problems:
        print(line)
    if hits or problems:
        print(f"\nFAIL: {len(hits)} line(s), {len(problems)} manifest problem(s)")
        return 1
    print("ok: nothing credits anyone but the author")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    raise SystemExit(main(sys.argv[1:]))
