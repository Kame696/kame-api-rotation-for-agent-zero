"""1.8.4.0 E4: reserve keys are the last to be used.

``key_reserve`` is a comma list of the short labels the panel shows. A reserve key is a candidate
only when no other key of the model is healthy, and it is never asked as a backup or in a burst.
A label that matches nothing is ignored. All keys reserved behaves as today.
"""
from __future__ import annotations

import os
import time

import pytest

from test_v1_8_2_7_speed_fallback import ID, KEYS, clean  # noqa: F401  (fixture used by name)
import kame_engine as K
import kame_pacing as P
import kame_settings as S


def short(key):
    return K._key_short_id(key)


@pytest.fixture(autouse=True)
def calm(clean):
    K.set_key_reserve("")
    P.PACER.clear()
    yield
    K.set_key_reserve("")
    P.PACER.clear()


def picks(n, keys=KEYS):
    return [K._get_best_key(ID, keys)[0] for _ in range(n)]


def test_the_setting_is_declared_with_hermes_words():
    row = S.SETTINGS["key_reserve"]
    assert row[0] == "text" and row[1] == "" and row[5] == "KAME_KEY_RESERVE"
    assert row[6] == "Reserve keys"
    assert row[7] == ("Keys listed here are used only when every other key of that model cannot "
                      "answer, and are never used for a backup request. Put your paid keys here "
                      "to spend your free quota first.")
    assert S.WHEN["key_reserve"] == "Only if some of your keys are paid and the others are free."
    assert "key_reserve" in S.ADVANCED


def test_the_setting_is_read_but_never_shown():
    """Owner, 2026-11-08: hidden like Hermes (``settings.HIDDEN``). Still loaded and settable."""
    assert "key_reserve" in S.HIDDEN and "key_reserve" in S.SETTINGS
    assert all("key_reserve" not in keys for _g, _t, _n, keys in S.GROUPS)
    page = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webui", "config.html"),
                encoding="utf-8").read()
    assert "config.key_reserve" not in page and "Reserve keys" not in page


def test_never_picked_while_another_is_healthy():
    K._get_identity_state(ID, KEYS)
    K.set_key_reserve(short(KEYS[0]))
    assert KEYS[0] not in picks(12)


def test_picked_when_the_others_rest():
    K._get_identity_state(ID, KEYS)
    K.set_key_reserve(short(KEYS[0]))
    pool = K._KAME_KEY_HEALTH[ID]["keys"]
    for k in KEYS[1:]:
        pool[k]["sick_until"] = time.time() + 600
    assert K._get_best_key(ID, KEYS) == (KEYS[0], "SUCCESS")


def test_the_longer_spelling_and_the_digits_also_name_a_key():
    import hashlib
    digest = hashlib.sha256(KEYS[0].encode()).hexdigest()[:6]
    for spelling in ("key:" + digest, digest):
        K._get_identity_state(ID, KEYS)
        K.set_key_reserve(spelling)
        assert KEYS[0] not in picks(8)


def test_an_unknown_label_is_harmless():
    K._get_identity_state(ID, KEYS)
    K.set_key_reserve("key:ffffff, nonsense")
    assert set(picks(16)) == set(KEYS)


def test_all_keys_reserved_behaves_as_today():
    K._get_identity_state(ID, KEYS)
    K.set_key_reserve(", ".join(short(k) for k in KEYS))
    assert set(picks(16)) == set(KEYS)


def test_an_edit_applies_without_a_restart():
    K._get_identity_state(ID, KEYS)
    assert KEYS[0] in picks(10)
    K.set_key_reserve(short(KEYS[0]))
    assert KEYS[0] not in picks(10)
    K.set_key_reserve("")
    assert KEYS[0] in picks(10)


def test_a_reserve_key_serving_a_reply_is_counted_like_any_other():
    K._get_identity_state(ID, KEYS)
    K.set_key_reserve(short(KEYS[0]))
    pool = K._KAME_KEY_HEALTH[ID]["keys"]
    for k in KEYS[1:]:
        pool[k]["sick_until"] = time.time() + 600
    K._get_best_key(ID, KEYS)
    assert len(pool[KEYS[0]]["request_log"]) == 1


def test_never_a_backup_and_never_in_a_burst():
    K._get_identity_state(ID, KEYS)
    K.set_key_reserve(f"{short(KEYS[1])}, {short(KEYS[2])}")
    got = K._kame_pick_more(ID, KEYS, {KEYS[0]}, 8)
    assert KEYS[1] not in got and KEYS[2] not in got
    assert set(got) == set(KEYS[3:])


def test_backup_picks_leave_everything_when_all_are_reserved():
    K._get_identity_state(ID, KEYS)
    K.set_key_reserve(", ".join(short(k) for k in KEYS))
    assert set(K._kame_pick_more(ID, KEYS, {KEYS[0]}, 8)) == set(KEYS[1:])


def test_the_panel_rows_carry_the_word_reserve():
    K._get_identity_state(ID, KEYS)
    K.set_key_reserve(short(KEYS[0]))
    rows = {r["id"]: r for p in K.pool_report()["pools"] if p["identity"] == ID for r in p["keys"]}
    assert rows[short(KEYS[0])]["reserve"] is True
    assert rows[short(KEYS[1])]["reserve"] is False
