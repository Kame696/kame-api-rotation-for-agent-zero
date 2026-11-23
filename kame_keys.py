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

import codecs
import os
import re
import secrets
import shutil
import time
import threading
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

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

# 1.8.1.6. Agent Zero v2.13's chat provider ids (conf/model_providers.yaml): the
# names `split_provider` accepts when the host's own list cannot be read. The
# command adds whatever the running Agent Zero lists, plugins included.
_A0_PROVIDERS = frozenset({
    "a0_venice", "anthropic", "azure", "bedrock", "cerebras", "cometapi", "deepseek",
    "github_copilot", "google", "groq", "huggingface", "llama_cpp", "lm_studio",
    "mistral", "moonshot", "nebius", "nvidia_nim", "ollama", "ollama_cloud", "omlx",
    "openai", "openrouter", "other", "sambanova", "venice", "vllm", "xai", "zai",
    "zai_coding",
})


def canonical_provider(provider: str, keys: Optional[List[str]] = None) -> str:
    """Agent Zero's id for `provider`: the key prefix first, then the alias table."""
    guessed = guess_provider(keys or []) if keys else ""
    if guessed:
        return guessed
    name = str(provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(name, name)


# Longest first: BOM_UTF32_LE begins with BOM_UTF16_LE. The codec names carry no
# endianness suffix so the codec itself consumes the mark. Ported from the
# Hermes port's core/keys.py (1.8.1.6).
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def decode_text(data: bytes) -> str:
    """An imported file as text, honouring whatever byte-order mark it starts with.

    1.8.1.6: the command decoded UTF-16 LE and UTF-8 only; a UTF-16 BE or
    UTF-32 file became mojibake with NULs in every key. Unmarked is UTF-8.
    ``errors="replace"``: a file that is not text yields refused tokens and a
    report, never an exception in a chat turn.
    """
    if not isinstance(data, (bytes, bytearray)):
        return str(data or "")
    for mark, encoding in _BOMS:
        if data.startswith(mark):
            return bytes(data).decode(encoding, errors="replace")
    return bytes(data).decode("utf-8", errors="replace")


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


# 1.8.1.5, ported from the Hermes port's core/keys.py: what a pasted token must
# look like before it is written into the .env as a key. `split_keys` wrote every
# token it was given -- the words of `minhas chaves: K1, K2`, a whole
# `OPENAI_API_KEY=sk-...` line, a key with a zero-width space or smart quotes
# from a mangled copy -- and each one sat in the pool failing at the provider.
MIN_KEY_LENGTH = 16
MAX_KEY_LENGTH = 512
_NOT_A_KEY = re.compile(r"^(?:https?://|[A-Z_]+=|#|//|-{2,})", re.I)
_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(.+?)\s*$")
_QUOTES = "\"'`"


def _unwrap(token: str) -> str:
    """Peel a byte-order mark, a `NAME=value` wrapper and matching quotes."""
    text = str(token or "").strip().strip("\ufeff\ufffe").strip()
    match = _ASSIGNMENT.match(text)
    # `https://host` has the assignment shape too; keep the evidence it is a URL.
    if match and not match.group(2).startswith("//"):
        text = match.group(2).strip()
    while len(text) >= 2 and text[0] in _QUOTES and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


def looks_like_key(token: str) -> bool:
    """Long enough, one word, printable ASCII, and not a URL or a comment."""
    text = str(token or "").strip()
    if not (MIN_KEY_LENGTH <= len(text) <= MAX_KEY_LENGTH):
        return False
    if _NOT_A_KEY.match(text):
        return False
    # A zero-width space, a smart quote or a U+FFFD from a mis-decoded file
    # fails at the provider with a confusing 401 much later.
    return all(32 < ord(char) < 127 for char in text)


def pasted_keys(text: str) -> Tuple[List[str], List[str]]:
    """``(keys, rejected)`` from text a person pasted or a file held.

    Order is kept and repeats collapse. ``rejected`` holds only tokens long
    enough to have been meant as a key: the short words of a sentence around
    the keys are dropped without comment.
    """
    keys: List[str] = []
    rejected: List[str] = []
    seen = set()
    for piece in _SPLIT.split(str(text or "")):
        candidate = _unwrap(piece)
        if not candidate:
            continue
        if not looks_like_key(candidate):
            if len(candidate) >= MIN_KEY_LENGTH:
                rejected.append(candidate)
            continue
        if candidate not in seen:
            seen.add(candidate)
            keys.append(candidate)
    return keys, rejected


def guess_provider(keys: List[str]) -> str:
    """The provider every key's prefix agrees on, or "" when they do not."""
    found = set()
    for key in keys:
        match = next((p for prefix, p in _PREFIXES if key.startswith(prefix)), "")
        if not match:
            return ""
        found.add(match)
    return found.pop() if len(found) == 1 else ""


def split_provider(text: str, known: Optional[Iterable[str]] = None,
                   env: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """`("openrouter", "sk-or-...")` when the first word names a provider.

    1.8.1.6: a provider Agent Zero knows -- its own list (``known``, from the
    host), the ids and aliases above -- or one the .env (``env``) already holds
    a key line for. Any other first word used to be taken as a provider:
    `/kame-keys add minhas chaves sk-...` wrote the key into API_KEY_MINHAS,
    a variable nothing reads. Such a word is now text, and a key whose prefix
    does not name its provider gets the "Which provider?" question instead.
    """
    text = str(text or "").strip()
    first, _, rest = text.partition(" ")
    word = first.lower()
    if (_PROVIDER.match(word) and not guess_provider([first]) and rest.strip()
            and _names_a_provider(word, known, env)):
        return word, rest.strip()
    return "", text


def _names_a_provider(word: str, known: Optional[Iterable[str]],
                      env: Optional[Dict[str, str]]) -> bool:
    name = _PROVIDER_ALIASES.get(word, word)
    names = set(_A0_PROVIDERS) | {str(k).strip().lower() for k in (known or ())}
    if word in names or name in names:
        return True
    up = word.upper()
    return any(str((env or {}).get(var, "") or "").strip() not in ("", "None")
               for var in (f"API_KEY_{up}", f"{up}_API_KEY", f"{up}_API_TOKEN"))


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
    if quote:
        # v1.8.1.5: an opening quote that never closes is not a value --
        # python-dotenv, which Agent Zero reads its .env with, refuses the line.
        # Kept, `"sk-a` was imported with the quote glued to the key.
        return ""
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
        # v1.8.1.5: a byte-order mark (Notepad, `Set-Content -Encoding
        # utf8BOM`) glued to the first name made that variable invisible, so
        # the first key line of such a file was silently skipped. The Hermes
        # port strips it too (core/keys.py _BOM_CHARS).
        line = line.strip().lstrip("\ufeff\ufffe")
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


def parse_import(text: str, provider: str = "",
                 rejected: Optional[List[str]] = None) -> Tuple[str, List[str]]:
    """Read either a raw key list or dotenv-formatted key file.

    A dotenv file may contain comments and unrelated settings. With an explicit
    provider only that provider's API variable is imported. Without one, KAME
    accepts the file only when all API-key assignments name one provider.

    Only tokens that look like keys are returned (1.8.1.5); the others are
    appended to ``rejected`` when the caller passes a list.
    """
    chosen, found = _parse_import(text, provider)
    keys, refused = pasted_keys("\n".join(found))
    if rejected is not None:
        rejected.extend(refused)
    return chosen, keys


def _parse_import(text: str, provider: str = "") -> Tuple[str, List[str]]:
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
        save: Callable[[str, str], None], rejected: Optional[List[str]] = None,
        check: bool = False) -> str:
    """Merge ``keys`` into the provider's line and say what happened.

    ``check`` (1.9.0.5, D2) is True only for an explicit ``/kame-keys add`` or ``import``: each NEW Google key
    is then asked once. Never at start, never from a loop.
    """
    # Agent Zero runs commands in one process. Serialize read/merge/save so
    # two imports cannot both read the same old list and lose one addition.
    added_keys: List[str] = []
    with _IMPORT_LOCK:
        message = _add_locked(path, provider, keys, save, added_keys)
    if check and added_keys and canonical_provider(provider, keys) == "google":
        # outside the lock: up to 10 s per key must not hold up another import
        message += "\n" + "\n".join(_check_with_google(added_keys))
    if rejected:
        # Masked like every key: a rejected token is usually junk, and
        # "usually" is not worth betting a key on.
        message += "\n" + "\n".join(
            f"- {mask(token)} skipped: does not look like an API key" for token in rejected
        )
    return message


def _add_locked(path: Path, provider: str, keys: List[str],
                save: Callable[[str, str], None], added_out: Optional[List[str]] = None) -> str:
    """Merge `keys` into the provider's line. Returns the message to show; the keys written go to ``added_out``."""
    keys = [k for k in dict.fromkeys(str(k or "").strip() for k in keys) if k]   # 1.9.0.5: no empty item, no repeat
    if not keys:
        return "No key found in that text."
    provider = canonical_provider(provider, keys) if provider else guess_provider(keys)
    if not provider:
        return ("Which provider? The keys do not say. Usage: "
                "`/kame-keys add <provider> key1,key2` — e.g. `openai`, `deepseek`.")
    # 1.9.0.5 (D2, as the Hermes port): a key of the wrong shape is named and still written; the user decides
    wrong = [k for k in keys if shape_problem(provider, k)]
    notes = [f"- key {_tail(k)} {shape_problem(provider, k)}; it is written anyway, check it." for k in wrong]
    env = read_env(path)
    var = env_var_for(provider, env)
    merged, added = plan_add(env.get(var, ""), keys)
    if not added:
        return "\n".join([f"`{var}` already holds all {len(keys)} key(s). Nothing changed."] + notes)
    existed = path.is_file()
    saved = backup(path)
    if existed and saved is None:
        return "Could not create a backup of the existing .env. Nothing changed."
    save(var, ",".join(merged))
    if added_out is not None:
        added_out.extend(added)
    lines = [f"Added {len(added)} key(s) to `{var}` — {len(merged)} in the pool now."]
    lines += [f"- {mask(k)}" for k in added]
    lines += notes
    skipped = len(keys) - len(added)
    if skipped:
        lines.append(f"{skipped} already there, skipped.")
    if saved:
        lines.append(f"Backup of the previous file: `usr/{saved}` (plaintext, last {MAX_BACKUPS} kept).")
    lines.append("KAME picks them up on the next call.")
    return "\n".join(lines)


# --- 1.9.0.5 (D2, decisions/0036): the keys are checked when they are added, never at start ------------------------

#: The shape a provider's keys have, by Agent Zero's provider id: ``(prefix, length, exact)``. Google's classic
#: ``AIza`` key is exactly 39 characters; its newer ``AQ.`` keys are at least 40. Every other provider is judged
#: only by "not empty".
_KEY_SHAPES = {
    "google": (("AIza", 39, True), ("AQ.", 40, False)),
}
_PROVIDER_LABELS = {"google": "Google"}
_PROBE_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1"
_PROBE_HEADER = "x-goog-api-key"
_PROBE_TIMEOUT_S = 10.0
_REFUSING_STATUSES = frozenset({400, 401, 403})


def shape_problem(provider: str, key: str) -> Optional[str]:
    """``"does not look like a Google key"`` when ``key`` breaks the provider's shape, else ``None``.

    Pure and offline. A provider with no listed shape accepts any non-empty key; an empty one is refused for
    every provider. ``gemini`` is ``google`` here, as everywhere in this module.
    """
    name = canonical_provider(provider)
    label = _PROVIDER_LABELS.get(name) or name or "provider"
    text = str(key or "").strip()
    if not text:
        return f"does not look like a {label} key"
    shape = _KEY_SHAPES.get(name)
    if shape is None:
        return None
    for prefix, length, exact in shape:
        if text.startswith(prefix) and (len(text) == length if exact else len(text) >= length):
            return None
    return f"does not look like a {label} key"


def http_status(url: str, headers: Optional[Dict[str, str]] = None,
                timeout: float = _PROBE_TIMEOUT_S) -> Optional[int]:
    """One GET and its status code; ``None`` when nothing answered. The tests replace this function.

    The key travels in a header, never in the URL, and an exception is reduced to "no answer": nothing here can
    put a key in a log.
    """
    import urllib.error
    import urllib.request
    try:
        request = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return int(reply.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return None


def _tail(key: str) -> str:
    return f"...{str(key)[-4:]}"


def _check_with_google(added: List[str]) -> List[str]:
    """Ask Google once per NEW key; the lines to add to the reply. Never raises."""
    refused, unchecked = [], 0
    for key in added:
        try:
            status = http_status(_PROBE_URL, {_PROBE_HEADER: key}, _PROBE_TIMEOUT_S)
        except Exception:
            status = None
        if status in _REFUSING_STATUSES:
            refused.append((key, int(status)))
        elif status is None or not 200 <= int(status) < 300:
            unchecked += 1
    lines = [f"- key {_tail(k)} was rejected by Google (HTTP {code}). It was added anyway; "
             "remove it if it is wrong." for k, code in refused]
    if unchecked:
        lines.append(f"- could not check {unchecked} key(s) with Google just now (no answer); they were added.")
    return lines


# --- 1.9.0.5 (D1, decisions/0036): leave Agent Zero clean ------------------------------------------------------------

def undo(path: Path, provider: str, save: Callable[[str, str], None]) -> str:
    """Keep only the FIRST key of every key line KAME rotates (or of ``provider``'s line), after a backup.

    If the plugin is removed while ``API_KEY_GOOGLE=k1,k2,k3`` is still in ``usr/.env``, Agent Zero would send the
    whole list as one key. The keys taken out stay in the backup. Counts and masked keys only.
    """
    with _IMPORT_LOCK:
        return _undo_locked(path, provider, save)


def _undo_locked(path: Path, provider: str, save: Callable[[str, str], None]) -> str:
    rows = pools(path)
    wanted = canonical_provider(provider) if str(provider or "").strip() else ""
    if wanted:
        rows = [row for row in rows if canonical_provider(row[0]) == wanted]
        if not rows:
            return f"There is no key line for `{wanted}` in `usr/.env`. Nothing changed."
    many = [row for row in rows if len(row[2]) > 1]
    if not many:
        return "Nothing to undo: every key line holds a single key already, so you can remove the plugin."
    existed = path.is_file()
    saved = backup(path)
    if existed and saved is None:
        return "Could not create a backup of the existing .env. Nothing changed."
    lines = [f"Kept the first key of {len(many)} line(s) in `usr/.env`:"]
    for _provider, variable, found in many:
        save(variable, found[0])
        out = ", ".join(mask(k) for k in found[1:])
        lines.append(f"- `{variable}`: kept 1 of {len(found)} ({mask(found[0])}); took out {out}")
    if saved:
        lines.append(f"The keys taken out are in the backup of the previous file: `usr/{saved}` "
                     f"(plaintext, last {MAX_BACKUPS} kept).")
    lines.append("Agent Zero now reads one valid key per provider; you can now remove the plugin.")
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
