"""`POST /plugins/api_rotation_by_kame/kame_status` — what the rotation is doing.

v1.6.0.1. Agent Zero v2.11 added WebUI plugin slots beside the model/context
strip; this is the endpoint the chip in that slot reads.

**It returns counts and fingerprints. It never returns a key.** That is enforced
one layer down, in `kame_engine.pool_report`, which uses `_key_short_id`
directly rather than the configurable `_key_display` — a rendering setting meant
for a developer's console must not be able to put a secret on a web page.

Read-only by construction: there is no branch in this file that writes anything.
A screen that can change the pool it is describing is a screen that can break a
run by being looked at.
"""

from helpers.api import ApiHandler, Input, Output, Request


class KameStatus(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        # Imported inside `process`, not at module scope, for the same reason
        # every other KAME entry point does it: a half-copied plugin directory
        # must degrade to one dead panel, never to an Agent Zero that will not
        # start. An ImportError here is answered, not raised.
        try:
            from usr.plugins.api_rotation_by_kame.kame_engine import pool_report
        except Exception as exc:  # pragma: no cover - import shape is host-side
            return {
                "available": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "pools": [],
                "totals": {"keys": 0, "ready": 0, "resting": 0, "retired": 0},
            }

        report = pool_report()
        report["available"] = True
        return report
