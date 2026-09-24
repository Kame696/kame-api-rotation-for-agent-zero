"""`/kame-keys` — add and inspect API keys in bulk. v1.8.1.0, port of Hermes.

    /kame-keys                          every provider line, masked, with health
    /kame-keys add [provider] k1,k2     merge keys into that provider's line
    /kame-keys import [provider] <file> the same, from a file (keeps keys out of the chat)
    /kame-keys reset                    every key starts again from zero

Keys go into Agent Zero's own `usr/.env`, in the comma-separated line KAME
rotates (`API_KEY_GOOGLE=k1,k2,k3`), through Agent Zero's own
`helpers.dotenv.save_dotenv_value`. Before each write the previous file is
copied beside it as `.env.kame-<timestamp>.bak` (last 5 kept, plaintext like
the `.env`). A key is never shown whole; pasting keys into a chat puts them in
that chat's history, which `import <file>` avoids.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def run(payload: dict[str, Any]) -> dict[str, Any]:
    invocation = payload.get("invocation") or {}
    raw = str(invocation.get("raw_arguments") or "").strip()
    verb, _, rest = raw.partition(" ")
    verb = verb.lower()
    try:
        from usr.plugins.api_rotation_by_kame import kame_keys as keys
        from usr.plugins.api_rotation_by_kame import kame_engine as engine
        from helpers import dotenv
    except Exception as exc:
        return _toast(f"KAME could not load ({type(exc).__name__}).", "error")

    env_path = Path(dotenv.get_dotenv_file_path())
    try:
        if verb in ("", "status", "list"):
            return _show("KAME — keys", _status(keys, engine, env_path))
        if verb == "reset":
            count = engine.reset_pool()
            return _show("KAME — keys", f"**{count} key(s) start again from zero.** "
                                         "Rests, refusal streaks and retirements cleared.")
        if verb == "add":
            provider, text = keys.split_provider(rest, _host_providers(), keys.read_env(env_path))
            found, rejected = keys.pasted_keys(text)
            return _show("KAME — keys added", keys.add(env_path, provider, found,
                                                        dotenv.save_dotenv_value, rejected))
        if verb == "import":
            provider, file_text = keys.split_provider(rest, _host_providers(), keys.read_env(env_path))
            path = Path(file_text.strip().strip("\"'")).expanduser()
            if not file_text.strip():
                return _toast("Which file? Usage: /kame-keys import [provider] <path>", "error")
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                return _toast(f"File not found: {path}", "error")
            text = keys.decode_text(data)
            rejected = []
            provider, found = keys.parse_import(text, provider, rejected)
            if not found and not provider and len(keys.import_providers(text)) > 1:
                return _toast(
                    "The file contains keys for more than one provider. Specify one: "
                    "/kame-keys import <provider> <path>", "error"
                )
            if not found:
                # Counted, not shown: a toast is one line, and the tokens are masked anyway.
                refused = f" {len(rejected)} token(s) did not look like an API key." if rejected else ""
                return _toast("No key found in that file." + refused, "error")
            return _show("KAME — keys imported", keys.add(env_path, provider, found,
                                                           dotenv.save_dotenv_value, rejected))
        return _toast(f"Unknown: {verb}. Use status, add, import or reset.", "error")
    except Exception as exc:
        # Never the arguments: on the `add` path they are live keys.
        return _toast(f"/kame-keys {verb} failed: {type(exc).__name__}", "error")


def _host_providers():
    """Every chat provider id the running Agent Zero lists, plugins included."""
    try:
        from helpers import providers
        return {str(option.get("value") or "").lower() for option in providers.get_providers("chat")}
    except Exception:
        return None


def _status(keys, engine, env_path: Path) -> str:
    rows = keys.pools(env_path)
    if not rows:
        return ("_No API key in `usr/.env` yet._ Add several at once: "
                "`/kame-keys add AIza...,AIza...` or `/kame-keys import google keys.txt`.")
    health = {}
    for pool in engine.pool_report().get("pools") or []:
        for key in pool.get("keys") or []:
            health.setdefault(key["id"], key["state"])
    lines = ["| provider | variable | keys | resting | left rotation |", "|---|---|---|---|---|"]
    for provider, variable, found in rows:
        ids = [engine._key_short_id(k) for k in found]
        resting = sum(1 for i in ids if health.get(i) == "resting")
        retired = sum(1 for i in ids if health.get(i) == "retired")
        lines.append(f"| {provider} | `{variable}` | {len(found)} | {resting} | {retired} |")
    lines.append("")
    for provider, variable, found in rows:
        if len(found) > 1:
            masked = ", ".join(f"{keys.mask(k)} ({engine._key_short_id(k)})" for k in found)
            lines.append(f"- **{provider}**: {masked}")
    return "\n".join(lines)


def _show(title: str, content: str) -> dict[str, Any]:
    return {"text": "", "effects": [{"type": "show_markdown", "title": title, "content": content}]}


def _toast(message: str, level: str = "success") -> dict[str, Any]:
    return {"text": "", "effects": [{"type": "toast", "message": message, "level": level}]}
