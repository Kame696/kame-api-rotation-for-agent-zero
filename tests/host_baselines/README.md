# Retained native-host baselines

v2.12.json is byte-for-byte a0_compat.json from published KAME commit
5241e93 (1.8.1.2), not a newly accepted fingerprint generated to hide a failure.
The release's a0_compat.json remains the v2.13 baseline. The sole fingerprint
difference is the adaptive LiteLLMChatWrapper.unified_turn body; native request,
stream and result execution belong to Agent Zero. Both hosts still have to pass
all original contracts and the extended actual SDK/TCP suite on remote runners.
