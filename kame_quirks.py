"""Meanings that live in a status or a phrase the generic tables missed (1.8.2.7).

R01 is closed: **no decision branches on provider identity.** Every rule below
reads the response only - a status code's HTTP meaning, a structured code, the
provider's own words - and none of them looks at who sent it. (An earlier draft
of this module keyed rules on provider names; that broke R01 and was removed
the same day: `decisions/0026`.) Where only the sender's name could tell two
meanings apart, nothing is taught and the case is listed in
`04_plugins/kame/providers/UNTEACHABLE.md`.

Each rule comes from rows of the independent answer key
(``research/1.8.2.7/expected/*.jsonl``); the row ids are named at each rule.

``reinterpret`` answers only when it is SURE, and only ever moves a kind to a
gentler or a more accurate one. It never invents a number: the rest that goes
with the new kind is the one the rest of the plugin already uses for it.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

_I = re.IGNORECASE

#: The provider is at capacity / its queue is full / demand is too high. The key
#: is fine and another moment answers; it is not a spent counter.
#: Rows: doc-mistral-429-service-tier-capacity-3505, doc-sambanova-429-queue-full,
#: doc-cloudflare-429-3040-capacity, doc-cerebras-429-queue-exceeded,
#: doc-chutes-429-infra-overload.
_CAPACITY = re.compile(
    r"service[\s_-]*tier[\s_-]*capacity|queue[\s_-]*(is[\s_-]*)?(full|exceeded)"
    r"|capacity[\s_-]*(temporarily[\s_-]*)?exceeded|experiencing[\s_-]*high[\s_-]*traffic"
    r"|(at|reached)[\s_-]*(its[\s_-]*)?maximum[\s_-]*capacity",
    _I,
)

#: "Function '<id>': Not found for account '<id>'" - this account may not use
#: that function; another key (account) may. Row:
#: doc-nvidia-404-function-not-found-for-account.
_NOT_FOUND_FOR_ACCOUNT = re.compile(r"not[\s_-]*found[\s_-]*for[\s_-]*account", _I)

#: A 4xx that says the plan / role / tier does not include the model. Rows:
#: doc-hyperbolic-401-role-required, doc-cloudflare-403-5035-paid-plan,
#: doc-vercel-403-free-tier-model.
_PLAN_REQUIRED = re.compile(
    r"requires?\s+(a\s+|an\s+)?(role|plan|paid|pro\b|subscription|upgrade)"
    r"|upgrade\s+(your\s+)?(plan|account)"
    r"|not\s+available\s+(on|for|in)\s+(the\s+)?free\s+(plan|tier)"
    r"|only\s+available\s+to\s+(pro|paid|premium|subscribers)",
    _I,
)

#: The account is out of money, said in words (not 402). Rows:
#: doc-novita-403-not-enough-balance, doc-fireworks-412-account-suspended.
_NOT_ENOUGH_BALANCE = re.compile(r"not[\s_-]*enough[\s_-]*balance|insufficient[\s_-]*balance", _I)
_SUSPENDED_FOR_SPEND = re.compile(
    r"suspended[^.]{0,120}(spending|billing|invoice|payment)", _I
)

#: A "quota" error that names a per-minute / concurrency counter in its own
#: words: a throttle, not an empty balance. Rows: doc-scaleway-429-*.
_PER_MINUTE_QUOTA = re.compile(
    r"(requests?|tokens?)\s+per\s+minute|concurrent\s+requests|per[\s-]*minute\s+(quota|limit)", _I
)

#: A 429 that is really a refused model, not a counter. Row:
#: doc-chutes-429-invalid-free-model.
_INVALID_FREE_MODEL = re.compile(r"invalid\s+free\s+model", _I)

#: Words that say the KEY is bad that the carousel's own tables do not carry.
#: Rows: doc-baseten-403-authentication-failed, doc-novita-403-invalid-api-key.
#: ("authorization failed" is a permission, NVIDIA's 403, and is not here.)
_BAD_KEY_WORDS = (
    "authentication failed", "api key not provided", "authentication error",
    "invalid credentials", "not authenticated",
)

#: A server status that carries a request-fault code: the request is what is wrong.
#: Row: doc-cometapi-500-invalid-request-as-500.
_REQUEST_FAULT_CODE = re.compile(r"""["']code["']\s*:\s*["']invalid_request["']""", _I)

#: More money-in-words: a credit cap and prepaid-currency shortfalls. Rows:
#: doc-nagaai-403-credit-limit-exceeded, doc-ai-horde-403-kudos-upfront.
_CREDIT_WORDS = re.compile(r"credit[\s_-]*limit[\s_-]*exceeded|kudos[\s_-]*upfront|not[\s_-]*enough[\s_-]*kudos", _I)

#: A content / safety refusal: the REQUEST is what was refused. Rows:
#: doc-nagaai-403-inappropriate-content, doc-zenmux-403-safety-check-failed.
_CONTENT_REFUSED = re.compile(r"inappropriate[\s_-]*content|safety[\s_-]*check[\s_-]*failed", _I)

#: A refused model by the plan's own structured reason. Row: doc-nous-429-welcome-model-not-free.
_MODEL_NOT_FREE = re.compile(r"model[\s_-]*not[\s_-]*free", _I)

#: The credential, said in words the table did not carry. Rows:
#: doc-copilot-400-bad-request-missing-authorization, doc-nous-x-invalid-jwt-expired-session.
_MISSING_AUTH = re.compile(r"missing\s+required\s+authorization|invalid\s+jwt|jwt\s+expired", _I)

#: Nobody can serve this model right now, said by the router/worker pool itself.
#: Rows: doc-zenmux-502-no-provider-available, doc-tokenrouter-503-no-available-channel,
#: doc-ai-horde-400-no-valid-workers, doc-ai-horde-410-request-expired.
_NO_SERVER_FOR_MODEL = re.compile(
    r"no[\s_-]*provider[\s_-]*available|no[\s_-]*available[\s_-]*channel"
    r"|no[\s_-]*active[\s_-]*worker|NoValidWorkers|RequestExpired|ModelNotReadyException"
    r"|not\s+ready\s+to\s+serve\s+inference", _I)

#: AWS error names, which botocore puts in the message ("An error occurred
#: (ThrottlingException) when calling ...") whatever the HTTP status says: Bedrock
#: files throttling under 400 and 429 alike. Rows: doc-bedrock-*.
_AWS_THROTTLE = re.compile(r"ThrottlingException|TooManyRequestsException|ServiceQuotaExceededException", _I)
#: A throttle that names a day (Bedrock "Too many tokens per day"): not a per-minute one.
_PER_DAY = re.compile(r"per\s+day|daily", _I)
_AWS_BAD_KEY = re.compile(
    r"UnrecognizedClientException|InvalidClientTokenId|ExpiredTokenException|SignatureDoesNotMatch"
    r"|security\s+token\s+included\s+in\s+the\s+request\s+is\s+invalid", _I)
_AWS_DENIED = re.compile(r"AccessDeniedException|NotAuthorized|use\s+case\s+details\s+have\s+not\s+been\s+submitted", _I)
_AWS_MODEL_FAULT = re.compile(r"ModelErrorException|ModelStreamErrorException|ModelTimeoutException|ServiceUnavailableException|InternalServerException", _I)

#: More money, in words or structured codes. Rows: doc-xai-403-credits-exhausted,
#: doc-upstage-401-api-key-blocked, doc-upstage-403-key-not-allowed,
#: doc-upstage-403-studio-quota, doc-upstage-429-usage-limit-exceeded,
#: doc-venice-401-inactive-pro-key, doc-commandcode-na-usage-exceeded.
_MONEY_WORDS = re.compile(
    r"used\s+all\s+available\s+credits|monthly\s+spending\s+limit"
    r"|api[\s_-]*key[\s_-]*is[\s_-]*(blocked|not[\s_-]*allowed)"
    r"|studio[\s_-]*subscription[\s_-]*quota[\s_-]*exceeded|usage[\s_-]*limit[\s_-]*exceeded"
    r"|subscription\s+is\s+inactive", _I)
# NOT taught: "You have reached your <window> usage limit". Ollama Cloud says it for
# a rolling session throttle (rest, rotate); CommandCode says it for a spent
# allowance. Same words, opposite meaning: only the sender's name tells them apart.

#: Batch D (Baidu, Volcano Ark, NAVER, Alibaba): structured codes / words that are
#: unambiguous on their own. Rows: doc-baidu-403-account-overdue,
#: doc-volcano-ark-403-account-overdue, doc-baidu-403-coding-plan-subscription-problem,
#: doc-volcano-ark-429-free-trial-quota-exceeded.
_MONEY_WORDS_2 = re.compile(
    r"account[\s_-]*overdue|overdue\s+(balance|account)|coding[\s_-]*plan[\s_-]*subscription"
    r"|exhausted\s+its\s+free\s+trial\s+quota|token[\s_-]*quota[\s_-]*reached", _I)
#: The REQUEST was refused (safety filter, model switched off). Rows:
#: doc-baidu-403-system-unsafe-content, doc-baidu-403-model-offline.
_REQUEST_REFUSED = re.compile(r"system[\s_-]*unsafe|unsafe[\s_-]*request|model[\s_-]*offline|content\s+of\s+\S+\s+field\s+is\s+invalid", _I)
#: This key/account may not use this model or endpoint. Rows: doc-baidu-401-invalid-appid,
#: doc-baidu-401-invalid-model-or-no-access, doc-volcano-ark-400-closed-endpoint,
#: doc-volcano-ark-404-model-not-open, doc-naver-clova-401-no-subscription-history.
_ENTITLEMENT = re.compile(
    r"invalid[\s_-]*appid|no\s+permission\s+to\s+use|ClosedEndpoint|ModelNotOpen"
    r"|no\s+subscription\s+request\s+history", _I)
# NOT taught: "The model X does not exist or you do not have access to it" is OpenAI's
# own model_not_found sentence (a typo in the model id): a request fault, and it must
# keep ending the turn (review B1, 2026-10-16).
#: A 429 that is the model loading or the service saturated: not a counter.
#: Rows: doc-volcano-ark-429-model-loading, doc-baidu-429-coding-plan-cluster-high-demand,
#: doc-tencent-tokenhub-429-upstream-rate-limit-429006.
_SATURATED = re.compile(
    r"ModelLoadingError|currently\s+being\s+loaded|experiencing\s+high\s+demand|busy\s+or\s+has\s+reached\s+its\s+capacity", _I)

#: A local server (Ollama, LM Studio, llama.cpp, vLLM) that has no model ready:
#: nothing loaded, not enough memory to load it. Rows: doc-lmstudio-*, doc-local-500-ollama-*,
#: doc-local-503-llamacpp-loading-model.
_LOCAL_NOT_READY = re.compile(
    r"no\s+models?\s+loaded|insufficient\s+(system\s+)?memory|failed\s+to\s+load\s+(the\s+)?model"
    r"|requires\s+more\s+system\s+memory|model\s+failed\s+to\s+load", _I)
#: The prompt does not fit the context window the server was started with:
#: the REQUEST is too big, whatever status the server dressed it in.
#: Rows: doc-local-500-llamacpp-context-exceeded-mid-decode, doc-lmstudio-nostatus-context-size-exceeded.
_CONTEXT_OVERFLOW = re.compile(
    r"context\s+size\s+has\s+been\s+exceeded|exceeds?\s+the\s+(available\s+)?context\s+(size|length|window)", _I)

_CLOSED_DOOR_503 = re.compile(
    r"""["']code["']\s*:\s*["']maintenance["']|no[\s_-]*instances[\s_-]*available|no[\s_-]*available[\s_-]*endpoints|temporarily\s+offline|loading\s+model|model\s+is\s+(still\s+)?loading""",
    _I,
)


def _text(message: Any, body: Any) -> str:
    try:
        body_text = body if isinstance(body, str) else json.dumps(body, default=str)
    except Exception:
        body_text = str(body)
    return f"{message or ''} {body_text or ''}"


# Agent Zero port: the Hermes carousel's tables these two readers trust, copied
# (Hermes `core/carousel.py`). The Hermes module imports them from `.carousel`;
# there is no such module here, and an import that fails inside `reinterpret`
# would silently turn every 403 rule off.
INVALID_KEY_INDICATORS = (
    "api key not valid", "api key expired", "api_key_invalid", "api key not found",
    "invalid api key", "invalid_api_key", "please renew the api key",
    "invalid authentication", "incorrect api key", "key is invalid", "key is no longer valid",
)
CREDENTIAL_PROBLEM_INDICATORS = (
    "invalid token", "token is invalid or expired", "access token is invalid or expired",
)
REQUEST_CONTENT_BLOCK_INDICATORS = (
    "content_policy_violation", "content filter", "content_filter", "safety filter",
)
CONTENT_POLICY_INDICATORS = (
    "content_policy", "content policy", "content filter", "safety", "blocked by",
)


def _matches(text: str, indicators) -> bool:
    return any(indicator in text for indicator in indicators)


def names_bad_key(message: Any, body: Any) -> bool:
    """Agent Zero port: the words say the KEY is bad (for a status the engine
    would otherwise read as a refused pairing, e.g. a 403)."""
    return _names_a_credential_problem(_text(message, body))


def _names_a_credential_problem(text: str) -> bool:
    """The refusal itself says the KEY is bad (the words the carousel already trusts)."""

    lowered = text.lower()
    return bool(
        _matches(lowered, INVALID_KEY_INDICATORS)
        or _matches(lowered, CREDENTIAL_PROBLEM_INDICATORS)
        or any(word in lowered for word in _BAD_KEY_WORDS)
    )


def _names_a_request_block(text: str) -> bool:
    """The refusal says the REQUEST was blocked (safety / content filter): the carousel
    already ends the turn on these, so no quirk may turn one into a key problem."""

    lowered = text.lower()
    return bool(_matches(lowered, REQUEST_CONTENT_BLOCK_INDICATORS) or _matches(lowered, CONTENT_POLICY_INDICATORS))


def reinterpret(status: Optional[int], kind: str, message: Any, body: Any) -> Optional[str]:
    """A better kind for this failure (``denied``, ``billing``, ``server``,
    ``per_minute``), or ``None`` to leave it alone."""
    if kind == "revoked":
        return None  # the provider used the words "invalid api key": nothing outranks that
    text = _text(message, body)
    lowered = text.lower()
    if _AWS_BAD_KEY.search(text):
        return "auth"
    if _AWS_THROTTLE.search(text) and kind not in ("daily", "insufficient_quota") and not _PER_DAY.search(text):
        return "per_minute"
    if _AWS_DENIED.search(text):
        return "denied"
    if _AWS_MODEL_FAULT.search(text) and (status is None or status >= 400):
        return "server"
    if status in (None, 401, 402, 403, 412, 429) and (_MONEY_WORDS.search(text) or _MONEY_WORDS_2.search(text)):
        return "billing"
    if status in (400, 401, 403) and _REQUEST_REFUSED.search(text):
        return "request_fault"
    if status in (None, 400, 413, 422, 500) and _CONTEXT_OVERFLOW.search(text):
        return "request_fault"
    if status in (400, 401, 403, 404) and _ENTITLEMENT.search(text):
        return "denied"
    if status == 429 and _SATURATED.search(text):
        return "server"
    if status in (402, 403, 412, 429) and _CREDIT_WORDS.search(text):
        return "billing"
    if status in (400, 403) and _CONTENT_REFUSED.search(text):
        return "request_fault"
    if status == 429 and _MODEL_NOT_FREE.search(text):
        return "denied"
    if status in (400, 401, None) and kind in ("other", "auth") and _MISSING_AUTH.search(text):
        return "auth"
    if status in (402, 403, 412) and (
        "customer_verification_required" in lowered
        or _NOT_ENOUGH_BALANCE.search(text)
        or (status in (403, 412) and _SUSPENDED_FOR_SPEND.search(text))
    ):
        # Money, said in words or in a structured type; waiting does not fix a
        # card or a balance.
        return "billing"
    if status == 429 and kind == "insufficient_quota" and _PER_MINUTE_QUOTA.search(text):
        return "per_minute"
    if status is not None and 500 <= status < 600 and _REQUEST_FAULT_CODE.search(text):
        return "request_fault"
    if status in (401, 403) and kind in ("auth", "server", "other") and _PLAN_REQUIRED.search(text):
        return "denied"
    if (status == 403 and kind in ("auth", "server") and not _names_a_credential_problem(text)
            and not _names_a_request_block(text)):
        # HTTP's own meaning (RFC 9110): 403 is "understood, and refused for
        # this caller", 401 is "credentials missing or wrong". A 403 that does
        # not say the key is bad is a refused model / plan / allowlist / org
        # policy, and three of those used to retire a healthy key (1.4.0).
        # Rows: doc-mistral-403-model-tier, doc-cloudflare-403-*,
        # doc-hetzner-403-*, doc-siliconflow-403-*, doc-vercel-403-*,
        # doc-kilocode-403-*, doc-pollinations-403-*, doc-cerebras-403-*,
        # doc-nvidia-403-*.
        return "denied"
    if status == 404 and _NOT_FOUND_FOR_ACCOUNT.search(text):
        return "denied"
    if status == 429 and kind == "per_minute" and _INVALID_FREE_MODEL.search(text):
        return "denied"
    if status == 429 and kind == "per_minute" and _CAPACITY.search(text):
        return "server"
    return None


def names_maintenance(status: Optional[int], message: Any, body: Any) -> bool:
    """A 503 that says the model has no instance / endpoint, or is down for
    maintenance by its structured code: every key of the pool meets the same
    closed door. Rows: doc-sambanova-503-maintenance, doc-chutes-503-no-instances,
    doc-nebius-503-no-available-endpoints."""
    text = _text(message, body)
    if status in (None, 404, 500, 503) and _LOCAL_NOT_READY.search(text):
        return True
    if status == 503 and _CLOSED_DOOR_503.search(text):
        return True
    return status in (400, 410, 502, 503) and bool(_NO_SERVER_FOR_MODEL.search(text))
