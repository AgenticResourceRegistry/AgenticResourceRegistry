# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Remote Agent2Agent (A2A) agents in the discovery index.

An A2A agent is a running service that publishes an Agent Card. Observal does
not own it, so it has no native listing: the discovery entry is the governed
record (``source_kind = imported``, ``kind = external``, media type
``application/a2a-agent-card+json``). A person registers a card by URL, a
reviewer approves it, and from then on the entry pins the exact card that was
reviewed. Delegating clients call the agent directly with the pinned card; the
server never proxies A2A traffic.

Cards are accepted in the A2A v1.0 shape (``supportedInterfaces``) and in the
v0.3 shape (``url`` + ``preferredTransport`` + ``additionalInterfaces``); both
are normalised to one list of interfaces. See ``docs/adr/0002-a2a-delegation.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from loguru import logger as optic

from models.discovery_entry import (
    DiscoveryEntry,
    DiscoveryKind,
    DiscoveryLifecycle,
    DiscoverySourceKind,
    DiscoveryVisibility,
)
from services.discovery.identity import MEDIA_TYPE_A2A, URN_PREFIX, identity_uri, is_publisher_domain
from services.discovery.projection import build_search_document

WELL_KNOWN_CARD_PATH = "/.well-known/agent-card.json"
MAX_CARD_BYTES = 256 * 1024
FETCH_TIMEOUT_SECONDS = 10.0
URN_NAMESPACE = "a2a"

MAX_SKILLS = 32
MAX_TEXT = 2000
MAX_QUERIES = 5
MAX_CAPABILITIES = 24

# Bindings the Observal client speaks today. Other bindings stay discoverable.
CLIENT_BINDINGS = frozenset({"JSONRPC"})
_KNOWN_BINDINGS = {"JSONRPC", "GRPC", "HTTP+JSON"}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class AgentCardError(ValueError):
    """A card could not be fetched or is not a usable A2A Agent Card.

    ``message`` is always text this module chose, safe to return to clients.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class AgentInterface:
    url: str
    binding: str
    protocol_version: str
    tenant: str | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"url": self.url, "protocolBinding": self.binding}
        if self.protocol_version:
            data["protocolVersion"] = self.protocol_version
        if self.tenant:
            data["tenant"] = self.tenant
        return data


@dataclass(frozen=True, slots=True)
class AgentSkill:
    id: str
    name: str
    description: str
    tags: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentCard:
    name: str
    description: str
    version: str
    interfaces: tuple[AgentInterface, ...]
    skills: tuple[AgentSkill, ...]
    streaming: bool
    push_notifications: bool
    security_schemes: tuple[str, ...]
    provider: str | None
    raw: dict[str, Any] = field(repr=False)

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def callable_interface(self) -> AgentInterface | None:
        """The first interface the Observal client can call, in the card's order of preference."""
        return next((i for i in self.interfaces if i.binding in CLIENT_BINDINGS), None)


# ── Parsing ──────────────────────────────────────────────────────────────


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _strings(value: Any, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        text = _text(item, 300)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return tuple(out)


def _http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return value.strip()


def _interfaces(card: dict[str, Any]) -> list[AgentInterface]:
    found: list[AgentInterface] = []

    def add(url: Any, binding: Any, version: Any, tenant: Any = None) -> None:
        clean = _http_url(url)
        if not clean:
            return
        name = str(binding or "JSONRPC").strip().upper().replace("_", "+") or "JSONRPC"
        name = "HTTP+JSON" if name in ("REST", "HTTP", "HTTPJSON") else name
        item = AgentInterface(
            url=clean,
            binding=name,
            protocol_version=_text(version, 20),
            tenant=_text(tenant, 200) or None,
        )
        if item not in found:
            found.append(item)

    # v1.0: supportedInterfaces, ordered by the agent's preference.
    for iface in card.get("supportedInterfaces") or []:
        if isinstance(iface, dict):
            add(
                iface.get("url"),
                iface.get("protocolBinding") or iface.get("transport"),
                iface.get("protocolVersion"),
                iface.get("tenant"),
            )
    # v0.3: url + preferredTransport, then additionalInterfaces.
    if card.get("url"):
        add(card.get("url"), card.get("preferredTransport"), card.get("protocolVersion"))
    for iface in card.get("additionalInterfaces") or []:
        if isinstance(iface, dict):
            add(iface.get("url"), iface.get("transport"), card.get("protocolVersion"))
    return found


def parse_agent_card(raw: Any) -> AgentCard:
    """Validate the parts of an Agent Card Observal relies on, keeping the rest untouched."""
    if not isinstance(raw, dict):
        raise AgentCardError("An Agent Card must be a JSON object.")
    name = _text(raw.get("name"), 255)
    if not name:
        raise AgentCardError("The Agent Card has no name.")
    interfaces = _interfaces(raw)
    if not interfaces:
        raise AgentCardError("The Agent Card declares no http(s) endpoint (supportedInterfaces or url).")
    for iface in interfaces:
        if iface.binding not in _KNOWN_BINDINGS:
            optic.debug("a2a card {} declares unknown binding {}", name, iface.binding)

    skills: list[AgentSkill] = []
    for item in (raw.get("skills") or [])[:MAX_SKILLS]:
        if not isinstance(item, dict):
            continue
        skill_name = _text(item.get("name"), 255) or _text(item.get("id"), 255)
        if not skill_name:
            continue
        skills.append(
            AgentSkill(
                id=_text(item.get("id"), 255) or skill_name,
                name=skill_name,
                description=_text(item.get("description")),
                tags=_strings(item.get("tags"), 12),
                examples=_strings(item.get("examples"), 6),
            )
        )

    capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
    schemes = raw.get("securitySchemes") if isinstance(raw.get("securitySchemes"), dict) else {}
    provider = raw.get("provider") if isinstance(raw.get("provider"), dict) else {}
    return AgentCard(
        name=name,
        description=_text(raw.get("description")),
        version=_text(raw.get("version"), 50) or "0.0.0",
        interfaces=tuple(interfaces),
        skills=tuple(skills),
        streaming=bool(capabilities.get("streaming")),
        push_notifications=bool(capabilities.get("pushNotifications")),
        security_schemes=tuple(sorted(str(k) for k in schemes)),
        provider=_text(provider.get("organization"), 255) or None,
        raw=raw,
    )


# ── Fetching ─────────────────────────────────────────────────────────────


def normalize_card_url(url: str) -> str:
    """Point a bare agent origin at its well-known card; leave explicit paths alone."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AgentCardError("The card URL must be an http(s) URL.")
    if parsed.username or parsed.password:
        raise AgentCardError("The card URL must not carry credentials.")
    path = parsed.path if parsed.path not in ("", "/") else WELL_KNOWN_CARD_PATH
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))


def parse_host_allowlist(value: str | None) -> frozenset[str]:
    return frozenset(h.strip().lower() for h in (value or "").split(",") if h.strip())


def check_card_host(url: str, private_hosts: frozenset[str]) -> str | None:
    """HTTPS to a public address, or a host the administrator allowed explicitly.

    Internal agents are normal (a team's triage agent on the intranet), so the
    SSRF guard is relaxed per host through ``discovery.a2a_private_hosts``
    rather than globally. Allowed hosts may also use plain http.

    Returns the checked address to connect to, or None for an allowed host.
    """
    from services.ssrf_guard import resolve_public_address

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in private_hosts:
        return None
    if parsed.scheme != "https":
        raise AgentCardError("Agent Cards must be served over https.")
    address = resolve_public_address(host)
    if address is None:
        raise AgentCardError(
            "The card URL resolves to a private address. An administrator can allow the host "
            "in the discovery.a2a_private_hosts setting."
        )
    return address


async def fetch_agent_card(
    url: str,
    *,
    private_hosts: frozenset[str] = frozenset(),
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[AgentCard, str]:
    """Fetch and parse a card. Returns the card and the URL it was fetched from.

    Redirects are not followed: a redirect could leave the checked host. The
    request goes to the address that passed the check, with the original Host
    header and TLS server name, so DNS rebinding cannot reach a private address.
    """
    card_url = normalize_card_url(url)
    # getaddrinfo blocks; keep it off the event loop.
    address = await asyncio.to_thread(check_card_host, card_url, private_hosts)
    request_url, headers, extensions = card_url, {"Accept": "application/json"}, {}
    if address:
        parsed = urlparse(card_url)
        host = f"[{address}]" if ":" in address else address
        request_url = urlunparse(parsed._replace(netloc=f"{host}:{parsed.port}" if parsed.port else host))
        headers["Host"] = parsed.netloc
        extensions["sni_hostname"] = parsed.hostname
    try:
        async with (
            asyncio.timeout(FETCH_TIMEOUT_SECONDS * 2),  # the httpx timeout is per read; bound the whole download
            httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False, transport=transport) as client,
            client.stream("GET", request_url, headers=headers, extensions=extensions) as response,
        ):
            if response.status_code != 200:
                raise AgentCardError(f"Fetching the Agent Card returned HTTP {response.status_code}.")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_CARD_BYTES:
                    raise AgentCardError("The Agent Card is larger than 256 KiB.")
    except (httpx.HTTPError, TimeoutError):
        optic.debug("a2a card fetch failed host={}", urlparse(card_url).hostname)
        raise AgentCardError("The Agent Card could not be fetched.") from None
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AgentCardError("The Agent Card is not valid JSON.") from None
    return parse_agent_card(raw), card_url


# ── Discovery entry ──────────────────────────────────────────────────────


def _slug(value: str) -> str:
    return _SLUG_RE.sub("-", value.lower()).strip("-")[:64] or "agent"


def a2a_identifier(card: AgentCard, card_url: str, fallback_domain: str, entry_id: uuid.UUID) -> str:
    """``urn:air:<card host>:a2a:<name>`` when the card's host is a real domain.

    The card host is the publisher. A card on an address or single-label host
    cannot anchor an ARD identifier, so it is published under this registry's
    domain with the entry's own UUID instead.
    """
    host = (urlparse(card_url).hostname or "").lower()
    if is_publisher_domain(host):
        return f"{URN_PREFIX}{host}:{URN_NAMESPACE}:{_slug(card.name)}"
    return f"{URN_PREFIX}{fallback_domain}:{URN_NAMESPACE}:{entry_id}"


def _capabilities(card: AgentCard) -> list[str]:
    values: list[str] = [f"a2a-skill:{s.id}" for s in card.skills]
    values += [f"binding:{i.binding.lower()}" for i in card.interfaces]
    if card.streaming:
        values.append("streaming")
    if card.push_notifications:
        values.append("push-notifications")
    return list(dict.fromkeys(values))[:MAX_CAPABILITIES]


def _queries(card: AgentCard) -> list[str]:
    queries: list[str] = [e for s in card.skills for e in s.examples]
    if card.description:
        queries.append(re.split(r"(?<=[.!?])\s", card.description, maxsplit=1)[0][:120])
    queries += [s.description[:120] for s in card.skills if s.description]
    return list(dict.fromkeys(q for q in queries if q))[:MAX_QUERIES]


def _tags(card: AgentCard) -> list[str]:
    tags = ["a2a", "remote-agent", *(t for s in card.skills for t in s.tags)]
    return list(dict.fromkeys(t.lower() for t in tags))[:12]


def build_a2a_document(
    entry: DiscoveryEntry, card: AgentCard, card_url: str, *, reviewer_note: str | None = None
) -> dict[str, Any]:
    """The ARD entry for an imported A2A agent, with the reviewed card pinned inside it."""
    iface = card.callable_interface
    document: dict[str, Any] = {
        "@context": ["https://agenticresourcediscovery.org/context/v1", {"obs": "https://observal.io/ns#"}],
        "identifier": entry.ard_identifier,
        "displayName": card.name,
        "type": MEDIA_TYPE_A2A,
        "url": card_url,
        "description": card.description,
        "version": card.version,
        "capabilities": _capabilities(card),
        "representativeQueries": _queries(card),
        "tags": _tags(card),
        "trustManifest": {"identity": identity_uri(entry.publisher_domain), "identityType": "https"},
        "obs:kind": DiscoveryKind.external.value,
        "obs:protocol": "a2a",
        "obs:source": DiscoverySourceKind.imported.value,
        "obs:lifecycle": entry.lifecycle_status.value,
        "obs:visibility": entry.visibility.value,
        "obs:activatable": False,
        "obs:delegable": entry.lifecycle_status == DiscoveryLifecycle.approved and iface is not None,
        "obs:artifactDigest": card.digest,
        "obs:capabilitiesSource": "publisher",
        "obs:representativeQueriesSource": "publisher",
        "obs:a2aInterface": iface.to_json() if iface else None,
        "obs:a2aSecuritySchemes": list(card.security_schemes),
        "obs:agentCard": card.raw,
    }
    if card.provider:
        document["obs:provider"] = card.provider
    if reviewer_note:
        document["obs:reviewNote"] = reviewer_note
    if entry.updated_at_source:
        document["updatedAt"] = entry.updated_at_source.isoformat()
    return document


def apply_card(
    entry: DiscoveryEntry,
    card: AgentCard,
    card_url: str,
    *,
    lifecycle: DiscoveryLifecycle,
    now: datetime | None = None,
    reviewer_note: str | None = None,
) -> DiscoveryEntry:
    """Write the card's terms onto an imported entry and rebuild its published document."""
    now = now or datetime.now(UTC)
    entry.media_type = MEDIA_TYPE_A2A
    entry.display_name = card.name[:255]
    entry.description = card.description
    entry.capabilities = _capabilities(card)
    entry.capabilities_source = "publisher"
    entry.representative_queries = _queries(card)
    entry.representative_queries_source = "publisher"
    entry.tags = _tags(card)
    entry.version = card.version[:50]
    entry.artifact_url = card_url[:1000]
    entry.artifact_digest = card.digest
    entry.updated_at_source = now
    entry.native_ref = None
    entry.supported_harnesses = []
    entry.lifecycle_status = lifecycle
    entry.activatable = False
    entry.search_document = build_search_document(
        card.name,
        card.provider,
        "a2a agent remote",
        card.description,
        [s.name for s in card.skills],
        [s.description for s in card.skills],
        _queries(card),
        _tags(card),
    )
    entry.last_seen_at = now
    entry.tombstoned_at = None
    document = build_a2a_document(entry, card, card_url, reviewer_note=reviewer_note)
    entry.raw_entry = document
    entry.content_hash = hashlib.sha256(
        json.dumps(document, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    return entry


def new_imported_entry(
    *,
    card: AgentCard,
    card_url: str,
    fallback_domain: str,
    visibility: DiscoveryVisibility,
    team_id: uuid.UUID | None,
    owner_id: uuid.UUID,
    now: datetime | None = None,
) -> DiscoveryEntry:
    entry_id = uuid.uuid4()
    host = (urlparse(card_url).hostname or "").lower()
    publisher = host if is_publisher_domain(host) else fallback_domain
    entry = DiscoveryEntry(
        id=entry_id,
        ard_identifier=a2a_identifier(card, card_url, fallback_domain, entry_id),
        kind=DiscoveryKind.external,
        source_kind=DiscoverySourceKind.imported,
        publisher_domain=publisher,
        source_identifier=card_url[:255],
        visibility=visibility,
        team_id=team_id,
        owner_user_id=owner_id,
        co_author_ids="",
        indexed_at=now or datetime.now(UTC),
    )
    return apply_card(entry, card, card_url, lifecycle=DiscoveryLifecycle.pending, now=now)


def pinned_card(entry: DiscoveryEntry) -> AgentCard | None:
    """The card as it was when the entry was last reviewed or refreshed."""
    raw = (entry.raw_entry or {}).get("obs:agentCard")
    try:
        return parse_agent_card(raw)
    except AgentCardError:
        return None


__all__ = [
    "MEDIA_TYPE_A2A",
    "WELL_KNOWN_CARD_PATH",
    "AgentCard",
    "AgentCardError",
    "AgentInterface",
    "AgentSkill",
    "a2a_identifier",
    "apply_card",
    "build_a2a_document",
    "check_card_host",
    "fetch_agent_card",
    "new_imported_entry",
    "normalize_card_url",
    "parse_agent_card",
    "parse_host_allowlist",
    "pinned_card",
]
