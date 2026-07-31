# KAME ↔ Agent Zero compatibility

**KAME 1.0.8 — verified against Agent Zero v2.7 (2026-07-30).**
Supported range: Agent Zero **v1.14+** and the **whole V2 line**.

This file is the single source of truth for *"Agent Zero shipped a new version — is
KAME still fine, and what do I have to look at?"* It is written to be actionable by
someone (or some agent) opening a brand-new session with no memory of the last one.

---

## 1. TL;DR — the whole check is one command

```bash
python tools/a0_upgrade_check.py --latest            # is there even a new A0?
git clone --depth 1 --branch <tag> https://github.com/agent0ai/agent-zero.git /tmp/a0
python tools/a0_upgrade_check.py /tmp/a0             # the real audit
```

Exit `0` = KAME is compatible, nothing to do.
Exit `1` = something needs a human — the output names the exact symbol and says *why
KAME cares about it*.
Exit `2` = the checker itself could not run (bad path, unreadable baseline).

After you have audited whatever it flagged **and the live harness is green**:

```bash
python tools/a0_upgrade_check.py /tmp/a0 --update-baseline v2.8
```

That rewrites `a0_compat.json`. Then update the four human-facing places listed in
§5.

The audit needs A0's runtime deps importable for the live-test stage
(`litellm`, `langchain-core`, `pillow`, `nest_asyncio`, …). Stages 1–2 (version +
fingerprints) work in a bare Python — they parse A0's source with `ast` instead of
importing it. Use `--skip-tests` if you only want those.

---

## 2. Compatibility matrix

| Agent Zero | KAME | Status | Notes |
|---|---|---|---|
| v1.14 – v1.x | 1.0.8 | supported | `unified_call` path; no `unified_turn` |
| V2.0 | 1.0.8 | supported | |
| V2.1 – V2.6 | 1.0.8 | supported | monologue switched to `unified_turn`; KAME patches both |
| **V2.7** | **1.0.8** | **verified** | new stateful `ChatCompletionsStreamParser` added upstream; KAME still uses the static `ChatCompletionsTransport.parse`, which v2.7 kept |

KAME is deliberately **defensive**: every patch target is looked up with
`getattr(..., None)` and skipped if absent, so a missing symbol degrades one feature
instead of crashing A0 at import time. That is exactly why the fingerprint check
matters — a silent skip looks like success.

---

## 3. Patch-point map — everything KAME touches in A0

`tools/a0_upgrade_check.py` watches all 14 of these (see `a0_compat.json` for the
per-symbol `why`). This is the map to read when one of them is flagged.

### Monkey-patched at runtime (`kame_engine.apply_kame_patch`)

| A0 symbol | File in A0 | What KAME does |
|---|---|---|
| `LiteLLMChatWrapper.unified_call` | `models.py` | Full replacement. Rotation + retry loop around the original. Must keep returning `(response, reasoning)`. |
| `LiteLLMChatWrapper.unified_turn` | `models.py` | Full replacement (V2.1+ monologue calls this one). Must keep returning an `LLMResult`. |
| `Topic.summarize_messages` | `helpers/history.py` | Wrapped with the compression timeout guard. |
| `Bulk.summarize` | `helpers/history.py` | Same guard. |
| `RateLimiter` | `helpers/rate_limiter.py` | Swaps `asyncio.Lock` for `threading.Lock`, replaces `cleanup` / `get_total`. Depends on `._lock`, `.values`, `.timeframe`. |

### Depended on, not patched

| A0 symbol | File in A0 | Why it matters |
|---|---|---|
| `ChatGenerationResult` | `models.py` | Stream accumulator. Needs `.add_chunk(parsed) -> {response_delta, reasoning_delta}`, `.response`, `.reasoning`. |
| `ChatCompletionsTransport.parse` | `helpers/litellm_transport.py` | KAME's V2 chunk parser. **v2.7 introduced a stateful `ChatCompletionsStreamParser` alongside it** — if a future A0 *removes* the static `parse`, KAME must switch to the parser object. |
| `LLMResult.from_chat` | `helpers/llm_result.py` | Built in `_kame_unified_turn`. KAME passes keyword-only args: added params are safe, renamed/newly-required ones break the turn path. |
| `Agent.handle_intervention` | `agent.py` | `_kame_honor_intervention()` calls it between rotations and inside every 1-second cooling slice, so a **nudge** is never slept through. |
| `ResponseTool.execute` | `tools/response.py` | The arg shape KAME Shield heals into. Which keys it reads (`text` / `message`) decides whether the salvage lands. |
| `get_dotenv_value` | `helpers/dotenv.py` | `_get_all_api_keys` reads `API_KEY_<PROVIDER>`, `<PROVIDER>_API_KEY`, `<PROVIDER>_API_TOKEN` through it. No keys found = no rotation at all. |
| `RepairableException` | `helpers/errors.py` | In KAME's passthrough tuple — must reach A0's repair loop, never be swallowed as a failed API call. |

### Extension points (the fragile ones)

A0's `@extensible` decorator derives an implicit extension folder from
**module + qualname**:

```
extensions/python/_functions/<module>/<Class>/<method>/{start,end}/
```

KAME ships two:

| Folder | Hooks | Breaks silently if… |
|---|---|---|
| `_functions/agent/Agent/monologue/start/_10_kame_api_rotation.py` | stashes the agent for intervention handling | `Agent.monologue` is renamed or moves out of `agent.py` |
| `_functions/agent/Agent/validate_tool_request/start/_10_kame_heal_tool_args.py` | KAME Shield's arg healer | `Agent.validate_tool_request` is renamed or moves |

Plus one classic extension: `extensions/python/message_loop_prompts_after/_91_recall_wait.py`.

**This is the #1 silent-failure risk in the whole plugin**: if A0 renames the
method, nothing errors — the folder simply never fires. `Agent.monologue` and
`Agent.validate_tool_request` are in the watch list precisely for this.

---

## 4. Where to look in the A0 tree — the endpoint / behavior cheat-sheet

Facts already traced from A0 v2.7 source. Re-verify rather than re-derive.

**Repo:** `https://github.com/agent0ai/agent-zero` · tags API:
`https://api.github.com/repos/agent0ai/agent-zero/tags`

| Question | Where the answer is |
|---|---|
| Why doesn't a new message interrupt a running agent? | `webui/index.js` — when `selectedContext?.running`, the UI calls `/message_queue_add` instead of `/message`. It is **queued**, not an interrupt. |
| Where does the queue drain? | `extensions/python/process_chain_end/_50_process_queue.py` — only after the monologue ends. |
| Where does the "(queued batch)" label come from? | `helpers/message_queue.py` |
| What actually interrupts? | The **nudge** button. Server-side it raises `InterventionException` via `Agent.handle_intervention()`. This is upstream A0 behavior, **not** a KAME bug. |
| "Agent stopped after N consecutive unusable model responses" | `extensions/python/_functions/agent/Agent/hist_add_warning/end/_90_stop_unusable_response_loop.py`, message text in `prompts/fw.msg_unusable_response_limit.md`, default `max_consecutive_unusable_responses = 2` in `helpers/settings.py`. It is A0's **cost circuit-breaker**, tripped by `fw.msg_misformat.md` / `fw.msg_repeat.md` in consecutive iterations. The API call *succeeded* — there is no error for KAME to rotate on. Raise the setting or use a model that emits clean JSON. |
| Early-stop contract for streaming | `agent.py`, inside `Agent.monologue`'s `stream_callback` — `handle_intervention()` then `return full.strip()`. |
| API keys / env var names | `helpers/dotenv.py` :: `get_dotenv_value` |
| Provider + model settings shape | `helpers/settings.py` |

---

## 5. Upgrade runbook

Do these in order. Every step is verifiable.

1. **Detect** — `python tools/a0_upgrade_check.py --latest`. Same tag as
   `verified_against` in `a0_compat.json`? Stop, nothing to do.
2. **Fetch** — shallow-clone the new tag into a scratch dir.
3. **Audit** — `python tools/a0_upgrade_check.py <path>`. Read *every* flagged
   symbol against the table in §3. `MISSING` is far more serious than `CHANGED`:
   missing means KAME's `getattr` will silently skip that patch.
4. **Diff the real thing** — for anything flagged:
   `git -C <a0> diff <old-tag> <new-tag> -- <file>`.
5. **Fix + test** — change `kame_engine.py`, then run the full suite:
   `python tests/test_a0_compat.py <a0-path>` (live, applies real patches) plus
   `python tests/test_v1_0_8.py` and the earlier `test_v1_0_*.py` regressions.
6. **Re-pin** — `python tools/a0_upgrade_check.py <path> --update-baseline vX.Y`.
7. **Update the four human-facing places** (the checker prints this reminder):
   - `plugin.yaml` → the `[UPDATED TO A0 VX.Y]` prefix at the **start** of
     `description` (users see it in A0's plugin list without opening anything)
   - `README.md` → the Agent Zero badge + the compatibility line
   - `CHANGELOG.md` → what changed and against which A0
   - `COMPATIBILITY.md` (this file) → the header line and the matrix in §2
8. **Ship** — rebuild `releases/KAME_v<ver>.zip`, commit, move/create the tag,
   replace the GitHub release asset, update the release body.

### Gotchas learned the hard way

- **Console encoding.** Windows `cp1252` cannot encode KAME's emoji banner. The
  banner is cosmetic and is now wrapped in its own `try/except` *after*
  `_KAME_PATCHED = True` — never let it decide whether the patch "succeeded".
  Same class of bug bit the checker's `subprocess` capture (fixed with
  `encoding="utf-8", errors="replace"`).
- **Empty `getattr` skips are silent.** Green tests do not prove a patch applied;
  the fingerprint check is what proves the target still exists.
- **429 vs 403.** A 403-class refusal (project suspended / API not enabled / model
  not authorized) is *not* a rate limit. Since 1.0.8 it is classified `denied` and
  quarantined for the daily cooldown, never retried every 20s.

---

## 6. Files in this plugin

```
kame_engine.py      the whole engine: rotation, classification, all monkey-patches
hooks.py            plugin entry point — calls apply_kame_patch()
plugin.yaml         name/version/description (the [UPDATED TO A0 VX.Y] tag lives here)
a0_compat.json      pinned baseline: watched symbols + source fingerprints
COMPATIBILITY.md    this file
tools/
  a0_upgrade_check.py   the one-command audit
tests/
  test_a0_compat.py     LIVE — applies real patches against a real A0 checkout
  test_v1_0_8.py        22 checks for the current version's behavior
  test_v1_0_[2-7]*.py   regression suites, keep them all green
extensions/python/...   the two _functions hooks + the recall-wait extension
webui/config.html       settings UI
```
