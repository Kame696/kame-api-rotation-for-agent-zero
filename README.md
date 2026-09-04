<div align="center">

# \\\ ~ 🐢⚡ Key-Aware Management Engine ⚡🐢 ~ // (API Rotation Plugin) for Agent Zero

### KAME API Rotation Engine — the learning carousel that keeps your AI agent alive

[![Version](https://img.shields.io/badge/version-1.2.0-blue.svg)](https://github.com/Kame696/kame-api-rotation-for-agent-zero/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Agent Zero](https://img.shields.io/badge/Agent_Zero-v1.14%2B_and_V2-purple.svg)](https://github.com/agent0ai/agent-zero)
[![Verified against](https://img.shields.io/badge/verified_against-A0_v1.14_%E2%86%92_v2.11-purple.svg)](COMPATIBILITY.md)
[![Python](https://img.shields.io/badge/python-3.10%2B-yellow.svg)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-production--validated-brightgreen.svg)](#production-validation)
[![GitHub stars](https://img.shields.io/github/stars/Kame696/kame-api-rotation-for-agent-zero?style=social)](https://github.com/Kame696/kame-api-rotation-for-agent-zero/stargazers)

<img src="https://raw.githubusercontent.com/Kame696/kame-api-rotation-for-agent-zero/main/webui/kame_banner.jpg" width="600" alt="KAME banner" />

### *4P1 R0T4T10N — 4FRE3D0M*

[![Donate Bitcoin](https://img.shields.io/badge/donate-bitcoin-f7931a.svg)](#-support-the-project)

**Free and MIT — built and paid for by one person.** If KAME saved a run, a tip keeps it going:
**BTC `36BGYhMEVFgY8PLGMVux93pjGt92KVM6dJ`** — *any amount helps, genuinely.*

**API key rotation, rate-limit recovery and 429 failover for [Agent Zero](https://github.com/agent0ai/agent-zero) — Gemini, OpenAI, OpenRouter, Anthropic, or a provider that does not exist yet.**

[Install](#-install--quick-start-3-steps) · [The 16 Shields](#-the-16-shields) · [Settings](#-faq) · [Compatibility](#-compatibility) · [Evolution](#-evolution-version-history) · [Changelog](CHANGELOG.md) · [Hermes port](https://github.com/Kame696/kame-api-rotation-for-hermes)

</div>

---

## 🎯 What is KAME?

**KAME is what API rotation should have been.**

Round-robin libraries cycle keys blindly. They keep banging on a key that just hit a 429 because they have no memory. They have no idea which key has capacity left. They retry through dead keys and call it "resilience."

KAME does the **opposite** of every assumption round-robin makes:

<table>
<tr>
<td width="50%" valign="top">

### 🧠 Learns from every 429

Parses the provider's own `retry-delay` and respects it **to the second** on per-minute limits. On a **daily quota** it knows not to trust a misleadingly short delay — it cools that key for a real cooldown instead.

No guessing. No fixed backoff. Per-minute or daily, on any provider, KAME does the right thing.

</td>
<td width="50%" valign="top">

### 🎯 Picks the right key, every time

A 60-second sliding window tracks each key's recent activity. KAME selects the key with the **most remaining capacity**, not just the next one in line.

LRU tie-break ensures even spreading across the pool.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 💤 Sleeps intelligently when keys cool

When the entire pool is temporarily exhausted, KAME reads the soonest recovery time and **sleeps until then** (capped at 60s, re-checking after) — instead of burning wasted 429 requests that prolong the cooldown.

Production-proven: **45 wasted requests in v0.5.7.x → 0 in v1.0.0.**

</td>
<td width="50%" valign="top">

### 🤝 Trusts the connection

**Zero artificial timeouts.** If the API accepts your request without error, KAME waits patiently for it to finish — even if it's a 90,000-token compression that takes 90 seconds.

No death-loops on slow models. No "timed out" crashes during legitimate work.

</td>
</tr>
<tr>
<td colspan="2" valign="top">

### 🥷 Stays invisible

Every wait includes `random.uniform(0.1, 1.5)` seconds of **jitter**. No two waits are identical. Anti-bot detection systems can't fingerprint KAME, and multi-client deployments never sync-collide on the same recovery moment.

KAME doesn't look like a bot. KAME looks like a thoughtful human who took a coffee break at exactly the right time.

</td>
</tr>
</table>

> **You give it a comma-separated list of API keys. It gives you an agent that never stops.**

---

## 🆚 KAME vs Plain Round-Robin

| | Plain round-robin | **KAME** |
|---|---|---|
| Selection logic | "next in line" | **most remaining capacity** (RPM-aware predictive) |
| Behavior on 429 | retry same key with backoff | **read provider's `retry-delay`, sleep that exact time** |
| Concurrent calls | all dogpile on key #1 | **spread across keys** (anti-dogpile + anti-thundering-herd) |
| Sick key recovery | guessed (often wrong) | **respected to the second** (parsed from response) |
| Wasted 429 requests | many | **zero** |
| Detectable as bot | yes (regular spin) | **no** (jitter on every wait) |
| Daily-quota / out-of-credit key | trusts a misleading short retry, hammers a dead key | **detected → real cooldown, any provider** |
| Compression flow | breaks on token limit | **rotates mid-compression**, finishes anyway |
| Memory of failures | none | **identity-aware health** (per `provider:model`) |
| Recovery from "all sick" pool | infinite retry, kills your quota | **ETA-driven sleep, wake exactly on time** |

> If you're using round-robin, your keys are spending half their quota proving they're still rate-limited. With KAME, every request that hits the API actually gets answered.

---

## ⚡ Install — Quick start (3 steps)

1. **Copy** the `api_rotation_by_kame/` folder into `/a0/usr/plugins/`
2. **In Agent Zero → Settings → Model Provider**, paste all your keys into that **one** API key field, glued together with commas — one string, no separate fields, no spaces needed:
   `key1,key2,key3,key4,etc`
3. **Restart Agent Zero.** That's it.

No config required. No tuning. No code changes anywhere. The plugin hooks Agent Zero's model layer at boot and reverts cleanly on uninstall.

> **Agent Zero v1.x *and* the V2 line (through v2.11) are both supported.** Since v1.0.9 KAME does not re-implement Agent Zero's model call at all — it picks the key and lets Agent Zero make the call. That is why one build works across both majors, and why a new Agent Zero release is far less likely to break it. Verified end-to-end on **v1.14, v1.20, v2.1, v2.4, v2.7, v2.8, v2.10 and v2.11**. Nothing for you to configure.

Look for this banner on startup:

```
=======================================================
  🐢⚡ KAME v1.2.0 — ACTIVE
  ✓ Identity-Aware Health
  ✓ Eternal Carousel Rotation
  ✓ RPM-Aware Predictive Selection
  ✓ Anti-Dogpile Guard
  ✓ Anti-Thundering-Herd (Pending Counter)
  ✓ Trust the Connection (No Artificial Timeouts)
  ✓ KAME-Aware Compression Guard
  ✓ Hybrid Learning (Parsed retry-delay + ETA-driven sleep)
  ✓ Daily-Quota & Account-Limit Aware (multi-provider)
  ✓ Adaptive Backoff (provider-agnostic safety net)
  ✓ Agent Zero V2.1+ Aware (turn-based calls + free-tier cache-safe)
  ✓ Rate Limiter Lock Fix
  ✓ Token Callback Support
  ✓ Friendly Error Reporting (real status + kind)
  ✓ Delegated Execution (Agent Zero makes the call, KAME picks the key)
  ✓ Bound to Agent Zero's model layer by shape: unified_turn, unified_call
  Note: keys are shown as anonymized ids (e.g. 'k3f9a1') — NOT your real keys.
=======================================================
```

---

## 🛡️ The 16 Shields

| # | Shield | What it gives you |
|---|---|---|
| 1 | 🆔 **Identity-Aware Health** | Tracks key health per `provider:model` pair. Your `gemini-2.5-flash` pool is separate from your `gemini-2.5-pro` pool — a 429 on one doesn't disable the other. |
| 2 | 🔄 **Eternal Carousel** | Infinite rotation. Never gives up, never crashes. Survives any combination of failures. |
| 3 | 📊 **RPM-Aware Predictive Selection** | 60-second sliding window per key. Picks the one with most remaining capacity. LRU tie-break for even spreading. |
| 4 | 🛡️ **Anti-Dogpile Guard** | At selection, the chosen key is marked busy NOW. Concurrent calls naturally pick different keys. |
| 5 | 🐎 **Anti-Thundering-Herd** | The pending request counts in the 60s window BEFORE it completes, so other threads route around it. |
| 6 | 💤 **ETA-Driven Sleep** | When all keys are sick, sleep until the soonest recovery (capped 60s, then re-check). Re-select after waking. **Never call the API with a sick key.** |
| 7 | 🎲 **Smart Hybrid Jitter** | `random.uniform(0.1, 1.5)` seconds on every wait. Anti-bot-detection. Prevents multi-client sync collisions. |
| 8 | 🤝 **Trust the Connection** | **Zero artificial timeouts.** Slow legitimate work runs to completion. |
| 9 | 📦 **KAME-Powered Compression** | History compression goes through the same eternal carousel. Multi-key rotation during summarization. |
| 10 | 📅 **Daily-Quota & Account-Limit Aware** | Detects daily-quota and out-of-credit (`insufficient_quota`) errors across providers and applies a real cooldown — instead of trusting a misleadingly short retry and hammering a dead key once per second. Configurable (`daily_quota_cooldown_seconds`, default 1h). |
| 11 | 📈 **Adaptive Backoff** | Provider-agnostic safety net: if the same key keeps hitting rate limits, its cooldown escalates (20s → 40s → 80s … up to the ceiling) and resets on the first success. |
| 12 | 🔒 **Rate Limiter Deadlock Fix** | Replaces A0's `asyncio.Lock` with `threading.Lock`, eliminating an async deadlock under specific concurrency patterns. |
| 13 | 🧹 **Clean Uninstall** | `hooks.py::uninstall()` reverts every monkey-patch. Drop the folder and KAME is gone — no leftover state. |
| 15 | 🧯 **Unusable-Response Floor** | *(new)* Agent Zero ends a turn after N consecutive replies it cannot parse into a tool request — a cost circuit-breaker that defaults to **2** on A0 v2.4–v2.7 (and stays at 2 forever in a settings file written by one of those, even after you upgrade to v2.8, which raised it to 5). One JSON-escaping slip from the model then kills the turn. KAME applies its own **floor, never a ceiling**: `effective = max(A0's setting, kame_unusable_response_limit)`, default 5. Past that count A0's stop is left completely alone, so a model stuck in a formatting loop still can't drain your pool. Set `0` to never interfere. |
| 16 | 💬 **The Wait, Said Out Loud** | *(new in v1.2.0)* When the whole pool is cooling, KAME already slept until the exact moment a key recovers — and said so on the console, which is not where you are looking. From the chat, that wait and a hung agent look identical, and the move that looks available is restarting Agent Zero, which throws away the wait *and* the context. Now a wait longer than ~90s puts **one** item in the chat and keeps it updated: how many keys are resting, when the earliest is back, how long it has run, and that **stop still works**. Counts and the pool name only — never a key — and it never enters the model's history. Turn it off with `kame_wait_notice`. |
| 14 | 🤝 **Delegated Execution** | *(new in v1.0.9)* KAME picks the key — **Agent Zero makes the call itself.** KAME no longer builds the request, parses the stream or constructs the result, so an Agent Zero update changes A0's own code path instead of a stale copy of it living inside the plugin. It also binds to A0's model layer **by signature, not by name**, and if a future Agent Zero ever moves out from under it, KAME prints one line and steps aside — your agent keeps running. |

---

<details>
<summary><b>🔬 How it works — internals & flow diagram</b></summary>

```mermaid
flowchart TD
    A[Agent Zero asks LiteLLM for a chat] --> B[KAME monkey-patched unified_call / unified_turn]
    B --> C[_get_best_key for provider:model]
    C --> D{Any healthy keys?}

    D -->|Yes| E[Pick key with most<br/>remaining capacity]
    D -->|No, all sick| F[Read soonest sick_until]

    E --> G[Mark anti-dogpile + anti-herd]
    G --> H[acompletion - real API call]
    H --> I{Success?}

    F --> J[Sleep min ETA+0.5s, 60s<br/>+ jitter 0.1-1.5s]
    J --> K[NO API calls during sleep]
    K --> C

    I -->|Yes| L[Mark healthy<br/>reset backoff<br/>Return response]
    I -->|No, rate-limit| M[Classify error +<br/>parse retry-delay]
    M --> N{Daily / account limit?}
    N -->|Yes| O[Long cooldown<br/>ignore misleading delay]
    N -->|No| P[Per-minute: trust delay<br/>+ adaptive backoff]
    O --> Q[Set sick_until]
    P --> Q
    Q --> C

    style E fill:#10b981
    style F fill:#f59e0b
    style L fill:#10b981
    style O fill:#f59e0b
    style J fill:#3b82f6
```

The whole engine is a single file (`kame_engine.py`), monkey-patching `LiteLLMChatWrapper.unified_call`, `LiteLLMChatWrapper.unified_turn` (A0 V2.1+), `Topic.summarize_messages`, `Bulk.summarize`, and the framework's rate limiter. Since v1.0.9 the wrapper **delegates**: it picks the key, injects it as `api_key=`, and calls A0's own method, returning A0's result untouched. A0's *internal* retry loop is switched off for that one call (its knob names are read out of A0's source at runtime), so a 503 surfaces in ~1s and the carousel rotates instantly instead of being swallowed by a silent retry on the same dead key.

### Per-key health state

Every API key carries this dictionary, scoped under `{provider}:{model}`:

```python
{
    "sick_until":    float,  # epoch time when key becomes available again
    "last_used":     float,  # for LRU tie-break + anti-dogpile
    "request_log":   [float],# 60s sliding window of request timestamps
    "last_sick_at":  float,  # for compression-aware "fresh recovery" filter
    "consecutive_rl":int,    # consecutive rate-limit fails -> adaptive backoff (resets on success)
}
```

### Selection algorithm

```python
best_key = min(healthy, key=lambda k: (
    len(pool[k]["request_log"]),  # primary: most remaining 60s-window capacity
    pool[k]["last_used"],         # secondary: LRU for even spreading
))
# Then: mark used NOW (anti-dogpile)
#       count pending NOW in request_log (anti-thundering-herd)
```

### ETA-driven sleep formula

```python
soonest_eta = min(sick_until - now  for each sick key)
if soonest_eta > 3.0:
    wait = min(soonest_eta + 0.5, 60.0) + random.uniform(0.1, 1.5)
else:
    wait = 2.0 + random.uniform(0.1, 1.5)
await asyncio.sleep(wait)
continue   # never fall through with a sick key
```

</details>

---

## ❓ FAQ

<details>
<summary><b>I sign in to Codex / Copilot with my subscription, not an API key. Does KAME break that?</b></summary>

**No — KAME does not touch those models at all.**

Agent Zero authenticates subscription providers (OpenAI Codex, GitHub Copilot, Gemini API, xAI Grok) through its own OAuth plugin, which supplies the credential via `models.get_api_key`. KAME never calls that function: it reads key pools straight from your `.env` (`API_KEY_<PROVIDER>`). Since a subscription model has no key pool there, KAME finds nothing to rotate and hands the call to Agent Zero **unchanged, exactly as if KAME were not installed**.

You can mix freely — rotate a pool of Gemini keys *and* keep a Codex subscription model in the same Agent Zero. Each takes its own path.

This is checked on every run of the compatibility harness, on every supported Agent Zero version, by comparing the same call with KAME uninstalled vs installed and requiring them to be identical.
</details>

<details>
<summary><b>Do I need to restart Agent Zero after installing KAME?</b></summary>

No. A0 hot-reloads plugins: dropping KAME into your plugins folder (or toggling it on) clears the plugin/extension caches, and KAME activates on the **next agent turn** — no container restart, no framework restart. The only prerequisite is having **multiple API keys** configured in A0's normal model settings — KAME never stores keys itself; it rotates the ones A0 already has.
</details>

<details>
<summary><b>Does KAME work on the new Agent Zero V2 line (V2 / V2.1 / v2.7 / v2.8 / v2.10 / v2.11)?</b></summary>

Yes, as of **v1.0.4** — both A0 majors, auto-detected. Agent Zero V2/V2.1 made three changes that KAME 1.0.3 didn't survive; 1.0.4 handles all of them:

1. **Streaming moved to a transport layer** (V2 removed `models._parse_chunk`). 1.0.4 detects the A0 version once and uses the right chunk parser automatically.
2. **The model entry point split** — V2.1's agent monologue calls `unified_turn`, not `unified_call`. 1.0.3 patched only `unified_call`, so rotation never engaged on V2.1. 1.0.4 wraps `unified_turn` too.
3. **Free-tier prompt caching** — V2.1 tries to cache big prompts, but free-tier keys have zero cache storage and 429 on it. 1.0.4 disables caching for its calls.

Behavior on A0 v1.x is unchanged. **v1.0.9 went further and removed the reason those breakages were possible**: KAME no longer parses A0's stream or rebuilds its result — it chooses the key and hands the call to Agent Zero. It also finds A0's model methods *by signature* rather than by name, so an upstream rename no longer disables rotation. Verified green end-to-end on **v1.14, v1.20, v2.1, v2.4, v2.7, v2.8, v2.10 and v2.11**. If you're on the V2 line, just install the latest KAME.
</details>

<details>
<summary><b>On Agent Zero V2.1 a model feels slow or throws `503 "high demand"` — is that KAME?</b></summary>

No. That message is **Google rate-limiting the model itself** — most often its newest preview models (`gemini-3.5-flash`, `gemini-3-flash-preview`) on the free tier. No key-rotation can add capacity Google isn't giving. KAME's job is to **ride it out** — rotate across your keys, rest the busy ones, sleep when all are cooling, and keep retrying until one answers (without ever surfacing the error). Use a stable model like `gemini-2.5-flash` or `gemini-3.1-flash-lite` for fast, reliable chat.
</details>

<details>
<summary><b>I only have one API key. Does KAME help?</b></summary>

With one key, KAME is roughly equivalent to A0's native rate limiter. The eternal-carousel magic needs multiple keys. **Recommend 5+ for good RPM spreading.**
</details>

<details>
<summary><b>KAME picked the same key twice in a row. Bug?</b></summary>

Likely not. If you have only 2-3 keys and one just succeeded with fresh RPM capacity, RPM-aware selection may legitimately re-pick it. With more keys (10+), this becomes very rare due to anti-dogpile.
</details>

<details>
<summary><b>I'm seeing "429 daily-quota → cooling 1h" — is this a bug?</b></summary>

No — that's the daily-quota shield working: KAME detected a daily or out-of-credit limit and is resting that key for a real cooldown (default 1h) instead of hammering a dead key once per second. Your other keys keep working; the rested key is re-tried after the cooldown. A **5xx** server error is always treated as a short server-busy retry, *never* as a daily quota — so a server blip can't cool a healthy key for an hour.
</details>

<details>
<summary><b>I'm seeing "403 access denied for this key/model → quarantined 1h". What now?</b></summary>

That key is being refused on purpose by the provider — the project was suspended (*"Your project has been denied access"*), the API was never enabled for it, or that model isn't authorized for the key's tier. Nothing KAME can retry away, so since v1.0.8 the key is quarantined for the daily cooldown instead of being re-probed every 20 seconds. The rest of the pool is unaffected, and the quarantine is scoped to that `provider:model` — the same key keeps serving other models. Fix or replace the key in your provider console; KAME re-probes it about once an hour and picks it back up automatically the moment it answers.
</details>

<details>
<summary><b>Compression takes a long time. Is KAME slowing it down?</b></summary>

No. KAME's "Trust the Connection" philosophy means zero artificial timeouts. A 90,000-token compression that legitimately takes 90 seconds takes 90 seconds. Without KAME, A0's native flow can crash with "timed out" — KAME lets it finish.
</details>

<details>
<summary><b>During an outage my log used to fill with hundreds of "503 server-busy" lines. Still?</b></summary>

No — at `normal` level KAME **collapses** a storm: it prints the first failure, then a single aggregate line every ~20s, then a "storm over" recap when a key answers again. Set `kame_collapse_storm_logs: false` (or use `verbose`) to get one line per failure again.
</details>

<details>
<summary><b>I sent a new message mid-run and the agent kept going. Is KAME blocking the interrupt?</b></summary>

No — that's Agent Zero's own design. Since A0 V2 the WebUI **queues** a message when the context is running (`webui/index.js` → `/message_queue_add`) and only sends the batch after the monologue ends (`extensions/python/process_chain_end/_50_process_queue.py`). You'll see it in the log as `User message (queued batch):`. The **nudge** button is the explicit interrupt. KAME honors `InterventionException` between every rotation and during every 1-second slice of a cooling sleep, so a nudge lands even on a fully cold pool — but it cannot deliver a message the UI never sent.
</details>

<details>
<summary><b>"Agent stopped after 2 consecutive unusable model responses to prevent further API charges" — shouldn't KAME rotate past that?</b></summary>

There's nothing to **rotate** — that stop is not an API error. It is A0's cost
circuit-breaker (`_90_stop_unusable_response_loop.py`), tripped when the model returns
a misformatted or repeated reply N times in a row. The **API call succeeded**; the
model just didn't follow A0's JSON contract (markdown fences, an unescaped quote,
plain prose). A different key would produce the same reply.

What KAME *does* do about it is raise the number. A0's own default moved from **2**
(v2.4–v2.7) to **5** (v2.8), but a settings file written by an older A0 keeps the tight
`2` forever — so on an upgraded install a single escaping slip ends the turn. KAME
applies a **floor**: `effective = max(A0's setting, kame_unusable_response_limit)`,
default `5`, and past that count A0's stop is left alone. It is a floor, not a bypass:
a model genuinely stuck in a formatting loop still cannot drain your pool. Change it in
**Settings → Plugins → KAME → Unusable-response floor**, or set `0` to never interfere.
If it keeps happening, the real fix is upstream of both: a model that keeps to the
format, or an agent profile that stops printing ```json tool examples inside its own
answer text.
</details>

<details>
<summary><b>Can I use KAME with Anthropic / OpenAI / others, not just Gemini?</b></summary>

Yes. KAME is provider-agnostic. It works wherever Agent Zero's LiteLLM layer works. The retry-delay parser handles Google, OpenAI, Anthropic, Groq, and generic HTTP `Retry-After` headers — including compound durations like "6m 11.52s".
</details>

<details>
<summary><b>What if all my keys die permanently?</b></summary>

KAME keeps cycling. If literally every key is dead, your agent sleeps (up to 60s per cycle), wakes, re-checks, and sleeps again until you fix it — announcing a long outage just once instead of spamming the log. **No infinite spin against a wall, no wasted API calls.**
</details>

<details>
<summary><b>Does verbose / debug logging cost extra API calls?</b></summary>

Zero. Every log level is pure local instrumentation. Even `silent` still tracks stats and key health internally; it just doesn't write them out.
</details>

---

<details>
<summary><b>⚙️ Settings — all configuration options</b></summary>

| Setting | Default | Purpose |
|---|---|---|
| `kame_log_level` | `normal` | How much KAME writes to the log: `silent` (nothing but hard errors), `normal` (one line per success + events; pool count only when degraded), `verbose` (full diagnostics), or `verbose+errors` (verbose **plus** the full raw exception on every failure — see the real error in the Docker log, not just KAME's one-line classification). A legacy `verbose_trace: true` still maps to `verbose`. |
| `daily_quota_cooldown_seconds` | `3600` | How long to rest a key after a **daily-quota / out-of-credit** error (any provider). Also the adaptive-backoff ceiling. Clamped 1–86400. |
| `key_log_style` | `fingerprint` | How keys appear in logs: `fingerprint` (anonymized id, never leaks the secret), `prefix8` (first 8 chars), or `full` (debug only). Exception: an **invalid/expired key** always shows a partial reveal (first 10 + last 4 chars) even under `fingerprint`, so you can find it in your provider console — that one event is always visible regardless of `kame_log_level`, since a dead key never self-recovers. |
| `kame_collapse_storm_logs` | `true` | Collapse a repetitive 503/error **storm** at `normal` level into one aggregate line every ~20s (+ a "storm over" recap) instead of hundreds of identical warnings. `verbose` always prints every line. Pure logging. |
| `kame_wait_notice` | `true` | Say in the **chat** when every key in a pool is cooling: one log item, updated about every 10s with the resting count, the earliest expected recovery, how long the wait has run, and a reminder that stop works. Only opens after ~90s, so a normal rotation stays silent. Counts and the pool name only — never a key — and UI-only, so the model never sees it. Set `false` for 1.0.9's console-only behaviour. |
| `kame_unusable_response_limit` | `5` | **Floor** for A0's "consecutive unusable model responses" cost stop (`effective = max(A0's setting, this)`). A0 shipped `2` in v2.4–v2.7 and `5` in v2.8, and an upgraded install keeps whatever its settings file was written with — so one JSON-escaping slip from the model can end a turn. Past this count A0's stop is honored untouched; `0` disables KAME's floor entirely. Only applies on A0 v2.4+, where that guard exists. |
| `kame_log_full_errors` | `false` | Debug escape hatch: ALSO print the **raw** exception (type, status, retry attrs, full body) beside KAME's classification, so you can verify there's no misclassification. Independent of `kame_log_level`. |

Everything else is opinionated and validated in production — the algorithm, sleep timing, jitter range, 60s RPM window, and quarantine logic are all tuned and tested.

</details>

<details>
<summary><b>📊 Logging — what you see at each level (silent / normal / verbose / verbose+errors)</b></summary>

KAME explains itself in plain language. One setting — `kame_log_level` — controls how much it writes to the Docker log. **The rotation algorithm is identical at every level; this only changes what you see.** Change it in **Settings → Plugins → KAME → Log level**; it takes effect on the next monologue start (no restart).

### `normal` (default)

One compact line per **successful** call, plus rotations, limit hits, sleeps and errors. The pool-health count shows **only when the pool is degraded**, so a healthy pool stays quiet:

```
[KAME] Chat|gemini-2.5-flash ✅ k0a770
[KAME] Chat|gemini-2.5-flash k0a770 ⏳ 429 per-minute → wait 37s · next key...
[KAME] Chat|gemini-2.5-flash ✅ k1b8c2 · 1 rotation · pool 14/15 healthy
```

### `verbose`

Everything in `normal`, plus a `Calling...` heartbeat, the picked-key line, per-call wall time, the **full** pool snapshot on every success, and a cascade breakdown:

```
[KAME] Chat|gemini-2.5-flash ➡ Calling...
[KAME] Chat|gemini-2.5-flash ➡ k0a770 picked in 0.08ms
[KAME] Chat|gemini-2.5-flash ✅ k0a770 in 2.4s | pool 15/15 healthy
[KAME] Chat|gemini-2.5-flash ✅ k2c9d4 in 9.4s | pool 13/15 healthy | 5 rotations, 1 sleep
```

### `verbose+errors`

Everything in `verbose`, **plus** the complete raw exception dumped on every failure — so the actual error appears in the Docker log, not just KAME's one-line classification. Best for diagnosing misclassifications.

### `silent`

No banner, no per-call line, no rotation or sleep notices. Only a **hard, unrecoverable error** still surfaces. Internal stats and key health are still tracked — only the log output is suppressed.

### Sleep is always visible (except in `silent`)

```
[KAME] Chat|gemini-2.5-flash 💤 All keys cooling. Sleeping 7.7s (no API calls) — earliest recovery ~7s.
[KAME] Chat|gemini-2.5-flash 💤 All keys cooling — earliest recovery in ~1h (around 19:05:00). Re-checking every ~60s.
```

The sleep is **interruptible** — a message or *nudge* during a cooldown is honored immediately.

</details>

---

## 🔧 Compatibility

- **Agent Zero**: v1.14+ through the v1.x line **and the whole V2 line — verified green end-to-end on v1.14, v1.20, v2.1, v2.4, v2.7, v2.8, v2.10 and v2.11** (2026-09-03). Since v1.0.9 KAME delegates the call to Agent Zero and binds to its model layer by signature, so it adapts on its own. Run `python tests/test_a0_compat.py /path/to/agent-zero` to re-verify against any newer build yourself.
- **Python**: 3.10+
- **Providers**: any LiteLLM-supported provider (Google, OpenAI, Anthropic, Mistral, Groq, DeepSeek, xAI, Together, ...)
- **Subscription / OAuth models are untouched** — Codex, GitHub Copilot, Gemini API and xAI Grok sign in through Agent Zero's own OAuth plugin, not with API keys. KAME finds no key pool for them and hands the call straight to Agent Zero, exactly as if the plugin were not installed. Verified every run, on every supported Agent Zero version. Details: [COMPATIBILITY.md §4.1](COMPATIBILITY.md)
- **No new dependencies** — uses stdlib only on top of what A0 already ships

### Agent Zero just shipped a new version — is KAME still fine?

One command answers it:

```bash
python tools/a0_upgrade_check.py --latest    # is there even a new A0?
python tools/a0_upgrade_check.py /path/to/agent-zero    # the real audit
```

It does three things: asks GitHub for A0's newest tag, **fingerprints the source of
all 12 A0 symbols KAME patches or depends on** and diffs them against the pinned
baseline in `a0_compat.json`, then runs the live harness that applies KAME's real
patches to the real classes *and drives a real key rotation through them*. Exit `0` =
compatible. Exit `1` = it names the exact function that changed and *why KAME cares
about it* — no hunting through A0's tree.

That list used to be 14. v1.0.9 delegated the model call back to Agent Zero, which
retired the stream parser, the chunk accumulator, the transport parser and the result
builder from KAME's compatibility surface entirely (11), then added back one
**optional** entry for A0's unusable-response guard — optional meaning the checker
reports `not present (optional)` instead of failing when you audit an A0 that predates
it (< v2.4). Each remaining symbol also
carries a **severity**, so the output tells you whether you are looking at "rotation
is down" or "one shield is down" — or at `adaptive`, which means KAME absorbs that
change on its own and the flag is informational.

Full patch-point map, the endpoint cheat-sheet and the step-by-step upgrade runbook
live in **[COMPATIBILITY.md](COMPATIBILITY.md)**.

---

## 🪪 Evolution (version history)

KAME has been in development since early 2026, learning from real production logs at every step.
**Every entry below is one line on purpose** — the full story of each release, with the logs it came
from and the verification that closed it, is in [CHANGELOG.md](CHANGELOG.md), where each version
opens with a short **In short** list and folds the detail underneath.

| Version | Focus | In one line |
|---|---|---|
| **v1.2.0** | The wait, said out loud | An all-keys-cooling wait now appears **in the chat**, not only on the console — and the settings screen was rebuilt so an on-by-default toggle stops rendering as off and saving that lie back. Verified on A0 **v2.10**. |
| **v1.0.9** | KAME stops re-implementing Agent Zero | KAME only **chooses the key**; A0 owns the request, the stream, the parsing and the result. Five upstream symbols and `litellm` left the dependency surface. Live-verified on six A0 tags, one code path. |
| **v1.0.8** | Early stop + denied keys | The stream now breaks where native A0 breaks it (no generation past a finished tool call), and a `403 PERMISSION_DENIED` is quarantined instead of returning to the carousel every 20 seconds. |
| **v1.0.7** | Response Shield | A `response` tool arriving with empty, null or wrongly-keyed arguments is healed instead of crashing the turn — the reply is salvaged rather than paid for with a repair round-trip. |
| **v1.0.6** | Faster failover, honest numbers | Zero-delay rotation (~750 ms saved per 15-key storm), the provider's own quota tag printed inline, one transient empty stream forgiven, and an invalid key always shown — with enough of it to find in your console. |
| **v1.0.5** | Daily quota, correctly | The configured daily cooldown always wins over the provider's misleading hint, an existing cooldown can never be shortened, and the carousel honours chat **pause**. |
| **v1.0.4** | Alive on Agent Zero V2 / V2.1 | V2 moved streaming to a transport layer and V2.1 split the entry point to `unified_turn` — rotation was being bypassed entirely. One engine now serves both majors. |
| **v1.0.3** | Observability + faster recovery | Two real Gemini 503 outages (one **83 minutes**) proved the engine was right and the logs were unreadable. Precise durations, storm collapse, fast pool thaw, and invalid-key rotation. |
| **v1.0.2** | A 5xx is not a daily quota | A transient 503 whose body mentioned "daily" cooled the whole pool for an hour and froze a chat for 38 minutes. Any 5xx is now classified as a short server retry **before** the quota text is read. |
| **v1.0.1** | Quota awareness across providers | Google sends `retryDelay: 1s` on a daily 429; trusting it re-probed a dead key once per second. Strict daily/account detection, adaptive backoff, and a `silent`/`normal`/`verbose` log tri-state. |
| **v1.0.0** | First stable release | Validated in production: 1,163 operations, 117 rate limits, 0 crashes. ETA-driven sleep proven. |
| v0.5.8.0 | The ETA Fix | Pulsing every 2s against sick keys burned ~45 wasted 429s in 26 seconds. Fixed by sleeping exactly until the next recovery. |
| v0.5.7.4 | Verbose Trace | Opt-in observability: key short id, selection latency, pool snapshot, cascade summary. |
| v0.5.7 | Packaging cleanup | A0 v1.15 schema compliance, clean uninstall hooks. |
| v0.5.6 | The Trust | "Trust the Connection" formalized — zero artificial timeouts. |
| v0.5.0–v0.5.5 | The Commander → The Refined | Identity-aware health, anti-dogpile, anti-thundering-herd, smart quarantine. |
| v0.4.x | The Seed → The Strategist | Foundational rotation, eternal carousel, basic RPM awareness. |

> **The lesson across versions:** the only way to build something this reliable is to **run it in production and read the logs honestly**. Every major improvement in KAME came from a real log showing real behavior — not from theory.

---

<details id="production-validation">
<summary><b>📈 Production validation — real logs, real numbers (click to expand)</b></summary>

### First run (May 2026) — 1,163 operations

| Metric | Value |
|---|---|
| KAME-managed operations | **1,163** |
| Rate limit (429) events encountered | **117** |
| Rate limits resolved by rotation alone | **116** (99.1%) |
| Pool-fully-sick events requiring sleep | **1** |
| False pulses (wasted retries against sick keys) | **0** |
| KAME engine crashes | **0** |
| Pool state "healthy" during operations | **~99%** |

The single sleep event tells the whole story:

- KAME predicted wake: **18:09:00**
- KAME actual wake: **18:09:00.291**
- Off by: **291 milliseconds** (the random jitter)
- After waking: picked the recovered key in **0.08ms**, request succeeded

> Predictions accurate within the jitter window. Zero crashes. Zero wasted requests.

### v1.0.1 update — surviving a daily-quota storm (May 29, 2026)

A second real-world run, on a **15-key Gemini pool**, hit the exact failure mode v1.0.1 was built for: a wave of daily-quota exhaustion that took the **entire pool cold** at once. KAME's own session summary:

```
[KAME] Session: 100 ok · 15 limited (min 0, daily 15, quota 0) · 1 long-sleep · 11 server · 0 timeout · 0 auth · 0 other
```

| Metric | Value |
|---|---|
| Operations completed | **100** |
| Rate limits classified as *daily* (correctly) | **15 / 15** |
| Auth / timeout / unknown errors | **0** |
| KAME engine crashes | **0** |

When all 15 keys went cold, KAME announced the outage **once**, slept quietly, and woke within seconds of the first recovery. The single hardest call:

- One call: **2154.1s** wall time · **9 rotations** · **18 sleeps** · **1049s** of local waiting · ✅ success.
- The pool then recovered all the way back to **15/15 healthy**.

> v1.0.0 would have trusted the provider's misleading short `retryDelay` and re-probed dead keys roughly once per second for hours. v1.0.1 rested each one for a real hour, slept through the total outage, and lost **zero** requests.

</details>

---

## 🐢 The Hermes sibling

KAME also runs on [Hermes](https://github.com/NousResearch/hermes). Same decision core, same version
line: **the same MAJOR.MINOR means the same generation of behaviour on both hosts**, and the patch
number moves independently. The 1.1.x series exists only on Hermes, because it fixed stream handling
that Agent Zero has owned itself since 1.0.9 — the two lines rejoin at 1.2.0.

**→ [kame-api-rotation-for-hermes](https://github.com/Kame696/kame-api-rotation-for-hermes)**

Both ports, the parity rule and the table of what each host already does itself:
**[kame-api-rotation](https://github.com/Kame696/kame-api-rotation)** — the family's front door.

---

## 🗑️ Uninstall

```bash
rm -rf /a0/usr/plugins/api_rotation_by_kame/
# Restart Agent Zero
```

KAME's `hooks.py::uninstall()` runs **BEFORE** deletion and reverts every monkey-patch. No leftover state.

---

## 🤝 Contributing

PRs welcome. The engine is intentionally small (single file). When proposing changes:

1. Keep the **engine algorithm** stable — selection, anti-dogpile, ETA-driven sleep are battle-tested.
2. Add features behind opt-in settings when possible (see `kame_log_level` / `key_log_style` as a pattern).
3. Log production behavior in `test_logs/` with version-named files so changes can be audited.

Bugs and feature requests via [GitHub issues](https://github.com/Kame696/kame-api-rotation-for-agent-zero/issues).

---

## ❤️ Support the project

If KAME saved you from a rate-limit hell, consider a tip:

**Bitcoin** — `36BGYhMEVFgY8PLGMVux93pjGt92KVM6dJ`

*Every sat helps me keep this project alive and learning.*

---

## 📜 License

**MIT License** — see [`LICENSE`](LICENSE).

`Copyright (c) 2026 KAME (https://github.com/Kame696)`

You can use, modify, distribute, and even sell KAME with the only requirement being to keep the copyright notice.

---

## 🎀 Credits & Star

Built by [**KAME**](https://github.com/Kame696). Engine refinement guided by real production log analysis. Special thanks to every 429 that taught KAME something new.

If KAME made your agent less frustrating, drop a star ⭐ — it costs you nothing and helps others find this.

[**⭐ Star Kame696/kame-api-rotation-for-agent-zero on GitHub →**](https://github.com/Kame696/kame-api-rotation-for-agent-zero/stargazers)

---

<div align="center">

🐢⚡ **KAME v1.2.0** — *because round-robin was never enough*

**Bitcoin** — `36BGYhMEVFgY8PLGMVux93pjGt92KVM6dJ`

*4P1 R0T4T10N — 4FRE3D0M*

</div>
