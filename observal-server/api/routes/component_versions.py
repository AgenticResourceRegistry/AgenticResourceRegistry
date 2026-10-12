# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Factory that generates versioning sub-routers for all 5 component types.

Usage in each type's route file::

    from api.routes.component_versions import create_version_router
    router.include_router(create_version_router("mcp", McpListing, McpVersion))
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger as optic
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002
from sqlalchemy.orm import defaultload, defer

import services.dynamic_settings as _ds
from api.deps import (
    check_listing_visibility_async,
    get_db,
    get_effective_component_permission,
    get_registry_user,
    may_view_unapproved,
    require_role,
    resolve_visible_listing,
)
from api.routes._skill_lock import approved_skill_base_id, lock_skill_version, should_promote_skill_version
from api.routes.skill_files import _BODY_FREE_LISTING, _authorized_version
from models.mcp import ListingStatus
from models.skill import SkillListing, SkillVersion
from models.user import User, UserRole
from schemas.component_version import VersionPublishRequest, VersionReviewRequest  # noqa: TC001
from services.agent_lock import INSTALLABLE_STATUSES
from services.component_version_extras import ALLOWED_FIELDS, REQUIRED_FIELDS, validate_and_extract
from services.editing_lock import is_actively_editing
from services.inbox import sources as inbox
from services.registry_fork import VERSION_MANAGED_FIELDS as _VERSION_MANAGED_FIELDS
from services.registry_fork import _copy_version_columns
from services.skill_bundle import declared_skill_folder_name, needs_bundle_delivery, validate_skill_bundle
from services.skill_revisions import skill_content_revision, verified_skill_revision
from services.skill_validator import SkillValidationError
from services.teamspace import can_review, review_scope
from services.versioning import parse_semver

# Semver pattern: X.Y.Z or X.Y.Z-prerelease
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$")


async def audit(*_args, **_kwargs):
    return None


def _parse_semver(v: str) -> tuple[int, ...]:
    """Parse 'X.Y.Z' or 'X.Y.Z-pre' into (X, Y, Z) for comparison."""
    optic.trace("v={}", v)
    base = v.split("-", 1)[0]
    return tuple(int(p) for p in base.split("."))


def _version_to_dict(v, component_type: str, *, summary: bool = False) -> dict:
    """Serialize a version ORM object to a plain dict for API responses."""
    optic.trace("v={}, component_type={}", v, component_type)
    d = {
        "id": str(v.id),
        "listing_id": str(v.listing_id),
        "version": v.version,
        "description": v.description,
        "changelog": v.changelog,
        "status": v.status.value if hasattr(v.status, "value") else v.status,
        "rejection_reason": v.rejection_reason,
        "download_count": v.download_count,
        "supported_harnesses": v.supported_harnesses,
        "released_by": str(v.released_by),
        "released_at": v.released_at,
        "created_at": v.created_at,
    }
    if component_type == "skill" and hasattr(v, "requires_global_review"):
        d["requires_global_review"] = bool(v.requires_global_review)
    for attr in ALLOWED_FIELDS.get(component_type, set()):
        if summary and component_type == "skill" and attr in {"extra_files", "skill_md_content", "script_content"}:
            continue
        if hasattr(v, attr):
            d[attr] = getattr(v, attr)
    return d


# ---------------------------------------------------------------------------
# Standalone async functions (exposed for direct testing)
# ---------------------------------------------------------------------------


async def _list_versions(
    listing_id: str,
    page: int,
    page_size: int,
    listing_model,
    version_model,
    component_type: str,
    db: AsyncSession,
    current_user: User | None,
) -> dict:
    optic.trace("listing_id={}, page={}", listing_id, page)
    summary_options = (
        (
            defaultload(SkillListing.latest_version)
            .defer(SkillVersion.extra_files)
            .defer(SkillVersion.skill_md_content)
            .defer(SkillVersion.script_content),
            defaultload(SkillListing.versions)
            .defer(SkillVersion.extra_files)
            .defer(SkillVersion.skill_md_content)
            .defer(SkillVersion.script_content),
        )
        if component_type == "skill"
        else ()
    )
    listing = await resolve_visible_listing(
        listing_model, listing_id, db, current_user, **({"load_options": summary_options} if summary_options else {})
    )
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    # A version carries the real payload: prompt templates, MCP commands and args,
    # hook handler config, SKILL.md. Seeing the listing is not enough to read a
    # version that has not been approved. This matters most right after a team
    # listing goes public: the listing is public immediately while its versions
    # return to the review queue, and without this filter an ordinary caller reads
    # content no global reviewer has accepted yet.
    version_filters = [version_model.listing_id == listing.id]
    if not may_view_unapproved(get_effective_component_permission(listing, current_user), current_user):
        if component_type == "skill":
            version_filters.extend(
                (SkillVersion.status.in_(INSTALLABLE_STATUSES), SkillVersion.requires_global_review.is_(False))
            )
        else:
            version_filters.append(version_model.status == ListingStatus.approved)

    offset = (page - 1) * page_size
    stmt = (
        select(version_model)
        .options(
            *(
                defer(version_model.extra_files),
                defer(version_model.skill_md_content),
                defer(version_model.script_content),
            )
            if component_type == "skill"
            else ()
        )
        .where(*version_filters)
        .order_by(version_model.released_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    versions = result.scalars().all()

    count_stmt = select(func.count(version_model.id)).where(*version_filters)
    total = (await db.execute(count_stmt)).scalar() or 0

    return {
        "items": [_version_to_dict(v, component_type, summary=True) for v in versions],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def _get_version(
    listing_id: str,
    version: str,
    listing_model,
    version_model,
    component_type: str,
    db: AsyncSession,
    current_user: User | None,
) -> dict:
    optic.trace("listing_id={}, version={}", listing_id, version)
    if component_type == "skill":
        listing = await resolve_visible_listing(
            SkillListing, listing_id, db, current_user, load_options=_BODY_FREE_LISTING
        )
        if not listing:
            raise HTTPException(status_code=404, detail="Listing not found")
        version_id = (
            await db.execute(
                select(SkillVersion.id).where(SkillVersion.listing_id == listing.id, SkillVersion.version == version)
            )
        ).scalar_one_or_none()
        if version_id is None:
            raise HTTPException(status_code=404, detail="Version not found")
        _, ver = await _authorized_version(str(listing.id), version_id, db, current_user)
        result = _version_to_dict(ver, component_type)
        try:
            result["revision"] = verified_skill_revision(listing, ver)
        except SkillValidationError as exc:
            raise HTTPException(status_code=409, detail="Stored skill version is not a valid release") from exc
        return result

    listing = await resolve_visible_listing(listing_model, listing_id, db, current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    version_filters = [version_model.listing_id == listing.id, version_model.version == version]
    if not may_view_unapproved(get_effective_component_permission(listing, current_user), current_user):
        version_filters.append(version_model.status == ListingStatus.approved)
    stmt = select(version_model).where(*version_filters)
    result = await db.execute(stmt)
    ver = result.scalar_one_or_none()
    if not ver:
        raise HTTPException(status_code=404, detail="Version not found")

    return _version_to_dict(ver, component_type)


async def _publish_version(
    listing_id: str,
    req: VersionPublishRequest,
    listing_model,
    version_model,
    component_type: str,
    db: AsyncSession,
    current_user: User,
) -> dict:
    optic.trace("listing_id={}, listing_model={}", listing_id, listing_model)
    if not SEMVER_RE.match(req.version):
        raise HTTPException(status_code=422, detail=f"Invalid semver string: {req.version!r}")

    listing = await resolve_visible_listing(listing_model, listing_id, db, current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Only the listing owner can publish versions")

    current_version = listing.latest_version
    approved_base = None
    if component_type == "skill":
        if current_version is None:
            raise HTTPException(status_code=409, detail="Skill listing has no version to publish from")
        latest_id, locked_current = await lock_skill_version(db, listing.id, current_version.id)
        if latest_id != locked_current.id:
            raise HTTPException(status_code=409, detail="Skill latest release changed; refresh before publishing")
        await db.refresh(listing, attribute_names=["submitted_by", "co_authors", "team_id", "is_private"])
        if not await check_listing_visibility_async(listing, current_user, db) or (
            get_effective_component_permission(listing, current_user) != "owner"
        ):
            raise HTTPException(status_code=403, detail="Only the listing owner can publish versions")
        approved_id = await approved_skill_base_id(db, listing.id, locked_current)
        if approved_id is not None:
            approved_base = (
                locked_current
                if approved_id == locked_current.id
                else (
                    await db.execute(
                        select(SkillVersion)
                        .where(SkillVersion.id == approved_id, SkillVersion.listing_id == listing.id)
                        .execution_options(populate_existing=True)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
            )
            if (
                approved_base is None
                or approved_base.status not in INSTALLABLE_STATUSES
                or approved_base.requires_global_review
            ):
                raise HTTPException(status_code=409, detail="Approved skill base changed; refresh before publishing")
        if approved_base is None:
            # Never derive an unmarked pending release from historical bytes
            # still owed global review. The current pointer can also be a
            # candidate behind an older, explicitly installable prerelease;
            # that is not a safe implicit base for a new version.
            older_installable = (
                await db.execute(
                    select(SkillVersion.id)
                    .where(SkillVersion.listing_id == listing.id, SkillVersion.status.in_(INSTALLABLE_STATUSES))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if locked_current.requires_global_review or older_installable is not None:
                raise HTTPException(
                    status_code=409, detail="No cleared stable approved skill base; review or select a base"
                )
            if needs_bundle_delivery(locked_current):
                raise HTTPException(
                    status_code=409, detail="Approve the first skill version before publishing another bundle"
                )
        current_version = approved_base or locked_current
        if current_version.delivery_mode == "registry_direct":
            try:
                verified_skill_revision(listing, current_version)
            except SkillValidationError as exc:
                raise HTTPException(status_code=409, detail="Approved skill base is not a valid folder") from exc

    # Duplicate check
    dup_stmt = select(version_model).where(
        version_model.listing_id == listing.id,
        version_model.version == req.version,
    )
    dup_result = await db.execute(dup_stmt)
    if dup_result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"Version {req.version!r} already exists for this listing")

    effective_extra = dict(req.extra or {})
    for field in REQUIRED_FIELDS.get(component_type, set()):
        if field not in effective_extra:
            value = getattr(current_version if component_type == "skill" else listing, field, None)
            if value is not None:
                effective_extra[field] = value
    extra_fields = validate_and_extract(component_type, effective_extra)
    now = datetime.now(UTC)
    snapshot = (
        _copy_version_columns(version_model, current_version, skip=_VERSION_MANAGED_FIELDS) if current_version else {}
    )
    if component_type == "skill" and snapshot.get("extra_files") is None:
        snapshot["extra_files"] = []
    if "supported_harnesses" in req.model_fields_set:
        snapshot["supported_harnesses"] = req.supported_harnesses
    ver = version_model(
        **snapshot,
        listing_id=listing.id,
        version=req.version,
        description=req.description,
        changelog=req.changelog,
        status=ListingStatus.pending,
        released_by=current_user.id,
        released_at=now,
    )
    for field_name, value in extra_fields.items():
        setattr(ver, field_name, deepcopy(value))

    if component_type == "skill":
        ver.base_version_id = approved_base.id if approved_base is not None else None
        if approved_base is not None:
            try:
                ver.base_revision = verified_skill_revision(listing, approved_base)
            except SkillValidationError as exc:
                raise HTTPException(status_code=409, detail="Approved skill base is not a valid release") from exc
        # Historical git rows can have one orphaned script field. A new version
        # may inherit that inert metadata, but an explicit script/mode override
        # must satisfy the current coherent-field contract.
        inherited_legacy_git_script = (
            current_version is not None
            and current_version.delivery_mode == ver.delivery_mode == "git_fetch"
            and (current_version.script_content is None) != (current_version.script_filename is None)
            and ver.script_content == current_version.script_content
            and ver.script_filename == current_version.script_filename
            and not {"script_content", "script_filename", "delivery_mode"}.intersection(req.extra or {})
        )
        try:
            validate_skill_bundle(
                delivery_mode=ver.delivery_mode,
                skill_md_content=ver.skill_md_content,
                script_content=ver.script_content,
                script_filename=ver.script_filename,
                extra_files=ver.extra_files or [],
                enforce_limits=not inherited_legacy_git_script,
            )
        except SkillValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.add(ver)
    await db.flush()
    if component_type == "skill":
        # A generic publish must owe the same exact revision-bound review as
        # a folder draft. Without this, approved-base successors become stuck
        # pending: listing review refuses them and exact review has no bound base.
        try:
            ver.content_revision = skill_content_revision(listing, ver)
        except SkillValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    # This route always creates a pending version, so a review is always owed.
    await inbox.on_publish(
        db,
        listing,
        subject_type=component_type,
        actor_id=current_user.id,
        auto_approved=False,
        version=ver.version,
    )
    await db.commit()

    return _version_to_dict(ver, component_type)


async def _version_suggestions(
    listing_id: str,
    listing_model,
    version_model,
    db: AsyncSession,
    current_user: User,
) -> dict:
    optic.trace("listing_id={}, listing_model={}", listing_id, listing_model)
    listing = await resolve_visible_listing(listing_model, listing_id, db, current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    from services.versioning import parse_semver, suggest_versions

    # Use the highest existing version (including pending) to avoid duplicate suggestions
    all_ver_stmt = (
        select(version_model.version)
        .where(version_model.listing_id == listing.id)
        .order_by(version_model.released_at.desc())
    )
    all_ver_result = await db.execute(all_ver_stmt)
    all_versions = [v for (v,) in all_ver_result.all()]

    highest = listing.latest_version.version if listing.latest_version else "0.0.0"
    for v in all_versions:
        parsed = parse_semver(v)
        if parsed and parsed > (parse_semver(highest) or (0, 0, 0)):
            highest = v

    return {"current": highest, "suggestions": suggest_versions(highest)}


async def _review_version(
    listing_id: str,
    version: str,
    req: VersionReviewRequest,
    listing_model,
    version_model,
    component_type: str,
    db: AsyncSession,
    current_user: User,
    *,
    selected_id: uuid.UUID | None = None,
) -> dict:
    optic.trace("listing_id={}, version={}", listing_id, version)
    listing = await resolve_visible_listing(
        listing_model,
        listing_id,
        db,
        current_user,
        **({"load_options": _BODY_FREE_LISTING} if component_type == "skill" else {}),
    )
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    latest_id = None
    if component_type == "skill":
        if not can_review(listing, await review_scope(db, current_user)):
            raise HTTPException(status_code=404, detail="Version not found")
        version_id = selected_id
        if version_id is None:
            version_id = (
                await db.execute(
                    select(SkillVersion.id).where(
                        SkillVersion.listing_id == listing.id, SkillVersion.version == version
                    )
                )
            ).scalar_one_or_none()
        if version_id is None:
            raise HTTPException(status_code=404, detail="Version not found")
        latest_id, ver = await lock_skill_version(db, listing.id, version_id)
        await db.refresh(
            listing,
            attribute_names=[
                "submitted_by",
                "co_authors",
                "team_id",
                "is_private",
                "name",
                "namespace",
                "slug",
                "owner",
            ],
        )
        scope = await review_scope(db, current_user)
        if (
            not await check_listing_visibility_async(listing, current_user, db)
            or not can_review(listing, scope)
            or (ver.requires_global_review and not scope.is_global_reviewer)
        ):
            raise HTTPException(status_code=404, detail="Version not found")
    else:
        version_filters = [version_model.listing_id == listing.id, version_model.version == version]
        if not may_view_unapproved(get_effective_component_permission(listing, current_user), current_user):
            version_filters.append(version_model.status == ListingStatus.approved)
        ver = (await db.execute(select(version_model).where(*version_filters))).scalar_one_or_none()
        if not ver:
            raise HTTPException(status_code=404, detail="Version not found")
    if ver.status != ListingStatus.pending:
        raise HTTPException(
            status_code=422, detail=f"Version is {ver.status.value!r}, only pending versions can be reviewed"
        )

    if component_type == "skill":
        if is_actively_editing(ver):
            raise HTTPException(status_code=409, detail="Cannot review: the owner is editing this skill version")
        if ver.requires_global_review and (selected_id is None or req.observed_revision is None):
            raise HTTPException(status_code=409, detail="Review the marked skill UUID with its observed revision")
        if needs_bundle_delivery(ver):
            if selected_id is None or req.observed_revision is None:
                raise HTTPException(status_code=409, detail="Review the bundled skill UUID with its observed revision")
            if not await _ds.get_bool("registry.skill_folder_delivery_enabled", False):
                raise HTTPException(status_code=409, detail="Skill folder review is disabled until fleet rollout")
            if req.action == "approve":
                try:  # Whatever route created this version, never approve a folder that cannot install.
                    declared_skill_folder_name(ver.skill_md_content)
                except SkillValidationError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
        if (ver.base_version_id is not None or ver.content_revision is not None or (ver.review_epoch or 0) > 0) and (
            req.observed_revision is None
        ):
            raise HTTPException(status_code=409, detail="Review this exact draft with its observed revision")
        if req.observed_revision is not None or ver.content_revision is not None:
            try:
                revision = verified_skill_revision(listing, ver)
            except SkillValidationError as exc:
                raise HTTPException(status_code=409, detail="Skill version is not a valid release") from exc
            if (ver.content_revision is not None and ver.content_revision != revision) or (
                req.observed_revision is not None and req.observed_revision != revision
            ):
                raise HTTPException(status_code=409, detail="Skill version changed; refresh before review")

    promote_skill = None
    marked = component_type == "skill" and ver.requires_global_review
    re_review = marked and ver.pre_public_status is not None
    if req.action == "approve" and component_type == "skill" and not re_review:
        if ver.base_version_id is not None:
            if latest_id != ver.base_version_id:
                raise HTTPException(status_code=409, detail="Approved base changed; rebase this draft before review")
            base = (
                await db.execute(
                    select(SkillVersion)
                    .where(SkillVersion.id == ver.base_version_id, SkillVersion.listing_id == listing.id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if base is None or base.status not in INSTALLABLE_STATUSES:
                raise HTTPException(status_code=409, detail="Approved base changed; rebase this draft before review")
            try:
                base_revision = verified_skill_revision(listing, base)
            except SkillValidationError as exc:
                raise HTTPException(status_code=409, detail="Approved base is not a valid release") from exc
            if ver.base_revision != base_revision:
                raise HTTPException(status_code=409, detail="Approved base changed; rebase this draft before review")
            if (
                base.delivery_mode == "git_fetch"
                and ver.delivery_mode == "registry_direct"
                and not req.git_base_acknowledged
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Git base files are not stored; explicitly acknowledge the limited comparison before approval",
                )
        promote_skill = await should_promote_skill_version(db, latest_id, ver)
        # Historical pending prereleases may still clear review and be pinned
        # explicitly; they never replace a newer stable default release.
        if not promote_skill and ("-" not in ver.version or not SEMVER_RE.fullmatch(ver.version)):
            raise HTTPException(status_code=409, detail="Newer approved skill release exists; refresh before review")
    if req.action == "approve":
        ver.status = (
            ListingStatus(ver.pre_public_status)
            if re_review and ver.pre_public_status is not None
            else ListingStatus.approved
        )
        ver.rejection_reason = None
        if re_review:
            ver.requires_global_review = False
            ver.pre_public_status = None
            ver.pre_public_reviewed_by = None
            ver.pre_public_reviewed_at = None
            await db.flush()
            # Historical re-review is not a new-release approval: older rows
            # can clear out of order. Repair a still-pending pointer to the
            # highest cleared stable release, without demoting a newer one.
            cleared = (
                await db.execute(
                    select(SkillVersion.id, SkillVersion.version, SkillVersion.status).where(
                        SkillVersion.listing_id == listing.id,
                        SkillVersion.status.in_(INSTALLABLE_STATUSES),
                        SkillVersion.requires_global_review.is_(False),
                    )
                )
            ).all()
            current = next((row for row in cleared if row.id == latest_id), None)
            approved = [row for row in cleared if row.status == ListingStatus.approved]
            stable = [(row, parse_semver(row.version)) for row in (approved or cleared)]
            stable = [(row, number) for row, number in stable if number is not None]
            if current is not None and current.status == ListingStatus.archived:
                pass  # Preserve an intentionally archived pointer.
            elif stable:
                best, best_number = max(stable, key=lambda pair: (pair[1], str(pair[0].id)))
                current_number = parse_semver(current.version) if current is not None else None
                if current is None or (current_number is not None and best_number > current_number):
                    listing.latest_version_id = best.id
            elif current is None and len(cleared) == 1:
                listing.latest_version_id = cleared[0].id
            elif current is None:
                raise HTTPException(status_code=409, detail="Cannot select a default from historical skill versions")
        # Only update latest if this version is newer than current latest
        elif component_type == "skill":
            if marked:
                ver.requires_global_review = False
            if promote_skill:
                listing.latest_version_id = ver.id
        else:
            current_latest = listing.latest_version
            if not current_latest or _parse_semver(ver.version) >= _parse_semver(current_latest.version):
                listing.latest_version_id = ver.id
    else:
        ver.status = ListingStatus.rejected
        ver.rejection_reason = req.reason

    ver.reviewed_by = current_user.id
    ver.reviewed_at = datetime.now(UTC)

    # Same fact as a decision made through api/routes/review.py: the version's
    # author hears the outcome, and every reviewer's open request item for this
    # version is cleared. Delivered before the commit, in this transaction.
    await inbox.on_review_decided(
        db,
        listing,
        subject_type=component_type,
        approved=req.action == "approve",
        actor_id=current_user.id,
        version=ver.version,
        reason=req.reason if req.action != "approve" else None,
        submitter_id=ver.released_by,
    )

    await db.commit()

    return {
        "version": ver.version,
        "new_status": ver.status.value,
        "reason": ver.rejection_reason,
    }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_version_router(
    component_type: str,
    listing_model,
    version_model,
) -> APIRouter:
    """Return an APIRouter with 4 version endpoints for the given component type."""

    optic.trace("component_type={}, listing_model={}", component_type, listing_model)
    router = APIRouter(tags=[f"{component_type}-versions"])

    @router.get("/{listing_id}/versions")
    async def list_versions(
        listing_id: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        db: AsyncSession = Depends(get_db),
        current_user: User | None = Depends(get_registry_user),
    ):
        optic.trace("listing_id={}, page={}", listing_id, page)
        return await _list_versions(
            listing_id=listing_id,
            page=page,
            page_size=page_size,
            listing_model=listing_model,
            version_model=version_model,
            component_type=component_type,
            db=db,
            current_user=current_user,
        )

    @router.get("/{listing_id}/versions/{version}")
    async def get_version(
        listing_id: str,
        version: str,
        db: AsyncSession = Depends(get_db),
        current_user: User | None = Depends(get_registry_user),
    ):
        optic.trace("listing_id={}, version={}", listing_id, version)
        return await _get_version(
            listing_id=listing_id,
            version=version,
            listing_model=listing_model,
            version_model=version_model,
            component_type=component_type,
            db=db,
            current_user=current_user,
        )

    @router.post("/{listing_id}/versions")
    async def publish_version(
        listing_id: str,
        req: VersionPublishRequest,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(require_role(UserRole.user)),
    ):
        optic.trace("listing_id={}", listing_id)
        return await _publish_version(
            listing_id=listing_id,
            req=req,
            listing_model=listing_model,
            version_model=version_model,
            component_type=component_type,
            db=db,
            current_user=current_user,
        )

    @router.post("/{listing_id}/versions/{version}/review")
    async def review_version(
        listing_id: str,
        version: str,
        req: VersionReviewRequest,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(require_role(UserRole.reviewer)),
    ):
        optic.trace("listing_id={}, version={}", listing_id, version)
        return await _review_version(
            listing_id=listing_id,
            version=version,
            req=req,
            listing_model=listing_model,
            version_model=version_model,
            component_type=component_type,
            db=db,
            current_user=current_user,
        )

    @router.get("/{listing_id}/version-suggestions")
    async def version_suggestions(
        listing_id: str,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(require_role(UserRole.user)),
    ):
        optic.trace("listing_id={}", listing_id)
        return await _version_suggestions(
            listing_id=listing_id,
            listing_model=listing_model,
            version_model=version_model,
            db=db,
            current_user=current_user,
        )

    return router
