# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Owner-authorized component observability: presence, activity and coverage.

Contract decisions:
- ``type=mcp`` is supported. ``skill`` and ``hook`` answer **501** with an
  explicit ``{"status": "unsupported"}`` body (never zero counts); any other
  type is 422.
- Access requires listing visibility (else 404, preserving private-listing
  semantics) *and* owner-level permission (owner, co-author or admin; else 403).
  Both checks run before any telemetry query.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from loguru import logger as optic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, get_effective_component_permission, resolve_visible_listing
from models.mcp import McpListing, McpVersion
from models.user import User
from observal_shared.migration.constants import DEFAULT_PROJECT_ID
from schemas.component_activity import (
    ActivitySessionsResponse,
    ActivitySummaryResponse,
    ComponentRef,
    UnsupportedComponentType,
)
from services.component_activity import queries

router = APIRouter(prefix="/api/v1/components", tags=["component-activity"])

_SUPPORTED = {"mcp": (McpListing, McpVersion)}
_NOT_YET_SUPPORTED = frozenset({"skill", "hook"})


def _unsupported(component_type: str) -> JSONResponse:
    body = UnsupportedComponentType(
        type=component_type,
        detail=f"Activity observability is not implemented for {component_type} components",
    )
    return JSONResponse(status_code=501, content=body.model_dump())


async def _authorize(
    component_type: str, identifier: str, version_id: UUID | None, db: AsyncSession, user: User
) -> tuple[object, ComponentRef, str | None]:
    """Resolve and authorize before any ClickHouse work."""
    if component_type not in _SUPPORTED:
        raise HTTPException(status_code=422, detail="Unknown component type")
    listing_model, version_model = _SUPPORTED[component_type]
    listing = await resolve_visible_listing(listing_model, identifier, db, user)
    if listing is None:
        raise HTTPException(status_code=404, detail="Component not found")
    if get_effective_component_permission(listing, user) != "owner":
        raise HTTPException(status_code=403, detail="Only owners, co-authors and admins can view component activity")
    version_label = None
    if version_id is not None:
        version_label = await db.scalar(
            select(version_model.version).where(version_model.id == version_id, version_model.listing_id == listing.id)
        )
        if version_label is None:
            raise HTTPException(status_code=404, detail="Component version not found for this listing")
    ref = ComponentRef(
        type=component_type,
        id=str(listing.id),
        qualified_name=f"{listing.namespace}/{listing.slug}",
        component_version_id=str(version_id) if version_id else None,
    )
    return listing, ref, version_label


def _period(days: int) -> tuple[datetime, datetime]:
    end = datetime.now(UTC)
    return end - timedelta(days=days), end


@router.get(
    "/{component_type}/{identifier:path}/activity/summary",
    response_model=ActivitySummaryResponse,
    responses={501: {"model": UnsupportedComponentType}},
)
async def component_activity_summary(
    component_type: str,
    identifier: str,
    period_days: int = Query(14, ge=1, le=90),
    component_version_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if component_type in _NOT_YET_SUPPORTED:
        return _unsupported(component_type)
    listing, ref, version_label = await _authorize(component_type, identifier, component_version_id, db, current_user)
    start, end = _period(period_days)
    optic.debug("component activity summary: type={}, listing={}, days={}", component_type, listing.id, period_days)
    summary = await queries.activity_summary(
        DEFAULT_PROJECT_ID,
        component_type,
        str(listing.id),
        ref.component_version_id,
        (start, end),
        component_version=version_label,
    )
    return ActivitySummaryResponse(
        component=ref,
        period_days=period_days,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        **summary,
    )


@router.get(
    "/{component_type}/{identifier:path}/activity/sessions",
    response_model=ActivitySessionsResponse,
    responses={501: {"model": UnsupportedComponentType}},
)
async def component_activity_sessions(
    component_type: str,
    identifier: str,
    period_days: int = Query(14, ge=1, le=90),
    component_version_id: UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=2048),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if component_type in _NOT_YET_SUPPORTED:
        return _unsupported(component_type)
    # Authorize first, including the version's listing membership. A cursor is
    # signed and scoped to this user, listing, version and period.
    listing, ref, _ = await _authorize(component_type, identifier, component_version_id, db, current_user)
    scope = (
        DEFAULT_PROJECT_ID,
        str(current_user.id),
        component_type,
        str(listing.id),
        ref.component_version_id or "",
        str(period_days),
    )
    key = None
    period = _period(period_days)
    if cursor is not None:
        try:
            period_end, key = queries.decode_cursor(cursor, scope=scope)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid cursor") from error
        period = (period_end - timedelta(days=period_days), period_end)
    optic.debug("component activity sessions: type={}, listing={}, limit={}", component_type, listing.id, limit)
    sessions, next_cursor = await queries.activity_sessions(
        DEFAULT_PROJECT_ID,
        component_type,
        str(listing.id),
        ref.component_version_id,
        period,
        limit=limit,
        cursor=key,
        cursor_scope=scope,
    )
    return ActivitySessionsResponse(component=ref, period_days=period_days, sessions=sessions, next_cursor=next_cursor)
