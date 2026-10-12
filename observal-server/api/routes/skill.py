# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from loguru import logger as optic
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defaultload

from api.deps import (
    apply_registry_scope,
    apply_visibility_filter,
    check_listing_visibility_async,
    commit_or_name_conflict,
    get_db,
    get_effective_component_permission,
    get_registry_user,
    require_role,
    resolve_listing,
    resolve_visible_listing,
)
from api.routes._component_archive import archive_listing, archived_install_warning, unarchive_listing
from api.routes._skill_lock import lock_skill_version
from api.routes.component_forks import attach_fork_info, component_response, component_responses, create_fork_router
from api.routes.component_versions import create_version_router
from api.routes.skill_files import _BODY_FREE_LISTING, _authorized_version
from api.routes.skill_files import router as file_router
from api.sanitize import escape_like
from api.search import keyword_search
from models.mcp import ListingStatus
from models.skill import SkillDownload, SkillListing, SkillVersion
from models.user import User, UserRole
from schemas.skill import (
    SkillDraftRequest,
    SkillFolderDraftRequest,
    SkillInstallRequest,
    SkillInstallResponse,
    SkillListingResponse,
    SkillListingSummary,
    SkillSubmitRequest,
    SkillUpdateRequest,
)
from schemas.skill_commands import normalize_slash_command
from schemas.skill_resources import SkillVersionManifest
from services.editing_lock import _is_lock_expired, acquire_edit_lock, release_edit_lock
from services.inbox import sources as inbox
from services.registry_namespace import identity_exists
from services.skill_bundle import needs_bundle_delivery, validate_skill_bundle
from services.skill_folder_edit import _validate_new_md
from services.skill_revisions import skill_content_revision, verified_skill_revision
from services.skill_validator import SkillValidationError, validate_skill_md, validate_skill_md_content_frontmatter
from services.teamspace import publish_auto_approves_for_entity, resolve_publish_target

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


def _summary_options(stmt):
    # Both relationships are selectin-loaded by default; keep the large JSON
    # out of their secondary SELECTs as well as the primary listing SELECT.
    return stmt.options(
        defaultload(SkillListing.latest_version)
        .defer(SkillVersion.extra_files)
        .defer(SkillVersion.skill_md_content)
        .defer(SkillVersion.script_content),
        defaultload(SkillListing.versions)
        .defer(SkillVersion.extra_files)
        .defer(SkillVersion.skill_md_content)
        .defer(SkillVersion.script_content),
    )


def _validate_stored_skill_md(skill_md_content: str | None, slash_command: str | None = None):
    try:
        return validate_skill_md_content_frontmatter(skill_md_content, slash_command=slash_command)
    except SkillValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _recheck_skill_owner(db, listing, current_user) -> None:
    await db.refresh(listing, attribute_names=["submitted_by", "co_authors", "team_id", "is_private"])
    if not await check_listing_visibility_async(listing, current_user, db) or (
        get_effective_component_permission(listing, current_user) != "owner"
    ):
        raise HTTPException(status_code=403, detail="Not the listing owner")


def _validate_effective_bundle(mode, md, script, filename, extras, *, enforce_limits=True) -> None:
    try:
        validate_skill_bundle(
            delivery_mode=mode,
            skill_md_content=md,
            script_content=script,
            script_filename=filename,
            extra_files=extras,
            enforce_limits=enforce_limits,
        )
    except SkillValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/submit", response_model=SkillListingResponse)
async def submit_skill(
    req: SkillSubmitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("submitting skill: {}", req.name)
    # Resolve name/description/slash_command - frontmatter wins when caller omits them.
    skill_md_content = req.skill_md_content
    validated = False
    name = req.name
    description = req.description
    slash_command = req.slash_command
    skill_path = req.skill_path
    delivery_mode = req.delivery_mode or "git_fetch"
    script_content = req.script_content
    script_filename = req.script_filename

    if delivery_mode == "registry_direct":
        # Registry direct: skill_md_content is required, no git validation
        if not skill_md_content:
            raise HTTPException(status_code=422, detail="skill_md_content is required for registry_direct delivery")
        content_analysis = _validate_stored_skill_md(skill_md_content, slash_command)
        fm = content_analysis.frontmatter
        if fm:
            fm_name = fm.get("name")
            fm_description = fm.get("description")
            if isinstance(fm_name, str) and not name:
                name = fm_name
            if isinstance(fm_description, str) and not description:
                description = fm_description
            if content_analysis.slash_command is not None:
                slash_command = content_analysis.slash_command
        validated = True  # Content is inline, no need to fetch from git
    elif req.git_url:
        try:
            analysis = await validate_skill_md(
                req.git_url,
                skill_path=req.skill_path,
                git_ref=req.git_ref or "main",
            )
            validated = True
            skill_md_content = skill_md_content or analysis.raw_content
            # Use discovered path if server auto-found it (user left skill_path as "/")
            if analysis.discovered_path:
                skill_path = analysis.discovered_path
            if not name:
                name = analysis.name
            if not description:
                description = analysis.description
            if slash_command is None:
                slash_command = analysis.slash_command
            elif analysis.slash_command is not None and slash_command != analysis.slash_command:
                raise HTTPException(status_code=422, detail="slash_command does not match SKILL.md frontmatter command")
        except SkillValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if skill_md_content:
        content_analysis = _validate_stored_skill_md(skill_md_content, slash_command)
        if content_analysis.slash_command is not None:
            slash_command = content_analysis.slash_command

    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if not description:
        raise HTTPException(status_code=422, detail="description is required")
    try:
        slash_command = normalize_slash_command(slash_command)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid slash_command: {exc}") from exc

    _validate_effective_bundle(delivery_mode, skill_md_content, script_content, script_filename, req.extra_files)

    target = await resolve_publish_target(
        db,
        current_user,
        name,
        team_id=req.team_id,
        visibility=req.visibility,
    )
    if await identity_exists(db, SkillListing, target.namespace, target.slug):
        raise HTTPException(status_code=409, detail=f"Skill '{target.namespace}/{target.slug}' already exists")

    listing = SkillListing(
        name=name,
        namespace=target.namespace,
        slug=target.slug,
        owner=target.owner if target.team_id else req.owner,
        submitted_by=current_user.id,
        team_id=target.team_id,
        is_private=target.visibility == "team",
    )
    db.add(listing)
    await db.flush()

    requires_delivery = delivery_mode == "registry_direct" and bool(
        req.extra_files or (script_filename is not None and script_content == "")
    )
    version = SkillVersion(
        listing_id=listing.id,
        version=req.version,
        description=description,
        skill_path=skill_path,
        git_url=req.git_url,
        git_ref=req.git_ref,
        skill_md_content=skill_md_content,
        delivery_mode=delivery_mode,
        script_content=script_content,
        script_filename=script_filename,
        extra_files=[file.model_dump() for file in req.extra_files],
        validated=validated,
        target_agents=req.target_agents,
        task_type=req.task_type,
        slash_command=slash_command,
        supported_harnesses=req.supported_harnesses,
        status=ListingStatus.approved if target.auto_approve and not requires_delivery else ListingStatus.pending,
        released_by=current_user.id,
        released_at=datetime.now(UTC),
        reviewed_by=current_user.id if target.auto_approve and not requires_delivery else None,
        reviewed_at=datetime.now(UTC) if target.auto_approve and not requires_delivery else None,
    )
    db.add(version)
    await db.flush()
    if delivery_mode == "registry_direct":
        version.content_revision = skill_content_revision(listing, version)

    listing.latest_version_id = version.id
    await inbox.on_publish(
        db,
        listing,
        subject_type="skill",
        actor_id=current_user.id,
        auto_approved=target.auto_approve and not requires_delivery,
        version=version.version,
    )
    await commit_or_name_conflict(db, "skill")
    await db.refresh(listing)
    return await component_response(listing, "skill", current_user, db)


@router.get("", response_model=list[SkillListingSummary])
async def list_skills(
    response: Response,
    task_type: str | None = Query(None),
    target_agent: str | None = Query(None),
    harness: str | None = Query(None),
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
    optic.debug("listing skills (task_type={}, search={})", task_type, search)
    stmt = (
        select(SkillListing)
        .join(SkillVersion, SkillListing.latest_version_id == SkillVersion.id)
        .where(SkillVersion.status == ListingStatus.approved)
    )
    if task_type:
        stmt = stmt.where(SkillVersion.task_type == task_type)
    if harness:
        stmt = stmt.where(cast(SkillVersion.supported_harnesses, String).ilike(f'%"{escape_like(harness)}"%'))
    if namespace:
        stmt = stmt.where(SkillListing.namespace == namespace.strip().lower())
    target_agents_text = cast(SkillVersion.target_agents, String)
    if target_agent:
        target_filter, _ = keyword_search(target_agent, [target_agents_text])
        if target_filter is not None:
            stmt = stmt.where(target_filter)
    search_rank = None
    if search:
        search_filter, search_rank = keyword_search(
            search,
            [
                SkillListing.name,
                SkillListing.slug,
                SkillListing.namespace,
                SkillListing.owner,
                SkillVersion.description,
                SkillVersion.task_type,
                SkillVersion.skill_path,
                SkillVersion.slash_command,
                SkillVersion.git_url,
                SkillVersion.skill_md_content,
                SkillVersion.delivery_mode,
                target_agents_text,
                cast(SkillVersion.supported_harnesses, String),
            ],
            name_field=SkillListing.name,
        )
        if search_filter is not None:
            stmt = stmt.where(search_filter)
    stmt = apply_registry_scope(
        stmt,
        SkillListing,
        current_user,
        team_id=team_id,
        composable_for_team_id=composable_for_team_id,
        public_only=public_only,
    )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    order_by = [SkillListing.created_at.desc()]
    if search_rank is not None:
        order_by.insert(0, search_rank.desc())
    result = await db.execute(_summary_options(stmt.order_by(*order_by).limit(limit).offset(offset)))
    listings = await component_responses(result.scalars().all(), "skill", current_user, db)
    response.headers["X-Total-Count"] = str(total or 0)
    return listings


@router.get("/my", response_model=list[SkillListingSummary])
async def my_skills(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.debug("my_skills called")
    stmt = (
        select(SkillListing)
        .where(SkillListing.submitted_by == current_user.id)
        .order_by(SkillListing.created_at.desc())
    )
    # Authorship is not a standing grant: a member removed from a teamspace keeps
    # the author column on its listings but must not keep reading them.
    stmt = apply_visibility_filter(stmt, SkillListing, current_user)

    result = await db.execute(_summary_options(stmt))
    listings = await component_responses(result.scalars().all(), "skill", current_user, db)
    return listings


@router.post("/folder-drafts", response_model=SkillVersionManifest)
async def create_skill_folder_draft(
    req: SkillFolderDraftRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Create a saved direct draft with a complete initial folder, never a blank tree."""
    _validate_stored_skill_md(req.skill_md_content)
    try:
        # Newly authored bytes must meet the Agent Skills name rules, or review would approve an uninstallable folder.
        _validate_new_md(req.skill_md_content, authored=True)
    except SkillValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    draft = SkillDraftRequest(
        name=req.name,
        version=req.version,
        description=req.description,
        owner=req.owner,
        team_id=req.team_id,
        visibility=req.visibility,
        delivery_mode="registry_direct",
        skill_md_content=req.skill_md_content,
        extra_files=req.extra_files,
        task_type=req.task_type,
        supported_harnesses=req.supported_harnesses,
    )
    listing = await _save_skill_draft(draft, db, current_user, folder_authoring=True)
    version = (await db.execute(select(SkillVersion).where(SkillVersion.listing_id == listing.id))).scalar_one()
    files = validate_skill_bundle(
        delivery_mode="registry_direct",
        skill_md_content=version.skill_md_content,
        extra_files=version.extra_files or [],
    )
    return SkillVersionManifest(
        listing_id=listing.id,
        version_id=version.id,
        revision=version.content_revision,
        files=[file.declaration for file in files],
    )


async def _selected_skill_release(
    listing: SkillListing, db: AsyncSession, current_user: User | None, *, requested=None
):
    """Resolve one version under shared listing/version locks before reading its bytes.

    The same selector drives public detail and standalone installation, so a
    legacy pending pointer cannot make GET advertise a release that POST refuses.
    """
    from services.agent_lock import INSTALLABLE_STATUSES, latest_release

    observed_pointer = listing.latest_version_id
    if observed_pointer is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    if requested:
        target_id = (
            await db.execute(
                select(SkillVersion.id).where(SkillVersion.listing_id == listing.id, SkillVersion.version == requested)
            )
        ).scalar_one_or_none()
        if target_id is None:
            raise HTTPException(status_code=404, detail=f"Version {requested!r} not found for this skill")
        listing, selected = await _authorized_version(str(listing.id), target_id, db, current_user)
    else:
        try:
            listing, selected = await _authorized_version(str(listing.id), observed_pointer, db, current_user)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            rows = (
                await db.execute(
                    select(SkillVersion.id, SkillVersion.version, SkillVersion.status).where(
                        SkillVersion.listing_id == listing.id,
                        SkillVersion.status.in_(INSTALLABLE_STATUSES),
                        SkillVersion.requires_global_review.is_(False),
                    )
                )
            ).all()
            fallback = latest_release(rows)
            if fallback is None:
                raise exc
            listing, selected = await _authorized_version(str(listing.id), fallback.id, db, current_user)
    await db.refresh(listing, attribute_names=["latest_version_id"])
    if listing.latest_version_id != observed_pointer:
        raise HTTPException(status_code=409, detail="Skill latest version changed; refresh the listing")
    return listing, selected


@router.get("/{listing_id}", response_model=SkillListingResponse)
async def get_skill(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.debug("fetching skill {}", listing_id)
    listing = await resolve_visible_listing(SkillListing, listing_id, db, current_user, load_options=_BODY_FREE_LISTING)
    if listing is None or listing.latest_version_id is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    observed_pointer = listing.latest_version_id
    listing, selected = await _selected_skill_release(listing, db, current_user)
    if selected.delivery_mode == "registry_direct":
        try:
            verified_skill_revision(listing, selected)
        except SkillValidationError as exc:
            raise HTTPException(status_code=409, detail="Selected skill folder has no valid review revision") from exc
    if selected.id == observed_pointer:
        resp = SkillListingResponse.model_validate(listing)
    else:
        # The listing's compatibility properties read the pointer, which may
        # be pending. Build from the authorized approved row, never from that
        # unrelated candidate (including nullable Git and script fields).
        resp = SkillListingResponse.model_validate(
            {
                "id": listing.id,
                "name": listing.name,
                "namespace": listing.namespace,
                "slug": listing.slug,
                "qualified_name": listing.qualified_name,
                "owner": listing.owner,
                "team_id": listing.team_id,
                "visibility": listing.visibility,
                "is_private": listing.is_private,
                "submitted_by": listing.submitted_by,
                "created_at": listing.created_at,
                "updated_at": listing.updated_at,
                **{
                    field: getattr(selected, field)
                    for field in (
                        "version",
                        "description",
                        "task_type",
                        "target_agents",
                        "supported_harnesses",
                        "skill_path",
                        "git_url",
                        "git_ref",
                        "skill_md_content",
                        "delivery_mode",
                        "script_content",
                        "script_filename",
                        "validated",
                        "slash_command",
                        "status",
                        "rejection_reason",
                        "download_count",
                    )
                },
            }
        )
    resp = await attach_fork_info(resp, listing, "skill", current_user, db)
    resp.user_permission = get_effective_component_permission(listing, current_user)
    return resp


@router.post("/{listing_id}/install", response_model=SkillInstallResponse)
async def install_skill(
    listing_id: str,
    req: SkillInstallRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.debug("installing skill {}", listing_id)
    # A newer candidate may temporarily be the listing's pointer on legacy
    # data. Resolve the requested persisted release before applying status
    # gates; otherwise an older approved release becomes impossible to install.
    listing = await resolve_visible_listing(SkillListing, listing_id, db, current_user, load_options=_BODY_FREE_LISTING)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found or not approved")

    from services.agent_lock import INSTALLABLE_STATUSES, content_digest

    listing, installed = await _selected_skill_release(listing, db, current_user, requested=req.version)
    if installed.status not in INSTALLABLE_STATUSES and (
        current_user is None
        or installed.status != listing.status
        or get_effective_component_permission(listing, current_user) != "owner"
    ):
        raise HTTPException(status_code=404, detail="Listing not found or not approved")
    warnings = []
    if installed.status == ListingStatus.archived or listing.status == ListingStatus.archived:
        warnings.append(archived_install_warning("skill", listing.name))

    if installed.requires_global_review:
        raise HTTPException(status_code=409, detail="Selected skill version requires global review before installation")
    bundle = None
    bundled = installed.delivery_mode == "registry_direct" and (
        needs_bundle_delivery(installed) or "skill_extra_files_v1" in req.supported_features
    )
    if bundled:
        from observal_shared.harness_registry import HARNESS_REGISTRY
        from services.skill_bundle import SKILL_EXTRA_FILES_FEATURE, complete_skill_folder

        if SKILL_EXTRA_FILES_FEATURE not in req.supported_features:
            raise HTTPException(
                status_code=409, detail="Client must support skill_extra_files_v1 to install this skill version"
            )
        harness_spec = HARNESS_REGISTRY.get(req.harness.replace("_", "-"), {})
        if "skills" not in harness_spec.get("capabilities", set()):
            raise HTTPException(status_code=409, detail="Harness does not support complete skill folders")
        if req.scope not in harness_spec.get("skills", {}):
            raise HTTPException(status_code=409, detail="Harness does not support complete skill folders in this scope")
        if needs_bundle_delivery(installed):
            import services.dynamic_settings as _ds

            if not await _ds.get_bool("registry.skill_folder_delivery_enabled", False):
                raise HTTPException(status_code=409, detail="Skill folder delivery is disabled until fleet rollout")
        try:
            validate_skill_bundle(
                delivery_mode=installed.delivery_mode,
                skill_md_content=installed.skill_md_content,
                script_content=installed.script_content,
                script_filename=installed.script_filename,
                extra_files=installed.extra_files or [],
                enforce_limits=False,
            )
        except SkillValidationError as exc:
            raise HTTPException(status_code=409, detail="Selected skill folder is invalid") from exc
    if installed.delivery_mode == "registry_direct":
        try:
            verified_skill_revision(listing, installed)
        except SkillValidationError as exc:
            raise HTTPException(status_code=409, detail="Selected skill folder has no valid review revision") from exc

    from api.routes.config import derive_endpoints
    from services.skill_config_generator import generate_skill_config

    folder_name = None
    if bundled and installed.extra_files:
        from services.skill_bundle import declared_skill_folder_name

        try:
            folder_name = declared_skill_folder_name(installed.skill_md_content)
        except SkillValidationError as exc:
            raise HTTPException(status_code=409, detail="Selected skill has no valid installed folder name") from exc
        if req.local_name is not None and req.local_name != folder_name:
            raise HTTPException(status_code=409, detail="local_name must match SKILL.md name for a complete folder")

    endpoints = await derive_endpoints(request)
    config = generate_skill_config(
        listing,
        req.harness,
        server_url=endpoints["api"],
        scope=req.scope,
        version_override=installed,
        local_name=req.local_name,
    )
    if bundled:
        skill_file = config.get("skills")
        if not isinstance(skill_file, dict) or not isinstance(skill_file.get("path"), str):
            raise HTTPException(status_code=409, detail="Harness has no usable skill folder destination")
        path = harness_spec["skills"][req.scope].format(name=folder_name) if folder_name else skill_file["path"]
        if not path.endswith("/SKILL.md"):
            raise HTTPException(status_code=409, detail="Harness has no usable skill folder destination")
        if folder_name:
            config["skill"]["name"] = folder_name
        try:
            bundle = complete_skill_folder(listing.id, installed, skill_file_path=path)
        except SkillValidationError as exc:
            raise HTTPException(status_code=409, detail="Selected skill folder is invalid or too large") from exc
        # Only the verified folder payload contains installable bytes. A second
        # SKILL.md/script copy in the legacy snippet would let old clients write
        # a partial tree or overwrite the declared one.
        config.pop("skills", None)
        for key in ("skill_md_content", "script_content", "script_filename"):
            config["skill"].pop(key, None)
        config["skill"]["bundle_version_id"] = str(installed.id)
    # Prepare and validate the entire response before recording usage: digest
    # errors and response-shape failures must not count as successful installs.
    response = SkillInstallResponse(
        listing_id=listing.id,
        harness=req.harness,
        config_snippet=config,
        warnings=warnings,
        version=installed.version,
        version_id=getattr(installed, "id", None),
        digest=content_digest("skill", installed),
        bundle=bundle,
    )
    if current_user is not None and not req.preview:
        db.add(SkillDownload(listing_id=listing.id, user_id=current_user.id, harness=req.harness))
        installed.download_count = (installed.download_count or 0) + 1
        await commit_or_name_conflict(db, "skill")
    return response


@router.post("/draft", response_model=SkillListingResponse)
async def save_skill_draft(
    req: SkillDraftRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    return await _save_skill_draft(req, db, current_user)


async def _save_skill_draft(req: SkillDraftRequest, db: AsyncSession, current_user: User, *, folder_authoring=False):
    _validate_effective_bundle(
        req.delivery_mode, req.skill_md_content, req.script_content, req.script_filename, req.extra_files
    )
    content_analysis = _validate_stored_skill_md(req.skill_md_content, req.slash_command)
    slash_command = content_analysis.slash_command

    target = await resolve_publish_target(
        db,
        current_user,
        req.name,
        team_id=req.team_id,
        visibility=req.visibility,
    )
    if await identity_exists(db, SkillListing, target.namespace, target.slug):
        raise HTTPException(status_code=409, detail=f"Skill '{target.namespace}/{target.slug}' already exists")
    listing = SkillListing(
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

    version = SkillVersion(
        listing_id=listing.id,
        version=req.version,
        description=req.description,
        skill_path=req.skill_path,
        git_url=req.git_url,
        git_ref=req.git_ref,
        skill_md_content=req.skill_md_content,
        delivery_mode=req.delivery_mode or "git_fetch",
        script_content=req.script_content,
        script_filename=req.script_filename,
        extra_files=[file.model_dump() for file in req.extra_files],
        target_agents=req.target_agents,
        task_type=req.task_type,
        slash_command=slash_command,
        supported_harnesses=req.supported_harnesses,
        status=ListingStatus.draft,
        released_by=current_user.id,
        released_at=datetime.now(UTC),
    )
    db.add(version)
    await db.flush()

    listing.latest_version_id = version.id
    if folder_authoring or needs_bundle_delivery(version):
        version.content_revision = skill_content_revision(listing, version)
    await commit_or_name_conflict(db, "skill")
    await db.refresh(listing)
    return await component_response(listing, "skill", current_user, db)


def _reject_visibility_edits(listing, req) -> None:
    """Refuse teamspace or visibility changes sent to the draft update route.

    Visibility has exactly one authoritative path, PATCH /api/v1/registry/skill/{listing_id}/visibility,
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
            detail=f"visibility cannot be changed here. Use PATCH /api/v1/registry/skill/{listing.id}/visibility.",
        )


@router.put("/{listing_id}/draft", response_model=SkillListingResponse)
async def update_skill_draft(
    listing_id: str,
    req: SkillUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(SkillListing, listing_id, db, current_user=current_user)
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
    latest_id, ver = await lock_skill_version(db, listing.id, ver.id)
    if latest_id != ver.id or ver.status not in (ListingStatus.draft, ListingStatus.rejected, ListingStatus.pending):
        raise HTTPException(status_code=409, detail="Skill version changed during editing")
    await _recheck_skill_owner(db, listing, current_user)
    if ver.status == ListingStatus.pending:
        raise HTTPException(status_code=409, detail="Withdraw this pending version before editing its files")
    if (
        req.version is not None
        and req.version != ver.version
        and (ver.content_revision is not None or ver.base_version_id is not None)
    ):
        raise HTTPException(status_code=409, detail="Saved draft release number is reserved; create a new version")
    if (
        ver.content_revision is not None
        or needs_bundle_delivery(ver)
        or ver.base_version_id is not None
        or (ver.review_epoch or 0) > 0
    ):
        try:
            revision = verified_skill_revision(listing, ver)
        except SkillValidationError as exc:
            raise HTTPException(status_code=409, detail="Saved skill version is not a valid folder") from exc
        if req.observed_revision != revision:
            raise HTTPException(status_code=409, detail="Skill version changed; refresh its manifest before saving")

    slash_command_should_update = "slash_command" in req.model_fields_set
    slash_command_explicit_clear = slash_command_should_update and req.slash_command is None
    slash_command = req.slash_command if slash_command_should_update else None
    if req.skill_md_content is not None:
        content_analysis = _validate_stored_skill_md(req.skill_md_content, slash_command)
        if (req.delivery_mode or ver.delivery_mode) == "registry_direct" and (
            req.skill_md_content != ver.skill_md_content or ver.delivery_mode != "registry_direct"
        ):
            try:
                _validate_new_md(req.skill_md_content)
            except SkillValidationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if content_analysis.slash_command is not None and not slash_command_explicit_clear:
            slash_command = content_analysis.slash_command
            slash_command_should_update = True
    elif slash_command_should_update:
        content_analysis = _validate_stored_skill_md(ver.skill_md_content, slash_command)
        if not slash_command_explicit_clear:
            slash_command = content_analysis.slash_command

    effective_script = req.script_content if "script_content" in req.model_fields_set else ver.script_content
    effective_filename = req.script_filename if "script_filename" in req.model_fields_set else ver.script_filename
    effective_extras = req.extra_files if "extra_files" in req.model_fields_set else (ver.extra_files or [])
    if (
        req.delivery_mode == "registry_direct"
        and ver.delivery_mode != "registry_direct"
        and req.skill_md_content is None
    ):
        try:
            _validate_new_md(ver.skill_md_content)
        except SkillValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    _validate_effective_bundle(
        req.delivery_mode or ver.delivery_mode,
        req.skill_md_content if req.skill_md_content is not None else ver.skill_md_content,
        effective_script,
        effective_filename,
        effective_extras,
        enforce_limits=(
            req.delivery_mode not in (None, ver.delivery_mode)
            or (req.skill_md_content is not None and req.skill_md_content != ver.skill_md_content)
            or effective_script != ver.script_content
            or effective_filename != ver.script_filename
            or effective_extras != (ver.extra_files or [])
        ),
    )

    for field in (
        "version",
        "description",
        "skill_path",
        "git_url",
        "git_ref",
        "skill_md_content",
        "delivery_mode",
        "script_content",
        "script_filename",
        "target_agents",
        "task_type",
        "supported_harnesses",
    ):
        val = getattr(req, field)
        if val is not None:
            setattr(ver, field, val)

    if "script_content" in req.model_fields_set:
        ver.script_content = req.script_content
    if "script_filename" in req.model_fields_set:
        ver.script_filename = req.script_filename
    if "extra_files" in req.model_fields_set:
        ver.extra_files = [file.model_dump() for file in req.extra_files]
    if slash_command_should_update:
        ver.slash_command = slash_command

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

    if ver.content_revision is not None or needs_bundle_delivery(ver):
        ver.content_revision = skill_content_revision(listing, ver)
    await commit_or_name_conflict(db, "skill")
    await db.refresh(listing)
    return await component_response(listing, "skill", current_user, db)


@router.post("/{listing_id}/start-edit")
async def start_edit_skill(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(SkillListing, listing_id, db, current_user=current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    ver = listing.latest_version
    if not ver:
        raise HTTPException(status_code=400, detail="Listing has no version")
    if ver.status not in (ListingStatus.pending, ListingStatus.draft, ListingStatus.rejected):
        raise HTTPException(status_code=400, detail=f"Cannot edit: listing is '{ver.status.value}'")
    # Follow the same listing-then-version lock order as review decisions.
    latest_id, ver = await lock_skill_version(db, listing.id, ver.id)
    if latest_id != ver.id or ver.status not in (ListingStatus.pending, ListingStatus.draft, ListingStatus.rejected):
        raise HTTPException(status_code=409, detail="Skill version changed during editing")
    await _recheck_skill_owner(db, listing, current_user)
    if ver.status == ListingStatus.pending:
        raise HTTPException(status_code=409, detail="Withdraw this pending version before editing its files")
    acquire_edit_lock(ver, current_user.id)
    await commit_or_name_conflict(db, "skill")
    return {"status": "locked"}


@router.post("/{listing_id}/cancel-edit")
async def cancel_edit_skill(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(SkillListing, listing_id, db, current_user=current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    ver = listing.latest_version
    if not ver:
        raise HTTPException(status_code=400, detail="Listing has no version")
    latest_id, ver = await lock_skill_version(db, listing.id, ver.id)
    if latest_id != ver.id:
        raise HTTPException(status_code=409, detail="Skill version changed during editing")
    await _recheck_skill_owner(db, listing, current_user)
    release_edit_lock(ver, current_user.id)
    await commit_or_name_conflict(db, "skill")
    return {"status": "unlocked"}


@router.post("/{listing_id}/submit", response_model=SkillListingResponse)
async def submit_skill_draft(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await resolve_listing(SkillListing, listing_id, db, current_user=current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    if listing.status not in (ListingStatus.draft, ListingStatus.rejected):
        raise HTTPException(status_code=400, detail="Listing is not a draft")

    ver = listing.latest_version
    if not ver:
        raise HTTPException(status_code=400, detail="Listing has no version")
    latest_id, ver = await lock_skill_version(db, listing.id, ver.id)
    if latest_id != ver.id or ver.status not in (ListingStatus.draft, ListingStatus.rejected):
        raise HTTPException(status_code=409, detail="Skill version changed before resubmission")
    await _recheck_skill_owner(db, listing, current_user)
    if ver.content_revision is not None or ver.base_version_id is not None or (ver.review_epoch or 0) > 0:
        raise HTTPException(status_code=409, detail="Submit this saved draft by version UUID and observed revision")
    if ver.requires_global_review or ver.pre_public_status is not None:
        raise HTTPException(status_code=409, detail="Global public re-review is not yet available")
    _validate_effective_bundle(
        ver.delivery_mode,
        ver.skill_md_content,
        ver.script_content,
        ver.script_filename,
        ver.extra_files or [],
        enforce_limits=False,  # An unchanged stored draft is not a newly written bundle.
    )
    content_analysis = _validate_stored_skill_md(ver.skill_md_content, ver.slash_command)
    if content_analysis.slash_command is not None:
        ver.slash_command = content_analysis.slash_command

    if not listing.description:
        raise HTTPException(status_code=400, detail="Description is required before submitting")
    if ver.content_revision is not None:
        ver.content_revision = skill_content_revision(listing, ver)

    auto_approved = not needs_bundle_delivery(ver) and await publish_auto_approves_for_entity(listing, current_user, db)
    if auto_approved:
        listing.status = ListingStatus.approved
        listing.latest_version.reviewed_by = current_user.id
        listing.latest_version.reviewed_at = datetime.now(UTC)
    else:
        listing.status = ListingStatus.pending
    await inbox.on_publish(
        db,
        listing,
        subject_type="skill",
        actor_id=current_user.id,
        auto_approved=auto_approved,
        version=getattr(listing.latest_version, "version", None),
    )
    await commit_or_name_conflict(db, "skill")
    await db.refresh(listing)
    return await component_response(listing, "skill", current_user, db)


@router.patch("/{listing_id}/archive")
async def archive_skill(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    return await archive_listing(SkillListing, listing_id, db, current_user, "skill")


@router.patch("/{listing_id}/unarchive")
async def unarchive_skill(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    return await unarchive_listing(SkillListing, listing_id, db, current_user, "skill")


# --- Version sub-routes ---
router.include_router(file_router)
router.include_router(create_version_router("skill", SkillListing, SkillVersion))
router.include_router(create_fork_router("skill", SkillListing, SkillVersion))
