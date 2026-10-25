# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shape discovery entries into the JSON the ARD endpoints return."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from models.discovery_entry import DiscoveryEntry, DiscoveryKind, DiscoveryLifecycle, DiscoverySourceKind
from observal_shared.harness_registry import get_harnesses_with_fact
from services.discovery.identity import MEDIA_TYPE_A2A, MEDIA_TYPE_REGISTRY, build_registry_urn, identity_uri

if TYPE_CHECKING:
    from services.discovery.search import Ranked

RESULT_DESCRIPTION_LIMIT = 280

# What a caller can do with the entry right now, from the CLI's point of view.
AVAILABILITY_NOW = "now"  # text resource, loads into the current session
AVAILABILITY_NEXT_SESSION = "next-session"  # config write, takes effect after restart
AVAILABILITY_EXPLICIT = "explicit-install"  # hooks: never activated implicitly
AVAILABILITY_DELEGATE = "delegate"  # remote A2A agent: callable now, nothing installed
AVAILABILITY_NOT_APPROVED = "not-approved"
AVAILABILITY_ARCHIVED = "archived"
AVAILABILITY_UNSUPPORTED = "unsupported-in-harness"

_NOW_KINDS = {DiscoveryKind.skill, DiscoveryKind.prompt}


def is_a2a(entry: DiscoveryEntry) -> bool:
    return entry.source_kind == DiscoverySourceKind.imported and entry.media_type == MEDIA_TYPE_A2A


def availability(entry: DiscoveryEntry, harness: str | None = None) -> str:
    if entry.lifecycle_status == DiscoveryLifecycle.archived:
        return AVAILABILITY_ARCHIVED
    if entry.lifecycle_status != DiscoveryLifecycle.approved:
        return AVAILABILITY_NOT_APPROVED
    if is_a2a(entry):
        return AVAILABILITY_DELEGATE
    if harness and entry.supported_harnesses and harness not in entry.supported_harnesses:
        return AVAILABILITY_UNSUPPORTED
    if entry.kind == DiscoveryKind.hook or (entry.kind == DiscoveryKind.skill and not entry.activatable):
        return AVAILABILITY_EXPLICIT
    if entry.kind in _NOW_KINDS:
        return AVAILABILITY_NOW
    return AVAILABILITY_NEXT_SESSION


def recommended(entry: DiscoveryEntry) -> bool:
    """Whether an admin marked the resource as recommended (editorial, never part of ``score``)."""
    return bool((entry.raw_entry or {}).get("obs:recommended"))


def delegable(entry: DiscoveryEntry) -> bool:
    """Whether a running agent may hand this entry a task (ADR 0002).

    Approved remote A2A agents always qualify. An approved Observal Agent
    qualifies when at least one harness it supports can run headless; an agent
    that lists no harnesses is treated as supporting all of them.
    """
    if entry.lifecycle_status != DiscoveryLifecycle.approved or entry.tombstoned_at is not None:
        return False
    if is_a2a(entry):
        return bool((entry.raw_entry or {}).get("obs:a2aInterface"))
    if entry.kind != DiscoveryKind.agent:
        return False
    headless = set(get_harnesses_with_fact("headless_run", True))
    supported = set(entry.supported_harnesses or [])
    return bool(headless & supported) if supported else bool(headless)


def _truncate(text: str | None, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def search_result_item(
    ranked: Ranked, *, source: str, harness: str | None = None, forked_from: str | None = None
) -> dict[str, Any]:
    """One entry of ``results`` in an ARD Search response.

    Only ``identifier`` is required by the spec; we include what a client
    needs to choose without a second request, and keep ``score`` relevance-only.
    """
    entry = ranked.entry
    item = {
        "identifier": entry.ard_identifier,
        "displayName": entry.display_name,
        "type": entry.media_type,
        "url": entry.artifact_url,
        "version": entry.version,
        "description": _truncate(entry.description, RESULT_DESCRIPTION_LIMIT),
        "capabilities": list(entry.capabilities or [])[:8],
        "score": int(ranked.score),
        "source": source,
        "matchedOn": list(ranked.matched_on),
        "obs:kind": entry.kind.value,
        "obs:nativeRef": entry.native_ref,
        "obs:approval": entry.lifecycle_status.value,
        "obs:visibility": entry.visibility.value,
        "obs:supportedHarnesses": list(entry.supported_harnesses or []),
        "obs:availability": availability(entry, harness),
        "obs:activatable": bool(entry.activatable),
        "obs:delegable": delegable(entry),
        "obs:recommended": recommended(entry),
        "obs:artifactDigest": entry.artifact_digest,
        "obs:publisher": entry.publisher_domain,
    }
    if forked_from:
        item["obs:forkedFrom"] = forked_from
    # The organization named on a remote agent's card, as the card states it.
    if is_a2a(entry) and (provider := (entry.raw_entry or {}).get("obs:provider")):
        item["obs:provider"] = provider
    return item


def entry_document(entry: DiscoveryEntry, *, forked_from: str | None = None) -> dict[str, Any]:
    """Enrich the projection with a live, public-only upstream link."""
    result = dict(entry.raw_entry or {})
    result.pop("obs:forkedFrom", None)  # Never trust stale projected provenance.
    if forked_from:
        result["obs:forkedFrom"] = forked_from
    return result


def registry_entry(publisher_domain: str, base_url: str, *, display_name: str = "Observal") -> dict[str, Any]:
    """The ``application/ai-registry+json`` entry that advertises this registry's search base."""
    return {
        "identifier": build_registry_urn(publisher_domain),
        "displayName": f"{display_name} Registry",
        "type": MEDIA_TYPE_REGISTRY,
        "url": f"{base_url.rstrip('/')}/api/v1/ard",
        "description": "Observal discovery registry: search approved agents, MCP servers, skills, hooks, prompts and sandboxes.",
        "representativeQueries": [
            "find an approved skill for reviewing pull requests",
            "which MCP servers can query our database",
        ],
        "trustManifest": {"identity": identity_uri(publisher_domain), "identityType": "https"},
    }


def manifest(
    entries: list[DiscoveryEntry],
    *,
    publisher_domain: str,
    base_url: str,
    upstreams: dict | None = None,
) -> dict[str, Any]:
    """The ``/.well-known/ard.json`` document: registry entry first, then resources."""
    return {
        "@context": ["https://agenticresourcediscovery.org/context/v1", {"obs": "https://observal.io/ns#"}],
        "entries": [
            registry_entry(publisher_domain, base_url),
            *(entry_document(e, forked_from=(upstreams or {}).get((e.kind, e.local_entity_id))) for e in entries),
        ],
    }


def list_item(entry: DiscoveryEntry, *, forked_from: str | None = None) -> dict[str, Any]:
    """Compact row for ARD List (``GET /agents``)."""
    item = {
        "identifier": entry.ard_identifier,
        "displayName": entry.display_name,
        "type": entry.media_type,
        "url": entry.artifact_url,
        "version": entry.version,
        "description": _truncate(entry.description, RESULT_DESCRIPTION_LIMIT),
        "obs:kind": entry.kind.value,
        "obs:nativeRef": entry.native_ref,
        "obs:approval": entry.lifecycle_status.value,
        "obs:supportedHarnesses": list(entry.supported_harnesses or []),
        "obs:recommended": recommended(entry),
        "updatedAt": entry.updated_at_source.isoformat() if entry.updated_at_source else None,
    }
    if forked_from:
        item["obs:forkedFrom"] = forked_from
    return item
