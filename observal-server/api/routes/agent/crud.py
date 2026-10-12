# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent CRUD routes: create, list, get, update, delete, archive, unarchive."""

import uuid
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Query, Response
from loguru import logger as optic
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import (
    ROLE_HIERARCHY,
    apply_registry_scope,
    get_db,
    get_effective_agent_permission,
    get_registry_user,
    require_role,
)
from api.search import keyword_search
from models.agent import (
    Agent,
    AgentStatus,
    AgentVersion,
)
from models.agent_component import AgentComponent
from models.skill import SkillListing
from models.user import User, UserRole
from schemas.agent import (
    AgentCreateRequest,
    AgentResponse,
    AgentRestoreRequest,
    AgentSummary,
    AgentUpdateRequest,
)
from services.cache import invalidate_namespace
from services.config_generator import validate_mcp_command
from services.harness_capability_inference import compute_supported_harnesses, infer_required_features
from services.registry_fork import agent_fork_counts, provenance_for, provenance_for_many
from services.registry_namespace import identity_exists, slugify
from services.registry_telemetry import emit_registry_event
from services.teamspace import resolve_publish_target

from ._router import router
from .helpers import (
    _agent_to_response,
    _load_agent,
    _resolve_component_identities,
    _resolve_component_names,
    _resolve_component_statuses,
    _validate_mcp_ids,
)

# Fields stored on the agent version rather than the agent identity.
_VERSION_OWNED_FIELDS = (
    "version",
    "version_bump_type",
    "description",
    "prompt",
    "model_name",
    "model_config_json",
    "models_by_harness",
    "supported_harnesses",
    "external_mcps",
    "components",
    "mcp_server_ids",
)
_EDITABLE_VERSION_STATUSES = frozenset({AgentStatus.draft, AgentStatus.pending, AgentStatus.rejected})


@router.post("", response_model=AgentResponse)
async def create_agent(
    req: AgentCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("creating agent")
    if not req.description:
        raise HTTPException(status_code=422, detail="Description must not be empty")

    target = await resolve_publish_target(
        db,
        current_user,
        req.name,
        team_id=req.team_id,
        visibility=req.visibility,
    )

    # If `components` is provided, it supersedes legacy `mcp_server_ids`
    if req.components:
        req.mcp_server_ids = []

    # Validate legacy mcp_server_ids
    await _validate_mcp_ids(
        req.mcp_server_ids,
        db,
        current_user=current_user,
        target_team_id=target.team_id if target.visibility == "team" else None,
        enforce_target=True,
    )

    # Validate new components field (component_type already validated by Pydantic Literal)
    if req.components:
        from services.agent_resolver import validate_component_ids

        errors = await validate_component_ids(
            [{"component_type": c.component_type, "component_id": c.component_id} for c in req.components],
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

    # Validate external MCP commands for shell safety

    for _mcp in req.external_mcps or []:
        _cmd = _mcp.get("command", "") if isinstance(_mcp, dict) else getattr(_mcp, "command", "")
        _args = _mcp.get("args", []) if isinstance(_mcp, dict) else getattr(_mcp, "args", [])
        try:
            validate_mcp_command(_cmd, _args or [])
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid MCP command: {e}")

    # The database remains the source of truth; this pre-check gives a clean 409.
    existing = await db.execute(
        select(Agent.id).where(
            Agent.namespace == target.namespace,
            Agent.slug == target.slug,
            Agent.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"Agent '{target.namespace}/{target.slug}' already exists")

    agent = Agent(
        name=req.name,
        namespace=target.namespace,
        slug=target.slug,
        owner=target.owner if target.team_id else (req.owner or current_user.username or current_user.email),
        category=req.category,
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
        status=AgentStatus.approved if target.auto_approve else AgentStatus.pending,
        released_by=current_user.id,
        reviewed_by=current_user.id if target.auto_approve else None,
        reviewed_at=datetime.now(UTC) if target.auto_approve else None,
        success_criteria=req.success_criteria.model_dump() if req.success_criteria else None,
    )
    db.add(version)
    await db.flush()

    agent.latest_version_id = version.id

    # Legacy mcp_server_ids come first, then typed components, each pinned to an
    # exact release (an explicit version, or the latest approved one).
    from services.agent_lock import attach_pinned_components

    await attach_pinned_components(
        db,
        version.id,
        [{"component_type": "mcp", "component_id": mid} for mid in req.mcp_server_ids] + list(req.components),
        current_user=current_user,
    )

    # Auto-infer harness feature requirements from the request data
    # (avoid accessing agent.components which would trigger a lazy load)
    all_crefs = list(req.components) + [
        type("_Ref", (), {"component_type": "mcp", "component_id": mid})() for mid in req.mcp_server_ids
    ]
    skill_comp_ids = [c.component_id for c in all_crefs if c.component_type == "skill"]
    skill_listings_map: dict = {}
    if skill_comp_ids:
        rows = (await db.execute(select(SkillListing).where(SkillListing.id.in_(skill_comp_ids)))).scalars().all()
        skill_listings_map = {row.id: row for row in rows}

    # Build a lightweight stand-in so the inference function can iterate components
    class _AgentProxy:
        components = all_crefs
        external_mcps = version.external_mcps

    version.required_capabilities = infer_required_features(_AgentProxy(), skill_listings=skill_listings_map)
    version.inferred_supported_harnesses = compute_supported_harnesses(version.required_capabilities)

    # Flush pending AgentComponent + goal rows so the snapshot builder picks
    # them up via its own SELECTs (the relationship cache is empty).
    await db.flush()
    from services.agent_lock import lock_agent_version
    from services.agent_snapshot import build_yaml_snapshot

    await lock_agent_version(db, agent, version)
    version.yaml_snapshot = await build_yaml_snapshot(version, db)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_agents_active_namespace_slug" in str(exc.orig):
            raise HTTPException(
                status_code=409,
                detail=f"Agent '{target.namespace}/{target.slug}' already exists",
            )
        raise

    agent = await _load_agent(db, str(agent.id), prefer_user_id=current_user.id, current_user=current_user)
    name_map = await _resolve_component_names(agent.components, db)

    emit_registry_event(
        action="agent.create",
        user_id=str(current_user.id),
        user_email=current_user.email,
        user_role=current_user.role.value,
        agent_id=str(agent.id),
        resource_name=req.name,
        metadata={"agent_name": req.name, "version": req.version, "component_count": str(len(req.components))},
    )

    return _agent_to_response(
        agent, name_map, created_by_email=current_user.email, created_by_username=current_user.username
    )


@router.get("", response_model=list[AgentSummary])
async def list_agents(
    response: Response,
    search: str | None = Query(None),
    namespace: str | None = Query(None),
    category: str | None = Query(None),
    team_id: uuid.UUID | None = Query(None, description="Only agents owned by this teamspace"),
    composable_for_team_id: uuid.UUID | None = Query(
        None, description="Public agents plus this teamspace's private ones"
    ),
    public_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200, description="Page size (1-200)"),
    offset: int = Query(0, ge=0, description="Items to skip"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.debug("listing agents")
    from models.feedback import Feedback

    base_filter = (AgentVersion.status == AgentStatus.approved) & (Agent.deleted_at.is_(None))
    search_filter = None
    search_rank = None
    if search:
        search_filter, search_rank = keyword_search(
            search,
            [
                Agent.name,
                Agent.slug,
                Agent.namespace,
                Agent.owner,
                Agent.category,
                AgentVersion.description,
                AgentVersion.model_name,
            ],
            name_field=Agent.name,
        )

    # Total count for pagination header
    count_stmt = (
        select(func.count(Agent.id)).join(AgentVersion, Agent.latest_version_id == AgentVersion.id).where(base_filter)
    )
    filters = [condition for condition in (search_filter,) if condition is not None]
    if namespace:
        filters.append(Agent.namespace == namespace.strip().lower())
    if category:
        filters.append(Agent.category == category)
    count_stmt = apply_registry_scope(
        count_stmt.where(*filters),
        Agent,
        current_user,
        team_id=team_id,
        composable_for_team_id=composable_for_team_id,
        public_only=public_only,
    )
    total = (await db.execute(count_stmt)).scalar_one()
    response.headers["X-Total-Count"] = str(total)

    stmt = select(Agent).join(AgentVersion, Agent.latest_version_id == AgentVersion.id).where(base_filter, *filters)
    stmt = apply_registry_scope(
        stmt,
        Agent,
        current_user,
        team_id=team_id,
        composable_for_team_id=composable_for_team_id,
        public_only=public_only,
    )
    order_by = [Agent.created_at.desc()]
    if search_rank is not None:
        order_by.insert(0, search_rank.desc())
    result = await db.execute(stmt.order_by(*order_by).offset(offset).limit(limit))
    agents = result.scalars().all()

    # Batch-fetch average ratings
    agent_ids = [a.id for a in agents]
    rating_map: dict[uuid.UUID, float] = {}
    if agent_ids:
        rows = await db.execute(
            select(Feedback.listing_id, func.avg(Feedback.rating))
            .where(Feedback.listing_id.in_(agent_ids), Feedback.listing_type == "agent")
            .group_by(Feedback.listing_id)
        )
        rating_map = {r[0]: round(float(r[1]), 2) for r in rows.all()}

    fork_counts = await agent_fork_counts(db, agent_ids)
    provenance = await provenance_for_many(agents, current_user, db)

    # Batch-fetch creator emails and usernames
    user_ids = {a.created_by for a in agents}
    email_map: dict[uuid.UUID, str] = {}
    username_map: dict[uuid.UUID, str | None] = {}
    if user_ids:
        rows = await db.execute(select(User.id, User.email, User.username).where(User.id.in_(user_ids)))
        for r in rows.all():
            email_map[r[0]] = r[1]
            username_map[r[0]] = r[2]

    return [
        AgentSummary(
            id=a.id,
            name=a.name,
            namespace=a.namespace,
            slug=a.slug,
            qualified_name=a.qualified_name,
            version=a.version,
            description=a.description,
            owner=a.owner,
            team_id=a.team_id,
            visibility=a.visibility,
            is_private=a.is_private,
            model_name=a.model_name,
            supported_harnesses=a.supported_harnesses,
            status=a.status,
            rejection_reason=a.rejection_reason,
            download_count=a.download_count,
            average_rating=rating_map.get(a.id),
            component_count=len(a.components),
            created_by=a.created_by,
            created_by_email=email_map.get(a.created_by, "") if current_user else "",
            created_by_username=username_map.get(a.created_by),
            created_at=a.created_at,
            updated_at=a.updated_at,
            is_recommended=a.is_recommended,
            forked_from=provenance.get(a.id),
            fork_count=fork_counts.get(a.id, 0),
        )
        for a in agents
    ]


@router.get("/my", response_model=list[AgentSummary])
async def my_agents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("my_agents called")
    from models.feedback import Feedback

    stmt = (
        select(Agent)
        .where(Agent.created_by == current_user.id, Agent.deleted_at.is_(None))
        .order_by(Agent.created_at.desc())
    )
    stmt = apply_registry_scope(stmt, Agent, current_user)
    agents = (await db.execute(stmt)).scalars().all()

    agent_ids = [a.id for a in agents]
    rating_map: dict[uuid.UUID, float] = {}
    if agent_ids:
        rows = await db.execute(
            select(Feedback.listing_id, func.avg(Feedback.rating))
            .where(Feedback.listing_id.in_(agent_ids), Feedback.listing_type == "agent")
            .group_by(Feedback.listing_id)
        )
        rating_map = {r[0]: round(float(r[1]), 2) for r in rows.all()}

    fork_counts = await agent_fork_counts(db, agent_ids)
    provenance = await provenance_for_many(agents, current_user, db)
    return [
        AgentSummary(
            id=a.id,
            name=a.name,
            namespace=a.namespace,
            slug=a.slug,
            qualified_name=a.qualified_name,
            version=a.version,
            description=a.description,
            owner=a.owner,
            team_id=a.team_id,
            visibility=a.visibility,
            is_private=a.is_private,
            model_name=a.model_name,
            supported_harnesses=a.supported_harnesses,
            status=a.status,
            rejection_reason=a.rejection_reason,
            download_count=a.download_count,
            average_rating=rating_map.get(a.id),
            component_count=len(a.components),
            created_by=a.created_by,
            created_by_email=current_user.email,
            created_by_username=current_user.username,
            created_at=a.created_at,
            updated_at=a.updated_at,
            is_recommended=a.is_recommended,
            forked_from=provenance.get(a.id),
            fork_count=fork_counts.get(a.id, 0),
        )
        for a in agents
    ]


@router.get("/archived", response_model=list[AgentSummary])
async def archived_agents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    optic.debug("archived_agents called")
    from models.feedback import Feedback

    stmt = (
        select(Agent)
        .join(AgentVersion, Agent.latest_version_id == AgentVersion.id)
        .where(AgentVersion.status == AgentStatus.archived, Agent.deleted_at.is_(None))
        .order_by(Agent.created_at.desc())
    )
    agents = (await db.execute(stmt)).scalars().all()

    agent_ids = [a.id for a in agents]
    rating_map: dict[uuid.UUID, float] = {}
    if agent_ids:
        rows = await db.execute(
            select(Feedback.listing_id, func.avg(Feedback.rating))
            .where(Feedback.listing_id.in_(agent_ids), Feedback.listing_type == "agent")
            .group_by(Feedback.listing_id)
        )
        rating_map = {r[0]: round(float(r[1]), 2) for r in rows.all()}

    user_ids = {a.created_by for a in agents}
    email_map: dict[uuid.UUID, str] = {}
    username_map: dict[uuid.UUID, str | None] = {}
    if user_ids:
        rows = await db.execute(select(User.id, User.email, User.username).where(User.id.in_(user_ids)))
        for r in rows.all():
            email_map[r[0]] = r[1]
            username_map[r[0]] = r[2]

    return [
        AgentSummary(
            id=a.id,
            name=a.name,
            namespace=a.namespace,
            slug=a.slug,
            qualified_name=a.qualified_name,
            version=a.version,
            description=a.description,
            owner=a.owner,
            team_id=a.team_id,
            visibility=a.visibility,
            is_private=a.is_private,
            model_name=a.model_name,
            supported_harnesses=a.supported_harnesses,
            status=a.status,
            rejection_reason=a.rejection_reason,
            download_count=a.download_count,
            average_rating=rating_map.get(a.id),
            component_count=len(a.components),
            created_by=a.created_by,
            created_by_email=email_map.get(a.created_by, ""),
            created_by_username=username_map.get(a.created_by),
            created_at=a.created_at,
            updated_at=a.updated_at,
            is_recommended=a.is_recommended,
        )
        for a in agents
    ]


@router.get("/deleted", response_model=list[AgentSummary])
async def deleted_agents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("deleted_agents called")
    from models.feedback import Feedback

    is_admin = ROLE_HIERARCHY.get(current_user.role, 999) <= ROLE_HIERARCHY[UserRole.admin]
    stmt = select(Agent).where(Agent.deleted_at.is_not(None)).order_by(Agent.deleted_at.desc())
    if not is_admin:
        stmt = stmt.where(Agent.created_by == current_user.id)

    agents = (await db.execute(stmt)).scalars().all()

    agent_ids = [a.id for a in agents]
    rating_map: dict[uuid.UUID, float] = {}
    if agent_ids:
        rows = await db.execute(
            select(Feedback.listing_id, func.avg(Feedback.rating))
            .where(Feedback.listing_id.in_(agent_ids), Feedback.listing_type == "agent")
            .group_by(Feedback.listing_id)
        )
        rating_map = {r[0]: round(float(r[1]), 2) for r in rows.all()}

    user_ids = {a.created_by for a in agents}
    email_map: dict[uuid.UUID, str] = {}
    username_map: dict[uuid.UUID, str | None] = {}
    if user_ids:
        rows = await db.execute(select(User.id, User.email, User.username).where(User.id.in_(user_ids)))
        for r in rows.all():
            email_map[r[0]] = r[1]
            username_map[r[0]] = r[2]

    return [
        AgentSummary(
            id=a.id,
            name=a.name,
            namespace=a.namespace,
            slug=a.slug,
            qualified_name=a.qualified_name,
            version=a.version,
            description=a.description,
            owner=a.owner,
            team_id=a.team_id,
            visibility=a.visibility,
            is_private=a.is_private,
            model_name=a.model_name,
            supported_harnesses=a.supported_harnesses,
            status=a.status,
            rejection_reason=a.rejection_reason,
            download_count=a.download_count,
            average_rating=rating_map.get(a.id),
            component_count=len(a.components),
            created_by=a.created_by,
            created_by_email=email_map.get(a.created_by, ""),
            created_by_username=username_map.get(a.created_by),
            created_at=a.created_at,
            deleted_at=a.deleted_at,
            updated_at=a.updated_at,
            is_recommended=a.is_recommended,
        )
        for a in agents
    ]


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.debug("fetching agent details")
    agent = await _load_agent(
        db,
        agent_id,
        prefer_user_id=current_user.id if current_user else None,
        current_user=current_user,
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    perm = get_effective_agent_permission(agent, current_user)
    if perm == "none":
        raise HTTPException(status_code=403, detail="Insufficient permissions to view this agent")
    name_map = await _resolve_component_names(agent.components, db)
    identity_map = await _resolve_component_identities(agent.components, db)
    status_map = await _resolve_component_statuses(agent.components, db)
    user_row = (await db.execute(select(User.email, User.username).where(User.id == agent.created_by))).first()
    return _agent_to_response(
        agent,
        name_map,
        created_by_email=user_row[0] if user_row and current_user else "",
        created_by_username=user_row[1] if user_row else None,
        user_permission=perm,
        status_map=status_map,
        identity_map=identity_map,
        forked_from=await provenance_for(agent, current_user, db),
        fork_count=(await agent_fork_counts(db, [agent.id])).get(agent.id, 0),
    )


@router.get("/{agent_id}/version-suggestions")
async def version_suggestions(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("agent_id={}", agent_id)
    agent = await _load_agent(
        db,
        agent_id,
        prefer_user_id=current_user.id,
        current_user=current_user,
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if get_effective_agent_permission(agent, current_user) == "none":
        raise HTTPException(status_code=403, detail="Insufficient permissions to view this agent")
    # Use the highest existing version (including pending) to avoid duplicate suggestions
    from models.agent import AgentVersion
    from services.versioning import suggest_versions

    all_versions_stmt = (
        select(AgentVersion.version).where(AgentVersion.agent_id == agent.id).order_by(AgentVersion.created_at.desc())
    )
    all_versions_result = await db.execute(all_versions_stmt)
    all_versions = [v for (v,) in all_versions_result.all()]

    # Find the highest semver among all existing versions
    from services.versioning import parse_semver

    highest = agent.version or "0.0.0"
    for v in all_versions:
        parsed = parse_semver(v)
        if parsed and parsed > (parse_semver(highest) or (0, 0, 0)):
            highest = v

    return {"current": highest, "suggestions": suggest_versions(highest)}


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    req: AgentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("agent_id={}", agent_id)
    agent = await _load_agent(db, agent_id, prefer_user_id=current_user.id, current_user=current_user)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    is_admin = ROLE_HIERARCHY.get(current_user.role, 999) <= ROLE_HIERARCHY[UserRole.admin]

    perm = get_effective_agent_permission(agent, current_user)
    if perm not in ("owner", "edit") and not is_admin:
        raise HTTPException(status_code=403, detail="Not the agent owner or editor")

    # Visibility and teamspace are not editable here. Echoing back the current
    # value is a no-op; asking for a different one is refused rather than
    # silently dropped, because both have their own authorized flows.
    if req.visibility is not None and (req.visibility == "team") != bool(agent.is_private):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Visibility cannot be changed here. Use PATCH /api/v1/registry/agent/{agent.id}/visibility instead."
            ),
        )
    if req.team_id is not None and req.team_id != agent.team_id:
        raise HTTPException(
            status_code=422,
            detail="Teamspace cannot be changed here. Recreate the agent under the target teamspace.",
        )

    # An approved version is a lock that installs and pins depend on. Its contents
    # never change in place; edits become a new release that goes through review.
    latest = agent.latest_version
    edits_version = "success_criteria" in req.model_fields_set or any(
        getattr(req, field) is not None for field in _VERSION_OWNED_FIELDS
    )
    if edits_version and latest is not None and latest.status not in _EDITABLE_VERSION_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Version {latest.version} is {latest.status.value} and cannot be edited in place. "
                f"Release a new version instead: observal agent release {agent.namespace}/{agent.slug} --bump patch "
                f"(POST /api/v1/agents/{agent.id}/versions)."
            ),
        )

    if req.version_bump_type and req.version is None:
        from services.versioning import bump_version

        req.version = bump_version(agent.version, req.version_bump_type)

    if req.name is not None and req.name != agent.name:
        existing = await db.execute(
            select(Agent.id).where(Agent.name == req.name, Agent.deleted_at.is_(None), Agent.id != agent.id)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail=f"An active agent named '{req.name}' already exists.")

    for field in (
        "name",
        "version",
        "description",
        "category",
        "owner",
        "prompt",
        "model_name",
        "model_config_json",
        "models_by_harness",
        "supported_harnesses",
    ):
        val = getattr(req, field)
        if val is not None:
            setattr(agent, field, val)

    if req.external_mcps is not None:
        for _mcp in req.external_mcps:
            _cmd = getattr(_mcp, "command", "")
            _args = getattr(_mcp, "args", [])
            try:
                validate_mcp_command(_cmd, _args or [])
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"Invalid MCP command: {e}")
        agent.external_mcps = [m.model_dump() for m in req.external_mcps]

    if "success_criteria" in req.model_fields_set:
        if not agent.latest_version:
            raise HTTPException(status_code=400, detail="Agent has no version to update")
        agent.latest_version.success_criteria = (
            req.success_criteria.model_dump() if req.success_criteria is not None else None
        )

    if req.components is not None:
        # New components field replaces ALL components (type validated by Pydantic Literal)
        from services.agent_resolver import validate_component_ids

        if not agent.latest_version:
            raise HTTPException(status_code=400, detail="Agent has no version to update components on")

        errors = await validate_component_ids(
            [{"component_type": c.component_type, "component_id": c.component_id} for c in req.components],
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
        from services.agent_lock import attach_pinned_components

        # Replace every component on the latest version. Components that were
        # already attached keep their pins unless the request names a version.
        version_id = agent.latest_version.id
        old_comps = (
            (await db.execute(select(AgentComponent).where(AgentComponent.agent_version_id == version_id)))
            .scalars()
            .all()
        )
        for comp in old_comps:
            await db.delete(comp)
        if old_comps:
            await db.flush()
        await attach_pinned_components(db, version_id, req.components, previous=old_comps, current_user=current_user)
    elif req.mcp_server_ids is not None:
        # Legacy: only update MCP components
        if not agent.latest_version:
            raise HTTPException(status_code=400, detail="Agent has no version to update components on")

        await _validate_mcp_ids(
            req.mcp_server_ids,
            db,
            current_user=current_user,
            target_team_id=agent.team_id if agent.is_private else None,
            enforce_target=True,
        )
        from services.agent_lock import attach_pinned_components

        version_id = agent.latest_version.id
        old_comps = (
            (
                await db.execute(
                    select(AgentComponent).where(
                        AgentComponent.agent_version_id == version_id,
                        AgentComponent.component_type == "mcp",
                    )
                )
            )
            .scalars()
            .all()
        )
        for comp in old_comps:
            await db.delete(comp)
        if old_comps:
            await db.flush()
        await attach_pinned_components(
            db,
            version_id,
            [{"component_type": "mcp", "component_id": mid} for mid in req.mcp_server_ids],
            previous=old_comps,
            current_user=current_user,
        )

    # Re-infer harness features only when components or external_mcps changed
    if req.components is not None or req.mcp_server_ids is not None or req.external_mcps is not None:
        if not agent.latest_version:
            raise HTTPException(status_code=400, detail="Agent has no version to update features on")
        current_comps = (
            (await db.execute(select(AgentComponent).where(AgentComponent.agent_version_id == agent.latest_version.id)))
            .scalars()
            .all()
        )
        skill_comp_ids = [c.component_id for c in current_comps if c.component_type == "skill"]
        skill_listings_map_update: dict = {}
        if skill_comp_ids:
            rows = (await db.execute(select(SkillListing).where(SkillListing.id.in_(skill_comp_ids)))).scalars().all()
            skill_listings_map_update = {row.id: row for row in rows}

        class _AgentProxy:
            components = current_comps
            external_mcps = agent.external_mcps

        agent.required_capabilities = infer_required_features(_AgentProxy(), skill_listings=skill_listings_map_update)
        agent.inferred_supported_harnesses = compute_supported_harnesses(agent.required_capabilities)

    snapshot_fields = (
        "version",
        "description",
        "prompt",
        "model_name",
        "model_config_json",
        "models_by_harness",
        "supported_harnesses",
    )
    snapshot_needs_refresh = (
        req.version_bump_type is not None
        or any(getattr(req, field) is not None for field in snapshot_fields)
        or req.external_mcps is not None
        or req.components is not None
        or req.mcp_server_ids is not None
        or "success_criteria" in req.model_fields_set
    )
    if snapshot_needs_refresh:
        await db.flush()
        from services.agent_lock import lock_agent_version
        from services.agent_snapshot import build_yaml_snapshot

        await lock_agent_version(db, agent, agent.latest_version)
        agent.latest_version.yaml_snapshot = await build_yaml_snapshot(agent.latest_version, db)

    await db.commit()
    agent = await _load_agent(db, str(agent.id), prefer_user_id=current_user.id, current_user=current_user)
    name_map = await _resolve_component_names(agent.components, db)

    emit_registry_event(
        action="agent.update",
        user_id=str(current_user.id),
        user_email=current_user.email,
        user_role=current_user.role.value,
        agent_id=str(agent.id),
        resource_name=agent.name,
    )

    return _agent_to_response(
        agent, name_map, created_by_email=current_user.email, created_by_username=current_user.username
    )


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("soft deleting agent")

    agent = await _load_agent(
        db, agent_id, prefer_user_id=current_user.id, current_user=current_user, include_all_statuses=True
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    is_admin = ROLE_HIERARCHY.get(current_user.role, 999) <= ROLE_HIERARCHY[UserRole.admin]
    perm = get_effective_agent_permission(agent, current_user)
    if perm != "owner" and not is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    agent.deleted_at = datetime.now(UTC)
    await db.commit()
    await invalidate_namespace("dashboard")

    emit_registry_event(
        action="agent.delete",
        user_id=str(current_user.id),
        user_email=current_user.email,
        user_role=current_user.role.value,
        agent_id=str(agent.id),
        resource_name=agent.name,
    )

    return {"deleted": str(agent.id), "name": agent.name, "deleted_at": agent.deleted_at.isoformat()}


@router.patch("/{agent_id}/restore")
async def restore_deleted_agent(
    agent_id: str,
    req: AgentRestoreRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("agent_id={}", agent_id)
    agent = await _load_agent(
        db,
        agent_id,
        prefer_user_id=current_user.id,
        current_user=current_user,
        include_all_statuses=True,
        include_deleted=True,
    )
    if not agent or agent.deleted_at is None:
        raise HTTPException(status_code=404, detail="Deleted agent not found")
    is_admin = ROLE_HIERARCHY.get(current_user.role, 999) <= ROLE_HIERARCHY[UserRole.admin]
    perm = get_effective_agent_permission(agent, current_user)
    if perm != "owner" and not is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    restore_name = req.name if req and req.name else agent.name
    restore_slug = slugify(restore_name) if req and req.name else agent.slug
    if await identity_exists(db, Agent, agent.namespace, restore_slug, exclude_id=agent.id):
        raise HTTPException(
            status_code=409,
            detail=f"Agent '{agent.namespace}/{restore_slug}' already exists. Restore with a new name.",
        )

    agent.name = restore_name
    agent.slug = restore_slug
    agent.deleted_at = None
    await db.commit()
    await invalidate_namespace("dashboard")

    emit_registry_event(
        action="agent.restore",
        user_id=str(current_user.id),
        user_email=current_user.email,
        user_role=current_user.role.value,
        agent_id=str(agent.id),
        resource_name=agent.name,
    )

    return {"id": str(agent.id), "name": agent.name, "status": agent.status}


@router.patch("/{agent_id}/archive")
async def archive_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("agent_id={}", agent_id)
    agent = await _load_agent(db, agent_id, prefer_user_id=current_user.id, current_user=current_user)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    is_admin = ROLE_HIERARCHY.get(current_user.role, 999) <= ROLE_HIERARCHY[UserRole.admin]
    if agent.created_by != current_user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Only the owner or an admin can archive this agent")
    if agent.status != AgentStatus.approved:
        raise HTTPException(status_code=400, detail="Only approved agents can be archived")
    if not agent.latest_version_id:
        raise HTTPException(status_code=400, detail="Agent has no version")
    await db.execute(
        update(AgentVersion).where(AgentVersion.id == agent.latest_version_id).values(status=AgentStatus.archived)
    )
    await db.commit()

    emit_registry_event(
        action="agent.archive",
        user_id=str(current_user.id),
        user_email=current_user.email,
        user_role=current_user.role.value,
        agent_id=str(agent.id),
        resource_name=agent.name,
    )

    return {"id": str(agent.id), "name": agent.name, "status": "archived"}


@router.patch("/{agent_id}/unarchive")
async def unarchive_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("agent_id={}", agent_id)
    agent = await _load_agent(
        db, agent_id, prefer_user_id=current_user.id, current_user=current_user, include_all_statuses=True
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    is_admin = ROLE_HIERARCHY.get(current_user.role, 999) <= ROLE_HIERARCHY[UserRole.admin]
    if agent.created_by != current_user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Only the owner or an admin can unarchive this agent")
    if agent.status != AgentStatus.archived:
        raise HTTPException(status_code=400, detail="Agent is not archived")
    if not agent.latest_version_id:
        raise HTTPException(status_code=400, detail="Agent has no version")
    await db.execute(
        update(AgentVersion).where(AgentVersion.id == agent.latest_version_id).values(status=AgentStatus.approved)
    )
    await db.commit()

    emit_registry_event(
        action="agent.unarchive",
        user_id=str(current_user.id),
        user_email=current_user.email,
        user_role=current_user.role.value,
        agent_id=str(agent.id),
        resource_name=agent.name,
    )

    return {"id": str(agent.id), "name": agent.name, "status": "approved"}
