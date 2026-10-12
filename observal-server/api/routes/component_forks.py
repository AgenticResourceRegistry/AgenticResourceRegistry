# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared component fork endpoints and visibility-safe response population."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import (
    commit_or_name_conflict,
    get_db,
    get_effective_component_permission,
    get_registry_user,
    may_view_unapproved,
    require_role,
    resolve_visible_listing,
)
from models.mcp import ListingStatus
from models.user import User, UserRole
from schemas.fork import ForkProvenance, ForkRequest
from schemas.hook import HookListingResponse, HookListingSummary
from schemas.mcp import McpListingResponse, McpListingSummary
from schemas.prompt import PromptListingResponse, PromptListingSummary
from schemas.sandbox import SandboxListingResponse, SandboxListingSummary
from schemas.skill import SkillListingResponse, SkillListingSummary
from services import dynamic_settings
from services.fork_diff import fork_version_diff
from services.registry_fork import (
    ForkRequestSpec,
    component_fork_counts,
    fork_component,
    provenance_for,
    provenance_for_many,
    public_component_fork_condition,
)
from services.registry_telemetry import emit_registry_event

RESPONSE_MODELS = {
    "mcp": McpListingResponse,
    "skill": SkillListingResponse,
    "hook": HookListingResponse,
    "prompt": PromptListingResponse,
    "sandbox": SandboxListingResponse,
}
SUMMARY_MODELS = {
    "mcp": McpListingSummary,
    "skill": SkillListingSummary,
    "hook": HookListingSummary,
    "prompt": PromptListingSummary,
    "sandbox": SandboxListingSummary,
}


_UNRESOLVED = object()


async def component_response(
    listing, kind: str, current_user, db, *, count: int = 0, summary: bool = False, provenance=_UNRESOLVED
):
    model = SUMMARY_MODELS[kind] if summary else RESPONSE_MODELS[kind]
    return await attach_fork_info(
        model.model_validate(listing), listing, kind, current_user, db, count=count, provenance=provenance
    )


async def attach_fork_info(
    response, listing, kind: str, current_user, db, *, count: int | None = None, provenance=_UNRESOLVED
):
    if count is None:
        from services.registry_fork import COMPONENT_MODELS

        listing_model, version_model = COMPONENT_MODELS[kind]
        count = (await component_fork_counts(db, listing_model, version_model, [listing.id])).get(listing.id, 0)
    if provenance is _UNRESOLVED:
        provenance = await provenance_for(listing, current_user, db)
    response.forked_from = ForkProvenance.model_validate(provenance) if provenance else None
    response.fork_count = count
    return response


async def component_responses(listings, kind: str, current_user, db, *, summary: bool = True):
    from services.registry_fork import COMPONENT_MODELS

    listing_model, version_model = COMPONENT_MODELS[kind]
    counts = await component_fork_counts(db, listing_model, version_model, [item.id for item in listings])
    provenance = await provenance_for_many(listings, current_user, db)
    return [
        await component_response(
            item, kind, current_user, db, count=counts.get(item.id, 0), summary=summary, provenance=provenance[item.id]
        )
        for item in listings
    ]


def create_fork_router(component_type: str, listing_model, version_model) -> APIRouter:
    router = APIRouter()
    response_model = RESPONSE_MODELS[component_type]

    @router.post("/{listing_id}/fork", response_model=response_model)
    async def create_component_fork(
        listing_id: str,
        req: ForkRequest,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(require_role(UserRole.user)),
    ):
        if not await dynamic_settings.get_bool("registry.fork.enabled"):
            raise HTTPException(status_code=403, detail="Forking is disabled by your administrator")
        source = await resolve_visible_listing(listing_model, listing_id, db, current_user)
        if source is None or (
            source.status != ListingStatus.approved
            and not may_view_unapproved(get_effective_component_permission(source, current_user), current_user)
        ):
            raise HTTPException(status_code=404, detail="Listing not found")
        result = await fork_component(
            db, component_type, source, ForkRequestSpec(**req.model_dump()), current_user=current_user
        )
        await commit_or_name_conflict(db, component_type)
        fork = await resolve_visible_listing(listing_model, str(result.entity.id), db, current_user)
        emit_registry_event(
            action=f"{component_type}.fork",
            resource_type=component_type,
            user_id=str(current_user.id),
            user_email=current_user.email,
            user_role=current_user.role.value,
            agent_id=str(fork.id),
            resource_name=fork.name,
            metadata={
                "source_id": str(source.id),
                "source_ref": result.entity.forked_from_ref,
                "base_version": next(
                    v.version for v in source.versions if v.id == result.entity.forked_from_version_id
                ),
                "target": fork.qualified_name,
            },
        )
        response = await component_response(fork, component_type, current_user, db)
        response.user_permission = get_effective_component_permission(fork, current_user)
        response.warnings = result.warnings
        return response

    @router.get("/{listing_id}/fork-diff")
    async def component_fork_diff(
        listing_id: str,
        version: str | None = Query(None, max_length=50),
        db: AsyncSession = Depends(get_db),
        current_user: User | None = Depends(get_registry_user),
    ):
        if not await dynamic_settings.get_bool("registry.fork.enabled"):
            raise HTTPException(status_code=403, detail="Forking is disabled by your administrator")
        fork = await resolve_visible_listing(listing_model, listing_id, db, current_user)
        if fork is None:
            raise HTTPException(status_code=404, detail="Listing not found")
        return await fork_version_diff(db, fork, component_type, current_user, version)

    @router.get("/{listing_id}/forks")
    async def list_component_forks(
        listing_id: str,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        db: AsyncSession = Depends(get_db),
        current_user: User | None = Depends(get_registry_user),
    ):
        if not await dynamic_settings.get_bool("registry.fork.enabled"):
            raise HTTPException(status_code=403, detail="Forking is disabled by your administrator")
        source = await resolve_visible_listing(listing_model, listing_id, db, current_user)
        if source is None or (
            source.status != ListingStatus.approved
            and not may_view_unapproved(get_effective_component_permission(source, current_user), current_user)
        ):
            raise HTTPException(status_code=404, detail="Listing not found")
        predicate = (listing_model.forked_from_id == source.id) & public_component_fork_condition(
            listing_model, version_model
        )
        statement = (
            select(listing_model)
            .join(version_model, listing_model.latest_version_id == version_model.id)
            .where(predicate)
        )
        total = (
            await db.scalar(
                select(func.count(listing_model.id))
                .select_from(listing_model)
                .join(version_model, listing_model.latest_version_id == version_model.id)
                .where(predicate)
            )
            or 0
        )
        result = await db.execute(statement.order_by(listing_model.created_at.desc()).offset(offset).limit(limit))
        listings = result.scalars().all()
        items = await component_responses(listings, component_type, current_user, db)
        return {"items": items, "total": total, "page": offset // limit + 1, "page_size": limit}

    return router
