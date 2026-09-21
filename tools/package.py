"""Build the installable zip for Agent Zero, reproducibly.

Every release up to v1.2.0 was zipped by hand. That is how a build ends up
carrying a stale `kame_engine.py`, a `__pycache__`, or somebody's scratch file,
and how two zips with the same version number end up holding different code —
the failure the Hermes port wrote `tools/fingerprints.py` to catch after it
happened there.

So this reads the version from the manifest rather than taking it as an
argument, refuses to build when the manifest and the engine disagree, walks a
declared file list rather than "everything in the folder", and prints a digest
of what went in.

    python tools/package.py

Writes `releases/KAME_v<version>.zip`. Nothing is uploaded and nothing is
installed — this only produces the file.
"""

from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT.parent / "releases"

#: What an Agent Zero install actually needs, named rather than globbed. A
#: glob would quietly ship whatever happened to be sitting in the folder.
INCLUDE_FILES = (
    "plugin.yaml",
    "kame_engine.py",
    "kame_activation.py",
    "hooks.py",
    "integrity.py",
    "default_config.yaml",
    "a0_compat.json",
    "CHANGELOG.md",
    "COMPATIBILITY.md",
    "README.md",
    "LICENSE",
)

#: Directories walked whole, minus the exclusions below.
INCLUDE_TREES = ("extensions", "commands", "api", "webui", "tests")

EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", ".git"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".zip", ".log"}


def manifest_version() -> str:
    for line in (ROOT / "plugin.yaml").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def engine_version() -> str:
    match = re.search(
        r'^KAME_VERSION\s*=\s*["\']([^"\']+)["\']',
        (ROOT / "kame_engine.py").read_text(encoding="utf-8"),
        re.M,
    )
    return match.group(1) if match else ""


def wanted(path: Path) -> bool:
    if any(part in EXCLUDE_PARTS for part in path.parts):
        return False
    return path.suffix.lower() not in EXCLUDE_SUFFIXES


def collect() -> list[Path]:
    found: list[Path] = []
    for name in INCLUDE_FILES:
        candidate = ROOT / name
        if candidate.is_file():
            found.append(candidate)
    for tree in INCLUDE_TREES:
        base = ROOT / tree
        if not base.is_dir():
            continue
        found.extend(p for p in sorted(base.rglob("*")) if p.is_file() and wanted(p))
    return found


def main() -> int:
    manifest, engine = manifest_version(), engine_version()
    if not manifest:
        print("could not read a version out of plugin.yaml")
        return 2
    # The check that makes the number mean something. A zip whose manifest and
    # engine disagree is a zip nobody can reason about later.
    if manifest != engine:
        print(f"plugin.yaml says {manifest!r} and kame_engine.py says {engine!r}")
        print("refusing to build until they agree")
        return 1

    files = collect()
    missing = [n for n in INCLUDE_FILES if not (ROOT / n).is_file()]
    if missing:
        print("required file(s) not found: " + ", ".join(missing))
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"KAME_v{manifest}.zip"

    digest = hashlib.sha256()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            arcname = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            digest.update(arcname.encode("utf-8"))
            digest.update(data)
            archive.writestr(arcname, data)

    print(f"version : {manifest}  (plugin.yaml and kame_engine.py agree)")
    print(f"wrote   : {target}")
    print(f"          {len(files)} file(s), {target.stat().st_size:,} bytes")
    print(f"fingerprint of the contents: {digest.hexdigest()[:12]}")
    print()
    print("Nothing was installed and nothing was uploaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
