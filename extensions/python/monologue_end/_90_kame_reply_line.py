"""KAME 1.8.2.8: when the top agent's turn ends, one line in Agent Zero's log -
the turn's time and the model that answered each step, and which model a
fallback answered for (setting ``reply_notice``). The log is what the person
sees; the model's history never carries it."""

from helpers.extension import Extension


class KameReplyLine(Extension):
    def execute(self, **kwargs):
        try:
            from usr.plugins.api_rotation_by_kame import kame_engine

            agent = getattr(self, "agent", None)
            line = kame_engine.reply_line(agent)
            if line:
                agent.context.log.log(type="info", heading=line, content="")
        except Exception:
            pass
