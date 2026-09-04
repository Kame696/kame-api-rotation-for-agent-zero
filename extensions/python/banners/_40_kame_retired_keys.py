"""A welcome-screen banner for the one KAME fact that outlives a chat.

v1.6.0.1. The rotation chip beside the composer answers *what is happening right
now*. This answers the different question — *is there something I have to do* —
and it is the only KAME state that is still true tomorrow morning: a credential
the provider refused is not coming back on its own.

Deliberately narrow:

* It fires ONLY for retired credentials. A resting key needs no human, and a
  banner that appears for ordinary throttling is a banner people learn to
  dismiss without reading, which costs the one that matters.
* Counts and fingerprints only, never a key — same rule as every other KAME
  surface.
* Best-effort throughout. A banner that raises would take the welcome screen
  down with it, and no notice is worth that.
"""

from helpers.extension import Extension


class KameRetiredKeys(Extension):
    async def execute(self, banners: list = [], frontend_context: dict = {}, **kwargs):
        try:
            from usr.plugins.api_rotation_by_kame.kame_engine import pool_report
        except Exception:
            return  # plugin not loaded, half-copied, or an older engine

        try:
            report = pool_report()
        except Exception:
            return

        retired = int((report.get("totals") or {}).get("retired") or 0)
        if retired <= 0:
            return

        # Name the pools, so the reader knows where to look, and the
        # fingerprints, so they know which row in their .env to replace.
        lines = []
        for pool in report.get("pools") or []:
            gone = [
                row.get("id", "")
                for row in (pool.get("keys") or [])
                if row.get("state") == "retired"
            ]
            if gone:
                lines.append(
                    f"<li><code>{pool.get('identity', '')}</code> — "
                    f"{', '.join(gone)}</li>"
                )

        banners.append({
            "id": "kame-retired-keys",
            "type": "warning",
            "priority": 60,
            "title": (
                f"{retired} API key{'s' if retired != 1 else ''} left rotation"
            ),
            "html": (
                "<p>The provider refused "
                + ("these credentials" if retired != 1 else "this credential")
                + ". KAME has stopped offering "
                + ("them" if retired != 1 else "it")
                + " so every turn does not spend a request rediscovering the "
                "same answer.</p>"
                "<ul>" + "".join(lines) + "</ul>"
                "<p><strong>Nothing was deleted.</strong> KAME never writes "
                "credentials. Paste a working key over the matching one in your "
                "<code>.env</code> and it returns by itself on the next "
                "successful call — no restart needed.</p>"
            ),
            "dismissible": True,
            "source": "backend",
        })
