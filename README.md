<div align="center">

<img src="webui/kame_banner.jpg" alt="KAME — Key-Aware Management Engine for Agent Zero" width="600" />

# 🐢⚡ KAME — API Key Rotation for Agent Zero

**Paste several API keys. KAME picks the healthiest one for every call, reads every refusal, and never lets a rate limit end your run.**

Smart API key rotation, 429 / `RESOURCE_EXHAUSTED` recovery and rate-limit failover for [Agent Zero](https://github.com/agent0ai/agent-zero) — Gemini, OpenAI, OpenRouter, Anthropic, or any provider LiteLLM speaks to.

[![Version](https://img.shields.io/badge/version-1.8.1.0-blue.svg)](CHANGELOG.md)
[![Agent Zero](https://img.shields.io/badge/Agent_Zero-v1.14%2B_·_verified_v2.12-purple.svg)](#verified)
[![Real sessions](https://img.shields.io/badge/real_sessions-26%2F26_answered-brightgreen.svg)](#verified)
[![Dependencies](https://img.shields.io/badge/dependencies-none-lightgrey.svg)](#privacy)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/Kame696/kame-api-rotation-for-agent-zero?style=social)](https://github.com/Kame696/kame-api-rotation-for-agent-zero/stargazers)

**[Install](#install) · [Why not round-robin](#vs) · [How it reads errors](#errors) · [What you see](#screens) · [Settings](#settings) · [Version history](#history) · [FAQ](#faq) · [Hermes version](https://github.com/Kame696/kame-api-rotation-for-hermes)**

</div>

---

<a id="tldr"></a>
## ⚡ In 30 seconds

- **One field, many keys.** Put `key1,key2,key3` in the provider's API key field. KAME reads it as three keys.
- **The healthiest key, every call** — not only after a failure. Fewest requests in the last 60 seconds wins, least recently used breaks the tie, and a key is marked busy the moment it is handed out, so parallel agents spread across the pool instead of piling on one key.
- **It reads the error instead of guessing.** A per-minute throttle, a daily quota, an outage and a dead key are four different problems, and each gets its own wait — the provider's own number whenever it states one.
- **No key sits out longer than an hour.**
- **When every key is resting, it waits** for the first one back and says so in the chat, instead of failing the run. Agent Zero makes the call itself; KAME only chooses the key.

<a id="vs"></a>
## 🆚 KAME vs round-robin

Most key rotators cycle keys in order and retry on a timer. KAME decides from the refusal itself.

| | Round-robin rotator | **KAME** |
|---|---|---|
| Which key is next | the next one in the list | **the least-loaded healthy key** (60-second window) |
| Parallel calls | all start on the same key | **spread across keys** (marked busy at hand-out) |
| After a 429 | retry on a fixed backoff | **the provider's own `retryDelay` / `Retry-After`, to the second** |
| Per-minute vs daily quota | same treatment | **told apart** (`quotaId`, reset hints, the pool's own behaviour) |
| Gemini bare `RESOURCE_EXHAUSTED` | fixed backoff or hammering | **1 → 2 → 4 → 8s … ladder, reset the moment the key answers** |
| Outage (5xx) | punishes every key | **1s, never escalates** — the key was never at fault |
| A key spent on one model | benched everywhere | **still used on your other models** |
| All keys resting | error, run over | **sleeps until the first recovery, and says so in the chat** |
| Longest a healthy key can be lost | whatever the provider claimed | **1 hour** (`max_hold_seconds`) |

<a id="install"></a>
## 🚀 Install

**From the Agent Zero Plugin Hub** — search for **KAME** and install.

**Or by hand** — copy this repository into your Agent Zero's user plugins folder as `api_rotation_by_kame`:

```
/a0/usr/plugins/api_rotation_by_kame/
```

Then **restart Agent Zero** once. Look for `🐢⚡ KAME v1.8.1.0 — ACTIVE` in the log.

| | |
|---|---|
| **Needs** | Agent Zero v1.14+ or the V2 line — nothing else, no extra package |
| **Configuration** | none required |
| **Uninstall** | remove the folder; `hooks.py` reverts every patch |

<a id="keys"></a>
## 🔑 Add your keys

In **Agent Zero → Settings → Model Provider**, paste all your keys into that provider's **one** API key field, separated by commas:

```
key1,key2,key3,key4
```

Or in `/a0/usr/.env`:

```
API_KEY_GEMINI=AIzaSy...aaa,AIzaSy...bbb,AIzaSy...ccc
```

One key works too; there is just nothing to rotate to.

<a id="errors"></a>
## 🧠 How KAME reads every error

Every refusal is sized from what the provider actually sent. Decisions are made on the payload, never on the provider's name, so a provider that did not exist when this was written is covered by the same rules.

| The provider says | KAME does |
|---|---|
| **429 with a stated wait** (`retryDelay`, `Retry-After`, "retry in N s") | rests that key **exactly that long**, up to the 1-hour ceiling |
| **Gemini `429 RESOURCE_EXHAUSTED`, no number at all** | rests it **1s, then 2, 4, 8, 16, 32, 64s** on each repeat; back to 1s the moment it answers |
| **Any other throttle with no number** | a flat **30s** — measured: retried sooner, a refused key answered 0 of 73 times |
| **A daily quota** (e.g. `PerDay` in `quotaId`) | re-probes in **5 minutes** while other keys still answer; rests **1 hour** once the whole pool has been silent for 20 minutes |
| **Out of credit** | **1 hour**, and the key rests on **every model** of that provider — the account is empty, not the model |
| **5xx / overloaded** | **1 second**, never escalates — an outage is not the key's fault |
| **Timeout** (the attempt really waited) | next key at once; the key is not rested |
| **A connection that never opened** | 3 seconds, so a network outage cannot turn into a tight loop |
| **Bare 401** | 20s, doubling on a repeat, out of rotation after 3 in a row |
| **"This key is invalid / revoked"** | out of rotation at once — replace it and it comes back by itself |
| **403 for one model only** | that key skips that model and keeps working on the others |
| **Anything it cannot size** | a short rest and the next key; the error is never swallowed silently |

<a id="screens"></a>
## 🖥️ What you see

- **A rotation chip beside the composer** — how many keys are ready right now; a click shows every pool.
- **A notice in the chat** when every key is resting for more than ~90 seconds: how many are resting, when the earliest is back, and that **stop still works**. Counts only — never a key — and it never enters the model's history.
- **`/kame`** — build, pools, what is ready. **`/kame doctor`** — every kind of refusal beside the rest it got, how often it happened, and anything only a person can fix.
- **One line per decision in the log**, with the key as an anonymous fingerprint (`k3f9a1`):

```
[KAME] Util|gemini-3.8-flash kfb76c ⏳ 503 server-busy → key cooled 1s · rotating to next key...
[KAME] Util|gemini-3.8-flash ✅ kaa51d · 2 rotations
[KAME] Chat|gemini-3.8-flash k0a770 ⏳ 429 per-minute → key waits 4s · rotating to next key... [backoff.3]
```

<details>
<summary><b>Everything inside</b></summary>

| | What it gives you |
|---|---|
| 🆔 **Identity-aware health** | Health per `provider:model` — a 429 on one model does not disable the key on another |
| 📊 **Predictive selection** | 60-second window per key; the least-loaded key wins, least recently used breaks the tie |
| 🛡️ **Anti-dogpile + anti-herd** | A key counts as busy the moment it is handed out, so concurrent calls pick different keys |
| 💤 **ETA-driven sleep** | When every key rests, sleep until the soonest recovery (checked at least every 60s, with a little jitter) — never call with a resting key |
| 🎯 **Evidence-sized rests** | The table above; the provider's number whenever it states one |
| 🪜 **RESOURCE_EXHAUSTED ladder** | 1-2-4-8…64s for Gemini's bare 429, reset by an answer |
| ⛔ **One-hour ceiling** | No hold survives `max_hold_seconds`, whatever set it |
| 🤝 **Delegated execution** | Agent Zero makes the call; KAME picks the key and binds to A0's model layer by shape, so A0 updates rarely break it |
| 📦 **Compression through the carousel** | History summarisation rotates keys too |
| 🔒 **Rate-limiter lock fix** | Replaces an `asyncio.Lock` that could deadlock under a specific concurrency pattern |
| 🧯 **Unusable-response floor** | Raises A0's "stop after N unparseable replies" to a floor (default 5), never a ceiling |
| 💬 **The wait, said out loud** | The chat notice above |
| 🧹 **Clean uninstall** | Every patch reverted by `hooks.py` |

</details>

<a id="settings"></a>
## ⚙️ Settings

Nothing needs changing. Every setting is on the plugin's settings page; the newest ones also read an environment variable (the environment wins).

<details>
<summary><b>The ones worth knowing</b></summary>

| Setting | Default | What it does |
|---|---|---|
| `max_hold_seconds` | `3600` | No key sits out longer than this (`KAME_MAX_HOLD`) |
| `unsized_throttle_backoff` | on | The 1-2-4-8s ladder for Gemini's bare `RESOURCE_EXHAUSTED` (`KAME_UNSIZED_BACKOFF`) |
| `unsized_backoff_max_seconds` | `64` | Where that ladder stops growing (`KAME_UNSIZED_BACKOFF_MAX`) |
| `unsized_throttle_rest_seconds` | `30` | Rest after any other throttle that names no wait (`KAME_UNSIZED_REST`) |
| `daily_quota_cooldown_seconds` | `3600` | Rest once a daily quota is confirmed by a silent pool |
| `kame_wait_notice` | on | The chat notice while every key rests |
| `kame_unusable_response_limit` | `5` | The floor under A0's unparseable-reply stop (`0` = leave A0 alone) |
| `kame_log_level` | `normal` | `silent` · `normal` · `verbose` · `verbose+errors` |
| `key_log_style` | `fingerprint` | How keys appear in logs; never the full key |

</details>

<a id="privacy"></a>
## 🔒 Privacy

- **KAME never prints, logs or sends a key.** Logs, the chip, the chat notice and `/kame` carry fingerprints and counts only.
- **No telemetry, no network call of its own, no third-party package.** Agent Zero makes every model call.

<a id="verified"></a>
## ✅ Verified

| Check | Result |
|---|---|
| Two real sessions — Agent Zero v2.12 code, real LiteLLM, 14 real Gemini keys | **26 / 26 answered**, 16 of them concurrent; 13 real `503`s absorbed at 1s each; all 14 keys carried traffic |
| Live harness — KAME's real patches applied to a real Agent Zero checkout | **all green** on v2.11 and v2.12 |
| Upgrade check — every Agent Zero symbol KAME touches | **12 / 12** fingerprints and **12 / 12** host facts hold on v2.12 |
| Offline tests | **14 / 14** suites green, including the 1.8.1.0 parity suite with the Hermes port |
| Adversarial review | a second model tried to break the port; its 8 findings are fixed and each is now a test |

The real sessions ran with Agent Zero's `nest_asyncio` shim replaced by a no-op, because it breaks HTTP timeouts on the Python 3.14 used for the test; Agent Zero's own Docker image runs an older Python where the shim works. The tool is `tools/live_a0_session.py`.

<a id="history"></a>
## 🪪 Version history

In development since early 2026, and every release came from a real log, not from theory. One line per version here; the full story of each is in [CHANGELOG.md](CHANGELOG.md).

> **v1.8.1.0's error reader, by the numbers:** built from **13,561 real
> refusals** (311 distinct messages) and graded against an independent answer
> key of **68 error shapes, sorted into 11 kinds of error, across 12 providers
> and gateways** — Google Gemini, OpenAI, OpenAI Codex, Anthropic, NVIDIA,
> OpenRouter, Groq, DeepSeek, AIHubMix, TokenRouter, ZenMux and GLM. Every
> verdict comes from the payload itself — status, structured error code,
> headers, then prose — never from a list of known provider names, so a
> provider outside these 12, including one that does not exist yet, is read
> by the same rules on its first refusal.

<details>
<summary><b>Every version, one line each (click to open)</b></summary>

| Version | Focus | In one line |
|---|---|---|
| **v1.8.1.0** | Every refusal sized from its own evidence | A new error reader since v1.2.0, built from **13,561 real refusals** and graded against **68 error shapes** (11 kinds) across **12 providers and gateways** — Gemini, OpenAI, Codex, Anthropic, NVIDIA, OpenRouter, Groq, DeepSeek, AIHubMix, TokenRouter, ZenMux, GLM — evidence-based, so an untested provider reads by the same rules. The provider's own number is obeyed and never inflated, per-minute told from per-day, a daily label costs a 5-minute re-probe instead of an hour, 5xx never escalates. Gemini's bare `429 RESOURCE_EXHAUSTED` climbs a 1-2-4-8…64s ladder, reset the moment a key answers; no key is held longer than an hour; a throttle with no number rests 30s; a real timeout rotates without benching; out of credit rests the key on every model. Verified in real sessions on A0 **v2.12**. |
| **v1.7.0.5** | Three numbers, measured on real keys | A daily-quota label alone buys a 5-minute re-probe, not an hour (refused keys came back in 6–36 minutes, 21 of 21); a retry hint in **milliseconds** is no longer read as minutes; a **5xx never escalates**. Shipped inside 1.8.1.0. |
| **v1.2.0** | The wait, said out loud | An all-keys-cooling wait now appears **in the chat**, not only on the console, and the settings screen was rebuilt so an on-by-default toggle stops rendering as off. Verified on A0 **v2.10**. |
| **v1.0.9** | KAME stops re-implementing Agent Zero | KAME only **chooses the key**; A0 owns the request, the stream, the parsing and the result. Live-verified on six A0 tags, one code path. |
| **v1.0.8** | Early stop + denied keys | The stream breaks where native A0 breaks it, and a `403 PERMISSION_DENIED` is quarantined instead of returning to the carousel every 20 seconds. |
| **v1.0.7** | Response Shield | A `response` tool arriving with empty or wrongly-keyed arguments is healed instead of crashing the turn. |
| **v1.0.6** | Faster failover, honest numbers | Zero-delay rotation (~750 ms saved per 15-key storm), the provider's own quota tag printed inline, one transient empty stream forgiven. |
| **v1.0.5** | Daily quota, correctly | An existing cooldown can never be shortened, and the carousel honours chat **pause**. |
| **v1.0.4** | Alive on Agent Zero V2 / V2.1 | V2 moved streaming and V2.1 split the entry point — rotation was being bypassed. One engine now serves both majors. |
| **v1.0.3** | Observability + faster recovery | Two real Gemini 503 outages (one **83 minutes**) made the logs readable: precise durations, storm collapse, fast pool thaw. |
| **v1.0.2** | A 5xx is not a daily quota | A 503 whose body mentioned "daily" cooled the whole pool for an hour. Any 5xx is now a short server retry. |
| **v1.0.1** | Quota awareness across providers | Strict daily/account detection, adaptive backoff, and a `silent`/`normal`/`verbose` log switch. |
| **v1.0.0** | First stable release | Validated in production: 1,163 operations, 117 rate limits, 0 crashes. |
| v0.5.x | The Commander → The Trust | Identity-aware health, anti-dogpile, anti-thundering-herd, sleeping exactly until the next key recovers. |
| v0.4.x | The Seed → The Strategist | Foundational rotation, the eternal carousel, basic RPM awareness. |

</details>

<a id="faq"></a>
## ❓ FAQ

<details>
<summary><b>Does it work with OpenAI, Anthropic, OpenRouter — not just Gemini?</b></summary>

Yes. KAME decides from the refusal — retry timing, headers, the shape of the error body — never from who the provider is.
</details>

<details>
<summary><b>Why does Gemini say <code>RESOURCE_EXHAUSTED</code> on every key at once?</b></summary>

Gemini's quotas are per Google Cloud project, and a bare `429 RESOURCE_EXHAUSTED` with no `quotaId` and no `retryDelay` often arrives on many keys at the same moment and clears on all of them together — which points at a busy model rather than at your quota. KAME retries those keys on a short 1-2-4-8s ladder instead of benching them, and the first key that answers resets its own ladder.
</details>

<details>
<summary><b>A key was rested for an hour. Is that a bug?</b></summary>

Only a confirmed daily quota or an empty balance rests a key that long. A daily label alone buys a 5-minute re-probe; the hour applies once the whole pool has gone 20 minutes without a single answer. Nothing is ever held longer than `max_hold_seconds`.
</details>

<details>
<summary><b>Will an Agent Zero update break it?</b></summary>

Rarely, by design: Agent Zero makes the call and KAME only chooses the key. Every release is checked with `tools/a0_upgrade_check.py`, which fingerprints each Agent Zero function KAME touches and runs the live harness against the new checkout. If a future Agent Zero ever moves out from under it, KAME prints one line and steps aside — your agent keeps running.
</details>

<details>
<summary><b>I only have one key. Does KAME help?</b></summary>

Yes: the right wait for each error, the one-hour ceiling, the chat notice and the unusable-response floor all apply. It simply has nothing to rotate to.
</details>

---

<a id="hermes"></a>
## 🐢 Also for Hermes

The same engine, ported to the [Hermes agent](https://github.com/NousResearch/hermes-agent): **[kame-api-rotation-for-hermes](https://github.com/Kame696/kame-api-rotation-for-hermes)**.

## ❤️ Support

KAME is free, MIT, and built by one person against real quotas. No company, no telemetry, nothing to upsell. If it saved you a run, a tip keeps it going:

**Bitcoin** — `36BGYhMEVFgY8PLGMVux93pjGt92KVM6dJ`

And a ⭐ costs nothing and helps other people find it.

## 📜 License

MIT — see [LICENSE](LICENSE). Bugs and ideas: [issues](https://github.com/Kame696/kame-api-rotation-for-agent-zero/issues).

<div align="center">

🐢⚡ **KAME 1.8.1.0** — *because round-robin was never enough*

</div>
