# KAME ↔ Agent Zero compatibility

**KAME 1.0.9 — verified against Agent Zero v2.8 (2026-08-09).**
Live-verified end-to-end on **v1.14, v1.20, v2.1, v2.4, v2.7, v2.8**.
Supported range: Agent Zero **v1.14+** and the **whole V2 line**.

This file is the single source of truth for *"Agent Zero shipped a new version — is
KAME still fine, and what do I have to look at?"* It is written to be actionable by
someone (or some agent) opening a brand-new session with no memory of the last one.

---

## 0. Read this first — what changed in 1.0.9

Up to 1.0.8, KAME **re-implemented** Agent Zero's model call: it picked a key, then
built the request itself, called `litellm.acompletion`, parsed the stream chunk by
chunk, accumulated the text and constructed the result object. That worked, but it
meant KAME had a copy of A0's internals — and every time A0 touched its model layer,
that copy went stale. Six of the fourteen watched symbols existed only to protect
that copy.

1.0.9 deletes the copy. **KAME now only chooses the key**, then calls A0's own
`unified_call` / `unified_turn` with `api_key=<chosen>` injected, and returns A0's
result object *by identity*. A0 owns the request, the stream, the parsing and the
result.

Three consequences that matter when you audit a new A0:

1. **The stream parser, the chunk accumulator, the transport parser, the result
   builder and `litellm` itself left KAME's compatibility surface entirely.** The
   watch list went 14 → 11 and, more importantly, the remaining model-layer entries
   are now `adaptive` rather than `critical` — A0 may rewrite those function bodies
   freely.
2. **The entry points are found by SHAPE, not by name.** `_kame_find_entry_points()`
   looks for coroutine methods on `LiteLLMChatWrapper` whose signature contains
   `messages`, `response_callback`, `reasoning_callback` and `tokens_callback`. An
   upstream *rename* is survivable.
3. **Failure is layered, and layer 3 is silent-safe.** See §3.1.

What KAME still needs from A0's model layer is small and stable:

- `LiteLLMChatWrapper` must exist in `models.py`,
- at least one coroutine on it must take those four parameter names,
- and it must keep forwarding unknown `**kwargs` (specifically `api_key`) down to
  litellm.

That is the whole contract now.

---

## 1. TL;DR — the whole check is one command

```bash
python tools/a0_upgrade_check.py --latest            # is there even a new A0?
git clone --depth 1 --branch <tag> https://github.com/agent0ai/agent-zero.git /tmp/a0
python tools/a0_upgrade_check.py /tmp/a0             # the real audit
```

Exit `0` = KAME is compatible, nothing to do.
Exit `1` = something needs a human — the output names the exact symbol, its
**severity**, and says *why KAME cares about it*.
Exit `2` = the checker itself could not run (bad path, unreadable baseline).

Severities (also in `a0_compat.json → severity_legend`):

| Severity | Meaning |
|---|---|
| `critical` | if this breaks, KAME does not rotate at all |
| `degraded` | if this breaks, ONE shield stops; rotation keeps working |
| `adaptive` | KAME absorbs a change here automatically — the fingerprint is informational, and the **live harness** is the real verdict |

A `CHANGED` on an `adaptive` symbol is expected noise; the checker says so out loud
so it does not read as a red flag.

After you have audited whatever it flagged **and the live harness is green**:

```bash
python tools/a0_upgrade_check.py /tmp/a0 --update-baseline v2.8
```

That rewrites `a0_compat.json` (fingerprints + `verified_against` + `verified_on`).
Then update the human-facing places listed in §6, step 7.

The audit needs A0's runtime deps importable for the live-test stage. They are
pinned in **`tests/requirements.txt`** — install that, not A0's own 60-package
`requirements.txt`:

```bash
pip install -r tests/requirements.txt
```

Stages 1–2 (version + fingerprints) work in a bare Python — they parse A0's source
with `ast` instead of importing it. Use `--skip-tests` if you only want those.

---

## 2. Compatibility matrix

| Agent Zero | KAME | Status | Notes |
|---|---|---|---|
| v1.14 – v1.x | 1.0.9 | **live-verified** (v1.14, v1.20) | only `unified_call` exists; KAME binds the one entry point it finds |
| V2.0 | 1.0.9 | supported | |
| V2.1 – V2.6 | 1.0.9 | **live-verified** (v2.1, v2.4) | monologue switched to `unified_turn`; KAME binds both |
| V2.7 | 1.0.9 | **live-verified** | |
| **V2.8** | **1.0.9** | **verified — current baseline** | 11/11 fingerprints unchanged, live harness green. See §2.1. |

"live-verified" means `tests/test_a0_compat.py` was run against a real checkout of
that tag: KAME's real patches applied to A0's real classes, a real
`LiteLLMChatWrapper` instance, a dead key returning `429` and a good key answering —
and the rotation actually happened, in-process, end to end.

### 2.1 What Agent Zero v2.8 changed (audited 2026-08-01, re-confirmed 2026-08-09)

v2.8 is a large release, but nothing it touches is load-bearing for KAME.

| Upstream change | Effect on KAME |
|---|---|
| `helpers/extension.py` gained `get_webui_extension_manifest()` | **Purely additive.** The `_functions/<module>/<Class>/<method>/` path derivation is byte-identical. |
| New `helpers/ui_bundler.py`; `ui_server.py` serves a splash page, `/safe`, and a gzipped `/ui/asset-bundle` | The bundler walks `plugins.get_enabled_plugin_paths(agent, "webui")`, so `webui/config.html` is picked up generically. No plugin-side change. |
| `max_consecutive_unusable_responses` default **2 → 5** (`helpers/settings.py`) | Upstream relaxed its own cost circuit-breaker. Still **not** a KAME condition — see §5. |
| New `/stop` endpoint (`api/stop.py`) + `stop.command.yaml` | A real hard stop (`context.kill_process()`), separate from **nudge**. Queued messages still do not interrupt — see §5. |
| `a0_api_mode: chat` added to 17 more providers (9 → 26, **including `google`**) | Neutral. Since 1.0.9 KAME does not choose the mode at all — A0 does, exactly as it would without the plugin. |
| `litellm_transport` request builder sets `tool_choice: "required"` + `parallel_tool_calls: False` when tools are present | Now **inside** what KAME delegates to. In 1.0.8 KAME bypassed this and lost the behavior; in 1.0.9 the plugin inherits it for free. |
| `monologue_start/_60_rename_chat.py` moved into a `_chat_naming` plugin | Unrelated extension point. |

KAME is deliberately **defensive**: every patch target is looked up with
`getattr(..., None)` and skipped if absent, so a missing symbol degrades one feature
instead of crashing A0 at import time. That is exactly why the fingerprint check
matters — a silent skip looks like success.

---

## 3. Patch-point map — everything KAME touches in A0

`tools/a0_upgrade_check.py` watches all 11 of these (see `a0_compat.json` for the
per-symbol `why` and `severity`). This is the map to read when one is flagged.

### 3.1 The rotation core — bound by shape, in three layers

KAME wraps A0's model entry points. It finds them in this order and stops at the
first that works:

| Layer | How the entry point is found | Result |
|---|---|---|
| **1** | **By shape** — a coroutine on `LiteLLMChatWrapper` whose signature contains `messages`, `response_callback`, `reasoning_callback`, `tokens_callback` | Full rotation. Survives an upstream **rename**. |
| **2** | **By legacy name** — `unified_turn`, then `unified_call` | Full rotation. The fallback if A0 changes those parameter names but keeps the methods. |
| **3** | **Nothing is wrapped** | KAME prints **one** honest console line and gets out of the way. Agent Zero runs natively — no crash, no exception, no half-patched state. |

Layer 3 is the whole safety story: the worst case of a future A0 release is that the
user loses rotation and is told so in one line, not that their agent breaks.

| A0 symbol | File in A0 | What KAME does | Severity |
|---|---|---|---|
| `LiteLLMChatWrapper.unified_call` | `models.py` | Wrapped. KAME picks the key, calls the **original** with `api_key=` injected, returns its result unchanged. | adaptive |
| `LiteLLMChatWrapper.unified_turn` | `models.py` | Same (V2.1+ monologue calls this one). | adaptive |
| `get_dotenv_value` | `helpers/dotenv.py` | Reads `API_KEY_<PROVIDER>`, `<PROVIDER>_API_KEY`, `<PROVIDER>_API_TOKEN`. No keys found = no rotation at all. | **critical** |

The two `adaptive` entries are informational: their bodies may change freely, they
may be renamed, and KAME never parses what they return. What must hold is the
`**kwargs → api_key` passthrough and the four parameter names.

**A0's own retry loop is disabled per call.** A0 retries internally
(`a0_retry_attempts` / `a0_retry_delay_seconds` popped from the call kwargs), which
in 1.0.4 caused a 30–40s hang before KAME ever saw the failure. KAME regex-extracts
those knob names *from A0's source at runtime* and sets attempts to 0, so an upstream
**rename of the knobs is picked up automatically**. If the extraction ever finds
nothing, rotation still works — it is just slower.

### 3.2 Accessory shields — each isolated in its own `try`

A failure in any of these can no longer prevent rotation from installing.

| A0 symbol | File in A0 | What KAME does | Severity |
|---|---|---|---|
| `Topic.summarize_messages` | `helpers/history.py` | Wrapped with the compression timeout guard. | degraded |
| `Bulk.summarize` | `helpers/history.py` | Same guard, bulk path. | degraded |
| `RateLimiter` | `helpers/rate_limiter.py` | Swaps `asyncio.Lock` for `threading.Lock`, replaces `cleanup` / `get_total`. Depends on `._lock`, `.values`, `.timeframe`. | degraded |
| `Agent.handle_intervention` | `agent.py` | Called between rotations and inside every 1-second cooling slice, so a **nudge** is never slept through. | degraded |
| `Agent.validate_tool_request` | `agent.py` | Extension folder for KAME Shield's tool-arg healer. | degraded |
| `ResponseTool.execute` | `tools/response.py` | The arg shape KAME Shield heals into. Which keys it reads (`text` / `message`) decides whether the salvage lands. | degraded |
| `RepairableException` | `helpers/errors.py` | In KAME's passthrough tuple — must reach A0's repair loop, never be swallowed as a failed API call. Degrades to an empty tuple if the import fails. | degraded |
| `Agent.monologue` | `agent.py` | One of three activation doors (§3.3). | degraded |

### 3.3 Activation — three independent doors

A0's `@extensible` decorator derives an implicit extension folder from
**module + qualname**:

```
extensions/python/_functions/<module>/<Class>/<method>/{start,end}/
```

Up to 1.0.8 that derived path was the **only** way KAME turned itself on — a silent
single point of failure, because a rename upstream produces no error, the folder
just never fires. 1.0.9 ships three doors, all calling the same idempotent
`kame_activation.activate()`:

| Door | Type | Breaks if… |
|---|---|---|
| `_functions/agent/Agent/monologue/start/_10_kame_api_rotation.py` | derived path | `Agent.monologue` is renamed or moves out of `agent.py` |
| `agent_init/_10_kame_api_rotation.py` | **named** point | A0 stops calling `call_extensions_sync("agent_init", ...)` |
| `monologue_start/_10_kame_api_rotation.py` | **named** point | A0 stops calling `call_extensions("monologue_start", ...)` |

The two named points are hardcoded strings in `agent.py` and are **identical from
v1.14 through v2.8**. Activation is idempotent (`_KAME_PATCHED` guard), so all three
firing costs nothing.

Plus one classic extension, unrelated to activation:
`extensions/python/message_loop_prompts_after/_91_recall_wait.py`.

---

## 4. What KAME no longer depends on (removed in 1.0.9)

Do **not** re-add these to the watch list. They are gone from KAME's source, and
`tests/test_v1_0_9.py` group F asserts they stay gone.

| Former dependency | Why it is gone |
|---|---|
| `models._parse_chunk` | KAME does not parse chunks. A0 does. |
| `models.ChatGenerationResult` | KAME does not accumulate a stream. A0 does. |
| `ChatCompletionsTransport.parse` | Same — and this was the v2.7 "stateful parser" landmine. |
| `LLMResult.from_chat` | KAME does not build a result. It returns A0's, by identity. |
| `litellm.acompletion` | KAME does not import `litellm` at all any more. |
| the V1/V2 chunk-mode detection | There is one code path now, for every A0 version. |

---

## 5. Where to look in the A0 tree — the endpoint / behavior cheat-sheet

Facts already traced from A0 v2.8 source. Re-verify rather than re-derive.

**Repo:** `https://github.com/agent0ai/agent-zero` · tags API:
`https://api.github.com/repos/agent0ai/agent-zero/tags`

| Question | Where the answer is |
|---|---|
| Why doesn't a new message interrupt a running agent? | `webui/index.js` — when `selectedContext?.running`, the UI calls `/message_queue_add` instead of `/message`. It is **queued**, not an interrupt. |
| Where does the queue drain? | `extensions/python/process_chain_end/_50_process_queue.py` — only after the monologue ends. |
| Where does the "(queued batch)" label come from? | `helpers/message_queue.py` |
| What actually interrupts? | The **nudge** button (raises `InterventionException` via `Agent.handle_intervention()`), or — new in v2.8 — the `/stop` endpoint (`api/stop.py`, also a `stop` slash command), which calls `context.kill_process()`. Queued messages still never interrupt. Upstream A0 behavior, **not** a KAME bug. |
| "Agent stopped after N consecutive unusable model responses" | `extensions/python/_functions/agent/Agent/hist_add_warning/end/_90_stop_unusable_response_loop.py`, text in `prompts/fw.msg_unusable_response_limit.md`, default in `helpers/settings.py` — **2 up to v2.7, raised to 5 in v2.8**. A0's **cost circuit-breaker**, tripped by `fw.msg_misformat.md` / `fw.msg_repeat.md` in consecutive iterations. The API call *succeeded* — there is no error for KAME to rotate on. Raise the setting or use a model that emits clean JSON. |
| Early-stop contract for streaming | `agent.py`, inside `Agent.monologue`'s `stream_callback` — `handle_intervention()` then `return full.strip()`. Since 1.0.9 KAME never touches this: A0's own callback runs, and its **return value** (the early-stop signal) is passed back untouched. |
| Where A0 merges caller kwargs over instance kwargs | `models.py` — `{**self.kwargs, **kwargs}` on v1.x, `_merge_litellm_call_kwargs` on v2.8. Both are plain dict updates, which is *why* injecting `api_key=` wins. **If this ever stops being caller-wins, KAME's whole delegation model breaks.** |
| API keys / env var names | `helpers/dotenv.py` :: `get_dotenv_value` |
| Provider + model settings shape | `helpers/settings.py` |

---

## 6. Upgrade runbook

Do these in order. Every step is verifiable.

1. **Detect** — `python tools/a0_upgrade_check.py --latest`. Same tag as
   `verified_against` in `a0_compat.json`? Stop, nothing to do.
2. **Fetch** — shallow-clone the new tag into a scratch dir.
3. **Audit** — `python tools/a0_upgrade_check.py <path>`. Read every flagged symbol
   against §3, **starting from its severity**:
   - `adaptive` **CHANGED** → expected. The live harness is the verdict.
   - `degraded` → one shield is at risk; rotation is not.
   - `critical`, or **any** `MISSING` → stop and read the source. `MISSING` is far
     more serious than `CHANGED`: it means KAME's `getattr` will silently skip.
4. **Diff the real thing** — for anything flagged:
   `git -C <a0> diff <old-tag> <new-tag> -- <file>`.
5. **Test** — the live harness is stage 3 of the checker, but run the unit suites
   too if you changed `kame_engine.py`:
   ```bash
   python tests/test_a0_compat.py <a0-path>   # LIVE: real patches, real rotation
   python tests/test_v1_0_9.py                # current architecture
   python tests/test_v1_0_8.py                # and the earlier regressions
   ```
6. **Re-pin** — `python tools/a0_upgrade_check.py <path> --update-baseline vX.Y`.
7. **Update the human-facing places** (the checker prints this reminder):
   - `plugin.yaml` → the `[... VERIFIED ON A0 ...]` prefix at the **start** of
     `description` (users see it in A0's plugin list without opening anything)
   - `README.md` → the two badges + the compatibility line
   - `CHANGELOG.md` → what changed and against which A0
   - `COMPATIBILITY.md` (this file) → the header, the matrix in §2, §2.x
8. **Ship** — rebuild `releases/KAME_v<ver>.zip`, commit, move/create the tag,
   replace the GitHub release asset, update the release body.

### Gotchas learned the hard way

- **Console encoding.** Windows `cp1252` cannot encode KAME's emoji banner. The
  banner is cosmetic and is wrapped in its own `try/except` *after*
  `_KAME_PATCHED = True` — never let it decide whether the patch "succeeded".
  Same class of bug bit the checker's `subprocess` capture (fixed with
  `encoding="utf-8", errors="replace"`).
- **Empty `getattr` skips are silent.** Green tests do not prove a patch applied;
  the fingerprint check is what proves the target still exists.
- **429 vs 403.** A 403-class refusal (project suspended / API not enabled / model
  not authorized) is *not* a rate limit. Since 1.0.8 it is classified `denied` and
  quarantined for the daily cooldown, never retried every 20s.
- **A blank answer is not always a failure.** Since A0 V2, a streamed response
  callback *returns* the accumulated text as soon as a complete tool request has
  been streamed, and A0 breaks the stream there — legitimately producing an empty
  final result. KAME only rotates on an empty result when **nothing at all** was
  streamed (`progress["any"]` is false), and even then at most twice per turn.
  Removing that gate re-breaks every tool call.
- **A0 mutates the message list you hand it.** `unified_call` does
  `messages.insert(0, SystemMessage(...))` and `.append(HumanMessage(...))`. Every
  rotation attempt therefore gets a fresh `list(...)` copy with the system/user
  strings blanked, or the prompt grows by one message per retry.
- **`pip install -r tests/requirements.txt` must actually resolve.** It did not
  before 1.0.9. If you re-pin a langchain version, dry-run it.

---

## 7. Files in this plugin

```
kame_engine.py          the whole engine: key selection, classification, delegation
kame_activation.py      shared, idempotent activate() — the three doors all call it
hooks.py                plugin entry point — calls apply_kame_patch()
plugin.yaml             name/version/description (the [VERIFIED ON A0 ...] tag)
a0_compat.json          pinned baseline: 11 watched symbols + severities + hashes
COMPATIBILITY.md        this file
CHANGELOG.md            per-version history
tools/
  a0_upgrade_check.py   the one-command audit
tests/
  requirements.txt      pinned deps for the live harness (NOT A0's full requirements)
  test_a0_compat.py     LIVE — real patches + real rotation against a real checkout
  test_v1_0_9.py        62 checks: delegation, shape binding, layers, retry knobs
  test_v1_0_8.py        22 checks: early-stop contract, 403 quarantine, banner
  test_v1_0_[2-7]*.py   regression suites, keep them all green
extensions/python/
  _functions/agent/Agent/monologue/start/            activation door 1 (derived)
  agent_init/                                        activation door 2 (named)
  monologue_start/                                   activation door 3 (named)
  _functions/agent/Agent/validate_tool_request/start/ KAME Shield's arg healer
  message_loop_prompts_after/_91_recall_wait.py      recall-wait extension
webui/config.html       settings UI
```
