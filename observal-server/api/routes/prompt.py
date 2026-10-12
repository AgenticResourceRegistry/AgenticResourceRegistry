# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from loguru import logger as optic
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import (
    apply_registry_scope,
    apply_visibility_filter,
    commit_or_name_conflict,
    get_db,
    get_effective_component_permission,
    get_registry_user,
    may_view_unapproved,
    require_role,
    resolve_listing,
    resolve_visible_listing,
)
from api.routes._component_archive import archive_listing, unarchive_listing
from api.routes.component_forks import component_response, component_responses, create_fork_router
from api.routes.component_versions import create_version_router
from api.search import keyword_search
from models.mcp import ListingStatus
from models.prompt import PromptListing, PromptVersion
from models.user import User, UserRole
from schemas.prompt import (
    PromptDraftRequest,
    PromptListingResponse,
    PromptListingSummary,
    PromptRenderRequest,
    PromptRenderResponse,
    PromptSubmitRequest,
    PromptUpdateRequest,
)
from services.editing_lock import _is_lock_expired, acquire_edit_lock, release_edit_lock
from services.inbox import sources as inbox
from services.registry_namespace import identity_exists
from services.teamspace import publish_auto_approves_for_entity, resolve_publish_target

router = APIRouter(prefix="/api/v1/prompts", tags=["prompts"])
_PROMPT_VARIABLE_RE = re.compile(r"\{\{\s*([^{}\r\n]*?\S)\s*\}\}")


@router.post("/submit", response_model=PromptListingResponse)
async def submit_prompt(
    req: PromptSubmitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("prompt submit: name={}", req.name)
    target = await resolve_publish_target(
        db,
        current_user,
        req.name,
        team_id=req.team_id,
        visibility=req.visibility,
    )
    if await identity_exists(db, PromptListing, target.namespace, target.slug):
        raise HTTPException(status_code=409, detail=f"Prompt '{target.namespace}/{target.slug}' already exists")

    listing = PromptListing(
        name=req.name,
        namespace=target.namespace,
        slug=target.slug,
        owner=target.owner if target.team_id else req.owner,
        submitted_by=current_user.id,
        team_id=target.team_id,
        is_private=target.visibility == "team",
    )
    db.add(listing)
    await db.flush()

    version = PromptVersion(
        listing_id=listing.id,
        version=req.version,
        description=req.description,
        category=req.category,
        template=req.template,
        variables=req.variables,
        model_hints=req.model_hints,
        tags=req.tags,
        supported_harnesses=req.supported_harnesses,
        status=ListingStatus.approved if target.auto_approve else ListingStatus.pending,
        released_by=current_user.id,
        released_at=datetime.now(UTC),
        reviewed_by=current_user.id if target.auto_approve else None,
        reviewed_at=datetime.now(UTC) if target.auto_approve else None,
    )
    db.add(version)
    await db.flush()

    listing.latest_version_id = version.id
    await inbox.on_publish(
        db,
        listing,
        subject_type="prompt",
        actor_id=current_user.id,
        auto_approved=target.auto_approve,
        version=version.version,
    )
    await commit_or_name_conflict(db, "prompt")
    await db.refresh(listing)
    return await component_response(listing, "prompt", current_user, db)


@router.get("", response_model=list[PromptListingSummary])
async def list_prompts(
    response: Response,
    category: str | None = Query(None),
    namespace: str | None = Query(None),
    search: str | None = Query(None),
    team_id: uuid.UUID | None = Query(None, description="Only listings owned by this teamspace"),
    composable_for_team_id: uuid.UUID | None = Query(
        None, description="Public listings plus this teamspace's private ones, for agent composition"
    ),
    public_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.debug("prompt list: search={}", search)
    stmt = (
        select(PromptListing)
        .join(PromptVersion, PromptListing.latest_version_id == PromptVersion.id)
        .where(PromptVersion.status == ListingStatus.approved)
    )
    if category:
        stmt = stmt.where(PromptVersion.category == category)
    if namespace:
        stmt = stmt.where(PromptListing.namespace == namespace.strip().lower())
    search_rank = None
    if search:
        search_filter, search_rank = keyword_search(
            search,
            [
                PromptListing.name,
                PromptListing.slug,
                PromptListing.namespace,
                PromptListing.owner,
                PromptVersion.description,
                PromptVersion.category,
                PromptVersion.template,
            ],
            name_field=PromptListing.name,
        )
        if search_filter is not None:
            stmt = stmt.where(search_filter)
    stmt = apply_registry_scope(
        stmt,
        PromptListing,
        current_user,
        team_id=team_id,
        composable_for_team_id=composable_for_team_id,
        public_only=public_only,
    )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    order_by = [PromptListing.created_at.desc()]
    if search_rank is not None:
        order_by.insert(0, search_rank.desc())
    result = await db.execute(stmt.order_by(*order_by).limit(limit).offset(offset))
    listings = await component_responses(result.scalars().all(), "prompt", current_user, db)
    response.headers["X-Total-Count"] = str(total or 0)
    return listings


@router.get("/my", response_model=list[PromptListingSummary])
async def my_prompts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("my_prompts called")
    stmt = (
        select(PromptListing)
        .where(PromptListing.submitted_by == current_user.id)
        .order_by(PromptListing.created_at.desc())
    )
    # Authorship is not a standing grant: a member removed from a teamspace keeps
    # the author column on its listings but must not keep reading them.
    stmt = apply_visibility_filter(stmt, PromptListing, current_user)

    result = await db.execute(stmt)
    listings = await component_responses(result.scalars().all(), "prompt", current_user, db)
    return listings


@router.get("/{listing_id}", response_model=PromptListingResponse)
async def get_prompt(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.debug("prompt get: listing_id={}", listing_id)
    listing = await resolve_visible_listing(
        PromptListing, listing_id, db, current_user, require_status=ListingStatus.approved
    )
    if listing is None:
        listing = await resolve_visible_listing(PromptListing, listing_id, db, current_user)
        may_view = listing is not None and may_view_unapproved(
            get_effective_component_permission(listing, current_user), current_user
        )
        if not may_view:
            raise HTTPException(status_code=404, detail="Listing not found")
    resp = (await component_responses([listing], "prompt", current_user, db, summary=False))[0]
    resp.user_permission = get_effective_component_permission(listing, current_user)
    return resp


@router.post("/{listing_id}/render", response_model=PromptRenderResponse)
async def render_prompt(
    listing_id: str,
    req: PromptRenderRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.debug("prompt render")
    listing = await resolve_visible_listing(
        PromptListing, listing_id, db, current_user, require_status=ListingStatus.approved
    )
    if not listing:
        listing = await resolve_visible_listing(PromptListing, listing_id, db, current_user)
        if (
            not listing
            or current_user is None
            or (
                listing.status != ListingStatus.archived
                and get_effective_component_permission(listing, current_user) != "owner"
            )
        ):
            raise HTTPException(status_code=404, detail="Listing not found or not approved")

    def replace_variable(match: re.Match[str]) -> str:
        key = match.group(1)
        return req.variables[key] if key in req.variables else match.group(0)

    rendered = _PROMPT_VARIABLE_RE.sub(replace_variable, listing.template)

    return PromptRenderResponse(listing_id=listing.id, rendered=rendered)


@router.post("/draft", response_model=PromptListingResponse)
async def save_prompt_draft(
    req: PromptDraftRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("req={}", req)
    target = await resolve_publish_target(
        db,
        current_user,
        req.name,
        team_id=req.team_id,
        visibility=req.visibility,
    )
    if await identity_exists(db, PromptListing, target.namespace, target.slug):
        raise HTTPException(status_code=409, detail=f"Prompt '{target.namespace}/{target.slug}' already exists")
    listing = PromptListing(
        name=req.name,
        namespace=target.namespace,
        slug=target.slug,
        owner=target.owner if target.team_id else (req.owner or current_user.username or current_user.email),
        submitted_by=current_user.id,
        team_id=target.team_id,
        is_private=target.visibility == "team",
    )
    db.add(listing)
    await db.flush()

    version = PromptVersion(
        listing_id=listing.id,
        version=req.version,
        description=req.description,
        category=req.category,
        template=req.template,
        variables=req.variables,
        model_hints=req.model_hints,
        tags=req.tags,
        supported_harnesses=req.supported_harnesses,
        status=ListingStatus.draft,
        released_by=current_user.id,
        released_at=datetime.now(UTC),
    )
    db.add(version)
    await db.flush()

    listing.latest_version_id = version.id
    await commit_or_name_conflict(db, "prompt")
    await db.refresh(listing)
    return await component_response(listing, "prompt", current_user, db)


def _reject_visibility_edits(listing, req) -> None:
    """Refuse teamspace or visibility changes sent to the draft update route.

    Visibility has exactly one authoritative path, PATCH /api/v1/registry/prompt/{listing_id}/visibility,
    which authorizes team owners and reviewers, writes audit metadata, and blocks privatizing a
    component that an approved public agent depends on. Accepting these fields here would either
    silently discard them or fork that policy into a weaker second implementation, so a real change
    is rejected. A request that repeats the values the listing already holds changes nothing and passes.
    """
    if req.team_id is not None and req.team_id != listing.team_id:
        raise HTTPException(
            status_code=400,
            detail="team_id cannot be changed here. A listing stays in the teamspace it was created under.",
        )
    if req.visibility is not None and req.visibility != listing.visibility:
        raise HTTPException(
            status_code=400,
            detail=f"visibility cannot be changed here. Use PATCH /api/v1/registry/prompt/{listing.id}/visibility.",
        )


@router.put("/{listing_id}/draft", response_model=PromptListingResponse)
async def update_prompt_draft(
    listing_id: str,
    req: PromptUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(PromptListing, listing_id, db, current_user=current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    if listing.status not in (ListingStatus.draft, ListingStatus.rejected, ListingStatus.pending):
        raise HTTPException(status_code=400, detail="Only draft, rejected, or pending listings can be edited")
    _reject_visibility_edits(listing, req)

    ver = listing.latest_version
    if not ver:
        raise HTTPException(status_code=400, detail="Listing has no version to update")

    for field in (
        "version",
        "description",
        "category",
        "template",
        "variables",
        "model_hints",
        "tags",
        "supported_harnesses",
    ):
        val = getattr(req, field)
        if val is not None:
            setattr(ver, field, val)

    # Don't allow saving over another user's active lock
    if ver.is_editing and ver.editing_by != current_user.id and not _is_lock_expired(ver.editing_since):
        raise HTTPException(
            status_code=409,
            detail="This item is currently being edited by another user. Please try again later.",
        )
    release_edit_lock(ver, current_user.id, force=True)
    await db.flush()

    for field in ("name", "owner"):
        val = getattr(req, field)
        if val is not None:
            setattr(listing, field, val)

    await commit_or_name_conflict(db, "prompt")
    await db.refresh(listing)
    return await component_response(listing, "prompt", current_user, db)


@router.post("/{listing_id}/start-edit")
async def start_edit_prompt(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(PromptListing, listing_id, db, current_user=current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    ver = listing.latest_version
    if not ver:
        raise HTTPException(status_code=400, detail="Listing has no version")
    if ver.status not in (ListingStatus.pending, ListingStatus.draft, ListingStatus.rejected):
        raise HTTPException(status_code=400, detail=f"Cannot edit: listing is '{ver.status.value}'")
    # Re-fetch with row-level lock to prevent TOCTOU race
    ver = (await db.execute(select(PromptVersion).where(PromptVersion.id == ver.id).with_for_update())).scalar_one()
    acquire_edit_lock(ver, current_user.id)
    await commit_or_name_conflict(db, "prompt")
    return {"status": "locked"}


@router.post("/{listing_id}/cancel-edit")
async def cancel_edit_prompt(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(PromptListing, listing_id, db, current_user=current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    ver = listing.latest_version
    if not ver:
        raise HTTPException(status_code=400, detail="Listing has no version")
    release_edit_lock(ver, current_user.id)
    await commit_or_name_conflict(db, "prompt")
    return {"status": "unlocked"}


@router.post("/{listing_id}/submit", response_model=PromptListingResponse)
async def submit_prompt_draft(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(PromptListing, listing_id, db, current_user=current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    if listing.status not in (ListingStatus.draft, ListingStatus.rejected):
        raise HTTPException(status_code=400, detail="Listing is not a draft")

    if not listing.description:
        raise HTTPException(status_code=400, detail="Description is required before submitting")
    if not listing.template:
        raise HTTPException(status_code=400, detail="Template is required before submitting")

    auto_approved = await publish_auto_approves_for_entity(listing, current_user, db)
    if auto_approved:
        listing.status = ListingStatus.approved
        listing.latest_version.reviewed_by = current_user.id
        listing.latest_version.reviewed_at = datetime.now(UTC)
    else:
        listing.status = ListingStatus.pending
    await inbox.on_publish(
        db,
        listing,
        subject_type="prompt",
        actor_id=current_user.id,
        auto_approved=auto_approved,
        version=getattr(listing.latest_version, "version", None),
    )
    await commit_or_name_conflict(db, "prompt")
    await db.refresh(listing)
    return await component_response(listing, "prompt", current_user, db)


@router.patch("/{listing_id}/archive")
async def archive_prompt(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    return await archive_listing(PromptListing, listing_id, db, current_user, "prompt")


@router.patch("/{listing_id}/unarchive")
async def unarchive_prompt(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    return await unarchive_listing(PromptListing, listing_id, db, current_user, "prompt")


# --- Version sub-routes ---
router.include_router(create_version_router("prompt", PromptListing, PromptVersion))
router.include_router(create_fork_router("prompt", PromptListing, PromptVersion))
