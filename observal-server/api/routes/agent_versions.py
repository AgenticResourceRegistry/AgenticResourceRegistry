# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Version-specific endpoints for agents.

Mounted on the /api/v1/agents router via::

    router.include_router(agent_version_router)

All paths are relative to /api/v1/agents (no extra prefix).
"""

from __future__ import annotations

import difflib
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger as optic
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from api.deps import (
    get_db,
    get_effective_agent_permission,
    get_registry_user,
    may_view_unapproved,
    require_role,
)
from models.agent import (
    Agent,
    AgentStatus,
    AgentVersion,
)
from models.mcp import McpListing
from models.skill import SkillListing
from models.user import User, UserRole
from schemas.agent import (  # noqa: TC001
    AgentVersionCreateRequest,
    AgentVersionReviewRequest,
)
from services.agent_resolver import validate_component_ids
from services.harness import generate_agent_config
from services.harness_capability_inference import compute_supported_harnesses, infer_required_features
from services.inbox import sources as inbox
from services.versioning import parse_semver, validate_semver

agent_version_router = APIRouter()


async def audit(*_args, **_kwargs):
    return None


class _AgentVersionConfigProxy:
    """Expose one immutable version with its parent agent identity."""

    def __init__(self, agent: Agent, version: AgentVersion, components: list) -> None:
        self._agent = agent
        self._version = version
        self.components = components
        self.id = agent.id
        self.name = agent.name
        self.namespace = agent.namespace
        self.slug = agent.slug
        self.team_id = agent.team_id
        self.is_private = agent.is_private

    def __getattr__(self, name: str):
        return getattr(self._version, name)


# Import _load_agent from agent.py to avoid duplication.
# This is a late import done inside each function to avoid circular imports
# at module load time (agent.py imports from schemas.agent which we also import here).


def _version_to_summary(ver: AgentVersion) -> dict:
    """Serialize an AgentVersion to a list-view dict."""
    optic.trace("ver={}", ver)
    return {
        "id": str(ver.id),
        "agent_id": str(ver.agent_id),
        "version": ver.version,
        "description": ver.description,
        "status": ver.status.value if hasattr(ver.status, "value") else ver.status,
        "is_prerelease": ver.is_prerelease,
        "download_count": ver.download_count,
        "supported_harnesses": ver.supported_harnesses,
        "released_by": str(ver.released_by),
        "released_at": ver.released_at,
        "created_at": ver.created_at,
        "rejection_reason": ver.rejection_reason,
        "component_count": len(ver.components) if ver.components else 0,
    }


def _version_to_detail(ver: AgentVersion) -> dict:
    """Serialize an AgentVersion to a full-detail dict."""
    optic.trace("ver={}", ver)
    d = _version_to_summary(ver)
    d.update(
        {
            "prompt": ver.prompt,
            "model_name": ver.model_name,
            "model_config_json": ver.model_config_json,
            "models_by_harness": ver.models_by_harness or {},
            "external_mcps": ver.external_mcps,
            "yaml_snapshot": ver.yaml_snapshot,
            "harness_configs": ver.harness_configs,
            "required_capabilities": ver.required_capabilities,
            "inferred_supported_harnesses": ver.inferred_supported_harnesses,
            "success_criteria": ver.success_criteria,
        }
    )
    d["components"] = [
        {
            "component_type": c.component_type,
            "component_id": str(c.component_id),
            "name": c.component_name or "",
            "resolved_version": c.resolved_version or "",
        }
        for c in (ver.components or [])
    ]
    return d


# ---------------------------------------------------------------------------
# Standalone async functions - exposed for direct testing
# ---------------------------------------------------------------------------


async def _load_agent(db: AsyncSession, agent_id: str, current_user: User | None = None) -> Agent | None:
    """Thin wrapper that delegates to the route-level _load_agent."""
    optic.trace("agent_id={}", agent_id)
    from api.routes.agent import _load_agent as _base_load

    return await _base_load(db, agent_id, current_user=current_user)


async def _list_agent_versions(
    agent_id: str,
    page: int,
    page_size: int,
    db: AsyncSession,
    current_user: User | None,
) -> dict:
    optic.trace("agent_id={}, page={}", agent_id, page)
    agent = await _load_agent(db, agent_id, current_user)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    perm = get_effective_agent_permission(agent, current_user)
    if perm == "none":
        raise HTTPException(status_code=403, detail="Insufficient permissions to view this agent")

    # A version carries the agent's prompt and generated harness config. Seeing the
    # agent is not enough to read a version nobody has approved: making a
    # team-private agent public returns its versions to the queue, and without this
    # an ordinary caller reads them before a reviewer does.
    version_filters = [AgentVersion.agent_id == agent.id]
    if not may_view_unapproved(perm, current_user):
        version_filters.append(AgentVersion.status == AgentStatus.approved)

    offset = (page - 1) * page_size
    stmt = (
        select(AgentVersion)
        .where(*version_filters)
        .order_by(AgentVersion.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    versions = result.scalars().all()

    count_stmt = select(func.count(AgentVersion.id)).where(*version_filters)
    total = (await db.execute(count_stmt)).scalar() or 0

    return {
        "items": [_version_to_summary(v) for v in versions],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def _get_agent_version(
    agent_id: str,
    version: str,
    db: AsyncSession,
    current_user: User | None,
) -> dict:
    optic.trace("agent_id={}, version={}", agent_id, version)
    agent = await _load_agent(db, agent_id, current_user)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    perm = get_effective_agent_permission(agent, current_user)
    if perm == "none":
        raise HTTPException(status_code=403, detail="Insufficient permissions to view this agent")

    # A version carries the agent's prompt and generated harness config. Seeing the
    # agent is not enough to read a version nobody has approved: making a
    # team-private agent public returns its versions to the queue, and without this
    # an ordinary caller reads them before a reviewer does.
    version_filters = [AgentVersion.agent_id == agent.id]
    if not may_view_unapproved(perm, current_user):
        version_filters.append(AgentVersion.status == AgentStatus.approved)

    stmt = select(AgentVersion).where(*version_filters, AgentVersion.version == version)
    ver = (await db.execute(stmt)).scalar_one_or_none()
    if not ver:
        raise HTTPException(status_code=404, detail="Version not found")

    from api.routes.agent import _resolve_component_names, _resolve_component_statuses

    components = ver.components or []
    name_map = await _resolve_component_names(components, db)
    status_map = await _resolve_component_statuses(components, db)
    detail = _version_to_detail(ver)
    for comp in detail.get("components", []):
        if not comp.get("name"):
            comp["name"] = name_map.get(comp["component_id"], "")
        comp["status"] = status_map.get(comp["component_id"])
    return detail


async def _create_agent_version(
    agent_id: str,
    req: AgentVersionCreateRequest,
    db: AsyncSession,
    current_user: User,
) -> dict:
    # Validate semver (guard against mock objects in tests that bypass the schema validator)
    optic.trace("agent_id={}", agent_id)
    if not validate_semver(req.version):
        raise HTTPException(status_code=422, detail=f"Invalid semver string: {req.version!r}")

    agent = await _load_agent(db, agent_id, current_user)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    perm = get_effective_agent_permission(agent, current_user)
    if perm != "owner":
        raise HTTPException(status_code=403, detail="Not authorized to release versions")

    # Duplicate check
    dup_stmt = select(AgentVersion).where(
        AgentVersion.agent_id == agent.id,
        AgentVersion.version == req.version,
    )
    if (await db.execute(dup_stmt)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"Version {req.version!r} already exists for this agent")

    # Validate components exist and are approved
    if req.components:
        errors = await validate_component_ids(
            [{"component_type": c.component_type, "component_id": c.component_id} for c in req.components],
            db,
            current_user=current_user,
            target_team_id=agent.team_id if agent.is_private else None,
            enforce_target=True,
        )
        if errors:
            raise HTTPException(
                status_code=400,
                detail=[
                    {"component_type": e.component_type, "component_id": str(e.component_id), "reason": e.reason}
                    for e in errors
                ],
            )

    # Check for existing pending versions to warn the caller
    pending_stmt = select(func.count(AgentVersion.id)).where(
        AgentVersion.agent_id == agent.id,
        AgentVersion.status == AgentStatus.pending,
    )
    pending_count = (await db.execute(pending_stmt)).scalar() or 0

    initial_status = AgentStatus.draft if req.save_as_draft else AgentStatus.pending
    now = datetime.now(UTC)
    ver = AgentVersion(
        agent_id=agent.id,
        version=req.version,
        description=req.description,
        prompt=req.prompt,
        model_name=req.model_name,
        model_config_json=req.model_config_json,
        models_by_harness=req.models_by_harness,
        external_mcps=[m.model_dump() for m in req.external_mcps] if req.external_mcps else [],
        supported_harnesses=req.supported_harnesses,
        yaml_snapshot=None,
        is_prerelease=req.is_prerelease,
        status=initial_status,
        released_by=current_user.id,
        released_at=now,
        success_criteria=req.success_criteria.model_dump() if req.success_criteria else None,
    )
    db.add(ver)
    await db.flush()

    # Pin components. A release carries forward every pin from the agent's current
    # release unless the component names a version or the author asked to refresh.
    from services.agent_lock import attach_pinned_components

    current = agent.latest_version
    version_components = await attach_pinned_components(
        db,
        ver.id,
        req.components,
        previous=list(current.components or []) if current is not None else [],
        refresh=req.refresh_components,
        require_approved=True,
        current_user=current_user,
    )

    # Infer harness features from components
    skill_comp_ids = [c.component_id for c in req.components if c.component_type == "skill"]
    skill_listings_map: dict = {}
    if skill_comp_ids:
        rows = (await db.execute(select(SkillListing).where(SkillListing.id.in_(skill_comp_ids)))).scalars().all()
        skill_listings_map = {row.id: row for row in rows}

    class _VersionProxy:
        components = req.components
        external_mcps = ver.external_mcps

    ver.required_capabilities = infer_required_features(_VersionProxy(), skill_listings=skill_listings_map)
    ver.inferred_supported_harnesses = compute_supported_harnesses(ver.required_capabilities)

    # Always build the snapshot from structured fields so caller-provided text
    # cannot drift from the version stored in the database.
    await db.flush()
    from services.agent_lock import lock_agent_version
    from services.agent_snapshot import build_yaml_snapshot

    await lock_agent_version(db, agent, ver)
    ver.yaml_snapshot = await build_yaml_snapshot(ver, db)

    # Pre-generate harness configs at release time (spec: no generation at request time)
    mcp_comp_ids = [c.component_id for c in req.components if c.component_type == "mcp"]
    mcp_listings_map: dict = {}
    if mcp_comp_ids:
        from services.agent_lock import PinnedListing, pinned_versions

        # These configs are stored and served as this release's, so generate them
        # from the MCP releases it pins, not from each listing's latest release.
        pinned = await pinned_versions(db, version_components)
        rows = (await db.execute(select(McpListing).where(McpListing.id.in_(mcp_comp_ids)))).scalars().all()
        mcp_listings_map = {
            row.id: PinnedListing(row, pinned[("mcp", row.id)]) if ("mcp", row.id) in pinned else row for row in rows
        }

    # Pre-generate harness configs for supported_harnesses (the user-declared list).
    # inferred_supported_harnesses is a compatibility analysis result used for display/filtering,
    # but supported_harnesses is authoritative for which configs to generate.
    harness_configs: dict = {}
    failed_harnesses: list[str] = []
    from services.model_resolver import resolve_model_for_harness

    config_agent = _AgentVersionConfigProxy(agent, ver, version_components)
    for harness in ver.supported_harnesses or []:
        try:
            resolved_model, model_warnings = await resolve_model_for_harness(
                harness,
                model_name=ver.model_name or "",
                models_by_harness=ver.models_by_harness or {},
                override=None,
            )
            harness_configs[harness] = generate_agent_config(
                config_agent,
                harness,
                mcp_listings=mcp_listings_map,
                options={"_resolved_model": resolved_model, "_model_warnings": model_warnings},
            )
        except Exception:
            optic.opt(exception=True).error(
                "harness config generation failed for agent={} version={} harness={}", agent.name, req.version, harness
            )
            failed_harnesses.append(harness)
    ver.harness_configs = harness_configs or {}

    # A draft is not in anyone's queue; only a pending release is.
    await inbox.on_publish(
        db,
        agent,
        subject_type="agent",
        actor_id=current_user.id,
        auto_approved=initial_status != AgentStatus.pending,
        version=ver.version,
    )

    # Do NOT update latest_version_id - that happens on approval
    await db.commit()

    warnings: list[str] = []
    if pending_count > 0:
        warnings.append(f"This agent already has {pending_count} pending version(s)")
    if failed_harnesses:
        warnings.append(
            f"harness config generation failed for: {', '.join(failed_harnesses)}. These will 404 until regenerated."
        )

    result = {
        "id": str(ver.id),
        "agent_id": str(ver.agent_id),
        "version": ver.version,
        "status": ver.status.value,
        "description": ver.description,
        "model_name": ver.model_name,
        "supported_harnesses": ver.supported_harnesses,
        "released_by": str(ver.released_by),
        "released_at": ver.released_at,
        "created_at": ver.created_at,
    }
    if warnings:
        result["warnings"] = warnings
    return result


async def _review_agent_version(
    agent_id: str,
    version: str,
    req: AgentVersionReviewRequest,
    db: AsyncSession,
    current_user: User,
) -> dict:
    optic.trace("agent_id={}, version={}", agent_id, version)
    agent = await _load_agent(db, agent_id, current_user)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    perm = get_effective_agent_permission(agent, current_user)

    # A version carries the agent's prompt and generated harness config. Seeing the
    # agent is not enough to read a version nobody has approved: making a
    # team-private agent public returns its versions to the queue, and without this
    # an ordinary caller reads them before a reviewer does.
    version_filters = [AgentVersion.agent_id == agent.id]
    if not may_view_unapproved(perm, current_user):
        version_filters.append(AgentVersion.status == AgentStatus.approved)

    stmt = select(AgentVersion).where(*version_filters, AgentVersion.version == version)
    ver = (await db.execute(stmt)).scalar_one_or_none()
    if not ver:
        raise HTTPException(status_code=404, detail="Version not found")

    if ver.status != AgentStatus.pending:
        raise HTTPException(
            status_code=422, detail=f"Version is {ver.status.value!r}, only pending versions can be reviewed"
        )

    if req.action == "approve":
        # Same gate as the review queue: the component releases this version pins
        # must themselves be approved, and approval freezes the lock.
        from api.routes.review import _check_agent_components_ready
        from services.agent_lock import lock_agent_version

        _ready, blocking = await _check_agent_components_ready(
            ver.components or [], db, require_public_skills=not agent.is_private
        )
        if blocking:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Cannot approve: some components are not approved yet",
                    "blocking_components": blocking,
                },
            )
        ver.status = AgentStatus.approved
        ver.rejection_reason = None
        ver.reviewed_by = current_user.id
        ver.reviewed_at = datetime.now(UTC)
        # Flush version status change first to avoid CircularDependencyError
        # between Agent.latest_version (ManyToOne) and Agent.versions (OneToMany)
        await db.flush()
        await lock_agent_version(db, agent, ver)
        # Update latest_version_id if this version is newer than (or equal to) the current latest
        current_latest = agent.latest_version
        new_parsed = parse_semver(ver.version)
        current_parsed = parse_semver(current_latest.version) if current_latest else None
        if not current_latest or (
            new_parsed is not None and current_parsed is not None and new_parsed >= current_parsed
        ):
            agent.latest_version_id = ver.id
    else:
        ver.status = AgentStatus.rejected
        ver.rejection_reason = req.reason
        ver.reviewed_by = current_user.id
        ver.reviewed_at = datetime.now(UTC)

    # Same fact as a decision made through api/routes/review.py: the version's
    # author hears the outcome, and every reviewer's open request item for this
    # version is cleared. Delivered before the commit, in this transaction.
    await inbox.on_review_decided(
        db,
        agent,
        subject_type="agent",
        approved=req.action == "approve",
        actor_id=current_user.id,
        version=ver.version,
        reason=req.reason if req.action != "approve" else None,
        submitter_id=ver.released_by,
    )

    await db.commit()

    return {
        "version": version,
        "new_status": ver.status.value,
        "reason": ver.rejection_reason,
    }


async def _get_agent_harness_config(
    agent_id: str,
    version: str,
    harness: str,
    db: AsyncSession,
    current_user: User | None,
) -> dict:
    optic.trace("agent_id={}, version={}", agent_id, version)
    agent = await _load_agent(db, agent_id, current_user)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    perm = get_effective_agent_permission(agent, current_user)
    if perm == "none":
        raise HTTPException(status_code=403, detail="Insufficient permissions to view this agent")

    # A version carries the agent's prompt and generated harness config. Seeing the
    # agent is not enough to read a version nobody has approved: making a
    # team-private agent public returns its versions to the queue, and without this
    # an ordinary caller reads them before a reviewer does.
    version_filters = [AgentVersion.agent_id == agent.id]
    if not may_view_unapproved(perm, current_user):
        version_filters.append(AgentVersion.status == AgentStatus.approved)

    stmt = select(AgentVersion).where(*version_filters, AgentVersion.version == version)
    ver = (await db.execute(stmt)).scalar_one_or_none()
    if not ver:
        raise HTTPException(status_code=404, detail="Version not found")

    # Serve pre-generated config only, no generation at request time.
    if not ver.harness_configs or harness not in ver.harness_configs:
        raise HTTPException(
            status_code=404,
            detail=f"harness '{harness}' not supported by this agent version. Available: {list(ver.harness_configs or {})}",
        )

    return ver.harness_configs[harness]


async def _get_version_diff(
    agent_id: str,
    v1: str,
    v2: str,
    db: AsyncSession,
    current_user: User | None,
) -> dict:
    optic.trace("agent_id={}, v1={}", agent_id, v1)
    agent = await _load_agent(db, agent_id, current_user)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    perm = get_effective_agent_permission(agent, current_user)
    if perm == "none":
        raise HTTPException(status_code=403, detail="Insufficient permissions to view this agent")

    version_filters = [AgentVersion.agent_id == agent.id]
    if not may_view_unapproved(perm, current_user):
        version_filters.append(AgentVersion.status == AgentStatus.approved)

    stmt1 = select(AgentVersion).where(*version_filters, AgentVersion.version == v1)
    ver1 = (await db.execute(stmt1)).scalar_one_or_none()
    if not ver1:
        raise HTTPException(status_code=404, detail=f"Version {v1!r} not found")

    stmt2 = select(AgentVersion).where(*version_filters, AgentVersion.version == v2)
    ver2 = (await db.execute(stmt2)).scalar_one_or_none()
    if not ver2:
        raise HTTPException(status_code=404, detail=f"Version {v2!r} not found")

    # Build YAML diff text from the snapshot, falling back to a freshly
    # rendered snapshot for legacy versions whose yaml_snapshot was never
    # backfilled.
    from services.agent_snapshot import build_yaml_snapshot

    async def _snapshot_text(ver: AgentVersion) -> str:
        optic.trace("ver={}", ver)
        if ver.yaml_snapshot:
            return ver.yaml_snapshot
        return await build_yaml_snapshot(ver, db)

    text1 = await _snapshot_text(ver1)
    text2 = await _snapshot_text(ver2)

    yaml_diff = "\n".join(
        line.rstrip("\n")
        for line in difflib.unified_diff(
            text1.splitlines(keepends=True),
            text2.splitlines(keepends=True),
            fromfile=f"v{v1}",
            tofile=f"v{v2}",
        )
    )

    # Compute component changes between versions
    comp1 = {(c.component_type, str(c.component_id)): c for c in (ver1.components or [])}
    comp2 = {(c.component_type, str(c.component_id)): c for c in (ver2.components or [])}
    component_changes = []
    for key, c in comp2.items():
        if key not in comp1:
            component_changes.append(
                {
                    "type": c.component_type,
                    "name": c.component_name or "",
                    "change": "added",
                    "version": c.resolved_version or "",
                }
            )
        elif comp1[key].resolved_version != c.resolved_version:
            component_changes.append(
                {
                    "type": c.component_type,
                    "name": c.component_name or "",
                    "change": "updated",
                    "from": comp1[key].resolved_version or "",
                    "to": c.resolved_version or "",
                }
            )
    for key, c in comp1.items():
        if key not in comp2:
            component_changes.append(
                {
                    "type": c.component_type,
                    "name": c.component_name or "",
                    "change": "removed",
                    "version": c.resolved_version or "",
                }
            )

    return {
        "agent_id": str(agent.id),
        "version_a": v1,
        "version_b": v2,
        "yaml_diff": yaml_diff,
        "component_changes": component_changes,
    }


async def _visible_agent_version(
    agent_id: str, version: str, db: AsyncSession, current_user: User | None
) -> tuple[Agent, AgentVersion]:
    agent = await _load_agent(db, agent_id, current_user)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    perm = get_effective_agent_permission(agent, current_user)
    if perm == "none":
        raise HTTPException(status_code=403, detail="Insufficient permissions to view this agent")
    version_filters = [AgentVersion.agent_id == agent.id, AgentVersion.version == version]
    if not may_view_unapproved(perm, current_user):
        version_filters.append(AgentVersion.status == AgentStatus.approved)
    ver = (await db.execute(select(AgentVersion).where(*version_filters))).scalar_one_or_none()
    if not ver:
        raise HTTPException(status_code=404, detail="Version not found")
    return agent, ver


async def _get_agent_version_lock(agent_id: str, version: str, db: AsyncSession, current_user: User | None) -> dict:
    """The lock for one agent version: the frozen snapshot once approved."""
    optic.trace("agent_id={}, version={}", agent_id, version)
    from services.agent_lock import build_lock_document

    agent, ver = await _visible_agent_version(agent_id, version, db, current_user)
    if ver.status == AgentStatus.approved and ver.lock_snapshot:
        try:
            return json.loads(ver.lock_snapshot)
        except ValueError:
            optic.warning("agent version {} has an unreadable lock snapshot; rebuilding", ver.id)
    # Drafts, pending versions, and legacy approved versions without a stored lock
    # get the lock their current pins describe. Reading never persists it.
    return await build_lock_document(db, agent, ver)


async def _get_agent_version_outdated(agent_id: str, version: str, db: AsyncSession, current_user: User | None) -> dict:
    """For each pinned component, whether a newer approved release exists."""
    optic.trace("agent_id={}, version={}", agent_id, version)
    from services.agent_lock import pin_freshness

    agent, ver = await _visible_agent_version(agent_id, version, db, current_user)
    components = await pin_freshness(db, ver.components or [])
    return {
        "agent_id": str(agent.id),
        "qualified_name": f"{agent.namespace}/{agent.slug}",
        "version": ver.version,
        "components": components,
        "summary": {
            "total": len(components),
            "outdated": sum(item["outdated"] for item in components),
            "unlocked": sum(not item["locked"] for item in components),
            "archived": sum(item["archived"] for item in components),
        },
    }


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@agent_version_router.get("/{agent_id}/versions")
async def list_agent_versions(
    agent_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.debug("agent versions list")
    return await _list_agent_versions(
        agent_id=agent_id,
        page=page,
        page_size=page_size,
        db=db,
        current_user=current_user,
    )


@agent_version_router.get("/{agent_id}/versions/{version}")
async def get_agent_version(
    agent_id: str,
    version: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.trace("agent_id={}, version={}", agent_id, version)
    return await _get_agent_version(
        agent_id=agent_id,
        version=version,
        db=db,
        current_user=current_user,
    )


@agent_version_router.post("/{agent_id}/versions")
async def create_agent_version(
    agent_id: str,
    req: AgentVersionCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("agent_id={}", agent_id)
    return await _create_agent_version(
        agent_id=agent_id,
        req=req,
        db=db,
        current_user=current_user,
    )


@agent_version_router.post("/{agent_id}/versions/{version}/review")
async def review_agent_version(
    agent_id: str,
    version: str,
    req: AgentVersionReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.reviewer)),
):
    optic.trace("agent_id={}, version={}", agent_id, version)
    return await _review_agent_version(
        agent_id=agent_id,
        version=version,
        req=req,
        db=db,
        current_user=current_user,
    )


@agent_version_router.get("/{agent_id}/versions/{version}/harness/{harness}")
async def get_agent_harness_config(
    agent_id: str,
    version: str,
    harness: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.trace("agent_id={}, version={}", agent_id, version)
    return await _get_agent_harness_config(
        agent_id=agent_id,
        version=version,
        harness=harness,
        db=db,
        current_user=current_user,
    )


@agent_version_router.get("/{agent_id}/versions/{version}/lock")
async def get_agent_version_lock(
    agent_id: str,
    version: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.trace("agent_id={}, version={}", agent_id, version)
    return await _get_agent_version_lock(agent_id=agent_id, version=version, db=db, current_user=current_user)


@agent_version_router.get("/{agent_id}/versions/{version}/outdated")
async def get_agent_version_outdated(
    agent_id: str,
    version: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.trace("agent_id={}, version={}", agent_id, version)
    return await _get_agent_version_outdated(agent_id=agent_id, version=version, db=db, current_user=current_user)


@agent_version_router.get("/{agent_id}/versions/{v1}/diff/{v2}")
async def get_version_diff(
    agent_id: str,
    v1: str,
    v2: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.trace("agent_id={}, v1={}", agent_id, v1)
    return await _get_version_diff(
        agent_id=agent_id,
        v1=v1,
        v2=v2,
        db=db,
        current_user=current_user,
    )
