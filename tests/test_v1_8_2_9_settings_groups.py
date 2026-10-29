"""1.8.2.9 (U4, C9) - the settings are on the same shelves as in the Hermes version.

Names, order and notes of the first five shelves are the Hermes ones word for word (copied here from its
`GROUPS`, with the host's name changed where the note names it); the Agent Zero-only settings are one more
shelf after "Rest times". Every setting is on exactly one shelf. No Agent Zero, no network.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from test_v1_8_2_7_speed_fallback import clean  # noqa: F401  (fixture used by name; also stubs Agent Zero)
import kame_api_stub as stub
import kame_engine as K
import kame_settings as S

ROOT = Path(__file__).resolve().parent.parent
STATUS = stub.load_api("kame_status", K, S)

HERMES_SHELVES = [
    ("extra", "If your model cannot answer",
     "When your provider is busy, every key of your model is resting, or a reply takes too long."),
    ("speed", "Speed",
     "How KAME keeps answers fast. Already tuned; the first two are the ones worth knowing."),
    ("keyring", "Your API keys",
     "One key per account (for Gemini, one per Google project): every key brings its own quota, "
     "and KAME rotates between them."),
    ("tuning", "Rest times (advanced)",
     "How long a refused key sits out. The provider's own numbers always win; these are the fallbacks and ceilings."),
]
OFF = ("off", "Troubleshooting: turn parts of KAME off",
       "Each switch turns one part of KAME off and gives that job back to Agent Zero. For finding out whether "
       "KAME is behind a problem. Leave them alone unless something is wrong.")


@pytest.fixture(autouse=True)
def fresh():
    stub.reset()
    yield
    stub.reset()


def test_the_first_four_shelves_are_hermes_shelves_word_for_word():
    assert [(g, t, n) for g, t, n, _k in S.GROUPS[:4]] == HERMES_SHELVES


def test_the_agent_zero_shelf_follows_rest_times_and_troubleshooting_closes_the_list():
    assert [g for g, *_ in S.GROUPS] == ["extra", "speed", "keyring", "tuning", "agentzero", "off"]
    assert S.GROUPS[4][1] == "Agent Zero log and chat"
    assert S.GROUPS[5][:3] == OFF


def test_hermes_keys_sit_where_hermes_puts_them():
    by_group = {g: list(keys) for g, _t, _n, keys in S.GROUPS}
    assert by_group["extra"] == ["model_switch", "failover_after_seconds", "reply_notice", "max_total_wait_seconds"]
    assert by_group["speed"][:3] == ["hedge_mode", "first_token_wait", "stream_silence_timeout_seconds"]
    assert by_group["keyring"] == ["share_pool_health", "pacing_rpm_overrides", "key_region_overrides"]
    assert by_group["tuning"][0] == "daily_quota_cooldown_seconds"
    assert set(by_group["off"]) == set(S.TROUBLESHOOTING)


def test_every_setting_is_on_exactly_one_shelf_and_the_list_has_its_own_tab():
    seen = {}
    for g, _t, _n, keys in S.GROUPS:
        for key in keys:
            assert key not in seen, f"{key} is on two shelves"
            assert key in S.SETTINGS, f"{key} is not a setting"
            seen[key] = g
    assert set(S.SETTINGS) - set(seen) == {"fallback_models"}
    assert S.group_of("fallback_models") == "fallback"
    assert S.group_of("hedge_mode") == "speed" and S.group_of("rotation_disabled") == "off"
    assert S.group_of("a_setting_added_later") == "tuning"          # a wrong shelf beats a missing setting


def test_the_basic_settings_are_the_first_two_of_each_shelf():
    expected = tuple(key for g, _t, _n, keys in S.GROUPS if g != "off" for key in keys[:2])
    assert S.VISIBLE == expected
    assert "rotation_disabled" not in S.VISIBLE and "fallback_models" not in S.VISIBLE


def test_the_panel_report_carries_the_shelf_of_every_setting_and_the_shelves():
    out = stub.call(STATUS.KameStatus, {"panel": True})
    meta = {m["name"]: m for m in out["settings_meta"]}
    assert meta["hedge_mode"]["group"] == "speed" and meta["hedge_mode"]["advanced"] is False
    assert meta["hedge_burst"]["advanced"] is True
    assert meta["rotation_disabled"]["group"] == "off" and meta["rotation_disabled"]["advanced"] is False
    assert meta["fallback_models"]["group"] == "fallback" and meta["fallback_models"]["advanced"] is False
    shelves = out["setting_groups"]
    assert [g["id"] for g in shelves] == ["extra", "speed", "keyring", "tuning", "agentzero", "off"]
    assert [g["folded"] for g in shelves] == [False] * 5 + [True]
    assert shelves[0]["shows_fallback"] is True and shelves[2]["shows_keys"] is True
    assert shelves[1]["keys"][0] == "hedge_mode"


def test_the_plain_status_call_does_not_carry_the_settings_table():
    out = stub.call(STATUS.KameStatus, {})
    assert "settings_meta" not in out and "setting_groups" not in out


def test_speed_words_carry_what_each_choice_costs():
    meta = {m["name"]: m for m in stub.call(STATUS.KameStatus, {"panel": True})["settings_meta"]}
    speed = meta["hedge_mode"]
    assert [speed["labels"][c] for c in speed["choice_order"]] == ["Saver · 1x", "Balanced · up to 1.1x", "Faster · up to 1.3x"]
    assert speed["choice_help"]["off"].startswith("One request per reply")
    assert "what was really spent" in speed["choice_footnote"]


def test_the_native_settings_screen_has_the_same_shelves_in_the_same_order():
    html = (ROOT / "webui" / "config.html").read_text(encoding="utf-8")
    at = [html.index(f">{title}<") for _g, title, _n, _k in S.GROUPS]
    assert at == sorted(at), "the shelves must appear in the order of kame_settings.GROUPS"
    for _g, title, note, _k in S.GROUPS:
        assert note in html
    assert "KAME - API key rotation" not in html and "<b>API Rotation</b>" in html
    # the basic settings are on show; the others wait behind "Show advanced settings"
    assert 'x-show="advanced"' in html and "Show advanced settings" in html
    for name in S.VISIBLE:
        block = html.split(f'x-model="config.{name}"', 1)[0].rsplit('<div class="field"', 1)[-1]
        assert 'x-show="advanced"' not in block.split(">", 1)[0], f"{name} must be on show"
