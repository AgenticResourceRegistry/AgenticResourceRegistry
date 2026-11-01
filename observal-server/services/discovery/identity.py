# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ARD identity: identifiers, media types and the compatibility inputs we normalise.

The decisions here are recorded in ``docs/adr/0001-agentic-resource-discovery.md``
(Decisions 3 and 7). Identifiers are ``urn:air:<publisher>:<kind>:<uuid>`` so
they survive renames and ownership transfers; media types are emitted in one
canonical form and older spellings are accepted on input.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from models.discovery_entry import DiscoveryKind

# ── Media types ──────────────────────────────────────────────────────────

MEDIA_TYPE_SKILL = "application/ai-skill+md"
MEDIA_TYPE_MCP = "application/mcp-server-card+json"
MEDIA_TYPE_PROMPT = "application/vnd.observal.prompt+json"
MEDIA_TYPE_SANDBOX = "application/vnd.observal.sandbox+json"
MEDIA_TYPE_HOOK = "application/vnd.observal.hook+json"
MEDIA_TYPE_AGENT = "application/vnd.observal.agent+json"
MEDIA_TYPE_REGISTRY = "application/ai-registry+json"
# A remote agent reachable over the Agent2Agent protocol; the artifact is its Agent Card.
MEDIA_TYPE_A2A = "application/a2a-agent-card+json"

KIND_MEDIA_TYPES: dict[DiscoveryKind, str] = {
    DiscoveryKind.skill: MEDIA_TYPE_SKILL,
    DiscoveryKind.mcp: MEDIA_TYPE_MCP,
    DiscoveryKind.prompt: MEDIA_TYPE_PROMPT,
    DiscoveryKind.sandbox: MEDIA_TYPE_SANDBOX,
    DiscoveryKind.hook: MEDIA_TYPE_HOOK,
    DiscoveryKind.agent: MEDIA_TYPE_AGENT,
}

MEDIA_TYPE_KINDS: dict[str, DiscoveryKind] = {media: kind for kind, media in KIND_MEDIA_TYPES.items()}

# Older or looser spellings seen in the ecosystem, mapped to the canonical form.
# The original value is always preserved by callers in ``raw_entry``.
_MEDIA_TYPE_ALIASES: dict[str, str] = {
    "application/mcp-server": MEDIA_TYPE_MCP,
    "application/mcp-server+json": MEDIA_TYPE_MCP,
    "application/mcp-server-card": MEDIA_TYPE_MCP,
    "application/ai-skill": MEDIA_TYPE_SKILL,
    "application/ai-skill+markdown": MEDIA_TYPE_SKILL,
    "application/a2a-agent-card": MEDIA_TYPE_A2A,
    "application/agent-card+json": MEDIA_TYPE_A2A,
}


def normalize_media_type(value: str | None) -> str | None:
    """Return the canonical media type for a possibly legacy spelling.

    Unknown types are returned lower-cased with parameters stripped, never
    rejected: an external resource with a type Observal cannot activate must
    still be discoverable.
    """
    if not value:
        return None
    base = value.split(";", 1)[0].strip().lower()
    return _MEDIA_TYPE_ALIASES.get(base, base)


# ── Identifiers ──────────────────────────────────────────────────────────

URN_PREFIX = "urn:air:"
_LEGACY_URN_PREFIX = "urn:ai:"

# Mirrors the pattern the vendored conformance tool enforces:
# urn:air:<publisher>:<namespace>:<name>, where namespace may itself contain colons.
URN_RE = re.compile(
    r"^urn:air:(?P<publisher>[a-zA-Z0-9.-]+)(?::(?P<namespace>[a-zA-Z0-9._:-]+))?:(?P<name>[a-zA-Z0-9._-]+)$"
)

_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")

DEFAULT_PUBLISHER_DOMAIN = "observal.local"


@dataclass(frozen=True, slots=True)
class ParsedUrn:
    publisher: str
    namespace: str | None
    name: str

    @property
    def kind(self) -> DiscoveryKind | None:
        """Kind for Observal-issued identifiers (namespace is the kind)."""
        if not self.namespace:
            return None
        try:
            return DiscoveryKind(self.namespace)
        except ValueError:
            return None

    @property
    def entity_id(self) -> uuid.UUID | None:
        try:
            return uuid.UUID(self.name)
        except ValueError:
            return None


def normalize_urn(value: str) -> str:
    """Accept the predecessor ``urn:ai:`` prefix and return the current form."""
    text = value.strip()
    if text.lower().startswith(_LEGACY_URN_PREFIX):
        return URN_PREFIX + text[len(_LEGACY_URN_PREFIX) :]
    return text


def parse_urn(value: str) -> ParsedUrn | None:
    match = URN_RE.match(normalize_urn(value))
    if not match:
        return None
    return ParsedUrn(match.group("publisher"), match.group("namespace"), match.group("name"))


def publisher_domain_from_url(public_url: str | None) -> str:
    """Derive the URN publisher segment from the deployment's public URL.

    ARD requires the publisher to be a fully qualified domain name. A
    deployment that has not configured ``deployment.public_url`` yet gets a
    stable placeholder so identifiers can still be minted; the operator is
    expected to set the URL before publishing externally.
    """
    if not public_url:
        return DEFAULT_PUBLISHER_DOMAIN
    candidate = public_url.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    host = (urlparse(candidate).hostname or "").lower().rstrip(".")
    if not is_publisher_domain(host):
        return DEFAULT_PUBLISHER_DOMAIN
    return host


def is_publisher_domain(host: str) -> bool:
    """A publisher must be a fully qualified domain name: at least two labels, not an address."""
    if not host or "." not in host or not _DOMAIN_RE.match(host):
        return False
    is_address = all(label.isdigit() for label in host.split("."))
    return host not in {"localhost", "0.0.0.0"} and not is_address


def identity_uri(publisher_domain: str) -> str:
    """The trustManifest.identity value: an HTTPS FQDN URI whose domain matches the URN publisher."""
    return f"https://{publisher_domain}"


def build_urn(publisher_domain: str, kind: DiscoveryKind, entity_id: uuid.UUID) -> str:
    return f"{URN_PREFIX}{publisher_domain}:{kind.value}:{entity_id}"


def build_registry_urn(publisher_domain: str) -> str:
    """Identifier for the registry's own ``application/ai-registry+json`` entry."""
    return f"{URN_PREFIX}{publisher_domain}:registry:observal"
