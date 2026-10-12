# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Authorize an exact skill version before returning its stored file bytes."""

import re
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defaultload

from api.deps import (
    check_listing_visibility_async,
    get_db,
    get_effective_component_permission,
    get_registry_user,
    require_role,
    resolve_listing,
)
from api.routes._skill_lock import approved_skill_base_id, lock_skill_version, should_promote_skill_version
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.user import User, UserRole
from schemas.skill import SkillCandidateDraftRequest, SkillFolderImportDraftRequest, SkillUpdateRequest
from schemas.skill_resources import (
    SkillDraftRebaseRequest,
    SkillFileContents,
    SkillFileOperations,
    SkillSnapshotReplace,
    SkillVersionManifest,
    SkillVersionRevisionRequest,
)
from services import dynamic_settings as _ds
from services.agent_lock import INSTALLABLE_STATUSES, release_key
from services.editing_lock import _is_lock_expired, acquire_edit_lock, release_edit_lock
from services.inbox import sources as inbox
from services.skill_bundle import (
    MAX_BUNDLE_BYTES,
    MAX_EXTRA_FILES,
    declared_skill_folder_name,
    needs_bundle_delivery,
    validate_bundle_path,
    validate_skill_bundle,
)
from services.skill_folder_edit import _validate_new_md, apply_file_operations, replace_folder
from services.skill_rebase import merge_skill_draft
from services.skill_revisions import skill_content_revision, verified_skill_revision
from services.skill_validator import SkillValidationError, validate_skill_md_content_frontmatter
from services.teamspace import can_review, review_scope

router = APIRouter(tags=["skill-files"])

_BODY_FREE_LISTING = (
    defaultload(SkillListing.latest_version)
    .defer(SkillVersion.extra_files)
    .defer(SkillVersion.skill_md_content)
    .defer(SkillVersion.script_content),
    defaultload(SkillListing.versions)
    .defer(SkillVersion.extra_files)
    .defer(SkillVersion.skill_md_content)
    .defer(SkillVersion.script_content),
)


async def _authorized_version(listing_id: str, version_id: uuid.UUID, db: AsyncSession, current_user: User | None):
    listing = await resolve_listing(
        SkillListing, listing_id, db, current_user=current_user, load_options=_BODY_FREE_LISTING
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Skill version not found")
    # Serialize with visibility changes and review decisions: acquire listing
    # then version share locks, and recheck visibility after a possible wait.
    locked = (
        await db.execute(select(SkillListing.id).where(SkillListing.id == listing.id).with_for_update(read=True))
    ).scalar_one_or_none()
    if locked is None:
        raise HTTPException(status_code=404, detail="Skill version not found")
    await db.refresh(
        listing,
        attribute_names=["submitted_by", "co_authors", "team_id", "is_private", "name", "namespace", "slug", "owner"],
    )
    if not await check_listing_visibility_async(listing, current_user, db):
        raise HTTPException(status_code=404, detail="Skill version not found")
    # Only metadata is read before permission checks, even when the listing's
    # selectin relationships contain many unrelated candidate versions.
    row = (
        await db.execute(
            select(SkillVersion.status, SkillVersion.requires_global_review)
            .where(SkillVersion.id == version_id, SkillVersion.listing_id == listing.id)
            .with_for_update(read=True)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Skill version not found")
    status, marked = row
    owner = get_effective_component_permission(listing, current_user) == "owner" and current_user is not None
    if not owner:
        if marked:
            allowed = (
                status == ListingStatus.pending
                and current_user is not None
                and not listing.is_private
                and (await review_scope(db, current_user)).is_global_reviewer
            )
        elif status in (ListingStatus.approved, ListingStatus.archived):
            allowed = True
        else:
            allowed = (
                status == ListingStatus.pending
                and current_user is not None
                and can_review(listing, await review_scope(db, current_user))
            )
        if not allowed:
            raise HTTPException(status_code=404, detail="Skill version not found")
    version = (
        await db.execute(
            select(SkillVersion)
            .where(SkillVersion.id == version_id, SkillVersion.listing_id == listing.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return listing, version


async def _authorized_files(listing_id: str, version_id: uuid.UUID, db: AsyncSession, current_user: User | None):
    listing, version = await _authorized_version(listing_id, version_id, db, current_user)
    if version.delivery_mode != "registry_direct":
        raise HTTPException(status_code=409, detail="This skill version is delivered by Git, not a stored folder")
    try:
        files = validate_skill_bundle(
            delivery_mode="registry_direct",
            skill_md_content=version.skill_md_content,
            script_filename=version.script_filename,
            script_content=version.script_content,
            extra_files=version.extra_files or [],
            enforce_limits=False,
        )
        if len(files) > MAX_EXTRA_FILES + 2 or sum(len(file.content) for file in files) > MAX_BUNDLE_BYTES:
            raise SkillValidationError("Stored folder exceeds the supported file limits")
        revision = verified_skill_revision(listing, version)
    except SkillValidationError as exc:
        raise HTTPException(status_code=409, detail="Stored skill version is not a valid folder") from exc
    return listing, version, revision, files


async def _editable_version(
    listing_id: str, version_id: uuid.UUID, observed_revision: str, db: AsyncSession, current_user: User
):
    listing = await resolve_listing(
        SkillListing, listing_id, db, current_user=current_user, load_options=_BODY_FREE_LISTING
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Skill version not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    _, version = await lock_skill_version(db, listing.id, version_id)
    await db.refresh(
        listing,
        attribute_names=["submitted_by", "co_authors", "team_id", "is_private", "name", "namespace", "slug", "owner"],
    )
    if not await check_listing_visibility_async(listing, current_user, db) or (
        get_effective_component_permission(listing, current_user) != "owner"
    ):
        raise HTTPException(status_code=403, detail="Not the listing owner")
    if version.status == ListingStatus.pending:
        raise HTTPException(status_code=409, detail="Withdraw this pending version before editing its files")
    if version.status not in (ListingStatus.draft, ListingStatus.rejected) or version.pre_public_status is not None:
        raise HTTPException(status_code=409, detail="Historical skill releases are immutable; create a new draft")
    if version.is_editing and version.editing_by != current_user.id and not _is_lock_expired(version.editing_since):
        raise HTTPException(status_code=409, detail="This skill version is being edited by another author")
    try:
        revision = verified_skill_revision(listing, version)
    except SkillValidationError as exc:
        raise HTTPException(status_code=409, detail="Saved skill version is not a valid folder") from exc
    if observed_revision != revision:
        raise HTTPException(status_code=409, detail="Skill version changed; refresh its manifest before saving")
    return listing, version


async def _save_file_edit(listing, version, edit, db: AsyncSession, *, replace: bool, actor_id: uuid.UUID):
    md_changed = version.skill_md_content != edit.skill_md_content
    version.skill_md_content = edit.skill_md_content
    version.extra_files = edit.extra_files
    version.script_filename = edit.script_filename
    version.script_content = edit.script_content
    if replace:
        version.delivery_mode = "registry_direct"
        version.git_url = None
        version.git_ref = None
        version.skill_path = "/"
        version.validated = True
    if md_changed:
        from services.skill_validator import validate_skill_md_content_frontmatter

        version.slash_command = validate_skill_md_content_frontmatter(version.skill_md_content).slash_command
    release_edit_lock(version, actor_id, force=True)
    await db.flush()
    version.content_revision = skill_content_revision(listing, version)
    await db.commit()
    return SkillVersionManifest(
        listing_id=listing.id,
        version_id=version.id,
        revision=version.content_revision,
        files=[file.declaration for file in edit.files],
    )


def _forkable_direct_base(version: SkillVersion) -> bool:
    """A stored direct release can be copied into a new bounded authoring draft."""
    if version.delivery_mode != "registry_direct":
        return False
    try:
        _validate_new_md(version.skill_md_content)
        validate_skill_bundle(
            delivery_mode="registry_direct",
            skill_md_content=version.skill_md_content,
            script_filename=version.script_filename,
            script_content=version.script_content,
            extra_files=version.extra_files or [],
        )
    except SkillValidationError:
        return False
    return True


@router.get("/{listing_id}/approved-base")
async def get_skill_approved_base(
    listing_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """One authoritative base for successor authoring, even after 100+ releases."""
    listing = await resolve_listing(
        SkillListing, listing_id, db, current_user=current_user, load_options=_BODY_FREE_LISTING
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Skill listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    if listing.latest_version_id is None:
        raise HTTPException(status_code=409, detail="Skill has no reviewed release")
    _, latest = await lock_skill_version(db, listing.id, listing.latest_version_id)
    await db.refresh(
        listing,
        attribute_names=["submitted_by", "co_authors", "team_id", "is_private", "name", "namespace", "slug", "owner"],
    )
    if not await check_listing_visibility_async(listing, current_user, db) or (
        get_effective_component_permission(listing, current_user) != "owner"
    ):
        raise HTTPException(status_code=403, detail="Not the listing owner")
    approved_id = await approved_skill_base_id(db, listing.id, latest)
    if approved_id is None:
        raise HTTPException(status_code=409, detail="Skill has no reviewed release")
    base = (
        await db.execute(
            select(SkillVersion)
            .where(SkillVersion.id == approved_id, SkillVersion.listing_id == listing.id)
            .execution_options(populate_existing=True)
            .with_for_update(read=True)
        )
    ).scalar_one()
    if base.status not in INSTALLABLE_STATUSES or base.requires_global_review:
        raise HTTPException(status_code=409, detail="Skill has no reviewed release")
    try:
        revision = verified_skill_revision(listing, base)
    except SkillValidationError as exc:
        raise HTTPException(status_code=409, detail="Approved skill base is invalid") from exc
    import_required = not _forkable_direct_base(base)
    response.headers["Cache-Control"] = "no-store"
    return {
        "listing_id": str(listing.id),
        "version_id": str(base.id),
        "version": base.version,
        "revision": revision,
        "delivery_mode": base.delivery_mode,
        "import_required": import_required,
    }


@router.post("/{listing_id}/drafts", response_model=SkillVersionManifest)
async def create_skill_candidate_draft(
    listing_id: str,
    req: SkillCandidateDraftRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Fork the current approved direct release without moving its public pointer."""
    listing = await resolve_listing(
        SkillListing, listing_id, db, current_user=current_user, load_options=_BODY_FREE_LISTING
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Skill listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    if listing.latest_version_id is None:
        raise HTTPException(status_code=409, detail="Skill has no reviewed release to fork")
    latest_id, latest = await lock_skill_version(db, listing.id, listing.latest_version_id)
    if latest_id != latest.id:
        raise HTTPException(status_code=409, detail="Skill latest release changed; refresh before forking a draft")
    await db.refresh(
        listing,
        attribute_names=["submitted_by", "co_authors", "team_id", "is_private", "name", "namespace", "slug", "owner"],
    )
    if not await check_listing_visibility_async(listing, current_user, db) or (
        get_effective_component_permission(listing, current_user) != "owner"
    ):
        raise HTTPException(status_code=403, detail="Not the listing owner")
    approved_id = await approved_skill_base_id(db, listing.id, latest)
    if approved_id is None or req.base_version_id != approved_id:
        raise HTTPException(status_code=409, detail="Approved skill base changed; refresh before creating a draft")
    base = (
        await db.execute(
            select(SkillVersion)
            .where(SkillVersion.id == approved_id, SkillVersion.listing_id == listing.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one()
    if base.status not in INSTALLABLE_STATUSES or base.requires_global_review:
        raise HTTPException(status_code=409, detail="Approved skill base changed; refresh before creating a draft")
    if base.delivery_mode != "registry_direct":
        raise HTTPException(status_code=422, detail="Use a full folder import to convert a Git skill")
    try:
        revision = verified_skill_revision(listing, base)
        _validate_new_md(base.skill_md_content)
        files = validate_skill_bundle(
            delivery_mode="registry_direct",
            skill_md_content=base.skill_md_content,
            script_filename=base.script_filename,
            script_content=base.script_content,
            extra_files=base.extra_files or [],
        )
    except SkillValidationError as exc:
        raise HTTPException(status_code=409, detail="Approved skill base is not a valid folder") from exc
    if revision != req.observed_base_revision:
        raise HTTPException(status_code=409, detail="Approved skill base changed; refresh before creating a draft")
    versions = (
        (await db.execute(select(SkillVersion.version).where(SkillVersion.listing_id == listing.id))).scalars().all()
    )
    if any(
        release_key(value) is None and not re.fullmatch(r"\d+\.\d+\.\d+-[0-9A-Za-z.-]+", value) for value in versions
    ):
        raise HTTPException(
            status_code=409, detail="Malformed historical skill version; choose an explicit approved base"
        )
    stable = [key for value in versions if (key := release_key(value)) is not None]
    candidate_key = release_key(req.version)
    if req.version in versions or (stable and candidate_key <= max(stable)):
        raise HTTPException(status_code=409, detail="Draft release number must be new and newer than approved releases")
    draft = SkillVersion(
        listing_id=listing.id,
        version=req.version,
        description=req.description,
        changelog=req.changelog,
        status=ListingStatus.draft,
        released_by=current_user.id,
        released_at=datetime.now(UTC),
        base_version_id=base.id,
        base_revision=revision,
        delivery_mode="registry_direct",
        skill_path="/",
        skill_md_content=base.skill_md_content,
        script_filename=base.script_filename,
        script_content=base.script_content,
        extra_files=deepcopy(base.extra_files or []),
        target_agents=deepcopy(base.target_agents or []),
        supported_harnesses=deepcopy(base.supported_harnesses or []),
        task_type=base.task_type,
        slash_command=base.slash_command,
        validated=True,
    )
    db.add(draft)
    await db.flush()
    draft.content_revision = skill_content_revision(listing, draft)
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return SkillVersionManifest(
        listing_id=listing.id,
        version_id=draft.id,
        revision=draft.content_revision,
        files=[file.declaration for file in files],
    )


@router.post("/{listing_id}/folder-import-drafts", response_model=SkillVersionManifest)
async def import_skill_folder_candidate(
    listing_id: str,
    req: SkillFolderImportDraftRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Convert a reviewed Git or historically invalid direct release atomically.

    The entire folder is supplied locally; Git bytes are never fetched or
    represented as a stored review base. Neither the listing's approved pointer
    nor its old approved version changes.
    """
    observed = await get_skill_approved_base(listing_id, Response(), db, current_user)
    if observed["version_id"] != str(req.base_version_id) or observed["revision"] != req.observed_base_revision:
        raise HTTPException(status_code=409, detail="Approved skill base changed; refresh before importing")
    listing = await db.get(SkillListing, uuid.UUID(observed["listing_id"]))
    base = (
        await db.execute(
            select(SkillVersion)
            .where(SkillVersion.id == req.base_version_id, SkillVersion.listing_id == listing.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one()
    if base.delivery_mode == "registry_direct":
        if _forkable_direct_base(base):
            raise HTTPException(status_code=422, detail="Use fork for an existing reviewed direct folder")
    elif base.delivery_mode != "git_fetch":
        raise HTTPException(status_code=422, detail="Only reviewed Git or historical direct skills can be imported")
    try:
        edit = replace_folder(req, authored=True)
    except SkillValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    versions = (
        (await db.execute(select(SkillVersion.version).where(SkillVersion.listing_id == listing.id))).scalars().all()
    )
    if any(
        release_key(value) is None and not re.fullmatch(r"\d+\.\d+\.\d+-[0-9A-Za-z.-]+", value) for value in versions
    ):
        raise HTTPException(
            status_code=409, detail="Malformed historical skill version; request a reviewed base repair"
        )
    stable = [key for value in versions if (key := release_key(value)) is not None]
    candidate_key = release_key(req.version)
    if req.version in versions or (stable and candidate_key <= max(stable)):
        raise HTTPException(status_code=409, detail="Draft release number must be new and newer than approved releases")
    draft = SkillVersion(
        listing_id=listing.id,
        version=req.version,
        description=req.description,
        changelog=req.changelog,
        status=ListingStatus.draft,
        released_by=current_user.id,
        released_at=datetime.now(UTC),
        base_version_id=base.id,
        base_revision=observed["revision"],
        delivery_mode="registry_direct",
        skill_path="/",
        skill_md_content=edit.skill_md_content,
        script_filename=None,
        script_content=None,
        extra_files=edit.extra_files,
        target_agents=deepcopy(base.target_agents or []),
        supported_harnesses=deepcopy(base.supported_harnesses or []),
        task_type=base.task_type,
        slash_command=validate_skill_md_content_frontmatter(edit.skill_md_content).slash_command,
        validated=True,
    )
    db.add(draft)
    await db.flush()
    draft.content_revision = skill_content_revision(listing, draft)
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return SkillVersionManifest(
        listing_id=listing.id,
        version_id=draft.id,
        revision=draft.content_revision,
        files=[file.declaration for file in edit.files],
    )


@router.post("/{listing_id}/versions/{version_id}/rebase", response_model=SkillVersionManifest)
async def rebase_skill_draft(
    listing_id: str,
    version_id: uuid.UUID,
    req: SkillDraftRebaseRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Explicitly merge disjoint author changes onto a newly reviewed direct base.

    Never move the listing's approved pointer or rewrite either historical
    release. Overlap, changed observations and non-new stable release numbers
    refuse before mutating the draft. A pending draft must first be withdrawn.
    """
    listing, draft = await _editable_version(listing_id, version_id, req.observed_revision, db, current_user)
    if draft.delivery_mode != "registry_direct" or draft.base_version_id is None or draft.base_revision is None:
        raise HTTPException(status_code=409, detail="Only saved direct release drafts can be rebased")
    current_id = (
        await db.execute(select(SkillListing.latest_version_id).where(SkillListing.id == listing.id))
    ).scalar_one()
    if current_id != req.current_version_id or current_id == draft.base_version_id:
        raise HTTPException(status_code=409, detail="Current approved release changed; refresh before rebasing")
    versions = (
        (
            await db.execute(
                select(SkillVersion)
                .where(SkillVersion.listing_id == listing.id, SkillVersion.id.in_([draft.base_version_id, current_id]))
                .order_by(SkillVersion.id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    by_id = {row.id: row for row in versions}
    base, current = by_id.get(draft.base_version_id), by_id.get(current_id)
    if (
        not base
        or not current
        or any(
            row.status not in INSTALLABLE_STATUSES
            or row.requires_global_review
            or row.delivery_mode != "registry_direct"
            for row in (base, current)
        )
    ):
        raise HTTPException(status_code=409, detail="Approved release is unavailable for rebasing")
    try:
        base_revision = verified_skill_revision(listing, base)
        current_revision = verified_skill_revision(listing, current)
        if draft.base_revision != base_revision:
            raise HTTPException(status_code=409, detail="Original approved base changed; manual resolution required")
        if req.observed_current_revision != current_revision:
            raise HTTPException(status_code=409, detail="Current approved release changed; refresh before rebasing")
        proposed = req.new_version or draft.version
        new_key, current_key = release_key(proposed), release_key(current.version)
        if new_key is None or current_key is None or new_key <= current_key:
            raise HTTPException(status_code=409, detail="Choose a new stable release number above the approved base")
        if (
            proposed != draft.version
            and (
                await db.execute(
                    select(SkillVersion.id).where(
                        SkillVersion.listing_id == listing.id, SkillVersion.version == proposed
                    )
                )
            ).scalar_one_or_none()
        ):
            raise HTTPException(status_code=409, detail="That release number is already reserved")
        edit, metadata, conflicts = merge_skill_draft(base, draft, current)
    except (SkillValidationError, ValidationError, UnicodeError) as exc:
        raise HTTPException(status_code=409, detail="Saved release cannot be safely rebased") from exc
    if conflicts["paths"] or conflicts["metadata"]:
        raise HTTPException(
            status_code=409, detail={"message": "Resolve overlapping edits in the saved draft", **conflicts}
        )
    draft.version = proposed
    for field, value in metadata.items():
        setattr(draft, field, value)
    draft.skill_md_content = edit.skill_md_content
    draft.extra_files = edit.extra_files
    draft.script_filename = edit.script_filename
    draft.script_content = edit.script_content
    draft.slash_command = validate_skill_md_content_frontmatter(edit.skill_md_content).slash_command
    draft.base_version_id = current.id
    draft.base_revision = current_revision
    release_edit_lock(draft, current_user.id, force=True)
    await db.flush()
    draft.content_revision = skill_content_revision(listing, draft)
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return SkillVersionManifest(
        listing_id=listing.id,
        version_id=draft.id,
        revision=draft.content_revision,
        files=[file.declaration for file in edit.files],
    )


@router.put("/{listing_id}/versions/{version_id}/draft", response_model=SkillVersionManifest)
async def update_skill_version_draft(
    listing_id: str,
    version_id: uuid.UUID,
    req: SkillUpdateRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Recover/edit a non-pointer draft without changing its approved release."""
    if req.observed_revision is None:
        raise HTTPException(status_code=409, detail="Refresh this version's manifest before editing")
    listing, version = await _editable_version(listing_id, version_id, req.observed_revision, db, current_user)
    forbidden = {"name", "owner", "team_id", "visibility", "delivery_mode"} & req.model_fields_set
    if forbidden:
        raise HTTPException(
            status_code=422, detail="Edit listing identity separately; use full replacement for delivery mode"
        )
    if version.delivery_mode == "registry_direct" and {"git_url", "git_ref", "skill_path"} & req.model_fields_set:
        raise HTTPException(
            status_code=422, detail="Git coordinates do not affect direct folders; use full replacement"
        )
    if "version" in req.model_fields_set and req.version != version.version:
        raise HTTPException(status_code=409, detail="Release number is reserved; create a new version instead")
    md = req.skill_md_content if req.skill_md_content is not None else version.skill_md_content
    script = req.script_content if "script_content" in req.model_fields_set else version.script_content
    filename = req.script_filename if "script_filename" in req.model_fields_set else version.script_filename
    extras = req.extra_files if "extra_files" in req.model_fields_set else (version.extra_files or [])
    md_changed = md != version.skill_md_content
    changed = (
        md_changed
        or script != version.script_content
        or filename != version.script_filename
        or extras != (version.extra_files or [])
    )
    try:
        if md_changed and version.delivery_mode == "registry_direct":
            _validate_new_md(md, authored=True)
        elif md_changed:
            validate_skill_md_content_frontmatter(md)
        unchanged_orphan_git_script = (
            version.delivery_mode == "git_fetch"
            and script == version.script_content
            and filename == version.script_filename
            and not extras
        )
        files = validate_skill_bundle(
            delivery_mode=version.delivery_mode,
            skill_md_content=md,
            script_content=script,
            script_filename=filename,
            extra_files=extras,
            enforce_limits=changed and not unchanged_orphan_git_script,
        )
    except SkillValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for field in (
        "description",
        "skill_path",
        "git_url",
        "git_ref",
        "target_agents",
        "task_type",
        "supported_harnesses",
    ):
        value = getattr(req, field)
        if value is not None:
            setattr(version, field, value)
    version.skill_md_content = md
    version.script_content = script
    version.script_filename = filename
    if "extra_files" in req.model_fields_set:
        version.extra_files = [file.model_dump() for file in req.extra_files]
    if "slash_command" in req.model_fields_set:
        version.slash_command = req.slash_command
    elif md_changed:
        version.slash_command = validate_skill_md_content_frontmatter(md).slash_command
    release_edit_lock(version, current_user.id, force=True)
    await db.flush()
    version.content_revision = skill_content_revision(listing, version)
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return SkillVersionManifest(
        listing_id=listing.id,
        version_id=version.id,
        revision=version.content_revision,
        files=[file.declaration for file in files],
    )


@router.post("/{listing_id}/versions/{version_id}/withdraw", response_model=SkillVersionManifest)
async def withdraw_skill_version(
    listing_id: str,
    version_id: uuid.UUID,
    req: SkillVersionRevisionRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Return pending author bytes to an editable draft; invalidate all old review observations."""
    listing = await resolve_listing(
        SkillListing, listing_id, db, current_user=current_user, load_options=_BODY_FREE_LISTING
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Skill version not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    _, version = await lock_skill_version(db, listing.id, version_id)
    await db.refresh(
        listing,
        attribute_names=["submitted_by", "co_authors", "team_id", "is_private", "name", "namespace", "slug", "owner"],
    )
    if not await check_listing_visibility_async(listing, current_user, db) or (
        get_effective_component_permission(listing, current_user) != "owner"
    ):
        raise HTTPException(status_code=403, detail="Not the listing owner")
    if version.status != ListingStatus.pending or version.pre_public_status is not None:
        raise HTTPException(status_code=409, detail="Only an ordinary pending author version can be withdrawn")
    if version.is_editing and version.editing_by != current_user.id and not _is_lock_expired(version.editing_since):
        raise HTTPException(status_code=409, detail="This skill version is being edited by another author")
    try:
        prior_revision = verified_skill_revision(listing, version)
        if req.observed_revision != prior_revision:
            raise HTTPException(status_code=409, detail="Skill version changed; refresh its manifest before withdrawal")
        files = validate_skill_bundle(
            delivery_mode=version.delivery_mode,
            skill_md_content=version.skill_md_content,
            script_filename=version.script_filename,
            script_content=version.script_content,
            extra_files=version.extra_files or [],
            enforce_limits=False,
        )
    except SkillValidationError as exc:
        raise HTTPException(status_code=409, detail="Saved skill version is not a valid folder") from exc
    version.review_epoch = (version.review_epoch or 0) + 1
    version.status = ListingStatus.draft
    version.rejection_reason = None
    version.reviewed_by = None
    version.reviewed_at = None
    release_edit_lock(version, current_user.id, force=True)
    version.content_revision = skill_content_revision(listing, version)
    await inbox.on_review_withdrawn(
        db, listing, subject_type="skill", actor_id=current_user.id, version=version.version
    )
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return SkillVersionManifest(
        listing_id=listing.id,
        version_id=version.id,
        revision=version.content_revision,
        files=[file.declaration for file in files],
    )


@router.post("/{listing_id}/versions/{version_id}/submit", response_model=SkillVersionManifest)
async def submit_skill_version_draft(
    listing_id: str,
    version_id: uuid.UUID,
    req: SkillVersionRevisionRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Submit a saved exact draft; every resource-bearing version requires the rollout gate."""
    listing, version = await _editable_version(listing_id, version_id, req.observed_revision, db, current_user)
    rollout_enabled = await _ds.get_bool("registry.skill_folder_delivery_enabled", False)
    if version.requires_global_review and not rollout_enabled:
        raise HTTPException(status_code=409, detail="Global public re-review is not yet available")
    if needs_bundle_delivery(version) and not rollout_enabled:
        raise HTTPException(status_code=409, detail="Resource-bearing skill review is not yet available")
    # _editable_version holds the listing lock, but the relationship may have
    # been loaded before waiting. Re-read the pointer, not its cached object.
    await db.refresh(listing, attribute_names=["latest_version_id"])
    latest_id = listing.latest_version_id
    if version.base_version_id is not None and latest_id != version.base_version_id:
        raise HTTPException(status_code=409, detail="Approved base changed; rebase this draft before submitting")
    if latest_id != version.id and not await should_promote_skill_version(db, latest_id, version):
        raise HTTPException(
            status_code=409, detail="Release number is older than the approved version; create a newer draft"
        )
    other_pending = (
        await db.execute(
            select(SkillVersion.id)
            .where(
                SkillVersion.listing_id == listing.id,
                SkillVersion.status == ListingStatus.pending,
                SkillVersion.id != version.id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if other_pending is not None:
        raise HTTPException(status_code=409, detail="Another skill version is already pending review")
    if not version.description:
        raise HTTPException(status_code=422, detail="Description is required before submitting")
    if version.delivery_mode == "git_fetch" and (not isinstance(version.git_url, str) or not version.git_url.strip()):
        raise HTTPException(status_code=422, detail="A Git skill requires a nonempty git_url before submission")
    try:
        files = validate_skill_bundle(
            delivery_mode=version.delivery_mode,
            skill_md_content=version.skill_md_content,
            script_filename=version.script_filename,
            script_content=version.script_content,
            extra_files=version.extra_files or [],
            enforce_limits=False,
        )
        if version.base_version_id is not None and version.delivery_mode == "registry_direct":
            _validate_new_md(version.skill_md_content)
        if needs_bundle_delivery(version):
            declared_skill_folder_name(version.skill_md_content)  # approval must never yield an uninstallable folder
        if version.skill_md_content:
            analysis = validate_skill_md_content_frontmatter(
                version.skill_md_content, slash_command=version.slash_command
            )
            if analysis.slash_command is not None:
                version.slash_command = analysis.slash_command
    except SkillValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if version.status == ListingStatus.rejected:
        # A previous reviewer observation belongs to the rejected round, even
        # when the owner resubmits unchanged bytes. Do not reuse its token.
        version.review_epoch = (version.review_epoch or 0) + 1
    version.status = ListingStatus.pending
    version.rejection_reason = None
    version.reviewed_by = None
    version.reviewed_at = None
    release_edit_lock(version, current_user.id, force=True)
    await db.flush()
    version.content_revision = skill_content_revision(listing, version)
    await inbox.on_review_requested(
        db, listing, subject_type="skill", actor_id=current_user.id, version=version.version
    )
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return SkillVersionManifest(
        listing_id=listing.id,
        version_id=version.id,
        revision=version.content_revision,
        files=[file.declaration for file in files],
    )


@router.post("/{listing_id}/versions/{version_id}/start-edit")
async def start_skill_version_edit(
    listing_id: str,
    version_id: uuid.UUID,
    req: SkillVersionRevisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    _, version = await _editable_version(listing_id, version_id, req.observed_revision, db, current_user)
    acquire_edit_lock(version, current_user.id)
    await db.commit()
    return {"status": "locked"}


@router.post("/{listing_id}/versions/{version_id}/cancel-edit")
async def cancel_skill_version_edit(
    listing_id: str,
    version_id: uuid.UUID,
    req: SkillVersionRevisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    _, version = await _editable_version(listing_id, version_id, req.observed_revision, db, current_user)
    release_edit_lock(version, current_user.id)
    await db.commit()
    return {"status": "unlocked"}


@router.patch("/{listing_id}/versions/{version_id}/files", response_model=SkillVersionManifest)
async def patch_skill_files(
    listing_id: str,
    version_id: uuid.UUID,
    req: SkillFileOperations,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    listing, version = await _editable_version(listing_id, version_id, req.observed_revision, db, current_user)
    try:
        edit = apply_file_operations(version, req)
    except SkillValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = await _save_file_edit(listing, version, edit, db, replace=False, actor_id=current_user.id)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return result


@router.put("/{listing_id}/versions/{version_id}/files", response_model=SkillVersionManifest)
async def replace_skill_files(
    listing_id: str,
    version_id: uuid.UUID,
    req: SkillSnapshotReplace,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    listing, version = await _editable_version(listing_id, version_id, req.observed_revision, db, current_user)
    try:
        edit = replace_folder(req)
    except SkillValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = await _save_file_edit(listing, version, edit, db, replace=True, actor_id=current_user.id)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return result


@router.get("/{listing_id}/versions/{version_id}/manifest", response_model=SkillVersionManifest)
async def get_skill_manifest(
    listing_id: str,
    version_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    listing, version, revision, files = await _authorized_files(listing_id, version_id, db, current_user)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return SkillVersionManifest(
        listing_id=listing.id, version_id=version.id, revision=revision, files=[file.declaration for file in files]
    )


@router.get(
    "/{listing_id}/versions/{version_id}/files/{file_path:path}",
    response_model=SkillFileContents,
    responses={
        200: {
            "description": "UTF-8 files return JSON; binary files download as raw attachment bytes.",
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def get_skill_file(
    listing_id: str,
    version_id: uuid.UUID,
    file_path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    _listing, version, revision, files = await _authorized_files(listing_id, version_id, db, current_user)
    try:
        validate_bundle_path(file_path)
    except SkillValidationError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    file = next((file for file in files if file.path == file_path), None)
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    try:
        content = file.content.decode("utf-8")
    except UnicodeDecodeError:
        basename = file.path.rsplit("/", 1)[-1]
        fallback = "".join(char if char.isascii() and (char.isalnum() or char in "._-") else "_" for char in basename)
        headers["Content-Disposition"] = (
            f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(basename, safe='')}"
        )
        return Response(content=file.content, media_type="application/octet-stream", headers=headers)
    details = SkillFileContents(
        version_id=version.id, revision=revision, file=file.declaration, content=content, encoding="utf-8"
    )
    return Response(content=details.model_dump_json(), media_type="application/json", headers=headers)
