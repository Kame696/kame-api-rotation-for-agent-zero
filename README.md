<div align="center">

# 🐢⚡ Key-Aware Management Engine (API Rotation)

### KAME — the learning carousel that keeps your AI agent alive

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/Kame696/kame-api-engine/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Agent Zero](https://img.shields.io/badge/Agent_Zero-v1.14%2B-purple.svg)](https://github.com/frdel/agent-zero)
[![Python](https://img.shields.io/badge/python-3.10%2B-yellow.svg)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-production--validated-brightgreen.svg)](#-real-world-impact-production-log-may-2026)
[![GitHub stars](https://img.shields.io/github/stars/Kame696/kame-api-engine?style=social)](https://github.com/Kame696/kame-api-engine/stargazers)

---

### ❤️ Support the project

If KAME saved you from a rate-limit hell, consider buying me a coffee:

**Bitcoin** — `36BGYhMEVFgY8PLGMVux93pjGt92KVM6dJ`

*Every sat helps me keep this project alive and learning.*

---

<img src="webui/kame_banner.jpg" width="600" alt="KAME banner" />

### *4P1 R0T4T10N — 4FRE3D0M*

</div>

---

## 🎯 What is KAME?

**KAME is what API rotation should have been.**

Round-robin libraries cycle keys blindly. They keep banging on a key that just hit a 429 because they have no memory. They have no idea which key has capacity left. They retry through dead keys and call it "resilience."

KAME does the opposite:

- **Learns** the provider's own retry-delay from every 429 and respects it to the second.
- **Picks** the key with the most remaining capacity, not the next one in line.
- **Sleeps** intelligently when all keys are cooling — instead of burning wasted requests that prolong the cooldown.
- **Trusts the connection** — no artificial timeouts. If the API accepts your request, KAME waits for it to finish, even if it's a 90,000-token compression.
- **Stays invisible** — jitter on every wait, no detectable spin pattern, no anti-bot trip.

> **You give it a comma-separated list of API keys. It gives you an agent that never stops.**

---

## ⚡ Quick start (3 steps)

1. **Copy** the `api_rotation_by_kame/` folder into `/a0/usr/plugins/`
2. **In Agent Zero settings → Model Provider**, enter your keys separated by commas:
   `key1, key2, key3, key4, ...`
3. **Restart Agent Zero.** That's it. KAME auto-detects the commas and takes over.

No config required. No tuning. No code changes anywhere. The plugin monkey-patches Agent Zero's LiteLLM layer at boot and reverts cleanly on uninstall.

---

## 🆚 Why round-robin isn't enough

| | Plain round-robin | **KAME** |
|---|---|---|
| Selection logic | "next in line" | **most remaining capacity** (RPM-aware predictive) |
| Behavior on 429 | retry same key with backoff | **read provider's `retry-delay`, sleep that exact time** |
| Concurrent calls | all dogpile on key #1 | **spread across keys** (anti-dogpile + anti-thundering-herd) |
| Sick key recovery | guessed (often wrong) | **respected to the second** (parsed from response) |
| Wasted 429 requests | many | **zero** |
| Detectable as bot | yes (regular spin) | **no** (jitter on every wait) |
| Long-delay handling | crashes or spins forever | **gracefully sleeps + warns operator** |
| Compression flow | breaks on token limit | **rotates mid-compression**, finishes anyway |
| Memory of failures | none | **identity-aware health** (per `provider:model`) |
| Recovery from "all sick" pool | infinite retry, kills your quota | **ETA-driven sleep, wake exactly on time** |

If you're using round-robin, your keys are spending half their quota proving they're still rate-limited. With KAME, every request that hits the API actually gets answered.

---

## 🛡️ The 12 Shields

| # | Shield | What it gives you |
|---|---|---|
| 1 | 🆔 **Identity-Aware Health** | Tracks key health per `provider:model` pair. Your `gemini-2.5-flash` pool is separate from your `gemini-2.5-pro` pool, so a 429 on one doesn't disable the other. |
| 2 | 🔄 **Eternal Carousel** | Infinite rotation — never gives up, never crashes. The eternal loop survives any combination of failures. |
| 3 | 📊 **RPM-Aware Predictive Selection** | 60-second sliding window per key tracks recent requests. KAME picks the key with the **most remaining capacity**, not just "the next one". LRU tie-break for even spreading. |
| 4 | 🛡️ **Anti-Dogpile Guard** | At selection time, the chosen key is marked `last_used = now`. Concurrent calls see it as "busy" and naturally pick different keys. No more 5 threads slamming the same key. |
| 5 | 🐎 **Anti-Thundering-Herd** | The pending request is counted in the 60s window BEFORE it completes, so other concurrent threads immediately see the key's reduced capacity and route around it. |
| 6 | 💤 **ETA-Driven Sleep** | When all keys are temporarily sick, KAME reads the soonest `sick_until` from its learned state and sleeps **exactly until then** (capped at 30s). After waking, re-selects from scratch — **never calls the API with a sick key**. Eliminates the "burn ~45 wasted 429 requests in 26s" pattern that plagues other rotators. |
| 7 | 🎲 **Smart Hybrid Jitter** | Every sleep includes `random.uniform(0.1, 1.5)` seconds of jitter. Anti-bot detection systems can't fingerprint KAME because no two waits are identical. Also prevents multi-client sync collisions. |
| 8 | 🤝 **Trust the Connection** | **Zero artificial timeouts.** If the API accepts your request without error, KAME waits patiently for it to finish — even if it's a massive 90,000-token compression that takes 90 seconds. No death-loops on slow models. |
| 9 | 📦 **KAME-Powered Compression** | Agent Zero's history compression calls go through the same eternal carousel. Multi-key rotation during summarization keeps compression flowing even when individual keys hit limits mid-process. |
| 10 | ⚠️ **Long-Delay Warning** | When a parsed retry-after exceeds 60s (typically a daily quota — RPD/TPD), KAME logs a clear warning so you can investigate. Value still respected, capped at 1 hour upstream. |
| 11 | 🔒 **Rate Limiter Deadlock Fix** | Replaces Agent Zero's `asyncio.Lock` with `threading.Lock` on the framework's rate limiter, eliminating an async deadlock that can freeze the agent under specific concurrency patterns. |
| 12 | 🧹 **Clean Uninstall** | `hooks.py::uninstall()` reverts every monkey-patch before deletion. No dangling state, no leftover hooks. Drop the folder and KAME is gone. |

---

## 🔬 How it actually works

```
                ┌──────────────────────────────────────┐
                │ Agent Zero asks LiteLLM for a chat   │
                └────────────────┬─────────────────────┘
                                 │
                                 ▼
                ┌──────────────────────────────────────┐
                │  KAME monkey-patched unified_call    │
                └────────────────┬─────────────────────┘
                                 │
                                 ▼
                ┌──────────────────────────────────────┐
                │   _get_best_key(provider:model)      │
                │                                      │
                │   1. clean expired RPM entries (60s) │
                │   2. filter healthy (sick_until<now) │
                │   3. pick min(len(request_log),      │
                │              last_used)              │
                │   4. mark used NOW (anti-dogpile)    │
                │   5. count pending NOW (anti-herd)   │
                └────────────────┬─────────────────────┘
                                 │
            ┌────────────────────┴──────────────────────┐
            │                                           │
            ▼                                           ▼
   [Healthy key found]                       [All keys sick]
            │                                           │
            ▼                                           ▼
   ┌─────────────────┐                  ┌──────────────────────────────┐
   │ acompletion()   │                  │ Read soonest sick_until      │
   │ → real API call │                  │ Sleep min(eta+0.5, 30s) +    │
   └────────┬────────┘                  │       jitter(0.1-1.5s)       │
            │                           │ NO API calls during sleep    │
            ▼                           │ continue → loop top          │
   ┌─────────────────┐                  └──────────────────────────────┘
   │ Success?        │
   └────────┬────────┘
            │
     ┌──────┴──────┐
     ▼             ▼
   YES           NO (429)
     │             │
     ▼             ▼
  mark         _classify_error_delay
  healthy +    └─→ parse retry-delay (provider)
  add to RPM       └─→ if >60s: WARN operator
  log              └─→ set sick_until = now + delay
                       (capped 3600s)
                   continue → loop top
```

### Identity-Aware Health, explained

Every API key has a small state dictionary tracked under `{provider}:{model}`:

```python
{
    "sick_until": float,      # epoch time when key becomes available again
    "last_used": float,       # for LRU tie-break + anti-dogpile
    "request_log": [float],   # 60s sliding window of request timestamps
    "last_sick_at": float,    # for compression-aware "fresh recovery" filter
}
```

When you have `gemini-2.5-flash` as **chat** AND `gemini-2.5-flash` as **utility**, they share the pool. When you have `gemini-2.5-flash` as chat and `gemini-2.5-pro` as utility, they're **two separate pools** — a 429 on flash doesn't take down pro.

### RPM-Aware Predictive Selection

Instead of "next in line", KAME picks the key with **fewest recent requests**, then **least recently used** as tie-break:

```python
best_key = min(healthy, key=lambda k: (
    len(pool[k]["request_log"]),  # most remaining 60s-window capacity
    pool[k]["last_used"],         # then LRU for even spreading
))
```

In a 15-key pool firing 100 requests in 60 seconds, KAME spreads them roughly evenly (~6-7 per key) without you doing anything. Plain round-robin would either hammer one key into oblivion (if its index comes first when others are recently sick) or burn cycles checking sick keys.

### ETA-Driven Sleep, the v1.0.0 breakthrough

The hardest scenario: **all your keys hit the limit roughly the same time**. Plain round-robin (and earlier KAME versions) would pulse every 2 seconds re-trying the same sick keys, each rejection re-arming the provider's cooldown.

KAME v1.0.0:

1. Looks at all `sick_until` timestamps in the pool
2. Picks the **soonest recovery** (e.g., 28 seconds from now)
3. Sleeps `min(28 + 0.5, 30) + jitter` seconds
4. **Continues** the loop — never calls the API with a still-sick key
5. Wakes up on time, finds the recovered key, completes the request

Real-world impact from a production log:

> **Before:** 15-key Gemini pool fully sick → ~45 wasted 429 requests in 26 seconds, cooldowns extended from ~28s to 56-59s by re-arm pattern.
>
> **After v1.0.0:** Same scenario → **1 sleep event, 0 wasted requests, cooldown respected exactly, wake-up off by 291ms (the jitter)**.

---

## 📊 Verbose mode (opt-in observability)

KAME ships with two log modes:

### Default (clean)
```
[KAME] Chat|gemini-2.5-flash ➡ Calling...
[KAME] Chat|gemini-2.5-flash ✅ AIzaSyDo... (1 attempt)
```

### Verbose trace (Settings → Plugins → KAME → ☑ Verbose trace mode)
```
[KAME] Chat|gemini-2.5-flash ➡ Calling...
[KAME] Chat|gemini-2.5-flash ➡ k0a770 picked in 0.08ms
[KAME] Chat|gemini-2.5-flash ✅ k0a770 in 2.4s | pool 15/15 healthy
```

When something interesting happens, you'll see:

```
[KAME] Chat|gemini-2.5-flash 💤 All keys cooling. Sleeping 7.7s (no API calls)
       - next key recovers in ~7s (wake at 18:09:00)
[KAME] Chat|gemini-2.5-flash ➡ k0a770 picked in 0.08ms
[KAME] Chat|gemini-2.5-flash ✅ k0a770 in 9.4s | pool 15/15 healthy | 5 rotations, 1 pulse, 7.7s local wait
```

The sleep line is **always visible**, regardless of verbose mode — so users never think the agent is stuck during a legitimate wait.

---

## ⚙️ Configuration

KAME is intentionally minimal-config. The only setting is:

| Setting | Default | Purpose |
|---|---|---|
| `verbose_trace` | `false` | Toggle the detailed log mode shown above. |

Set via Agent Zero's plugin settings UI, or in `default_config.yaml`, or in your A0 config at any scope.

**Everything else is opinionated and validated in production.** The algorithm, sleep timing, jitter range, RPM window, and quarantine logic are all tuned. You don't need to tweak them — and if you do, you can read the well-commented code in `kame_engine.py`.

---

## 📈 Real-world impact (production log, May 2026)

Over **8,571 lines** of a single day of intensive Agent Zero usage:

| Metric | Value |
|---|---|
| KAME-managed operations | **1,163** |
| Rate limit (429) events encountered | **117** |
| Rate limits resolved by rotation alone | **116** (99.1%) |
| Pool-fully-sick events requiring sleep | **1** |
| Pulses (false retries against sick keys) | **0** |
| Long-delay warnings (>60s) | 0 |
| KAME engine crashes | 0 |
| Pool state "healthy" during operations | **~99%** |

The single sleep event:
- Predicted wake: 18:09:00
- Actual wake: 18:09:00.291 (off by 291ms — the random jitter)
- Behavior: picked recovered key in 0.08ms, request succeeded

That's what production-validated means.

---

## 🛠️ Install / Uninstall

### Install
```bash
# Drop the folder into Agent Zero's user-plugins directory
cp -r api_rotation_by_kame /a0/usr/plugins/
# Restart Agent Zero (Docker restart, or process restart depending on setup)
```

Then in **Agent Zero → Settings → Model Provider**, enter your API keys comma-separated:
```
key1, key2, key3, key4, ...
```

That's it. The activation extension fires on the first monologue and applies the patches automatically.

### Uninstall
```bash
rm -rf /a0/usr/plugins/api_rotation_by_kame/
# Restart Agent Zero
```

KAME's `hooks.py::uninstall()` runs BEFORE deletion and reverts every monkey-patch. No leftover state.

### Verify it's running

Look for this banner on Agent Zero startup:
```
=======================================================
  🐢⚡ KAME v1.0.0 — ACTIVE
  ✓ Identity-Aware Health
  ✓ Eternal Carousel Rotation
  ✓ RPM-Aware Predictive Selection
  ✓ Anti-Dogpile Guard
  ✓ Anti-Thundering-Herd (Pending Counter)
  ✓ Trust the Connection (No Artificial Timeouts)
  ✓ KAME-Aware Compression Guard
  ✓ Hybrid Learning (Parsed retry-delay + ETA-driven sleep)
  ✓ Long-Delay Warning (>60s flagged for operator)
  ✓ Rate Limiter Lock Fix
  ✓ Token Callback Support
  ✓ Friendly Error Reporting
=======================================================
```

---

## ❓ FAQ / Troubleshooting

**Q: I only have one API key. Does KAME help?**
A: With one key, KAME is roughly equivalent to A0's native rate limiter. The eternal-carousel magic needs multiple keys. Recommend 5+ for good RPM spreading.

**Q: KAME picked the same key twice in a row. Bug?**
A: Likely not — if you have only 2-3 keys and one just succeeded with fresh RPM capacity, RPM-aware selection may legitimately re-pick it. With more keys (10+), this becomes very rare due to anti-dogpile.

**Q: I'm seeing "Long retry delay parsed: 536s" — is this a bug?**
A: No, it's a feature working correctly. Google (or whoever) told KAME this specific key needs to wait 9 minutes — typically a daily quota reset. KAME respects it and surfaces the warning so you know.

**Q: Compression takes a long time. Is KAME slowing it down?**
A: No. KAME's "Trust the Connection" philosophy means zero artificial timeouts. A 90,000-token compression that legitimately takes 90 seconds takes 90 seconds. Without KAME, A0's native flow would crash with "timed out" — KAME lets it finish.

**Q: Does verbose_trace cost extra API calls?**
A: Zero. It's pure local instrumentation — just adds log lines.

**Q: Can I use KAME with Anthropic / OpenAI / others, not just Gemini?**
A: Yes. KAME is provider-agnostic. It works wherever Agent Zero's LiteLLM layer works. The retry-delay parser handles Google, OpenAI, Anthropic, and generic HTTP `Retry-After` headers.

**Q: What if all my keys die permanently?**
A: KAME keeps cycling. Auth errors (401) trigger a 1-hour quarantine for that key. If literally every key is dead, your agent will sleep 30s, wake, try again, sleep 30s, etc., until you fix it. No infinite spin against a wall.

---

## 🔧 Compatibility

- **Agent Zero**: v1.14+ (verified through v1.15)
- **Python**: 3.10+ (uses modern union syntax)
- **Providers**: any LiteLLM-supported provider (Google, OpenAI, Anthropic, Mistral, Groq, DeepSeek, xAI, Together, ...)
- **No new dependencies** — uses stdlib only on top of what A0 already ships

---

## 🤝 Contributing

PRs welcome. The engine is intentionally small (~800 LOC, single file). When proposing changes:

1. Keep the **engine algorithm** stable — selection, anti-dogpile, ETA-driven sleep are battle-tested.
2. Add features behind opt-in flags when possible (see `verbose_trace` as a pattern).
3. Log production behavior in `test_logs/` with version-named files so we can audit changes.

Bugs and feature requests via GitHub issues.

---

## 📜 License

**MIT License** — see [`LICENSE`](LICENSE) for the full text.

`Copyright (c) 2026 KAME (https://github.com/Kame696)`

You can use, modify, distribute, and even sell KAME with the only requirement
being to keep the copyright notice. The author keeps all rights to KAME as
the original work; the license simply grants permissive usage rights to
others.

---

## 🪪 Evolution / Version history

KAME has been in development since early 2026, learning from real production logs at every step:

| Version | Codename / Focus | Key insight |
|---|---|---|
| **v1.0.0** | First stable release | Engine validated across 1,163 ops, 117 rate limits, 0 crashes. ETA-driven sleep proven in production. |
| v0.5.8.0 | The ETA Fix | Discovered the v0.5.7.x pulse bug from real logs: KAME was burning ~45 wasted 429 requests in 26 seconds when all keys went sick. Fixed by sleeping exactly until next recovery instead of pulsing blindly. |
| v0.5.7.4 | Verbose Trace | Added opt-in observability: key short id, selection latency, pool snapshot, cascade summary, compression-aware filter. |
| v0.5.7.3 | The Trust Restored | Rolled back a misattributed "bug fix" that was actually the production-validated dispersion brake. Trust the maintainer's intent. |
| v0.5.7 | Packaging Cleanup | A0 v1.15 schema compliance, clean uninstall hooks, plugin.yaml conformance. |
| v0.5.6 | The Trust | "Trust the Connection" philosophy formalized — zero artificial timeouts. |
| v0.5.0 - v0.5.5 | The Commander → The Refined | Identity-aware health, anti-dogpile, anti-thundering-herd, smart quarantine. |
| v0.4.x | The Seed → The Strategist | Foundational rotation, eternal carousel, basic RPM-awareness. |

**The lesson learned across versions**: the only way to build something this reliable is to **run it in production and read the logs honestly**. Every major improvement in KAME came from a real log showing real behavior — not from theory.

---

## 🎀 Credits

Built by [**KAME**](https://github.com/Kame696). Engine refinement guidance came from real production log analysis — including the v0.5.7.4 log that revealed the wasted-pulse bug fixed in v0.5.8.0. Special thanks to every 429 that taught KAME something new.

### ⭐ Star this repo

If KAME made your agent less frustrating, drop a star ⭐ — it costs you nothing and helps others find this. The more stars, the more visibility, the more contributors, the better KAME gets for everyone.

[**Star Kame696/kame-api-engine on GitHub →**](https://github.com/Kame696/kame-api-engine/stargazers)

---

<div align="center">

🐢⚡ **KAME v1.0.0** — *because round-robin was never enough*

**Bitcoin** — `36BGYhMEVFgY8PLGMVux93pjGt92KVM6dJ`

*4P1 R0T4T10N — 4FRE3D0M*

</div>
