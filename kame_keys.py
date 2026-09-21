"""Adding keys in bulk — the logic behind `/kame-keys` (v1.8.1.0).

Port of the Hermes `/kame-keys add|import|status|reset`. Agent Zero keeps a
provider's keys in one `usr/.env` line, comma-separated (`API_KEY_GOOGLE=k1,k2`),
which is exactly the shape KAME rotates, so adding a key is merging it into
that line. Hermes writes its credential store; this writes the `.env`, and
backs it up first the same way (`.env.kame-<timestamp>.bak`, last 5 kept —
plaintext, like the `.env` itself).

Framework-free: no Agent Zero import. The command passes in the path of the
`.env` and a function that saves one value; this module plans, masks and
backs up. A key is never returned whole in any message.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

MAX_BACKUPS = 5

# Prefix -> Agent Zero provider id (conf/model_providers.yaml). Only prefixes
# that belong to ONE provider: a bare `sk-` is OpenAI, DeepSeek, Moonshot and
# half the gateways, so it never decides alone.
_PREFIXES = (
    ("sk-or-", "openrouter"),
    ("sk-ant-", "anthropic"),
    ("AIza", "google"),
    ("nvapi-", "nvidia_nim"),
    ("gsk_", "groq"),
    ("xai-", "xai"),
    ("hf_", "huggingface"),
)

_SPLIT = re.compile(r"[\s,;|]+")
_PROVIDER = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def mask(key: str) -> str:
    """`AIzaSy…q7R8` — enough to find it in a console, never the key."""
    key = str(key or "")
    if len(key) <= 12:
        return "…"
    return f"{key[:6]}…{key[-4:]}"


def split_keys(text: str) -> List[str]:
    """Commas, spaces, newlines, semicolons and pipes all separate keys."""
    seen, out = set(), []
    for piece in _SPLIT.split(str(text or "")):
        piece = piece.strip().strip("\"'")
        if piece and piece not in seen:
            seen.add(piece)
            out.append(piece)
    return out


def guess_provider(keys: List[str]) -> str:
    """The provider every key's prefix agrees on, or "" when they do not."""
    found = set()
    for key in keys:
        match = next((p for prefix, p in _PREFIXES if key.startswith(prefix)), "")
        if not match:
            return ""
        found.add(match)
    return found.pop() if len(found) == 1 else ""


def split_provider(text: str) -> Tuple[str, str]:
    """`("openrouter", "sk-or-...")` when the first word names a provider."""
    text = str(text or "").strip()
    first, _, rest = text.partition(" ")
    if _PROVIDER.match(first.lower()) and not guess_provider([first]) and rest.strip():
        return first.lower(), rest.strip()
    return "", text


def env_var_for(provider: str, env: Dict[str, str]) -> str:
    """The variable already holding this provider's keys, else API_KEY_<P>."""
    up = provider.upper()
    for name in (f"API_KEY_{up}", f"{up}_API_KEY", f"{up}_API_TOKEN"):
        if str(env.get(name, "") or "").strip() not in ("", "None"):
            return name
    return f"API_KEY_{up}"


def plan_add(existing: str, new: List[str]) -> Tuple[List[str], List[str]]:
    """``(merged, added)``: keys already present are skipped, order kept."""
    current = [k for k in split_keys(existing) if k != "None"]
    added = [k for k in new if k not in current]
    return current + added, added


def read_env(path: Path) -> Dict[str, str]:
    """`NAME=value` lines of the .env, without exporting anything."""
    out: Dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            out[name.strip()] = value.strip().strip("\"'")
    except OSError:
        pass
    return out


def backup(path: Path) -> Optional[str]:
    """Copy the .env aside, keep the last five. Returns the backup's name."""
    try:
        if not path.is_file():
            return None
        target = path.with_name(f"{path.name}.kame-{time.strftime('%Y%m%d-%H%M%S')}.bak")
        shutil.copy2(path, target)
        for stale in sorted(path.parent.glob(f"{path.name}.kame-*.bak"))[:-MAX_BACKUPS]:
            try:
                stale.unlink()
            except OSError:
                pass
        return target.name
    except Exception:
        return None


def add(path: Path, provider: str, keys: List[str],
        save: Callable[[str, str], None]) -> str:
    """Merge `keys` into the provider's line. Returns the message to show."""
    if not keys:
        return "No key found in that text."
    provider = (provider or guess_provider(keys)).lower()
    if not provider:
        return ("Which provider? The keys do not say. Usage: "
                "`/kame-keys add <provider> key1,key2` — e.g. `openai`, `deepseek`.")
    env = read_env(path)
    var = env_var_for(provider, env)
    merged, added = plan_add(env.get(var, ""), keys)
    if not added:
        return f"`{var}` already holds all {len(keys)} key(s). Nothing changed."
    saved = backup(path)
    save(var, ",".join(merged))
    lines = [f"Added {len(added)} key(s) to `{var}` — {len(merged)} in the pool now."]
    lines += [f"- {mask(k)}" for k in added]
    skipped = len(keys) - len(added)
    if skipped:
        lines.append(f"{skipped} already there, skipped.")
    if saved:
        lines.append(f"Backup of the previous file: `usr/{saved}` (plaintext, last {MAX_BACKUPS} kept).")
    lines.append("KAME picks them up on the next call.")
    return "\n".join(lines)


def pools(path: Path) -> List[Tuple[str, str, List[str]]]:
    """``(provider, variable, keys)`` for every multi- or single-key line."""
    env = read_env(path)
    out = []
    for name, value in sorted(env.items()):
        m = re.match(r"^API_KEY_([A-Z0-9_]+)$", name) or re.match(r"^([A-Z0-9_]+)_API_(?:KEY|TOKEN)$", name)
        if not m:
            continue
        keys = [k for k in split_keys(value) if k != "None"]
        if keys:
            out.append((m.group(1).lower(), name, keys))
    return out
