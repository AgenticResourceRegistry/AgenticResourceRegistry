# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Fork an approved agent into a provenance-linked independent draft."""

import uuid

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import (
    commit_or_name_conflict,
    get_db,
    get_effective_agent_permission,
    get_registry_user,
    may_view_unapproved,
    require_role,
)
from models.agent import Agent, AgentStatus, AgentVersion
from models.user import User, UserRole
from schemas.agent import AgentResponse, AgentSummary
from schemas.fork import ForkRequest
from services import dynamic_settings
from services.fork_diff import fork_version_diff
from services.registry_fork import (
    ForkRequestSpec,
    agent_fork_counts,
    fork_agent,
    provenance_for,
    provenance_for_many,
    public_agent_fork_condition,
)
from services.registry_telemetry import emit_registry_event

from ._router import router
from .helpers import _agent_to_response, _load_agent


@router.post("/{agent_id}/fork", response_model=AgentResponse)
async def create_agent_fork(
    agent_id: str,
    req: ForkRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    if not await dynamic_settings.get_bool("registry.fork.enabled"):
        raise HTTPException(status_code=403, detail="Forking is disabled by your administrator")
    source = await _load_agent(db, agent_id, current_user=current_user, include_all_statuses=True)
    if source is None or (
        source.status != AgentStatus.approved
        and not may_view_unapproved(get_effective_agent_permission(source, current_user), current_user)
    ):
        raise HTTPException(status_code=404, detail="Agent not found")
    result = await fork_agent(db, source, ForkRequestSpec(**req.model_dump()), current_user=current_user)
    await commit_or_name_conflict(db, "agent")
    fork = await _load_agent(db, str(result.entity.id), prefer_user_id=current_user.id, current_user=current_user)
    emit_registry_event(
        action="agent.fork",
        user_id=str(current_user.id),
        user_email=current_user.email,
        user_role=current_user.role.value,
        agent_id=str(fork.id),
        resource_name=fork.name,
        metadata={
            "source_id": str(source.id),
            "source_ref": result.entity.forked_from_ref,
            "base_version": next(v.version for v in source.versions if v.id == result.entity.forked_from_version_id),
            "target": fork.qualified_name,
        },
    )
    return _agent_to_response(
        fork,
        created_by_email=current_user.email,
        created_by_username=current_user.username,
        forked_from=await provenance_for(fork, current_user, db),
        warnings=result.warnings,
    )


@router.get("/{agent_id}/fork-diff")
async def agent_fork_diff(
    agent_id: str,
    version: str | None = Query(None, max_length=50),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    if not await dynamic_settings.get_bool("registry.fork.enabled"):
        raise HTTPException(status_code=403, detail="Forking is disabled by your administrator")
    # Privacy is enforced here; version-state access (owner, reviewer in scope,
    # or approved only) is decided by fork_version_diff with a uniform 404.
    # Only exact UUIDs widen the status scope: a name lookup could otherwise
    # list other users' unapproved agents in an ambiguity error.
    try:
        uuid.UUID(agent_id)
        by_id = True
    except ValueError:
        by_id = False
    fork = await _load_agent(db, agent_id, current_user=current_user, include_all_statuses=by_id)
    if fork is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return await fork_version_diff(db, fork, "agent", current_user, version)


@router.get("/{agent_id}/forks")
async def list_agent_forks(
    agent_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    if not await dynamic_settings.get_bool("registry.fork.enabled"):
        raise HTTPException(status_code=403, detail="Forking is disabled by your administrator")
    source = await _load_agent(db, agent_id, current_user=current_user)
    if source is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    predicate = (Agent.forked_from_id == source.id) & public_agent_fork_condition()
    total = (
        await db.scalar(
            select(func.count(Agent.id)).join(AgentVersion, Agent.latest_version_id == AgentVersion.id).where(predicate)
        )
        or 0
    )
    rows = await db.execute(
        select(Agent)
        .join(AgentVersion, Agent.latest_version_id == AgentVersion.id)
        .where(predicate)
        .order_by(Agent.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    forks = rows.scalars().all()
    counts = await agent_fork_counts(db, [fork.id for fork in forks])
    provenance = await provenance_for_many(forks, current_user, db)
    items = []
    for fork in forks:
        response = _agent_to_response(
            fork,
            forked_from=provenance[fork.id],
            fork_count=counts.get(fork.id, 0),
        )
        items.append(AgentSummary.model_validate(response.model_dump()))
    return {"items": items, "total": total, "page": offset // limit + 1, "page_size": limit}
