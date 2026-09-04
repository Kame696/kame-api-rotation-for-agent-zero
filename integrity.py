"""What is actually on disk, hashed — v1.6.0.1.

A version string survives a copy that dropped half the package. A fingerprint
computed from the files cannot describe files that are not there.

That distinction is not theoretical. The Hermes port of this plugin spent nine
days deploying fixes into a directory the host never read: every version check
agreed, every changelog was right, and none of it was running. The tell would
have been a fingerprint that never changed while the version did.

Agent Zero has never had that failure, and nothing about Agent Zero makes it
impossible — the install is still a copied tree, and the version still comes
from whoever last edited `plugin.yaml`. So this ships before it is needed rather
than after.

Deliberately framework-free: no Agent Zero import, no settings, no network. It
reads its own directory and hashes bytes. That is what lets it answer honestly
even when the thing that is broken is the plugin's own loading.
"""

from __future__ import annotations

import hashlib
import os

#: Every module this plugin needs in order to actually rotate. A file missing
#: from here is not a degraded feature — it is a plugin that will not work and
#: will usually not say so, because Python's import error happens somewhere the
#: user never looks.
#:
#: The WebUI files are deliberately NOT in this list. A missing chip is a
#: missing screen; a missing engine is a missing plugin, and conflating the two
#: would make `complete` mean nothing.
REQUIRED = (
    "kame_engine.py",
    "kame_activation.py",
    "hooks.py",
    "plugin.yaml",
    "default_config.yaml",
    # This file is in its own list on purpose. A build fingerprint that does not
    # cover the module computing it can be changed without changing, which is
    # the one property it exists to deny.
    "integrity.py",
)

#: Files that make the plugin whole but whose absence costs one feature, not
#: rotation. Reported separately so a reader can tell a broken install from a
#: partial one.
OPTIONAL = (
    "api/kame_status.py",
    "webui/kame-rotation-store.js",
    "webui/config.html",
    "extensions/webui/model-context-strip-end/kame-rotation.html",
    "extensions/webui/apply_snapshot_before/refresh-kame-rotation.js",
    "extensions/python/banners/_40_kame_retired_keys.py",
    "extensions/python/agent_init/_10_kame_api_rotation.py",
    "extensions/python/monologue_start/_10_kame_api_rotation.py",
    "commands/kame.command.yaml",
    "commands/kame_command.py",
)

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _digest(path: str):
    """SHA-256 of one file's bytes, or None when it is not there.

    Bytes, not text: a file that differs only by line endings is a different
    copy, and pretending otherwise is exactly how two installs that behave
    differently come to report the same build.
    """
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def report() -> dict:
    """`{fingerprint, complete, missing, degraded, files}` for this copy.

    `fingerprint` is twelve hex characters — short enough to read out loud over
    a support conversation, long enough that two different trees colliding is
    not something that happens. It is derived from the REQUIRED set only, so
    adding a screen does not change the identity of the engine.
    """
    files, missing, degraded = {}, [], []
    roll = hashlib.sha256()
    for name in REQUIRED:
        digest = _digest(os.path.join(_ROOT, *name.split("/")))
        files[name] = digest
        if digest is None:
            missing.append(name)
            # A missing file still contributes to the hash, as its own absence.
            # Otherwise two trees — one whole, one missing a module — could
            # fingerprint identically, which is the precise failure this exists
            # to make impossible.
            roll.update(f"{name}:MISSING".encode())
        else:
            roll.update(f"{name}:{digest}".encode())
    for name in OPTIONAL:
        digest = _digest(os.path.join(_ROOT, *name.split("/")))
        files[name] = digest
        if digest is None:
            degraded.append(name)
    return {
        "fingerprint": roll.hexdigest()[:12],
        "complete": not missing,
        "missing": missing,
        "degraded": degraded,
        "root": _ROOT,
        "files": files,
    }


def fingerprint() -> str:
    """Just the twelve characters, for a banner or a status line."""
    try:
        return report()["fingerprint"]
    except Exception:
        # A fingerprint that cannot be computed must not be reported as a
        # fingerprint. Twelve dashes is unmistakably not a hash.
        return "------------"
