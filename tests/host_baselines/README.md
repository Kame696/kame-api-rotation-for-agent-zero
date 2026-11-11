# Retained native-host baselines

Each file here is byte-for-byte the a0_compat.json of a published KAME release,
not a newly accepted fingerprint generated to hide a failure.

- v2.12.json is a0_compat.json from published commit 5241e93 (1.8.1.2). Its
  sole fingerprint difference from later hosts is the adaptive
  LiteLLMChatWrapper.unified_turn body; native request, stream and result
  execution belong to Agent Zero.
- v2.14.json is a0_compat.json from published commit fb47394 (1.8.3.1),
  verified against v2.14. Agent Zero v2.13 matches it, and the v2.13 job reads it.

The release's own a0_compat.json is the v2.15 baseline, and the v2.15 job reads
it. Every host still has to pass all original contracts and the extended actual
SDK/TCP suite on remote runners.
