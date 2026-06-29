"""POST /api/plugins/api_rotation_by_kame/reset — clear in-memory key cooldowns.

Body (optional JSON): { "identity": "google:gemini/gemini-3.5-flash" }
If identity is omitted, ALL pools are reset.

This is equivalent to what a container restart does for key health — everything
becomes immediately available. No disk state is written; a real restart still
resets everything from scratch. Useful when you know the daily quota has reset
but KAME is still holding stale cooldowns.

Returns: { "ok": true, "cleared": 7 }
v1.0.5 addition.
"""
from __future__ import annotations

from helpers.api import ApiHandler, Request, Response


class KameReset(ApiHandler):

    @classmethod
    def requires_auth(cls) -> bool:
        return True  # write action — require user session

    @classmethod
    def requires_csrf(cls) -> bool:
        return True

    async def process(self, input: dict, request: Request) -> dict | Response:
        identity = input.get("identity") or None
        try:
            from usr.plugins.api_rotation_by_kame.kame_engine import reset_pool_health
            cleared = reset_pool_health(identity)
        except Exception as e:
            return {"ok": False, "error": str(e), "cleared": 0}
        return {"ok": True, "cleared": cleared}
