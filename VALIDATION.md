# Agent Zero 1.8.2.9 validation

## Verified runtime

Offline only, so far: 52 / 52 suites green (341 new 1.8.2.9 tests in 23 files). Among the new tests: a busy best model is asked at most
once per 20 seconds across conversations; no reply waits behind two silent models; Faster sends
one request on a good reply and steps back to Balanced after a quota refusal; a background job
stops inside its budget; the instrument logs rotate; every number on the page and in the chip comes
from one view; the page and the settings carry the Hermes port's shelves and words (checked
against a frozen copy of them, `tests/fixtures/hermes_1829_ui_strings.json`); the fallback list
editor merges in the right order and keeps lines written by hand.

## Boundaries

- **Not yet run on a real Agent Zero.** The native checks on pinned v2.12, v2.13 and v2.14, the
  upgrade check against those checkouts and a session on a real server have not been run for
  1.8.2.9. The status-line heading and the page are checked in tests and in the sources, not on a
  live screen.
- **Not measured:** the cost of the reply path and the idle CPU (`tools/bench_reply_path.py`,
  `tools/bench_status.py` exist and are tested offline; their numbers have not been taken). This
  version claims no speed-up and no saving there.
- The speed words give the most a day may spend at the default budget; what a given day spent is
  on the page ("Extra requests this hour").
- Hermes items with no Agent Zero twin: the fallback-list reach warning, reading YAML without
  PyYAML, the Gemini signature check on cross-model legs (reasons in `CHANGELOG.md`).

---

# Agent Zero 1.8.2.8 validation

## Verified runtime

[CI 39127288740](https://github.com/Kame696/kame-api-rotation-for-agent-zero/actions/runs/39127288740) on the
release commit: the offline suites on Linux, Windows and macOS, Python 3.11 / 3.12 / 3.13 / 3.14,
and the pinned official Agent Zero v2.12, v2.13 and v2.14 hosts (Python 3.13). Per host, the 23
1.8.1.8 checks (`tests/native_1818.py`) and 41 checks of `tests/native_1827.py`, all through the
real LiteLLM / OpenAI SDK over local TCP:

- everything listed for 1.8.2.7 below;
- a key that accepts and then says nothing is dropped after the first-token wait, the next one
  answers; an answer cut mid-stream is continued on the next key, which is sent what was shown,
  and nothing is shown twice; an error every key refuses reaches Agent Zero's `format_error`
  text with the line telling the agent that retrying will not help;
- **1.8.2.8:** a model answering 503 "overloaded" on two keys is answered by the next model of
  the list after those two requests, within seconds, and remembered as busy; without a list the
  configured model keeps being asked; a turn ends with the line naming its model and time.

Offline: 29 / 29 suites, 68 new tests (`tests/test_v1_8_2_8.py`, `tests/test_v1_8_2_7_parity.py`), among them: a reply silent past its model's slow bound answered by the next model of the list; a late reply raced by the next model and, when that one is silent too, by the one after it, the first to answer shown and nothing recorded on the first pool; a raced model never cut before its own bound; a model known busy passed down without a request.

## Boundaries

As for 1.8.2.7 below: native Agent Zero ran only on disposable remote runners, with real SDK and
TCP but no real provider credentials. The automatic numbers (switch time, first-token wait) are
learned from each installation's own traffic and start from the stated cold values; their effect
on a real provider's latency has not been measured on this port.

---

# Agent Zero 1.8.2.7 validation

## Verified runtime

[CI 39056656954](https://github.com/Kame696/kame-api-rotation-for-agent-zero/actions/runs/39056656954)
passed all 16 jobs at candidate ef204b4eb14af1ba811553c8ed340f4c12f3bcd6:

- All 27 offline suites on Linux, Windows and macOS, Python 3.11/3.12/3.13/3.14
  (88 new 1.8.2.7 tests: 37 provider, 27 speed and fallback, 24 review regressions).
- Pinned official Agent Zero v2.12 (b1cbd1f960a1a5c4482b324dcff4742aa67b7a51),
  v2.13 (e3051fb584b1a36be2b0a0c90606f1c2c2d356ec) and v2.14
  (ff2a5b9c9c4e490ba38dd7c313e779510bb2071a), Python 3.13.
- Per host: every existing native check and the 23 1.8.1.8 checks
  (`tests/native_1818.py`), then 27 1.8.2.7 checks (`tests/native_1827.py`), all
  through the real LiteLLM/OpenAI SDK over local TCP:
  - a key that stays silent for 4 s gets a backup; the backup answers well before,
    whole and native, and only its words reach the screen;
  - every key of the configured model resting: the first fallback model answers,
    built by Agent Zero's own `models.get_chat_model`, the configured model is not
    asked, and the next call answers on the configured model again;
  - a call outside the conversation (a plugin's own streaming call) never switches;
  - a closed door (503 "no instances") goes to the fallback model after one knock;
    without a list the whole pool waits for the door, one request, not one per key;
  - the panel's two API handlers run in the host's real plugin layout
    (`usr/plugins/api_rotation_by_kame`), a setting is saved and in force, a bad
    value is refused, no key appears in any report.

## Provider answer keys

774 documented error shapes of 82 providers (`tests/provider_keys/`, the keys the
Hermes port is graded on), through `_is_terminal_error` and `_kame_decide_failure`
(`tools/provider_doc_gate.py`). Floors pinned in `tests/test_v1_8_2_7_providers.py`.

| Shape | family | action | window | scope | stated waits |
|---|---|---|---|---|---|
| openai-SDK exception | 674 | 675 | 116 | 96 | 6 / 6 |
| LiteLLM exception (what Agent Zero receives) | 670 | 674 | 115 | 96 | 6 / 6 |
| Hermes 1.8.2.7, same keys | 670 | 670 | 116 | 92 | — |

## Reviews

Three adversarial reviews of the whole change.
Every finding is fixed and pinned by a test that fails on the build the review read.

## Boundaries

Native Agent Zero ran only on disposable remote GitHub runners, not on the owner's
computer. Network fixtures used the real SDK and TCP but no real provider
credentials; they do not establish live provider quota, latency gains or model
quality. 1.8.2.7 has not run in a real Agent Zero session with real keys yet;
earlier real-provider results remain historical. The backup and burst speed-ups
were measured on Hermes with real refusal timings, not on this port. Finite tests
establish these contracts, not zero bugs or universal uptime. Restart Agent Zero
after upgrading.

## Preservation

Classification, healthiest-key selection, quota policy, native stream/result
ownership, global settings and public history are retained. Every 1.8.1.8 suite
passes; the pinned source counts that 1.8.2.7 changes on purpose say why in the test.
Model switching happens only through the user's own `fallback_models` list, for the
conversation only, never after anything was shown. Release ZIP inventory, CRC,
SHA-256 and Git-blob equality are checked at publication.

---

# Agent Zero 1.8.1.8 validation (previous release)

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
