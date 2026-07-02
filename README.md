<div align="center">

# \\\ ~ 🐢⚡ Key-Aware Management Engine ⚡🐢 ~ // (API Rotation Plugin) for Agent Zero

### KAME API Rotation Engine — the learning carousel that keeps your AI agent alive

[![Version](https://img.shields.io/badge/version-1.0.6-blue.svg)](https://github.com/Kame696/kame-api-engine/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Agent Zero](https://img.shields.io/badge/Agent_Zero-v1.14%2B_and_V2-purple.svg)](https://github.com/agent0ai/agent-zero)
[![Python](https://img.shields.io/badge/python-3.10%2B-yellow.svg)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-production--validated-brightgreen.svg)](#production-validation)
[![GitHub stars](https://img.shields.io/github/stars/Kame696/kame-api-engine?style=social)](https://github.com/Kame696/kame-api-engine/stargazers)

<img src="https://raw.githubusercontent.com/Kame696/kame-api-engine/main/webui/kame_banner.jpg" width="600" alt="KAME banner" />

### *4P1 R0T4T10N — 4FRE3D0M*

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
2. **In Agent Zero → Settings → Model Provider**, enter your keys separated by commas:
   `key1, key2, key3, key4, ...`
3. **Restart Agent Zero.** That's it.

No config required. No tuning. No code changes anywhere. The plugin hooks Agent Zero's model layer at boot and reverts cleanly on uninstall.

> **Agent Zero v1.x *and* V2/V2.1 are both supported.** v1.0.6 auto-detects which model-streaming layer your A0 build uses and adapts — the rotation engine is identical on both. Nothing for you to configure.

Look for this banner on startup:

```
=======================================================
  🐢⚡ KAME v1.0.6 — ACTIVE
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
  ✓ Agent Zero V2.1 Aware (unified_turn + free-tier cache-safe)
  ✓ Rate Limiter Lock Fix
  ✓ Token Callback Support
  ✓ Friendly Error Reporting (real status + kind)
  Note: keys are shown as anonymized ids (e.g. 'k3f9a1') — NOT your real keys.
=======================================================
```

---

## 🛡️ The 13 Shields

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

The whole engine is a single file (`kame_engine.py`), monkey-patching `LiteLLMChatWrapper.unified_call`, `LiteLLMChatWrapper.unified_turn` (A0 V2.1+), `Topic.summarize_messages`, `Bulk.summarize`, and the framework's rate limiter. KAME calls `litellm.acompletion` directly — bypassing A0's internal transport retry loops — so a 503 returns in ~1s and the carousel rotates instantly.

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
<summary><b>Do I need to restart Agent Zero after installing KAME?</b></summary>

No. A0 hot-reloads plugins: dropping KAME into your plugins folder (or toggling it on) clears the plugin/extension caches, and KAME activates on the **next agent turn** — no container restart, no framework restart. The only prerequisite is having **multiple API keys** configured in A0's normal model settings — KAME never stores keys itself; it rotates the ones A0 already has.
</details>

<details>
<summary><b>Does KAME work on the new Agent Zero V2 / V2.1?</b></summary>

Yes, as of **v1.0.4** — both A0 majors, auto-detected. Agent Zero V2/V2.1 made three changes that KAME 1.0.3 didn't survive; 1.0.4 handles all of them:

1. **Streaming moved to a transport layer** (V2 removed `models._parse_chunk`). 1.0.4 detects the A0 version once and uses the right chunk parser automatically.
2. **The model entry point split** — V2.1's agent monologue calls `unified_turn`, not `unified_call`. 1.0.3 patched only `unified_call`, so rotation never engaged on V2.1. 1.0.4 wraps `unified_turn` too.
3. **Free-tier prompt caching** — V2.1 tries to cache big prompts, but free-tier keys have zero cache storage and 429 on it. 1.0.4 disables caching for its calls.

Behavior on A0 v1.x is unchanged. If you're on V2 or V2.1, just install 1.0.4.
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
<summary><b>Compression takes a long time. Is KAME slowing it down?</b></summary>

No. KAME's "Trust the Connection" philosophy means zero artificial timeouts. A 90,000-token compression that legitimately takes 90 seconds takes 90 seconds. Without KAME, A0's native flow can crash with "timed out" — KAME lets it finish.
</details>

<details>
<summary><b>During an outage my log used to fill with hundreds of "503 server-busy" lines. Still?</b></summary>

No — at `normal` level KAME **collapses** a storm: it prints the first failure, then a single aggregate line every ~20s, then a "storm over" recap when a key answers again. Set `kame_collapse_storm_logs: false` (or use `verbose`) to get one line per failure again.
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
| `key_log_style` | `fingerprint` | How keys appear in logs: `fingerprint` (anonymized id, never leaks the secret), `prefix8` (first 8 chars), or `full` (debug only). |
| `kame_collapse_storm_logs` | `true` | Collapse a repetitive 503/error **storm** at `normal` level into one aggregate line every ~20s (+ a "storm over" recap) instead of hundreds of identical warnings. `verbose` always prints every line. Pure logging. |
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

- **Agent Zero**: v1.14+ through the v1.x line **and Agent Zero V2 / V2.1** — v1.0.4 auto-detects which is installed and adapts.
- **Python**: 3.10+
- **Providers**: any LiteLLM-supported provider (Google, OpenAI, Anthropic, Mistral, Groq, DeepSeek, xAI, Together, ...)
- **No new dependencies** — uses stdlib only on top of what A0 already ships

---

## 🪪 Evolution (version history)

KAME has been in development since early 2026, learning from real production logs at every step:

| Version | Focus | Key insight |
|---|---|---|
| **v1.0.6** | Faster failover + verifiable quota logs + gentler empty/daily handling | **(1)** Near-instant key failover — dropped the fixed 50ms inter-rotation delay for a zero-delay event-loop yield (saved ~750ms per 15-key storm). **(2)** Every quota failure line now shows the provider's own quota tag inline (`[quota: PerDay]` / `[quota: PerMinute]`) so daily/per-minute classification is verifiable at `normal` level. **(3)** One transient empty stream no longer cools a healthy key — it gets an un-penalized retry; only a 2nd empty from the same key rests it. **(4)** Daily cooldowns get up to 120s random spread so keys cooled together don't re-probe in one tight wave (no escalation, ~hourly cadence unchanged). Cooldowns still never shorten. |
| **v1.0.5** | Daily-quota logic fix + chat pause | Two confirmed bugs fixed from overnight log analysis: **(1)** daily-quota cooldown now always uses the configured `daily_quota_cooldown_seconds` — Google's retryDelay is ignored for daily quotas since it is often wrong; **(2)** existing cooldowns can never be shortened — a 503 (10s) can no longer wipe a 1h daily-quota protection (fixed by `max()` on `sick_until`). Plus: the carousel now honors chat **pause** (waits until unpaused, resumes cleanly). Rotation / selection / ETA-sleep unchanged from 1.0.4. (An early 1.0.5 build's key-status panel was removed in 1.0.6 — it showed incorrect data.) |
| **v1.0.4** | Agent Zero V2 / V2.1 compatibility | Three V2/V2.1 changes broke 1.0.3, all fixed here. **(1)** V2 moved streaming to a transport layer and removed `models._parse_chunk`; 1.0.4 detects the A0 version once and picks the right parser automatically. **(2)** V2.1 split the entry point — the agent monologue now calls `unified_turn`, not `unified_call`, so 1.0.3's rotation was bypassed entirely; 1.0.4 also wraps `unified_turn`, calling `litellm.acompletion` directly (bypassing A0's internal Responses transport so 503s return in ~1s instead of ~40s). **(3)** V2.1's free-tier prompt-caching 429 is sidestepped by disabling explicit caching. The selection / health / cooldown carousel is unchanged from 1.0.3. |
| **v1.0.3** | Observability + faster recovery + invalid-key fix | Two real Gemini-`503` outages (one **83 minutes straight**) proved the engine handled outages correctly — but the **logs** were hard to read and recovery **trickled**. Added: raw full-error toggle, precise durations (`1m30s` not "2m"), fast pool recovery (`_thaw_server_cooled_keys`), 503-storm log collapse, and an **invalid-key fix** so an expired/typo'd 400 key is quarantined + rotated instead of aborting the run. Selection path UNCHANGED. |
| **v1.0.2** | Critical 5xx-misclassification fix + deeper nudge + honest waiting | A real ~6-hour Gemini run froze the chat ~38 min: transient `503`s whose bodies mentioned "daily" were misclassified as daily-quota and cooled the whole pool for 1h. Fixed by classifying any 5xx as a short server retry BEFORE the quota-text check. Plus interruptible cooling sleep and the true recovery clock. Engine selection path unchanged. |
| **v1.0.1** | Quota awareness + reliability fixes + log overhaul | Google sends a misleading `retryDelay: 1s` on a daily 429 — trusting it re-probed a dead key once per second. Fixed with strict daily/account detection + provider-agnostic adaptive backoff. Logs reworked into a clear `silent`/`normal`/`verbose` tri-state. Engine selection path unchanged. |
| **v1.0.0** | First stable release | Engine validated: 1,163 ops / 117 rate limits / 0 crashes. ETA-driven sleep proven in production. |
| v0.5.8.0 | The ETA Fix | Real log revealed: pulsing every 2s against sick keys burned ~45 wasted 429s in 26s. Fixed by sleeping exactly until next recovery. |
| v0.5.7.4 | Verbose Trace | Added opt-in observability: key short id, selection latency, pool snapshot, cascade summary. |
| v0.5.7 | Packaging Cleanup | A0 v1.15 schema compliance, clean uninstall hooks. |
| v0.5.6 | The Trust | "Trust the Connection" philosophy formalized — zero artificial timeouts. |
| v0.5.0 - v0.5.5 | The Commander → The Refined | Identity-aware health, anti-dogpile, anti-thundering-herd, smart quarantine. |
| v0.4.x | The Seed → The Strategist | Foundational rotation, eternal carousel, basic RPM-awareness. |

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

Bugs and feature requests via [GitHub issues](https://github.com/Kame696/kame-api-engine/issues).

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

[**⭐ Star Kame696/kame-api-engine on GitHub →**](https://github.com/Kame696/kame-api-engine/stargazers)

---

<div align="center">

🐢⚡ **KAME v1.0.6** — *because round-robin was never enough*

**Bitcoin** — `36BGYhMEVFgY8PLGMVux93pjGt92KVM6dJ`

*4P1 R0T4T10N — 4FRE3D0M*

</div>
