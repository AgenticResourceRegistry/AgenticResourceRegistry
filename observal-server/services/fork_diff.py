# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Compare a visible fork release with its approved, currently visible upstream base."""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

import yaml
from fastapi import HTTPException
from sqlalchemy import select

from api.deps import (
    get_effective_agent_permission,
    get_effective_component_permission,
    may_view_unapproved,
)
from models.agent import AgentStatus, AgentVersion
from models.agent_component import AgentComponent
from models.mcp import ListingStatus
from services.registry_fork import COMPONENT_MODELS, provenance_for
from services.teamspace import can_review, review_scope

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _render_diff(before: dict, after: dict, *, source: str, target: str) -> dict:
    left = yaml.safe_dump(before, sort_keys=True, allow_unicode=True).splitlines(keepends=True)
    right = yaml.safe_dump(after, sort_keys=True, allow_unicode=True).splitlines(keepends=True)
    diff = "".join(difflib.unified_diff(left, right, fromfile=source, tofile=target))
    return {"diff": diff, "unchanged": before == after}


async def _agent_content(db: AsyncSession, version) -> dict:
    """Behavior-relevant agent fields; release metadata such as the version number is excluded."""
    pins = (
        (
            await db.execute(
                select(AgentComponent)
                .where(AgentComponent.agent_version_id == version.id)
                .order_by(AgentComponent.order_index)
            )
        )
        .scalars()
        .all()
    )
    return {
        "description": version.description or "",
        "prompt": version.prompt or "",
        "model_name": version.model_name or "",
        "model_config_json": version.model_config_json or {},
        "models_by_harness": {str(k): str(v) for k, v in (version.models_by_harness or {}).items() if v},
        "supported_harnesses": sorted(version.supported_harnesses or []),
        "external_mcps": version.external_mcps or [],
        "success_criteria": version.success_criteria or None,
        # A pin's identity is its exact release and content digest, not its display name.
        "components": [
            {
                "type": pin.component_type,
                "id": str(pin.component_id),
                "name": pin.component_name or "",
                "version": pin.resolved_version,
                "digest": pin.resolved_digest,
                "config_override": pin.config_override or None,
            }
            for pin in pins
        ],
    }


def _without_names(content: dict) -> dict:
    return {**content, "components": [{k: v for k, v in c.items() if k != "name"} for c in content["components"]]}


async def _readable_statuses(db: AsyncSession, fork, kind: str, current_user, status) -> set | None:
    """Fork version states this caller may compare; ``None`` means every state.

    Owners, co-authors, admins and global reviewers read every state, as on the
    version routes. A teamspace owner or reviewer who may review this fork
    (``can_review``) also reads pending versions, which is their review queue,
    but never someone else's drafts or rejections.
    """
    permission = (
        get_effective_agent_permission(fork, current_user)
        if kind == "agent"
        else get_effective_component_permission(fork, current_user)
    )
    if may_view_unapproved(permission, current_user):
        return None
    if current_user is not None and can_review(fork, await review_scope(db, current_user)):
        return {status.approved, status.pending}
    return {status.approved}


async def fork_version_diff(db: AsyncSession, fork, kind: str, current_user, version: str | None = None) -> dict:
    """Never read the base snapshot unless the source is still accessible to this caller.

    Owners and reviewers in scope may compare an unapproved fork version; other
    viewers only see approved releases. An inaccessible upstream always answers 404,
    without disclosing its saved reference, ID, or version.
    """
    version_model = AgentVersion if kind == "agent" else COMPONENT_MODELS[kind][1]
    status = AgentStatus if kind == "agent" else ListingStatus
    fk = version_model.agent_id if kind == "agent" else version_model.listing_id
    statuses = await _readable_statuses(db, fork, kind, current_user, status)
    # Authorize the fork itself before saying anything about its upstream, so
    # an unapproved fork's existence is not revealed by a different 404.
    readable = select(version_model.id).where(fk == fork.id)
    if statuses is not None:
        readable = readable.where(version_model.status.in_(statuses))
    if not getattr(fork, "is_fork", False) or (await db.execute(readable.limit(1))).first() is None:
        raise HTTPException(status_code=404, detail="Fork not found")

    selected = (
        version if version is not None else (fork.latest_version.version if fork.latest_version is not None else None)
    )
    query = select(version_model).where(fk == fork.id, version_model.version == selected)
    if statuses is not None:
        query = query.where(version_model.status.in_(statuses))
    candidate = (await db.execute(query)).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Version not found")

    provenance = await provenance_for(fork, current_user, db)
    if not provenance or not provenance["available"] or not provenance.get("version"):
        raise HTTPException(status_code=404, detail="Upstream unavailable")

    source = await db.get(type(fork), fork.forked_from_id)
    base = await db.get(version_model, fork.forked_from_version_id)
    if (
        source is None
        or base is None
        or base.status != status.approved
        or (base.agent_id if kind == "agent" else base.listing_id) != source.id
    ):
        raise HTTPException(status_code=404, detail="Upstream unavailable")

    if kind == "agent":
        # Compare structured content, never snapshot text: snapshots are frozen
        # at different times, embed live component names, and older ones lack
        # fields newer builders render, all of which would read as edits.
        before = await _agent_content(db, base)
        after = await _agent_content(db, candidate)
        # Names are shown for the reviewer but are not content: the pinned
        # release and digest are what install, so a rename alone is no change.
        if _without_names(before) == _without_names(after):
            return {"version": candidate.version, "base_version": base.version, "diff": "", "unchanged": True}
    else:
        from api.routes.component_versions import _version_to_dict
        from services.agent_lock import _named, content_digest
        from services.registry_fork import VERSION_MANAGED_FIELDS

        # The install digest deliberately ignores secret environment/header
        # values. Show only their names/required flags, as the digest does.
        if content_digest(kind, base) == content_digest(kind, candidate):
            return {"version": candidate.version, "base_version": base.version, "diff": "", "unchanged": True}
        excluded = (VERSION_MANAGED_FIELDS - {"description"}) | {
            "mcp_validated",
            "validated",
            "validated_at",
            "tools_schema",
            # Skill scripts and bundled files are install content, not diffable text.
            "script_content",
            "extra_files",
        }

        def visible_content(row):
            fields = {k: v for k, v in _version_to_dict(row, kind).items() if k not in excluded}
            for key in ("environment_variables", "headers"):
                if key in fields:
                    fields[key] = _named(fields[key])
            return fields

        before = visible_content(base)
        after = visible_content(candidate)
        if before == after or kind == "skill":
            # Skill scripts and bundled files are excluded above. Signal their
            # change with the install digest instead of their contents.
            before["additional_install_content_digest"] = content_digest(kind, base)
            after["additional_install_content_digest"] = content_digest(kind, candidate)

    result = _render_diff(before, after, source=f"upstream@{base.version}", target=f"fork@{candidate.version}")
    return {"version": candidate.version, "base_version": base.version, **result}
