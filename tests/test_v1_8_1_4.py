"""1.8.1.4 - A0 port: a busy server is a server, and its stated wait is obeyed;
the plaintext .env backup is owner-only from birth.

The first half was found by replaying every refusal the Hermes suite hands its
classifier through both real engines: a 429 saying "overloaded" rested a healthy
key 30s as a throttle here and 1s as a busy server on Hermes, and a 5xx with
`Retry-After: 7` rested 1s here and 7s there. Hermes' rules, ported: busy prose
is a server unless a structured type says rate_limit (the Kimi case), and on a
server failure only a retry instruction counts (R26), never quota-reset
telemetry.

`/kame-keys add|import` copies usr/.env aside before changing it. The copy was
created with `open("xb")` (the umask's 0644) and only then given the .env's own
mode by copystat, so for that moment every key sat in a world-readable file.

    python -m pytest tests/test_v1_8_1_4.py
"""
import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _keys():
    spec = importlib.util.spec_from_file_location("kame_keys_under_test_1814", ROOT / "kame_keys.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_env_backup_is_owner_only_from_the_moment_it_exists(tmp_path, monkeypatch):
    keys = _keys()
    env = tmp_path / ".env"
    env.write_text("API_KEY_OPENAI=sk-one,sk-two\n")
    env.chmod(0o600)
    monkeypatch.setattr(keys.shutil, "copystat", lambda *a, **k: None)
    old = os.umask(0o022)
    try:
        name = keys.backup(env)
    finally:
        os.umask(old)
    assert name
    backup = tmp_path / name
    assert backup.read_bytes() == env.read_bytes()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_a_second_backup_in_the_same_instant_gets_its_own_name(tmp_path):
    keys = _keys()
    env = tmp_path / ".env"
    env.write_text("API_KEY_OPENAI=sk-one\n")
    first, second = keys.backup(env), keys.backup(env)
    assert first and second and first != second


# -- busy servers (engine) ----------------------------------------------------
if not any(name in sys.modules for name in ("test_v1_8_1_1_reliability", "tests.test_v1_8_1_1_reliability")):
    import test_v1_8_1_1_reliability  # noqa: F401,E402  (installs the Agent Zero stubs)
import kame_engine as engine  # noqa: E402


class _Refusal(Exception):
    def __init__(self, message, status, body=None, headers=None):
        super().__init__(message)
        self.status_code = status
        if body is not None:
            self.body = body
        if headers is not None:
            self.headers = headers


def test_a_429_that_says_overloaded_is_a_busy_server():
    delay, kind, status = engine._classify_error(
        _Refusal("overloaded", 429, {"error": {"code": 429, "message": "overloaded"}}))
    assert (kind, status) == ("server", 429)
    assert delay == engine._KAME_SERVER_BASE_S


def test_a_structured_rate_limit_type_still_wins_over_overloaded_prose():
    # Kimi: HTTP 429, type rate_limit_error, "The engine is currently overloaded".
    exc = _Refusal("The engine is currently overloaded, please try again later.", 429,
                   {"error": {"type": "rate_limit_error",
                              "message": "The engine is currently overloaded, please try again later."}})
    _delay, kind, _status = engine._classify_error(exc)
    assert kind == "per_minute"


@pytest.mark.parametrize("status", [500, 502, 503, 504, 529])
def test_a_server_failure_that_names_its_wait_is_obeyed(status):
    exc = _Refusal("Service temporarily unavailable", status, headers={"retry-after": "7"})
    assert engine._classify_error(exc)[:2] == (7.0, "server")
    assert engine._delay_source(exc, "server") == "provider"


def test_retry_after_ms_is_read_too():
    exc = _Refusal("Overloaded", 529, headers={"retry-after-ms": "2500"})
    assert engine._classify_error(exc)[:2] == (2.5, "server")


@pytest.mark.parametrize("status", [500, 503, 529])
def test_quota_reset_telemetry_is_not_a_server_retry_instruction(status):
    exc = _Refusal("Service temporarily unavailable", status,
                   headers={"x-ratelimit-reset-requests": "180s", "x-ratelimit-remaining-requests": "999"})
    assert engine._classify_error(exc)[:2] == (engine._KAME_SERVER_BASE_S, "server")
    assert engine._delay_source(exc, "server") == "kame"


# -- Gemini's bare RESOURCE_EXHAUSTED, whatever shape the body arrives in -------
_BARE_BODY = {"error": {"code": 429, "message": "Resource has been exhausted (e.g. check quota).",
                        "status": "RESOURCE_EXHAUSTED"}}


def _first_rest(exc, key):
    ident = "gemini:gemini-3.8-flash"
    engine._get_identity_state(ident, [key])
    applied, kind, _sc, label, _src = engine._kame_decide_failure(ident, key, exc)
    return applied, kind, label


def test_a_bare_refusal_with_a_parsed_body_starts_the_ladder_at_one_second():
    # An SDK that hands the payload over as a dict: `_evidence_text` renders it
    # with repr(), single quotes. The detector only knew JSON's double quotes.
    exc = _Refusal("Resource has been exhausted (e.g. check quota).", 429, _BARE_BODY)
    assert engine._is_bare_resource_exhausted(exc) is True
    assert _first_rest(exc, "AIzaSyA-bare-dict-000000000000000000") == (1.0, "per_minute", "backoff.1")


def test_a_bare_refusal_quoted_as_json_text_still_starts_at_one_second():
    # litellm's native Gemini path puts the raw JSON in the message.
    import json as _json
    exc = _Refusal("litellm.RateLimitError: VertexAIException - " + _json.dumps(_BARE_BODY, indent=2), 429)
    assert engine._is_bare_resource_exhausted(exc) is True
    assert _first_rest(exc, "AIzaSyA-bare-json-000000000000000000") == (1.0, "per_minute", "backoff.1")


def test_a_refusal_that_names_a_quota_id_is_not_bare_in_either_shape():
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded",
                      "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                   "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}}
    assert engine._is_bare_resource_exhausted(_Refusal("Quota exceeded", 429, body)) is False


# -- a moderation block is the request's fault ---------------------------------
@pytest.mark.parametrize("message,status", [
    ("response blocked by safety filter", None),
    ("The prompt was blocked by the safety filter", 403),
    ("The response was blocked by the content filter", 400),
    ("content_policy_violation", None),
])
def test_a_moderation_block_of_the_request_is_handed_back(message, status):
    assert engine._is_terminal_error(_Refusal(message, status)) is True


@pytest.mark.parametrize("message,status", [
    ("API key blocked by admin", 403),
    ("Your access is blocked by your organization's policy", 403),
    ("Your API key was suspended for violating our content policy", 403),
    ("Organization safety settings prevent this key from calling the model", 403),
    ("Request blocked by upstream proxy", None),
])
def test_a_bare_key_denial_in_moderation_words_still_rotates(message, status):
    # No structured code: only the words. These are about the KEY, and the
    # next key may answer -- never a reason to end the turn.
    assert engine._is_terminal_error(_Refusal(message, status)) is False


def test_a_key_denial_saying_blocked_by_is_still_a_key_problem():
    # Settled by the auth rules before the content list is ever read.
    exc = _Refusal("API key blocked by admin", 403, {"error": {"code": "permission_denied"}})
    assert engine._is_terminal_error(exc) is False


def test_a_throttle_mentioning_safety_is_still_a_throttle():
    exc = _Refusal("Rate limit exceeded for safety tier", 429)
    assert engine._is_terminal_error(exc) is False


# -- "429" is a status only when it stands alone --------------------------------
@pytest.mark.parametrize("message,body", [
    ("This model's maximum context length is 32768 tokens. However, you requested 34290 tokens.",
     {"error": {"message": "...34290 tokens.", "type": "invalid_request_error"}}),
    ("prompt is too long: 214290 tokens > 200000 maximum",
     {"type": "error", "error": {"type": "invalid_request_error", "message": "prompt is too long: 214290 tokens"}}),
    ("The input token count (1429000) exceeds the maximum number of tokens allowed (1048576).",
     {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "The input token count (1429000) exceeds"}}),
], ids=["openai-compat", "anthropic", "gemini"])
def test_a_context_length_400_with_429_in_a_token_count_is_terminal(message, body):
    assert engine._is_terminal_error(_Refusal(message, 400, body)) is True


@pytest.mark.parametrize("text", ["error code: 429 - too many requests", "http 429", "(429)", "429"])
def test_a_429_written_as_a_status_is_still_a_throttle(text):
    assert engine._names_a_throttle(text) is True


@pytest.mark.parametrize("text", ["142935 tokens", "34290", "token count (1429000)", "version 4.29"])
def test_429_inside_another_number_is_not(text):
    assert engine._names_a_throttle(text) is False


# -- a plan without the service, whatever the sentence --------------------------
@pytest.mark.parametrize("body", [
    {"type": "usage_not_included", "message": "Upgrade to Plus."},              # SDK: inner error
    {"error": {"type": "usage_not_included", "message": "Upgrade to Plus."}},   # parsed from text
], ids=["inner", "nested"])
def test_usage_not_included_is_billing_by_its_field(body):
    exc = _Refusal("To use Codex with your plan, upgrade to Plus.", 429, body)
    delay, kind, _status = engine._classify_error(exc)
    assert kind == "insufficient_quota"
    assert engine._no_clock_fixes(exc) is True


def test_a_per_minute_quota_limit_in_parsed_details_is_not_bare():
    exc = _Refusal("Gemini HTTP 429 (RESOURCE_EXHAUSTED): You exceeded your current quota.", 429)
    exc.details = {"reason": "", "status": "RESOURCE_EXHAUSTED",
                   "metadata": {"quota_limit": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                                "quota_limit_value": "15"}}
    assert engine._is_bare_resource_exhausted(exc) is False


# -- redaction: query parameters and x-api-key fields ---------------------------
import kame_journal as _journal  # noqa: E402


@pytest.mark.parametrize("url", [
    "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent?key={k}",
    "https://api.example.com/v1/chat?model=m&api_key={k}&stream=true",
])
def test_a_query_parameter_credential_is_redacted_whatever_its_shape(url):
    key = "NoDigitsNoPrefixJustLettersHereOk"
    out = _journal.redact(f"POST {url.format(k=key)} returned 429", limit=0)
    assert key not in out


@pytest.mark.parametrize("field", ["x-api-key", "x-goog-api-key", "api_key"])
def test_an_api_key_field_in_json_text_is_redacted(field):
    key = "NoDigitsNoPrefixJustLettersHereOk"
    out = _journal.redact('{"error": {"%s": "%s"}}' % (field, key), limit=0)
    assert key not in out


# -- the debug dump is untruncated, not unredacted -------------------------------
def test_the_full_error_dump_keeps_the_message_but_not_the_key():
    key = "AIzaSyA-" + "x" * 31
    exc = _Refusal(f"litellm.APIConnectionError: POST https://generativelanguage.googleapis.com/v1beta/"
                   f"models/g:generateContent?key={key} failed after 3 retries", None)
    dump = engine._raw_error_detail(exc, "server", 1.0, None)
    assert key not in dump
    assert "failed after 3 retries" in dump


# -- under a request-fault status, only a throttle phrase rotates ---------------
@pytest.mark.parametrize("message", [
    "Invalid value 429 for parameter max_tokens",
    "Invalid request: could not parse: 'we have exceeded your current quota'",
])
def test_a_request_fault_that_mentions_429_or_quota_is_terminal(message):
    assert engine._is_terminal_error(_Refusal(message, 400)) is True


@pytest.mark.parametrize("message", [
    "Quota exceeded for quota metric 'Generate Content API requests per minute'",
    "upstream returned 429 Too Many Requests",
])
def test_a_throttle_phrase_on_a_400_still_rotates(message):
    assert engine._is_terminal_error(_Refusal(message, 400)) is False


# -- dotenv import: a BOM is not part of a name; an unclosed quote is not a value -
# (1.8.1.5; found by a differential test against python-dotenv over 22 shapes)
import kame_keys as _kk  # noqa: E402


def test_a_bom_at_the_start_of_the_file_does_not_hide_the_first_key():
    assert _kk.parse_env_text("\ufeffAPI_KEY_OPENAI=sk-one,sk-two\nX=1\n")["API_KEY_OPENAI"] == "sk-one,sk-two"


def test_an_unclosed_quote_imports_nothing_rather_than_a_glued_quote():
    assert _kk.parse_env_text('API_KEY_OPENAI="sk-one\n').get("API_KEY_OPENAI", "") == ""


def test_quoted_and_commented_values_still_read_as_dotenv_reads_them():
    env = _kk.parse_env_text('API_KEY_OPENAI="sk-a,sk-b" # mine\nAPI_KEY_GROQ=gsk_x # c\n')
    assert env["API_KEY_OPENAI"] == "sk-a,sk-b" and env["API_KEY_GROQ"] == "gsk_x"
