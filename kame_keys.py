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
import secrets
import shutil
import time
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

MAX_BACKUPS = 5
_IMPORT_LOCK = threading.RLock()

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
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 1.8.1.2. The names people actually give these variables, mapped to Agent
# Zero's provider ids (conf/model_providers.yaml). `GEMINI_API_KEY` is the
# Google AI Studio convention and `NVIDIA_API_KEY` NVIDIA's; Agent Zero reads
# `API_KEY_GOOGLE` and `API_KEY_NVIDIA_NIM`. Without this, 1.8.1.1 imported a
# Gemini .env into `API_KEY_GEMINI`, reported success, and no key was ever used.
_PROVIDER_ALIASES = {
    "gemini": "google", "google_ai": "google", "googleai": "google",
    "nvidia": "nvidia_nim", "nim": "nvidia_nim",
    "hf": "huggingface", "grok": "xai", "claude": "anthropic",
}


def canonical_provider(provider: str, keys: Optional[List[str]] = None) -> str:
    """Agent Zero's id for `provider`: the key prefix first, then the alias table."""
    guessed = guess_provider(keys or []) if keys else ""
    if guessed:
        return guessed
    name = str(provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(name, name)


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
    """Parse dotenv assignments without exporting anything.

    Comments only start outside quotes. Both quote styles are accepted and an
    optional ``export`` prefix is ignored. This is deliberately small and
    deterministic: KAME needs the key values, not shell expansion.
    """
    try:
        return parse_env_text(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}


def _dotenv_value(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    quote = ""
    escaped = False
    kept: List[str] = []
    for index, char in enumerate(text):
        if escaped:
            kept.append(char)
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            kept.append(char)
            continue
        if quote:
            kept.append(char)
            if char == quote:
                quote = ""
            continue
        if char in ("'", '"'):
            quote = char
            kept.append(char)
            continue
        if char == "#" and (index == 0 or text[index - 1].isspace()):
            break
        kept.append(char)
    value = "".join(kept).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        quote = value[0]
        value = value[1:-1]
        if quote == '"':
            value = value.replace(r'\"', '"').replace(r"\\", "\\")
    return value


def parse_env_text(text: str) -> Dict[str, str]:
    """Return valid ``NAME=value`` assignments from dotenv-like text."""
    out: Dict[str, str] = {}
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, _, raw = line.partition("=")
        name = name.strip()
        if not _ENV_NAME.fullmatch(name):
            continue
        out[name] = _dotenv_value(raw)
    return out


def _provider_for_env_var(name: str) -> str:
    match = re.match(r"^API_KEY_([A-Z0-9_]+)$", name, re.I) or re.match(
        r"^([A-Z0-9_]+)_API_(?:KEY|TOKEN)$", name, re.I
    )
    return match.group(1).lower() if match else ""


def parse_import(text: str, provider: str = "") -> Tuple[str, List[str]]:
    """Read either a raw key list or dotenv-formatted key file.

    A dotenv file may contain comments and unrelated settings. With an explicit
    provider only that provider's API variable is imported. Without one, KAME
    accepts the file only when all API-key assignments name one provider.
    """
    env = parse_env_text(text)
    api_rows = [(name, value, _provider_for_env_var(name)) for name, value in env.items()]
    api_rows = [(name, value, canonical_provider(found, split_keys(value)))
                for name, value, found in api_rows if found and value]
    wanted = canonical_provider(provider) if str(provider or "").strip() else ""
    if api_rows:
        if wanted:
            values = [value for _name, value, found in api_rows if found == wanted]
            return wanted, split_keys("\n".join(values))
        providers = {found for _name, _value, found in api_rows}
        if len(providers) == 1:
            chosen = next(iter(providers))
            return chosen, split_keys("\n".join(value for _name, value, _found in api_rows))
        return "", []
    return wanted, split_keys(text)


def import_providers(text: str) -> set:
    """Every Agent Zero provider a dotenv text assigns an API key to."""
    return {canonical_provider(_provider_for_env_var(name), split_keys(value))
            for name, value in parse_env_text(text).items()
            if _provider_for_env_var(name) and value}


def backup(path: Path) -> Optional[str]:
    """Copy the .env aside with a collision-resistant name; keep the last five."""
    try:
        if not path.is_file():
            return None
        target = None
        for _ in range(4):
            candidate = path.with_name(
                f"{path.name}.kame-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}-{secrets.token_hex(3)}.bak"
            )
            try:
                # 1.8.1.4: created owner-only, not at the umask's 0644. The
                # copystat below then gives it the .env's own mode, but until
                # then a plaintext copy of every key sat world-readable.
                fd = os.open(str(candidate), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with path.open("rb") as source, os.fdopen(fd, "wb") as dest:
                    shutil.copyfileobj(source, dest)
                if candidate.read_bytes() != path.read_bytes():
                    return None
                try:
                    shutil.copystat(path, candidate)
                except OSError:
                    pass
                target = candidate
                break
            except FileExistsError:
                continue
        if target is None:
            return None
        backups = sorted(
            path.parent.glob(f"{path.name}.kame-*.bak"),
            key=lambda item: (item.stat().st_mtime_ns, item.name),
        )
        for stale in backups[:-MAX_BACKUPS]:
            try:
                stale.unlink()
            except OSError:
                pass
        return target.name
    except Exception:
        return None


def add(path: Path, provider: str, keys: List[str],
        save: Callable[[str, str], None]) -> str:
    # Agent Zero runs commands in one process. Serialize read/merge/save so
    # two imports cannot both read the same old list and lose one addition.
    with _IMPORT_LOCK:
        return _add_locked(path, provider, keys, save)


def _add_locked(path: Path, provider: str, keys: List[str],
                save: Callable[[str, str], None]) -> str:
    """Merge `keys` into the provider's line. Returns the message to show."""
    if not keys:
        return "No key found in that text."
    provider = canonical_provider(provider, keys) if provider else guess_provider(keys)
    if not provider:
        return ("Which provider? The keys do not say. Usage: "
                "`/kame-keys add <provider> key1,key2` — e.g. `openai`, `deepseek`.")
    env = read_env(path)
    var = env_var_for(provider, env)
    merged, added = plan_add(env.get(var, ""), keys)
    if not added:
        return f"`{var}` already holds all {len(keys)} key(s). Nothing changed."
    existed = path.is_file()
    saved = backup(path)
    if existed and saved is None:
        return "Could not create a backup of the existing .env. Nothing changed."
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
