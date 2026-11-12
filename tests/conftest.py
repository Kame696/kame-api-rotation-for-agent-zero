"""pytest defaults for the Agent Zero suites.

1.8.4.2 (S1): remembered trouble (``kame_model_health``) is switched off for every suite by default, so a
failure one test provokes cannot make a model "risky" for the next one. The 1.8.4.2 suites switch it back on
for themselves (``monkeypatch.delenv``). ``run_all.py`` sets the same variable for the script-style suites.
"""
import os

os.environ.setdefault("KAME_NO_REMEMBERED_TROUBLE", "1")
