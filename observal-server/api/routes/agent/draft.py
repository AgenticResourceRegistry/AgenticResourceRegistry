# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent draft workflow routes: save, update, start/cancel edit, submit."""

from datetime import UTC, datetime

from fastapi import Depends, HTTPException
from loguru import logger as optic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import commit_or_name_conflict, get_db, get_effective_agent_permission, require_role
from models.agent import Agent, AgentStatus, AgentVersion
from models.agent_component import AgentComponent
from models.skill import SkillListing
from models.team import Team, TeamRole
from models.user import User, UserRole
from schemas.agent import AgentCreateRequest, AgentResponse, AgentUpdateRequest
from services.config_generator import validate_mcp_command
from services.editing_lock import _is_lock_expired, acquire_edit_lock, release_edit_lock
from services.harness_capability_inference import compute_supported_harnesses, infer_required_features
from services.inbox import sources as inbox
from services.registry_fork import provenance_for
from services.registry_telemetry import emit_registry_event
from services.teamspace import (
    is_admin,
    publish_auto_approves_for_entity,
    resolve_publish_target,
    review_publication_to_public,
    team_membership,
)

from ._router import router
from .helpers import _agent_to_response, _load_agent, _resolve_component_names

# ---------------------------------------------------------------------------
# Draft workflow
# ---------------------------------------------------------------------------


async def _authorize_visibility_change(
    agent: Agent,
    current_user: User,
    db: AsyncSession,
    *,
    target_is_private: bool,
) -> None:
    """Authorize and serialize a team agent visibility change."""
    if agent.team_id is None:
        return
    team = (await db.execute(select(Team).where(Team.id == agent.team_id).with_for_update())).scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Teamspace not found")
    if team.visibility_request_status == "pending":
        raise HTTPException(status_code=409, detail="Teamspace is locked pending public visibility review")
    if not target_is_private and team.is_private:
        raise HTTPException(status_code=409, detail="Private teamspaces can only contain team-private items")
    if is_admin(current_user):
        return
    membership = await team_membership(db, agent.team_id, current_user.id)
    if not membership or membership.role not in (TeamRole.owner, TeamRole.reviewer):
        raise HTTPException(status_code=403, detail="Only team owners and reviewers can change visibility")


async def _reject_components_outside_target(
    components: list,
    db: AsyncSession,
    current_user: User,
    *,
    target_team_id,
    target_visibility: str,
) -> None:
    """Re-run the shared composition validation against a new publish target.

    Used when visibility changes without the caller resending components: the
    components already attached must still be legal under the new target.
    """
    refs = [{"component_type": c.component_type, "component_id": c.component_id} for c in components]
    if not refs:
        return

    from services.agent_resolver import validate_component_ids

    errors = await validate_component_ids(
        refs,
        db,
        require_approved=False,
        current_user=current_user,
        target_team_id=target_team_id,
        enforce_target=True,
    )
    if not errors:
        return

    name_map = await _resolve_component_names(components, db)
    offenders = ", ".join(
        f"{error.component_type} '{name_map.get(str(error.component_id)) or error.component_id}'" for error in errors
    )
    raise HTTPException(
        status_code=409,
        detail=f"Cannot change visibility to '{target_visibility}': {offenders} cannot be used by a "
        f"{target_visibility} agent",
    )


@router.post("/draft", response_model=AgentResponse)
async def save_draft(
    req: AgentCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Create an agent as a draft (relaxed validation, not submitted for review)."""
    optic.trace("req={}", req)
    target = await resolve_publish_target(
        db,
        current_user,
        req.name,
        team_id=req.team_id,
        visibility=req.visibility,
    )
    from services.agent_resolver import validate_component_ids

    component_refs = [{"component_type": c.component_type, "component_id": c.component_id} for c in req.components] + [
        {"component_type": "mcp", "component_id": mid} for mid in req.mcp_server_ids
    ]
    errors = await validate_component_ids(
        component_refs,
        db,
        require_approved=False,
        current_user=current_user,
        target_team_id=target.team_id if target.visibility == "team" else None,
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

    agent = Agent(
        name=req.name,
        namespace=target.namespace,
        slug=target.slug,
        owner=target.owner if target.team_id else (req.owner or current_user.username or current_user.email),
        created_by=current_user.id,
        team_id=target.team_id,
        is_private=target.visibility == "team",
    )
    db.add(agent)
    await db.flush()

    version = AgentVersion(
        agent_id=agent.id,
        version=req.version,
        description=req.description,
        prompt=req.prompt,
        model_name=req.model_name,
        model_config_json=req.model_config_json,
        models_by_harness=req.models_by_harness,
        external_mcps=[m.model_dump() for m in req.external_mcps],
        supported_harnesses=req.supported_harnesses,
        status=AgentStatus.draft,
        released_by=current_user.id,
        success_criteria=req.success_criteria.model_dump() if req.success_criteria else None,
    )
    db.add(version)
    await db.flush()

    agent.latest_version_id = version.id

    from services.agent_lock import attach_pinned_components

    # Legacy mcp_server_ids only apply when typed components were not sent.
    legacy_refs = (
        [{"component_type": "mcp", "component_id": mid} for mid in req.mcp_server_ids] if not req.components else []
    )
    await attach_pinned_components(db, version.id, legacy_refs + list(req.components), current_user=current_user)
    # Auto-infer harness features for draft (use request data, not ORM relationship)
    all_crefs_draft = list(req.components) + [
        type("_Ref", (), {"component_type": "mcp", "component_id": mid})() for mid in req.mcp_server_ids
    ]
    skill_comp_ids = [c.component_id for c in all_crefs_draft if c.component_type == "skill"]
    skill_listings_map_draft: dict = {}
    if skill_comp_ids:
        rows = (await db.execute(select(SkillListing).where(SkillListing.id.in_(skill_comp_ids)))).scalars().all()
        skill_listings_map_draft = {row.id: row for row in rows}

    class _DraftProxy:
        components = all_crefs_draft
        external_mcps = version.external_mcps

    version.required_capabilities = infer_required_features(_DraftProxy(), skill_listings=skill_listings_map_draft)
    version.inferred_supported_harnesses = compute_supported_harnesses(version.required_capabilities)

    await db.flush()
    from services.agent_lock import lock_agent_version
    from services.agent_snapshot import build_yaml_snapshot

    await lock_agent_version(db, agent, version)
    version.yaml_snapshot = await build_yaml_snapshot(version, db)

    await commit_or_name_conflict(db, "agent")
    agent = await _load_agent(db, str(agent.id), prefer_user_id=current_user.id, current_user=current_user)
    return _agent_to_response(
        agent,
        created_by_email=current_user.email,
        created_by_username=current_user.username,
        forked_from=await provenance_for(agent, current_user, db) if agent.is_fork else None,
    )


@router.put("/{agent_id}/draft", response_model=AgentResponse)
async def update_draft(
    agent_id: str,
    req: AgentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Update a draft agent."""
    optic.trace("agent_id={}, req={}", agent_id, req)
    agent = await _load_agent(db, agent_id, prefer_user_id=current_user.id, current_user=current_user)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    perm = get_effective_agent_permission(agent, current_user)
    if perm not in ("owner", "edit"):
        raise HTTPException(status_code=403, detail="Not the agent owner or editor")
    if agent.status not in (AgentStatus.draft, AgentStatus.rejected, AgentStatus.pending):
        raise HTTPException(status_code=400, detail="Only draft, rejected, or pending agents can be edited")

    version = agent.latest_version
    if not version:
        raise HTTPException(status_code=400, detail="Agent has no version to update")

    # Moving an item between teamspaces is a separate operation and must never
    # ride along on a draft save.
    if req.team_id is not None and req.team_id != agent.team_id:
        raise HTTPException(
            status_code=422,
            detail="Teamspace cannot be changed here. Recreate the agent under the target teamspace.",
        )
    if req.mcp_server_ids is not None:
        raise HTTPException(
            status_code=422,
            detail="mcp_server_ids is not accepted here. Send MCP servers in 'components' instead.",
        )

    if req.visibility == "team" and agent.team_id is None:
        raise HTTPException(status_code=422, detail="Team visibility requires a teamspace")
    target_is_private = bool(agent.is_private) if req.visibility is None else req.visibility == "team"
    visibility_changed = target_is_private != bool(agent.is_private)
    target_team_id = agent.team_id if target_is_private else None
    if visibility_changed:
        await _authorize_visibility_change(agent, current_user, db, target_is_private=target_is_private)
        # A caller who resends components gets them validated below; otherwise
        # the currently attached set has to survive the new target.
        if req.components is None:
            await _reject_components_outside_target(
                list(agent.components),
                db,
                current_user,
                target_team_id=target_team_id,
                target_visibility="team" if target_is_private else "public",
            )
    was_private = bool(agent.is_private)
    agent.is_private = target_is_private
    # This route only accepts draft, rejected, and pending agents, so nothing here is
    # approved yet and the call cannot currently fire. It stays as an invariant guard:
    # every site that writes is_private must route a private-to-public transition back
    # through review, so loosening the status gate above can never open that bypass.
    if await review_publication_to_public(agent, current_user, db, was_private=was_private):
        await inbox.on_review_requested(
            db, agent, subject_type="agent", actor_id=current_user.id, version=agent.version
        )

    if req.version_bump_type and req.version is None:
        from services.versioning import bump_version

        req.version = bump_version(agent.version, req.version_bump_type)

    for field in (
        "version",
        "description",
        "prompt",
        "model_name",
        "model_config_json",
        "models_by_harness",
        "supported_harnesses",
    ):
        val = getattr(req, field)
        if val is not None:
            setattr(version, field, val)

    if "success_criteria" in req.model_fields_set:
        version.success_criteria = req.success_criteria.model_dump() if req.success_criteria is not None else None

    if req.external_mcps is not None:
        for _mcp in req.external_mcps:
            _cmd = getattr(_mcp, "command", "")
            _args = getattr(_mcp, "args", [])
            try:
                validate_mcp_command(_cmd, _args or [])
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"Invalid MCP command: {e}")
        version.external_mcps = [m.model_dump() for m in req.external_mcps]

    if req.components is not None:
        from services.agent_lock import attach_pinned_components
        from services.agent_resolver import validate_component_ids

        errors = await validate_component_ids(
            [{"component_type": c.component_type, "component_id": c.component_id} for c in req.components],
            db,
            require_approved=False,
            current_user=current_user,
            target_team_id=target_team_id,
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

        version_id = version.id
        old_comps = (
            (await db.execute(select(AgentComponent).where(AgentComponent.agent_version_id == version_id)))
            .scalars()
            .all()
        )
        for comp in old_comps:
            await db.delete(comp)
        if old_comps:
            await db.flush()
        # Components already on this draft keep their pins unless a version is named.
        await attach_pinned_components(db, version_id, req.components, previous=old_comps, current_user=current_user)

    # Re-infer harness features only when components or external_mcps changed
    if req.components is not None or req.external_mcps is not None:
        if not agent.latest_version:
            raise HTTPException(status_code=400, detail="Agent has no version to update features on")
        current_comps_draft = (
            (await db.execute(select(AgentComponent).where(AgentComponent.agent_version_id == version.id)))
            .scalars()
            .all()
        )
        skill_comp_ids = [c.component_id for c in current_comps_draft if c.component_type == "skill"]
        skill_listings_map_draft_update: dict = {}
        if skill_comp_ids:
            rows = (await db.execute(select(SkillListing).where(SkillListing.id.in_(skill_comp_ids)))).scalars().all()
            skill_listings_map_draft_update = {row.id: row for row in rows}

        class _DraftUpdateProxy:
            components = current_comps_draft
            external_mcps = version.external_mcps

        version.required_capabilities = infer_required_features(
            _DraftUpdateProxy(), skill_listings=skill_listings_map_draft_update
        )
        version.inferred_supported_harnesses = compute_supported_harnesses(version.required_capabilities)

    # Don't allow saving over another user's active lock
    if version.is_editing and version.editing_by != current_user.id and not _is_lock_expired(version.editing_since):
        raise HTTPException(
            status_code=409,
            detail="This item is currently being edited by another user. Please try again later.",
        )
    release_edit_lock(version, current_user.id, force=True)
    await db.flush()

    for field in ("name", "owner", "category"):
        val = getattr(req, field)
        if val is not None:
            setattr(agent, field, val)

    # Always rebuild the lock and snapshot so reviewers see the latest state
    # including per-harness model overrides, prompt edits, and component swaps.
    from services.agent_lock import lock_agent_version
    from services.agent_snapshot import build_yaml_snapshot

    await lock_agent_version(db, agent, version)
    version.yaml_snapshot = await build_yaml_snapshot(version, db)

    await db.commit()
    agent = await _load_agent(db, str(agent.id), prefer_user_id=current_user.id, current_user=current_user)
    return _agent_to_response(
        agent,
        created_by_email=current_user.email,
        created_by_username=current_user.username,
        forked_from=await provenance_for(agent, current_user, db) if agent.is_fork else None,
    )


@router.post("/{agent_id}/start-edit")
async def start_edit_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("agent_id={}", agent_id)
    agent = await _load_agent(db, agent_id, prefer_user_id=current_user.id, current_user=current_user)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    perm = get_effective_agent_permission(agent, current_user)
    if perm not in ("owner", "edit"):
        raise HTTPException(status_code=403, detail="Not the agent owner or editor")
    version = agent.latest_version
    if not version:
        raise HTTPException(status_code=400, detail="Agent has no version")
    if version.status not in (AgentStatus.pending, AgentStatus.draft, AgentStatus.rejected):
        raise HTTPException(status_code=400, detail=f"Cannot edit: agent version is '{version.status.value}'")
    # Re-fetch with row-level lock to prevent TOCTOU race
    version = (
        await db.execute(select(AgentVersion).where(AgentVersion.id == version.id).with_for_update())
    ).scalar_one()
    acquire_edit_lock(version, current_user.id)
    await db.commit()
    return {"status": "locked"}


@router.post("/{agent_id}/cancel-edit")
async def cancel_edit_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("agent_id={}", agent_id)
    agent = await _load_agent(db, agent_id, prefer_user_id=current_user.id, current_user=current_user)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    perm = get_effective_agent_permission(agent, current_user)
    if perm not in ("owner", "edit"):
        raise HTTPException(status_code=403, detail="Not the agent owner or editor")
    version = agent.latest_version
    if not version:
        raise HTTPException(status_code=400, detail="Agent has no version")
    release_edit_lock(version, current_user.id)
    await db.commit()
    return {"status": "unlocked"}


@router.post("/{agent_id}/submit", response_model=AgentResponse)
async def submit_draft(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Submit a draft agent for review (transitions draft -> pending)."""
    optic.trace("agent_id={}", agent_id)
    agent = await _load_agent(db, agent_id, prefer_user_id=current_user.id, current_user=current_user)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    perm = get_effective_agent_permission(agent, current_user)
    if perm not in ("owner", "edit"):
        raise HTTPException(status_code=403, detail="Not the agent owner or editor")
    if agent.status not in (AgentStatus.draft, AgentStatus.rejected):
        raise HTTPException(status_code=400, detail="Agent is not a draft")
    if not agent.description:
        raise HTTPException(status_code=400, detail="Description is required before submitting")

    # Validate components exist
    if agent.components:
        from services.agent_resolver import validate_component_ids

        errors = await validate_component_ids(
            [{"component_type": c.component_type, "component_id": c.component_id} for c in agent.components],
            db,
            require_approved=False,
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

    # Scan for anti-gaming patterns before transitioning to pending
    from services.anti_gaming import scan_for_gaming, summarize_flags

    if agent.latest_version:
        flags = scan_for_gaming(agent.latest_version.prompt)
        agent.latest_version.gaming_flags = summarize_flags(flags)
        # Defensive refresh - covers older drafts created before snapshot
        # backfill landed and guarantees the reviewer sees current state.
        from services.agent_snapshot import build_yaml_snapshot

        agent.latest_version.yaml_snapshot = await build_yaml_snapshot(agent.latest_version, db)

    if await publish_auto_approves_for_entity(agent, current_user, db):
        agent.status = AgentStatus.approved
        agent.latest_version.reviewed_by = current_user.id
        agent.latest_version.reviewed_at = datetime.now(UTC)
    else:
        agent.status = AgentStatus.pending
    if agent.latest_version:
        # Refresh the lock on submit; an auto-approved submit freezes it here.
        from services.agent_lock import lock_agent_version

        await lock_agent_version(db, agent, agent.latest_version)
    await db.commit()
    agent = await _load_agent(db, str(agent.id), prefer_user_id=current_user.id, current_user=current_user)
    name_map = await _resolve_component_names(agent.components, db)

    emit_registry_event(
        action="agent.submit",
        user_id=str(current_user.id),
        user_email=current_user.email,
        user_role=current_user.role.value,
        agent_id=str(agent.id),
        resource_name=agent.name,
    )

    return _agent_to_response(
        agent,
        name_map,
        created_by_email=current_user.email,
        created_by_username=current_user.username,
        forked_from=await provenance_for(agent, current_user, db) if agent.is_fork else None,
    )


from api.routes.agent_versions import agent_version_router  # noqa: E402

router.include_router(agent_version_router)
