"""KAME 1.8.2.8: a new turn of the top agent starts the clock behind the reply
line ("which model answered, and how long it took"). See
``extensions/python/monologue_end/_90_kame_reply_line.py``."""

from helpers.extension import Extension


class KameTurnClock(Extension):
    def execute(self, **kwargs):
        try:
            from usr.plugins.api_rotation_by_kame import kame_engine

            kame_engine.turn_started(getattr(self, "agent", None))
        except Exception:
            pass
