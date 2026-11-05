# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Component source CRUD and sync endpoints."""

import asyncio
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger as optic
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, require_role
from models.component_source import ComponentSource
from models.team import TeamMembership, TeamRole
from models.user import User, UserRole
from schemas.component_source import (
    ComponentSourceCreate,
    ComponentSourceResponse,
    SyncResponse,
)
from services.git_mirror_service import sync_source
from services.teamspace import is_admin, resolve_publish_target, team_membership

router = APIRouter(prefix="/api/v1/component-sources", tags=["component-sources"])


async def _source_visible(source: ComponentSource, current_user: User, db: AsyncSession) -> bool:
    if source.is_public or is_admin(current_user):
        return True
    if source.team_id is None:
        return False
    return (
        await db.scalar(
            select(TeamMembership.id).where(
                TeamMembership.team_id == source.team_id,
                TeamMembership.user_id == current_user.id,
            )
        )
        is not None
    )


@router.post("", response_model=ComponentSourceResponse, status_code=201)
async def add_source(
    req: ComponentSourceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    # Detect provider from URL
    optic.trace("req={}", req)
    provider = "github"
    url_lower = req.url.lower()
    if "gitlab" in url_lower:
        provider = "gitlab"
    elif "bitbucket" in url_lower:
        provider = "bitbucket"

    target = await resolve_publish_target(
        db,
        current_user,
        "source",
        team_id=req.team_id,
        visibility=req.visibility,
    )
    source = ComponentSource(
        url=req.url,
        provider=provider,
        component_type=req.component_type,
        is_public=target.visibility == "public",
        team_id=target.team_id,
    )
    try:
        db.add(source)
        await db.commit()
        await db.refresh(source)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Source with this URL and component type already exists")
    return ComponentSourceResponse.model_validate(source)


@router.get("", response_model=list[ComponentSourceResponse])
async def list_sources(
    component_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("component_source list")
    stmt = select(ComponentSource)
    if component_type:
        stmt = stmt.where(ComponentSource.component_type == component_type)
    if not is_admin(current_user):
        member = (
            select(TeamMembership.id)
            .where(TeamMembership.team_id == ComponentSource.team_id, TeamMembership.user_id == current_user.id)
            .correlate(ComponentSource)
            .exists()
        )
        stmt = stmt.where(
            or_(
                ComponentSource.is_public == True,  # noqa: E712
                and_(ComponentSource.is_public == False, member),  # noqa: E712
            )
        )
    result = await db.execute(stmt.order_by(ComponentSource.created_at.desc()))
    sources = result.scalars().all()
    return [ComponentSourceResponse.model_validate(s) for s in sources]


@router.get("/{source_id}", response_model=ComponentSourceResponse)
async def get_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("source_id={}", source_id)
    source = await db.get(ComponentSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if not await _source_visible(source, current_user, db):
        raise HTTPException(status_code=404, detail="Source not found")
    return ComponentSourceResponse.model_validate(source)


@router.post("/{source_id}/sync", response_model=SyncResponse)
async def trigger_sync(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    optic.trace("source_id={}", source_id)
    source = await db.get(ComponentSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    source.sync_status = "syncing"
    await db.commit()

    result = await asyncio.to_thread(sync_source, source.url, source.component_type)

    source.last_synced_at = datetime.now(UTC)
    if result.success:
        source.sync_status = "success"
        source.sync_error = None
    else:
        source.sync_status = "failed"
        source.sync_error = result.error

    await db.commit()
    await db.refresh(source)
    return SyncResponse(
        source_id=source.id,
        status=source.sync_status,
        components_found=len(result.components),
        commit_sha=result.commit_sha,
        error=result.error or None,
    )


@router.delete("/{source_id}")
async def delete_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("component_source delete")
    source = await db.get(ComponentSource, source_id)
    if not source or not await _source_visible(source, current_user, db):
        raise HTTPException(status_code=404, detail="Source not found")
    if not is_admin(current_user):
        membership = await team_membership(db, source.team_id, current_user.id) if source.team_id else None
        if not membership or membership.role != TeamRole.owner:
            raise HTTPException(status_code=403, detail="Only teamspace owners and admins can delete this source")
    await db.delete(source)
    await db.commit()
    return {"deleted": str(source_id)}
