# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
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
from api.routes._component_archive import archive_listing, archived_install_warning, unarchive_listing
from api.routes.component_forks import component_response, component_responses, create_fork_router
from api.routes.component_versions import create_version_router
from api.search import keyword_search
from models.hook import HookDownload, HookListing, HookVersion
from models.mcp import ListingStatus
from models.user import User, UserRole
from schemas.hook import (
    HookDraftRequest,
    HookFileEntry,
    HookInstallRequest,
    HookInstallResponse,
    HookListingResponse,
    HookListingSummary,
    HookSubmitRequest,
    HookUpdateRequest,
)
from services.editing_lock import _is_lock_expired, acquire_edit_lock, release_edit_lock
from services.inbox import sources as inbox
from services.registry_namespace import identity_exists
from services.teamspace import publish_auto_approves_for_entity, resolve_publish_target

router = APIRouter(prefix="/api/v1/hooks", tags=["hooks"])


@router.post("/submit", response_model=HookListingResponse)
async def submit_hook(
    req: HookSubmitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("hook submit: name={}", req.name)
    target = await resolve_publish_target(
        db,
        current_user,
        req.name,
        team_id=req.team_id,
        visibility=req.visibility,
    )
    if await identity_exists(db, HookListing, target.namespace, target.slug):
        raise HTTPException(status_code=409, detail=f"Hook '{target.namespace}/{target.slug}' already exists")

    listing = HookListing(
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

    version = HookVersion(
        listing_id=listing.id,
        version=req.version,
        description=req.description,
        event=req.event,
        execution_mode=req.execution_mode,
        priority=req.priority,
        handler_type=req.handler_type,
        handler_config=req.handler_config,
        scope=req.scope,
        tool_filter=req.tool_filter,
        supported_harnesses=req.supported_harnesses,
        script_content=req.script_content,
        script_filename=req.script_filename,
        source_url=req.source_url,
        source_ref=req.source_ref,
        source_path=req.source_path,
        requirements=req.requirements,
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
        subject_type="hook",
        actor_id=current_user.id,
        auto_approved=target.auto_approve,
        version=version.version,
    )
    await commit_or_name_conflict(db, "hook")
    await db.refresh(listing)
    return await component_response(listing, "hook", current_user, db)


@router.get("", response_model=list[HookListingSummary])
async def list_hooks(
    response: Response,
    event: str | None = Query(None),
    scope: str | None = Query(None),
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
    optic.debug("hook list: search={}", search)
    stmt = (
        select(HookListing)
        .join(HookVersion, HookListing.latest_version_id == HookVersion.id)
        .where(HookVersion.status == ListingStatus.approved)
    )
    if event:
        stmt = stmt.where(HookVersion.event == event)
    if scope:
        stmt = stmt.where(HookVersion.scope == scope)
    if namespace:
        stmt = stmt.where(HookListing.namespace == namespace.strip().lower())
    search_rank = None
    if search:
        search_filter, search_rank = keyword_search(
            search,
            [
                HookListing.name,
                HookListing.slug,
                HookListing.namespace,
                HookListing.owner,
                HookVersion.description,
                HookVersion.event,
                HookVersion.scope,
                HookVersion.handler_type,
            ],
            name_field=HookListing.name,
        )
        if search_filter is not None:
            stmt = stmt.where(search_filter)
    stmt = apply_registry_scope(
        stmt,
        HookListing,
        current_user,
        team_id=team_id,
        composable_for_team_id=composable_for_team_id,
        public_only=public_only,
    )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    order_by = [HookListing.created_at.desc()]
    if search_rank is not None:
        order_by.insert(0, search_rank.desc())
    result = await db.execute(stmt.order_by(*order_by).limit(limit).offset(offset))
    listings = await component_responses(result.scalars().all(), "hook", current_user, db)
    response.headers["X-Total-Count"] = str(total or 0)
    return listings


@router.get("/my", response_model=list[HookListingSummary])
async def my_hooks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("my_hooks called")
    # Authorship is not a standing grant: a member removed from a teamspace keeps
    # submitted_by on its listings but must not keep reading them, so /my is
    # scoped like every other list rather than trusting the author column.
    stmt = (
        select(HookListing).where(HookListing.submitted_by == current_user.id).order_by(HookListing.created_at.desc())
    )
    stmt = apply_visibility_filter(stmt, HookListing, current_user)
    result = await db.execute(stmt)
    listings = await component_responses(result.scalars().all(), "hook", current_user, db)
    return listings


@router.get("/{listing_id}", response_model=HookListingResponse)
async def get_hook(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.debug("hook get: listing_id={}", listing_id)
    listing = await resolve_visible_listing(
        HookListing, listing_id, db, current_user, require_status=ListingStatus.approved
    )
    if listing is None:
        listing = await resolve_visible_listing(HookListing, listing_id, db, current_user)
        may_view = listing is not None and may_view_unapproved(
            get_effective_component_permission(listing, current_user), current_user
        )
        if not may_view:
            raise HTTPException(status_code=404, detail="Listing not found")
    resp = (await component_responses([listing], "hook", current_user, db, summary=False))[0]
    resp.user_permission = get_effective_component_permission(listing, current_user)
    return resp


@router.post("/{listing_id}/install", response_model=HookInstallResponse)
async def install_hook(
    listing_id: str,
    req: HookInstallRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.debug("hook install: listing_id={}", listing_id)
    listing = await resolve_visible_listing(
        HookListing, listing_id, db, current_user, require_status=ListingStatus.approved
    )
    if not listing:
        listing = await resolve_visible_listing(HookListing, listing_id, db, current_user)
        if not listing or current_user is None:
            raise HTTPException(status_code=404, detail="Listing not found or not approved")
        if (
            listing.status != ListingStatus.archived
            and get_effective_component_permission(listing, current_user) != "owner"
        ):
            raise HTTPException(status_code=404, detail="Listing not found or not approved")

    warnings = []
    if listing.status == ListingStatus.archived:
        warnings.append(archived_install_warning("hook", listing.name))

    from services.agent_lock import PinnedListing, content_digest, select_install_version

    requested = await select_install_version(db, "hook", listing, req.version)
    source = PinnedListing(listing, requested) if requested is not None else listing
    installed = requested if requested is not None else getattr(listing, "latest_version", None)

    if current_user is not None:
        db.add(HookDownload(listing_id=listing.id, user_id=current_user.id, harness=req.harness))
        if installed is not None:
            installed.download_count = (installed.download_count or 0) + 1
        await commit_or_name_conflict(db, "hook")

    from services.hook_install_generator import generate_hook_install_config

    result = generate_hook_install_config(source, req.harness, local_name=req.local_name)
    return HookInstallResponse(
        listing_id=listing.id,
        harness=req.harness,
        config_snippet=result.get("config_snippet", {}),
        config_path=result.get("config_path", ""),
        files=[HookFileEntry(**f) for f in result.get("files", [])],
        requirements=result.get("requirements", []),
        source_fetch=result.get("source_fetch"),
        notes=result.get("notes", []),
        warnings=warnings,
        version=source.version,
        version_id=getattr(installed, "id", None),
        digest=content_digest("hook", installed) if installed is not None else None,
    )


@router.post("/draft", response_model=HookListingResponse)
async def save_hook_draft(
    req: HookDraftRequest,
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
    if await identity_exists(db, HookListing, target.namespace, target.slug):
        raise HTTPException(status_code=409, detail=f"Hook '{target.namespace}/{target.slug}' already exists")
    listing = HookListing(
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

    version = HookVersion(
        listing_id=listing.id,
        version=req.version,
        description=req.description,
        event=req.event,
        execution_mode=req.execution_mode,
        priority=req.priority,
        handler_type=req.handler_type,
        handler_config=req.handler_config,
        scope=req.scope,
        tool_filter=req.tool_filter,
        supported_harnesses=req.supported_harnesses,
        script_content=req.script_content,
        script_filename=req.script_filename,
        source_url=req.source_url,
        source_ref=req.source_ref,
        source_path=req.source_path,
        requirements=req.requirements,
        status=ListingStatus.draft,
        released_by=current_user.id,
        released_at=datetime.now(UTC),
    )
    db.add(version)
    await db.flush()

    listing.latest_version_id = version.id
    await commit_or_name_conflict(db, "hook")
    await db.refresh(listing)
    return await component_response(listing, "hook", current_user, db)


def _reject_visibility_edits(listing, req) -> None:
    """Refuse teamspace or visibility changes sent to the draft update route.

    Visibility has exactly one authoritative path, PATCH /api/v1/registry/hook/{listing_id}/visibility,
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
            detail=f"visibility cannot be changed here. Use PATCH /api/v1/registry/hook/{listing.id}/visibility.",
        )


@router.put("/{listing_id}/draft", response_model=HookListingResponse)
async def update_hook_draft(
    listing_id: str,
    req: HookUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(HookListing, listing_id, db, current_user=current_user)
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
        "event",
        "execution_mode",
        "priority",
        "handler_type",
        "handler_config",
        "scope",
        "tool_filter",
        "supported_harnesses",
        "script_content",
        "script_filename",
        "source_url",
        "source_ref",
        "source_path",
        "requirements",
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

    await commit_or_name_conflict(db, "hook")
    await db.refresh(listing)
    return await component_response(listing, "hook", current_user, db)


@router.post("/{listing_id}/start-edit")
async def start_edit_hook(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(HookListing, listing_id, db, current_user=current_user)
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
    ver = (await db.execute(select(HookVersion).where(HookVersion.id == ver.id).with_for_update())).scalar_one()
    acquire_edit_lock(ver, current_user.id)
    await commit_or_name_conflict(db, "hook")
    return {"status": "locked"}


@router.post("/{listing_id}/cancel-edit")
async def cancel_edit_hook(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(HookListing, listing_id, db, current_user=current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    ver = listing.latest_version
    if not ver:
        raise HTTPException(status_code=400, detail="Listing has no version")
    release_edit_lock(ver, current_user.id)
    await commit_or_name_conflict(db, "hook")
    return {"status": "unlocked"}


@router.post("/{listing_id}/submit", response_model=HookListingResponse)
async def submit_hook_draft(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(HookListing, listing_id, db, current_user=current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    if listing.status not in (ListingStatus.draft, ListingStatus.rejected):
        raise HTTPException(status_code=400, detail="Listing is not a draft")

    if not listing.description:
        raise HTTPException(status_code=400, detail="Description is required before submitting")

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
        subject_type="hook",
        actor_id=current_user.id,
        auto_approved=auto_approved,
        version=getattr(listing.latest_version, "version", None),
    )
    await commit_or_name_conflict(db, "hook")
    await db.refresh(listing)
    return await component_response(listing, "hook", current_user, db)


@router.patch("/{listing_id}/archive")
async def archive_hook(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    return await archive_listing(HookListing, listing_id, db, current_user, "hook")


@router.patch("/{listing_id}/unarchive")
async def unarchive_hook(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    return await unarchive_listing(HookListing, listing_id, db, current_user, "hook")


# --- Version sub-routes ---
router.include_router(create_version_router("hook", HookListing, HookVersion))
router.include_router(create_fork_router("hook", HookListing, HookVersion))
