"""GET /api/plugins/api_rotation_by_kame/status — live key-pool health snapshot.

Returns a JSON object with the current state of every key in every KAME pool:
  {
    "pools": {
      "google:gemini/gemini-3.5-flash": [
        { "fingerprint": "k3f9a1", "healthy": true, "seconds_remaining": 0, "eta": null, "recent_requests": 3, "consecutive_rl": 0 },
        { "fingerprint": "k7d2b8", "healthy": false, "seconds_remaining": 2340, "eta": "11:30:00", "recent_requests": 0, "consecutive_rl": 2 },
        ...
      ],
      ...
    }
  }

No auth required (read-only, contains only anonymized key fingerprints — never the real key).
v1.0.5 addition.
"""
from __future__ import annotations

from helpers.api import ApiHandler, Request, Response


class KameStatus(ApiHandler):

    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    async def process(self, input: dict, request: Request) -> dict | Response:
        try:
            from usr.plugins.api_rotation_by_kame.kame_engine import get_pool_status
            pools = get_pool_status()
        except Exception as e:
            pools = {}
            return {"ok": False, "error": str(e), "pools": pools}
        return {"ok": True, "pools": pools}
