"""1.8.1.4 - A0 port: the plaintext .env backup is owner-only from birth.

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
