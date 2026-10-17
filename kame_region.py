# Agent Zero port of the Hermes 1.8.2.7 `core/region.py`, verbatim.
"""Region parsing for the pool identity (PC-01).

A regional provider meters a key per region — Vertex AI and Bedrock grant
and refuse quota per location, and a refusal in ``us-central1`` says nothing
about the same key in ``europe-west4``. The pool identity therefore carries
the region as a third segment, and every bench, pacing log and latency
sample is keyed by the full identity so one region's cooldown cannot leak
into another.

The region is read from the endpoint URL the call actually uses, with a
per-key override for endpoints that hide it (proxies, custom gateways).
"""

from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import unquote, urlsplit

__all__ = ["parse_region", "region_from_overrides", "identity_segment"]


#: A region token as it appears in a URL: ``global``, ``us-central1``,
#: ``europe-west4``, ``asia-northeast1``. Two letters plus one-or-more
#: ``-digit-or-word`` groups, or a bare ``us``/``eu``/``asia``.
_REGION_RE = re.compile(r"^(global|[a-z]{2,}(?:-[a-z0-9]+)+|us|eu|asia|jp|au|ca)$")

#: ``{region}-aiplatform.googleapis.com`` — Vertex regional endpoints.
_VERTEX_HOST = re.compile(r"^(?P<r>[a-z]+-[a-z]+\d+)-aiplatform\.googleapis\.com$")

#: ``bedrock-runtime.{region}.amazonaws.com`` (+ the -fips variant).
_BEDROCK_HOST = re.compile(
    r"^bedrock-runtime(?:-fips)?\.(?P<r>[a-z]{2}(?:-gov)?-[a-z]+-\d)\.amazonaws\.com$"
)

#: ``{resource}.openai.azure.com`` / ``{resource}.cognitiveservices.azure.com``.
#: Azure meters per resource, not per URL region — the resource *is* the
#: quota scope, so it becomes the identity segment.
_AZURE_HOST = re.compile(r"^(?P<res>[a-z0-9-]+)\.(?:openai|cognitiveservices)\.azure\.com$")

#: Bedrock cross-region inference profile prefixes — their own quota scope.
_XR_PREFIXES = frozenset({"us", "eu", "apac", "global", "jp", "au", "ca"})


def parse_region(
    base_url: Optional[str],
    model: str = "",
    override: Optional[str] = None,
) -> Tuple[str, str]:
    """``(region, source)`` for one endpoint.

    ``source`` is one of ``override``, ``path``, ``subdomain``, ``bedrock``,
    ``azure``, ``default``, ``invalid`` — the panel and the events log show
    it so a wrong region is debuggable from the outside rather than from a
    silent identity split.

    Resolution order:

    * an explicit override always wins (validated; invalid falls back to
      ``global`` with ``source="invalid"`` so the mistake is visible);
    * a Google endpoint: the ``locations/{region}`` path segment is
      authoritative (a proxy can rewrite the subdomain; the path is what
      the request actually addresses);
    * a Vertex regional subdomain when no path says otherwise;
    * Bedrock and Azure per their own host shapes;
    * everything else — including localhost, proxies and every flat REST
      provider — is ``global``, because a refusal there is not regional.
    """
    if override:
        r = str(override).strip().lower()
        if _REGION_RE.match(r) or r.startswith(("az-", "xr-")):
            return r, "override"
        return "global", "invalid"
    if not base_url:
        return "global", "default"
    try:
        u = urlsplit(str(base_url).strip())
        host = (u.hostname or "").lower().rstrip(".")
        segs = [unquote(s).lower() for s in u.path.split("/") if s]
    except ValueError:
        return "global", "invalid"

    if host.endswith("googleapis.com"):
        # ``projects/{p}/locations/{loc}/...`` — the path is authoritative.
        # Requiring the full ``projects/{p}/locations/{x}`' shape keeps the
        # word "locations" inside a model name from being read as a region.
        for i, s in enumerate(segs[:-1]):
            if s == "locations" and i >= 2 and segs[i - 2] == "projects":
                loc = segs[i + 1]
                if _REGION_RE.match(loc):
                    return loc, "path"
        m = _VERTEX_HOST.match(host)
        if m:
            return m["r"], "subdomain"
        return "global", "default"

    m = _BEDROCK_HOST.match(host)
    if m:
        prefix = str(model or "").split(".", 1)[0].lower() if "." in str(model or "") else ""
        if prefix in _XR_PREFIXES:
            return f"xr-{prefix}", "bedrock"
        return m["r"], "bedrock"

    m = _AZURE_HOST.match(host)
    if m:
        return f"az-{m['res']}", "azure"

    return "global", "default"


def _bare_label(label: str) -> str:
    """A key label without its ``key:`` prefix, lower case."""
    text = str(label or "").strip().lower()
    return text[4:] if text.startswith("key:") else text


def region_from_overrides(raw: str, key_label: str) -> Optional[str]:
    """The region named for one key in the overrides setting, if any.

    ``key_label`` is the short form the panel shows (``...a1b2``) — the
    overrides string is written against that, because a person typing a
    region for a key has the panel open, not the credential store.

    Entries are ``label=region`` pairs, comma separated. Unknown labels and
    malformed entries are ignored: one bad entry must not disable the rest.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    # 1.8.2.4: ``key:a1b2c3`` (as the Events tab shows it) and ``a1b2c3``
    # name the same key. The 1.8.2.3 help text taught a bare form the code
    # compared against the prefixed one, so a typed entry never matched.
    want = _bare_label(key_label)
    if not want:
        return None
    for entry in text.split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        label, _, value = entry.partition("=")
        if _bare_label(label) == want:
            value = value.strip()
            return value or None
    return None


def identity_segment(region: str, source: str) -> str:
    """The string appended to an identity for one region.

    ``global`` contributes nothing — the identity for a flat provider stays
    exactly the two-part ``provider:model`` every earlier version wrote,
    so state files, logs and panels do not churn for the providers that
    never had a region. Any other region appends ``:{region}``.
    """
    if not region or region == "global" or source == "invalid":
        return ""
    return region
