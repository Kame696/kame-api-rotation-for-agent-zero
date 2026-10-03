# Agent Zero 1.8.1.8 validation

## Verified runtime

[CI 37091291526](https://github.com/Kame696/kame-api-rotation-for-agent-zero/actions/runs/37091291526)
passed all 14 jobs at candidate 8f47029ab72a0e356d2a68adbfd9cdd831a5e41b:

- All 24 offline suites on Linux, Windows and macOS, Python 3.11/3.12/3.13/3.14.
- Pinned official Agent Zero v2.12 (b1cbd1f960a1a5c4482b324dcff4742aa67b7a51)
  and v2.13 (e3051fb584b1a36be2b0a0c90606f1c2c2d356ec), Python 3.13.
- All existing native-host compatibility checks, then 23 extended checks per
  host: 20 load/unload cycles, real LiteLLM/OpenAI SDK over local TCP, 429
  rotation, truncated HTTP streams, original messages/model/temperature,
  incomplete tool request followed by exactly one complete ResponseTool call,
  native early-stop, cancellation and a disabled-rotation negative control.
- 18 targeted offline lifecycle/control-flow tests. Fourteen witnesses failed
  on unchanged 1.8.1.6 before the repair; the original 23 suites already passed.

The first candidate CI failure is retained: stale version assertions and using
the v2.13 fingerprint baseline for v2.12. Assertions remain strict. The v2.12
baseline is the unchanged, previously published KAME 1.8.1.2 baseline; no
fingerprint was regenerated merely to accept a failing check.

## Local dispatcher comparison

Matched offline healthy calls, diagnostics disabled equally, six alternating
rounds and 2,850 measured calls per version/key-count. Same answer/reasoning.
This excludes provider latency and is not a claim about end-to-end speed.

| Keys | 1.8.1.6 median / p95 (ms) | 1.8.1.8 median / p95 (ms) |
|---|---|---|
| 1 | 0.0805 / 0.2093 | 0.0915 / 0.2004 |
| 2 | 0.0717 / 0.1435 | 0.0786 / 0.1283 |
| 14 | 0.1362 / 0.4016 | 0.1223 / 0.1993 |

All pass the declared noise gate: p95 increase at most max(0.025 ms, 10% of
baseline). Small mixed median changes and host scheduling noise preclude a
universal speedup claim. Hermes's 63–65% snapshot saving is not attributed here.

## Boundaries

Native Agent Zero ran only on disposable remote GitHub runners, not on the
owner's computer. Network fixtures used the real SDK and TCP but no real
provider credentials; they do not establish live provider quota or model quality.
Earlier 1.8.1.6 real-provider results remain historical, not 1.8.1.8 validation.
One existing platform-specific filesystem test skips on Windows. Earlier host
versions retain historical coverage, not a new native-runtime matrix claim.
Finite tests establish these contracts, not zero bugs or universal uptime.
Restart Agent Zero after upgrading; no transparent legacy hot-reload claim.

## Preservation

Rotation classification, healthiest-key selection, quota policy, native stream/
result ownership, global settings and existing public history are retained.
No request racing, model downgrade, automatic updater or owner installation.
Release ZIP inventory, CRC, SHA-256 and Git-blob equality are checked separately
at publication; the release page records the exact asset digest.
