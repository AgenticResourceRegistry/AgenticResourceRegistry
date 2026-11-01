# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Read-time, public-only upstream links for discovery; never use saved fork refs."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import aliased

from models.agent import Agent, AgentStatus, AgentVersion
from models.discovery_entry import (
    DiscoveryEntry,
    DiscoveryKind,
    DiscoveryLifecycle,
    DiscoverySourceKind,
    DiscoveryVisibility,
)
from models.mcp import ListingStatus
from services.registry_fork import COMPONENT_MODELS

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


_NATIVE_KINDS = {DiscoveryKind.agent} | {DiscoveryKind(kind) for kind in COMPONENT_MODELS}


async def public_upstreams(
    db: AsyncSession, entries: list[DiscoveryEntry]
) -> dict[tuple[DiscoveryKind, uuid.UUID], str]:
    """Link only two *currently* public approved listings with live projected IDs.

    The native rows are authoritative even if the discovery projection is stale
    after a visibility change. The upstream's ARD row supplies its stable URN,
    not the saved forked_from_ref, name, or version string.
    """
    groups = defaultdict(list)
    for entry in entries:
        if (
            entry.kind in _NATIVE_KINDS
            and entry.kind != DiscoveryKind.external
            and entry.source_kind == DiscoverySourceKind.local
            and entry.visibility == DiscoveryVisibility.public
            and entry.lifecycle_status == DiscoveryLifecycle.approved
            and entry.tombstoned_at is None
            and entry.local_entity_id is not None
        ):
            groups[entry.kind].append(entry.local_entity_id)
    links = {}
    for kind, ids in groups.items():
        listing_model, version_model = (
            (Agent, AgentVersion) if kind == DiscoveryKind.agent else COMPONENT_MODELS[kind.value]
        )
        fork = aliased(listing_model)
        source = aliased(listing_model)
        fork_version = aliased(version_model)
        source_version = aliased(version_model)
        upstream = aliased(DiscoveryEntry)
        approved = AgentStatus.approved if kind == DiscoveryKind.agent else ListingStatus.approved
        query = (
            select(fork.id, upstream.ard_identifier)
            .join(source, source.id == fork.forked_from_id)
            .join(fork_version, fork_version.id == fork.latest_version_id)
            .join(source_version, source_version.id == source.latest_version_id)
            .join(upstream, upstream.local_entity_id == source.id)
            .where(
                fork.id.in_(ids),
                fork.is_private.is_(False),
                source.is_private.is_(False),
                fork_version.status == approved,
                source_version.status == approved,
                upstream.kind == kind,
                upstream.source_kind == DiscoverySourceKind.local,
                upstream.lifecycle_status == DiscoveryLifecycle.approved,
                upstream.visibility == DiscoveryVisibility.public,
                upstream.tombstoned_at.is_(None),
            )
        )
        if kind == DiscoveryKind.agent:
            query = query.where(fork.deleted_at.is_(None), source.deleted_at.is_(None))
        for fork_id, urn in (await db.execute(query)).all():
            links[(kind, fork_id)] = urn
    return links
