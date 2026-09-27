# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Batch-resolve historical component pins without guessing on ambiguous IDs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import or_, select

from models.agent import Agent, AgentVersion
from models.agent_component import AgentComponent
from models.hook import HookListing, HookVersion
from models.mcp import McpListing, McpVersion
from models.skill import SkillListing, SkillVersion

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from services.layer_components.normalizer import Occurrence

MODELS = {"mcp": (McpListing, McpVersion), "skill": (SkillListing, SkillVersion), "hook": (HookListing, HookVersion)}


@dataclass(frozen=True)
class ResolvedOccurrence:
    occurrence: Occurrence
    component_id: str
    component_version_id: str
    identity_status: str


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except (ValueError, TypeError, AttributeError):
        return None


async def resolve_occurrences(db: AsyncSession, occurrences: list[Occurrence]) -> list[ResolvedOccurrence]:
    """Direct correct-type UUID → exact parent component → unique name/semver.

    Any syntactically valid supplied UUID that no longer exists is unresolved,
    never silently reassigned by name. Version UUID is looked up independently.
    """
    if not occurrences:
        return []
    listing_rows: dict[str, dict[UUID, tuple[str, str, str]]] = {}
    for kind, (model, _) in MODELS.items():
        pins = [occ for occ in occurrences if occ.component_type == kind]
        identifiers = {_uuid(occ.raw_listing_id) for occ in pins} - {None}
        names = {occ.raw_name for occ in pins if occ.raw_name}
        slugs = {occ.qualified_name.rpartition("/")[2] for occ in pins if "/" in occ.qualified_name}
        if not pins:
            continue
        predicates = []
        if identifiers:
            predicates.append(model.id.in_(identifiers))
        if names:
            predicates.append(model.name.in_(names))
        if slugs:
            predicates.append(model.slug.in_(slugs))
        if not predicates:
            listing_rows[kind] = {}
            continue
        rows = (
            await db.execute(select(model.id, model.name, model.namespace, model.slug).where(or_(*predicates)))
        ).all()
        listing_rows[kind] = {row.id: (row.name, row.namespace, row.slug) for row in rows}

    parents = {(_uuid(occ.parent_agent_id), occ.parent_agent_version) for occ in occurrences if occ.source == "agent"}
    parent_ids = {parent_id for parent_id, _ in parents if parent_id is not None}
    parent_versions: dict[tuple[UUID, str], UUID] = {}
    if parent_ids:
        rows = (
            await db.execute(
                select(AgentVersion.id, AgentVersion.agent_id, AgentVersion.version)
                .join(Agent, AgentVersion.agent_id == Agent.id)
                .where(Agent.id.in_(parent_ids), Agent.deleted_at.is_(None))
            )
        ).all()
        parent_versions = {(row.agent_id, row.version): row.id for row in rows}
    component_rows: dict[UUID, list[tuple[str, UUID, str, str]]] = defaultdict(list)
    if parent_versions:
        rows = (
            await db.execute(
                select(
                    AgentComponent.agent_version_id,
                    AgentComponent.component_type,
                    AgentComponent.component_id,
                    AgentComponent.component_name,
                    AgentComponent.resolved_version,
                ).where(AgentComponent.agent_version_id.in_(parent_versions.values()))
            )
        ).all()
        for row in rows:
            component_rows[row.agent_version_id].append(
                (row.component_type, row.component_id, row.component_name, row.resolved_version)
            )

    for kind, (model, _) in MODELS.items():
        linked_ids = {
            component_id
            for items in component_rows.values()
            for component_type, component_id, _, _ in items
            if component_type == kind
        } - set(listing_rows.get(kind, {}))
        if linked_ids:
            rows = (
                await db.execute(
                    select(model.id, model.name, model.namespace, model.slug).where(model.id.in_(linked_ids))
                )
            ).all()
            listing_rows.setdefault(kind, {}).update({row.id: (row.name, row.namespace, row.slug) for row in rows})

    # Version lookup is batched independently of listing resolution; exact
    # semver is required for the fallback and for a version-scoped cohort.
    version_ids: dict[tuple[str, UUID, str], str] = {}
    for kind, (_, version_model) in MODELS.items():
        candidates = listing_rows.get(kind, {})
        versions = {occ.raw_version for occ in occurrences if occ.component_type == kind and occ.raw_version}
        if not candidates or not versions:
            continue
        rows = (
            await db.execute(
                select(version_model.listing_id, version_model.version, version_model.id).where(
                    version_model.listing_id.in_(candidates),
                    version_model.version.in_(versions),
                )
            )
        ).all()
        version_ids.update({(kind, row.listing_id, row.version): str(row.id) for row in rows})

    resolved: list[tuple[Occurrence, UUID | None, str]] = []
    for occurrence in occurrences:
        candidates = listing_rows.get(occurrence.component_type, {})
        supplied_id = _uuid(occurrence.raw_listing_id)
        if supplied_id is not None:
            chosen = supplied_id if supplied_id in candidates else None
            status = "resolved" if chosen else "unresolved"
        else:
            parent_key = (_uuid(occurrence.parent_agent_id), occurrence.parent_agent_version)
            version_id = parent_versions.get(parent_key)
            linked = {
                component_id
                for kind, component_id, name, version in component_rows.get(version_id, [])
                if kind == occurrence.component_type
                and name == occurrence.raw_name
                and version == occurrence.raw_version
            }
            if occurrence.source == "agent" and parent_key[0] is not None and version_id is None:
                chosen, status = None, "unresolved"  # Deleted parent UUID must not trigger name fallback.
            elif linked:
                linked_id = next(iter(linked)) if len(linked) == 1 else None
                chosen = linked_id if linked_id in candidates else None
                status = "resolved" if chosen else "ambiguous" if len(linked) > 1 else "unresolved"
            elif occurrence.raw_name and occurrence.raw_version:
                matched = {
                    listing_id
                    for listing_id, (name, namespace, slug) in candidates.items()
                    if name == occurrence.raw_name
                    and (not occurrence.qualified_name or occurrence.qualified_name == f"{namespace}/{slug}")
                    and (occurrence.component_type, listing_id, occurrence.raw_version) in version_ids
                }
                chosen = next(iter(matched)) if len(matched) == 1 else None
                status = "resolved" if chosen else "ambiguous" if len(matched) > 1 else "unresolved"
            else:
                chosen, status = None, "unresolved"
        resolved.append((occurrence, chosen, status))

    return [
        ResolvedOccurrence(
            occurrence=occurrence,
            component_id=str(chosen) if chosen else "",
            component_version_id=version_ids.get((occurrence.component_type, chosen, occurrence.raw_version), "")
            if chosen
            else "",
            identity_status=status,
        )
        for occurrence, chosen, status in resolved
    ]
