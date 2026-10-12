# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from loguru import logger as optic
from sqlalchemy import delete, func, select
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
from database import async_session
from models.mcp import ListingStatus, McpDownload, McpListing, McpValidationResult, McpVersion
from models.user import User, UserRole
from schemas.mcp import (
    ClientAnalysis,
    McpAnalyzeRequest,
    McpAnalyzeResponse,
    McpDraftRequest,
    McpInstallRequest,
    McpInstallResponse,
    McpListingResponse,
    McpListingSummary,
    McpSubmitRequest,
    McpUpdateRequest,
)
from services.config_generator import generate_config
from services.editing_lock import _is_lock_expired, acquire_edit_lock, release_edit_lock
from services.inbox import sources as inbox
from services.mcp_validator import analyze_repo, run_validation
from services.registry_namespace import identity_exists
from services.teamspace import publish_auto_approves_for_entity, resolve_publish_target

router = APIRouter(prefix="/api/v1/mcps", tags=["mcp"])


@router.post("/analyze", response_model=McpAnalyzeResponse)
async def analyze_mcp(
    req: McpAnalyzeRequest,
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("user_id={}", current_user.id)
    result = await analyze_repo(req.git_url)
    return McpAnalyzeResponse(**result)


async def _store_client_analysis(listing: McpListing, analysis: ClientAnalysis, db: AsyncSession) -> None:
    """Store validation results from client-side (CLI) analysis."""
    optic.trace("listing={}, analysis={}", listing, analysis)
    await db.execute(delete(McpValidationResult).where(McpValidationResult.listing_id == listing.id))

    has_entry = bool(analysis.entry_point or analysis.framework)
    tool_count = len(analysis.tools)
    issue_count = len(analysis.issues)

    if analysis.framework:
        listing.framework = analysis.framework
    if analysis.command and not listing.command:
        listing.command = analysis.command
    if analysis.args and not listing.args:
        listing.args = analysis.args
    if analysis.docker_image and not listing.docker_image:
        listing.docker_image = analysis.docker_image

    if has_entry:
        detail = "Client-side analysis: found entry point"
        if analysis.framework:
            detail += f" ({analysis.framework})"
        listing.mcp_validated = True
    else:
        detail = "Client-side analysis: no recognized MCP framework detected"
        listing.mcp_validated = True

    db.add(McpValidationResult(listing_id=listing.id, stage="clone_and_inspect", passed=has_entry, details=detail))

    if tool_count or issue_count:
        manifest_detail = f"Client-side analysis: {tool_count} tool(s) found"
        if analysis.issues:
            manifest_detail += "\nIssues:\n- " + "\n- ".join(analysis.issues)
        db.add(
            McpValidationResult(
                listing_id=listing.id,
                stage="manifest_validation",
                passed=issue_count == 0,
                details=manifest_detail,
            )
        )

    await commit_or_name_conflict(db, "listing")


async def _run_validation_background(listing_id: str) -> None:
    """Run validation in the background with its own DB session."""
    optic.trace("listing_id={}", listing_id)
    async with async_session() as db:
        result = await db.execute(select(McpListing).where(McpListing.id == listing_id))
        listing = result.scalar_one_or_none()
        if not listing:
            return
        try:
            await run_validation(listing, db)
        except Exception:
            optic.error("Background validation failed for listing {}", listing_id)


@router.post("/submit", response_model=McpListingResponse)
async def submit_mcp(
    req: McpSubmitRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("mcp submit: name={}", req.name)
    target = await resolve_publish_target(
        db,
        current_user,
        req.name,
        team_id=req.team_id,
        visibility=req.visibility,
    )
    existing = (
        (
            await db.execute(
                select(McpListing).where(
                    McpListing.namespace == target.namespace,
                    McpListing.slug == target.slug,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing:
        if existing.submitted_by == current_user.id and existing.status != ListingStatus.approved:
            await db.delete(existing)
            await db.flush()
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Approved MCP server '{target.namespace}/{target.slug}' already exists",
            )

    listing = McpListing(
        name=req.name,
        namespace=target.namespace,
        slug=target.slug,
        category=req.category,
        owner=target.owner if target.team_id else req.owner,
        submitted_by=current_user.id,
        team_id=target.team_id,
        is_private=target.visibility == "team",
    )
    db.add(listing)
    await db.flush()

    version = McpVersion(
        listing_id=listing.id,
        version=req.version,
        description=req.description,
        transport=req.transport or ("sse" if req.url and not req.command else "stdio" if req.command else None),
        framework=req.framework,
        docker_image=req.docker_image,
        command=req.command,
        args=req.args,
        url=req.url,
        headers=[h.model_dump() for h in req.headers] if req.headers else None,
        auto_approve=req.auto_approve,
        environment_variables=[ev.model_dump() for ev in req.environment_variables],
        supported_harnesses=req.supported_harnesses,
        setup_instructions=req.setup_instructions,
        changelog=req.changelog,
        source_url=req.git_url,
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
        subject_type="mcp",
        actor_id=current_user.id,
        auto_approved=target.auto_approve,
        version=version.version,
    )
    await commit_or_name_conflict(db, "listing")
    await db.refresh(listing)

    if req.client_analysis:
        # CLI already cloned and analyzed locally - store results directly
        await _store_client_analysis(listing, req.client_analysis, db)
    elif req.git_url:
        # Only run background validation if we have a git URL to clone
        background_tasks.add_task(_run_validation_background, str(listing.id))
    # Direct config submissions (no git_url) skip validation - config is user-provided
    return await component_response(listing, "mcp", current_user, db)


@router.get("", response_model=list[McpListingSummary])
async def list_mcps(
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
    optic.debug("mcp list: search={}", search)
    stmt = (
        select(McpListing)
        .join(McpVersion, McpListing.latest_version_id == McpVersion.id)
        .where(McpVersion.status == ListingStatus.approved)
    )
    if category:
        stmt = stmt.where(McpListing.category == category)
    if namespace:
        stmt = stmt.where(McpListing.namespace == namespace.strip().lower())
    search_rank = None
    if search:
        search_filter, search_rank = keyword_search(
            search,
            [
                McpListing.name,
                McpListing.slug,
                McpListing.namespace,
                McpListing.owner,
                McpListing.category,
                McpVersion.description,
                McpVersion.framework,
                McpVersion.setup_instructions,
            ],
            name_field=McpListing.name,
        )
        if search_filter is not None:
            stmt = stmt.where(search_filter)
    stmt = apply_registry_scope(
        stmt,
        McpListing,
        current_user,
        team_id=team_id,
        composable_for_team_id=composable_for_team_id,
        public_only=public_only,
    )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    order_by = [McpListing.created_at.desc()]
    if search_rank is not None:
        order_by.insert(0, search_rank.desc())
    result = await db.execute(stmt.order_by(*order_by).limit(limit).offset(offset))
    listings = await component_responses(result.scalars().all(), "mcp", current_user, db)
    response.headers["X-Total-Count"] = str(total or 0)
    return listings


@router.get("/my", response_model=list[McpListingSummary])
async def my_mcps(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("my_mcps called")
    # Authorship is not a standing grant: a member removed from a teamspace keeps
    # submitted_by on its listings but must not keep reading them, so /my is
    # scoped like every other list rather than trusting the author column.
    stmt = select(McpListing).where(McpListing.submitted_by == current_user.id).order_by(McpListing.created_at.desc())
    stmt = apply_visibility_filter(stmt, McpListing, current_user)
    result = await db.execute(stmt)
    listings = await component_responses(result.scalars().all(), "mcp", current_user, db)
    return listings


@router.get("/{listing_id}", response_model=McpListingResponse)
async def get_mcp(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.debug("mcp get: listing_id={}", listing_id)
    listing = await resolve_visible_listing(
        McpListing, listing_id, db, current_user, require_status=ListingStatus.approved
    )
    if listing is None:
        listing = await resolve_visible_listing(McpListing, listing_id, db, current_user)
        may_view = listing is not None and may_view_unapproved(
            get_effective_component_permission(listing, current_user), current_user
        )
        if not may_view:
            raise HTTPException(status_code=404, detail="Listing not found")
    resp = (await component_responses([listing], "mcp", current_user, db, summary=False))[0]
    resp.user_permission = get_effective_component_permission(listing, current_user)
    return resp


@router.post("/{listing_id}/install", response_model=McpInstallResponse)
async def install_mcp(
    listing_id: str,
    req: McpInstallRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.debug("mcp install: listing_id={}", listing_id)
    listing = await resolve_visible_listing(
        McpListing, listing_id, db, current_user, require_status=ListingStatus.approved
    )
    if not listing:
        listing = await resolve_visible_listing(McpListing, listing_id, db, current_user)
        if (
            not listing
            or current_user is None
            or (
                listing.status != ListingStatus.archived
                and get_effective_component_permission(listing, current_user) != "owner"
            )
        ):
            raise HTTPException(status_code=404, detail="Listing not found or not approved")

    from services.agent_lock import PinnedListing, content_digest, select_install_version

    requested = await select_install_version(db, "mcp", listing, req.version)
    source = PinnedListing(listing, requested) if requested is not None else listing
    installed = requested if requested is not None else getattr(listing, "latest_version", None)

    warnings = []
    if listing.status == ListingStatus.archived:
        warnings.append(archived_install_warning("MCP", listing.name))
    if source.setup_instructions:
        warnings.append(f"MCP '{listing.name}' requires local setup before use:\n{source.setup_instructions}")

    if current_user is not None:
        db.add(McpDownload(listing_id=listing.id, user_id=current_user.id, harness=req.harness))
        if installed is not None:
            installed.download_count = (installed.download_count or 0) + 1
        await commit_or_name_conflict(db, "listing")

    from api.routes.config import derive_endpoints

    endpoints = await derive_endpoints(request)
    snippet = generate_config(
        source,
        req.harness,
        observal_url=endpoints["api"],
        env_values=req.env_values,
        header_values=req.header_values,
        local_name=req.local_name,
    )
    return McpInstallResponse(
        listing_id=listing.id,
        harness=req.harness,
        config_snippet=snippet,
        warnings=warnings,
        version=source.version,
        version_id=getattr(installed, "id", None),
        digest=content_digest("mcp", installed) if installed is not None else None,
    )


@router.post("/draft", response_model=McpListingResponse)
async def save_mcp_draft(
    req: McpDraftRequest,
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
    if await identity_exists(db, McpListing, target.namespace, target.slug):
        raise HTTPException(status_code=409, detail=f"MCP server '{target.namespace}/{target.slug}' already exists")
    listing = McpListing(
        name=req.name,
        namespace=target.namespace,
        slug=target.slug,
        category=req.category,
        owner=target.owner if target.team_id else (req.owner or current_user.username or current_user.email),
        submitted_by=current_user.id,
        team_id=target.team_id,
        is_private=target.visibility == "team",
    )
    db.add(listing)
    await db.flush()

    version = McpVersion(
        listing_id=listing.id,
        version=req.version,
        description=req.description,
        transport=req.transport or ("sse" if req.url and not req.command else "stdio" if req.command else None),
        framework=req.framework,
        docker_image=req.docker_image,
        command=req.command,
        args=req.args,
        url=req.url,
        headers=[h.model_dump() for h in req.headers] if req.headers else None,
        auto_approve=req.auto_approve,
        environment_variables=[ev.model_dump() for ev in req.environment_variables],
        supported_harnesses=req.supported_harnesses,
        setup_instructions=req.setup_instructions,
        changelog=req.changelog,
        source_url=req.git_url,
        status=ListingStatus.draft,
        released_by=current_user.id,
        released_at=datetime.now(UTC),
    )
    db.add(version)
    await db.flush()

    listing.latest_version_id = version.id
    await commit_or_name_conflict(db, "listing")
    await db.refresh(listing)
    return await component_response(listing, "mcp", current_user, db)


def _reject_visibility_edits(listing, req) -> None:
    """Refuse teamspace or visibility changes sent to the draft update route.

    Visibility has exactly one authoritative path, PATCH /api/v1/registry/mcp/{listing_id}/visibility,
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
            detail=f"visibility cannot be changed here. Use PATCH /api/v1/registry/mcp/{listing.id}/visibility.",
        )


@router.put("/{listing_id}/draft", response_model=McpListingResponse)
async def update_mcp_draft(
    listing_id: str,
    req: McpUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(McpListing, listing_id, db, current_user=current_user)
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
        "framework",
        "docker_image",
        "command",
        "args",
        "url",
        "auto_approve",
        "transport",
        "supported_harnesses",
        "setup_instructions",
        "changelog",
    ):
        val = getattr(req, field)
        if val is not None:
            setattr(ver, field, val)

    if req.git_url is not None:
        ver.source_url = req.git_url
    if req.headers is not None:
        ver.headers = [h.model_dump() for h in req.headers]
    if req.environment_variables is not None:
        ver.environment_variables = [ev.model_dump() for ev in req.environment_variables]

    # Don't allow saving over another user's active lock
    if ver.is_editing and ver.editing_by != current_user.id and not _is_lock_expired(ver.editing_since):
        raise HTTPException(
            status_code=409,
            detail="This item is currently being edited by another user. Please try again later.",
        )
    release_edit_lock(ver, current_user.id, force=True)
    await db.flush()

    for field in ("name", "category", "owner"):
        val = getattr(req, field)
        if val is not None:
            setattr(listing, field, val)

    await commit_or_name_conflict(db, "listing")
    await db.refresh(listing)
    return await component_response(listing, "mcp", current_user, db)


@router.post("/{listing_id}/start-edit")
async def start_edit_mcp(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(McpListing, listing_id, db, current_user=current_user)
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
    ver = (await db.execute(select(McpVersion).where(McpVersion.id == ver.id).with_for_update())).scalar_one()
    acquire_edit_lock(ver, current_user.id)
    await commit_or_name_conflict(db, "listing")
    return {"status": "locked"}


@router.post("/{listing_id}/cancel-edit")
async def cancel_edit_mcp(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(McpListing, listing_id, db, current_user=current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    ver = listing.latest_version
    if not ver:
        raise HTTPException(status_code=400, detail="Listing has no version")
    release_edit_lock(ver, current_user.id)
    await commit_or_name_conflict(db, "listing")
    return {"status": "unlocked"}


@router.post("/{listing_id}/submit", response_model=McpListingResponse)
async def submit_mcp_draft(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(McpListing, listing_id, db, current_user=current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    if listing.status not in (ListingStatus.draft, ListingStatus.rejected):
        raise HTTPException(status_code=400, detail="Listing is not a draft")

    if not listing.description:
        raise HTTPException(status_code=400, detail="Description is required before submitting")
    if not listing.git_url and not listing.command and not listing.url:
        raise HTTPException(status_code=400, detail="At least one of git_url, command, or url is required")

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
        subject_type="mcp",
        actor_id=current_user.id,
        auto_approved=auto_approved,
        version=getattr(listing.latest_version, "version", None),
    )
    await commit_or_name_conflict(db, "listing")
    await db.refresh(listing)
    return await component_response(listing, "mcp", current_user, db)


@router.patch("/{listing_id}/archive")
async def archive_mcp(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    return await archive_listing(McpListing, listing_id, db, current_user, "listing")


@router.patch("/{listing_id}/unarchive")
async def unarchive_mcp(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    return await unarchive_listing(McpListing, listing_id, db, current_user, "listing")


# --- Version sub-routes ---
router.include_router(create_version_router("mcp", McpListing, McpVersion))
router.include_router(create_fork_router("mcp", McpListing, McpVersion))
