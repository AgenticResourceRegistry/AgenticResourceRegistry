# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import asyncio
import enum
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from loguru import logger as optic
from pydantic import BaseModel
from sqlalchemy import String, cast, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defaultload, defer

from api.deps import get_current_user, get_db, resolve_prefix_id
from api.routes._skill_lock import lock_skill_version, should_promote_skill_version
from api.routes.component_versions import SEMVER_RE as SKILL_SEMVER_RE
from api.routes.component_versions import _review_version
from api.routes.skill_files import _authorized_files, _authorized_version
from api.sanitize import escape_like
from models.agent import Agent, AgentStatus, AgentVersion
from models.component_bundle import ComponentBundle
from models.hook import HookListing, HookVersion
from models.mcp import ListingStatus, McpListing, McpVersion
from models.prompt import PromptListing, PromptVersion
from models.sandbox import SandboxListing, SandboxVersion
from models.skill import SkillListing, SkillVersion
from models.user import User
from schemas.component_version import VersionReviewRequest
from schemas.mcp import ReviewActionRequest
from services.cache import invalidate_namespace
from services.editing_lock import is_actively_editing
from services.inbox import sources as inbox
from services.redis import publish as redis_publish
from services.registry_fork import provenance_for, provenance_for_many
from services.security_events import EventType, SecurityEvent, Severity, emit_security_event
from services.skill_bundle import needs_bundle_delivery
from services.skill_revisions import skill_content_revision
from services.skill_validator import SkillValidationError
from services.teamspace import ReviewScope, can_review, review_scope

router = APIRouter(prefix="/api/v1/review", tags=["review"])

LISTING_MODELS = {
    "mcp": McpListing,
    "skill": SkillListing,
    "hook": HookListing,
    "prompt": PromptListing,
    "sandbox": SandboxListing,
}

VERSION_MODELS = {
    "mcp": McpVersion,
    "skill": SkillVersion,
    "hook": HookVersion,
    "prompt": PromptVersion,
    "sandbox": SandboxVersion,
}

# The bundle routes iterate LISTING_MODELS by value and lose the type key, but an
# inbox item has to name what it is about. Invert the map once here rather than
# threading the key through _bundle_listings.
_TYPE_BY_MODEL = {model: listing_type for listing_type, model in LISTING_MODELS.items()}


def _listing_type_of(listing) -> str:
    return _TYPE_BY_MODEL.get(type(listing), "mcp")


# ---------------------------------------------------------------------------
# Review authorization
#
# Every route below used to sit behind require_role(UserRole.reviewer), the
# GLOBAL role. That routed team-private items to reviewers outside the team and
# left the team blocked until one of them acted, while the team's own owners and
# reviewers got a 403. Authorization is now capability scoped: the queue, the
# detail view, and the approve and reject actions all read the same ReviewScope,
# so what a caller can list and what a caller can act on cannot drift apart.
# ---------------------------------------------------------------------------


async def _require_review_scope(db: AsyncSession, current_user: User) -> ReviewScope:
    """Resolve the caller's review capability, refusing callers who have none.

    A caller with neither a global review role nor an owner or reviewer seat in
    any teamspace has no business on these routes at all, so this keeps the 403
    that require_role used to raise rather than handing back an empty queue.
    """
    scope = await review_scope(db, current_user)
    if scope.is_empty:
        await emit_security_event(
            SecurityEvent(
                event_type=EventType.PERMISSION_DENIED,
                severity=Severity.WARNING,
                outcome="failure",
                actor_id=str(current_user.id),
                actor_email=current_user.email,
                actor_role=current_user.role.value,
                detail="Review access requires a global review role or a team owner or reviewer seat",
            )
        )
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return scope


def _authorize_item(entity, scope: ReviewScope) -> None:
    """Authorize one item from its own visibility and teamspace.

    A team-private item the caller cannot review answers 404, matching the read
    paths: the queue already hides it, so a 403 naming it as team-private would
    confirm both that the id exists and that it is private. A public item is
    already discoverable, so refusing it can name the review-scope boundary.
    """
    if can_review(entity, scope):
        return
    if getattr(entity, "is_private", False):
        raise HTTPException(status_code=404, detail="Submission not found")
    raise HTTPException(
        status_code=403,
        detail="Public item is outside your review scope",
    )


def _in_scope(entity, scope: ReviewScope, team_id: uuid.UUID | None) -> bool:
    """Whether one pending item belongs in this caller's queue."""
    if not can_review(entity, scope):
        return False
    return team_id is None or getattr(entity, "team_id", None) == team_id


def _check_team_filter(team_id: uuid.UUID | None, scope: ReviewScope) -> None:
    """Reject a ?team_id= narrowing to a teamspace the caller does not review for."""
    if team_id is None:
        return
    if scope.is_admin or scope.is_global_reviewer or team_id in scope.team_ids:
        return
    raise HTTPException(status_code=403, detail="You do not review for this teamspace")


_SKILL_REVIEW_LOOKUP_OPTIONS = (
    defaultload(SkillListing.latest_version)
    .defer(SkillVersion.extra_files)
    .defer(SkillVersion.skill_md_content)
    .defer(SkillVersion.script_content),
    defaultload(SkillListing.versions)
    .defer(SkillVersion.extra_files)
    .defer(SkillVersion.skill_md_content)
    .defer(SkillVersion.script_content),
)


async def _find_listing(listing_id: str, db: AsyncSession):
    """Find a listing by ID, prefix, or name without preloading skill bodies."""
    optic.trace("listing_id={}", listing_id)
    hits = []
    for listing_type, model in LISTING_MODELS.items():
        try:
            listing = await resolve_prefix_id(
                model, listing_id, db, load_options=_SKILL_REVIEW_LOOKUP_OPTIONS if model is SkillListing else None
            )
            hits.append((listing_type, listing))
        except HTTPException as e:
            if e.status_code == 400 and "too short" not in str(e.detail):
                raise e
            continue

    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        types = [h[0] for h in hits]
        raise HTTPException(
            status_code=400,
            detail=f"Prefix '{listing_id}' matches records across multiple types: {', '.join(types)}",
        )

    # Fallback: name-based lookup
    for listing_type, model in LISTING_MODELS.items():
        stmt = select(model).where(model.name == listing_id)
        if model is SkillListing:
            stmt = stmt.options(*_SKILL_REVIEW_LOOKUP_OPTIONS)
        result = await db.execute(stmt)
        listing = result.scalar_one_or_none()
        if listing:
            return listing_type, listing

    return None, None


async def _check_agent_components_ready(
    components, db: AsyncSession, *, require_public_skills: bool = False
) -> tuple[bool, list[dict]]:
    """Check that the component version each pin names is approved.

    An agent version is approved together with the exact component releases it
    pins, so the gate reads those releases, not each listing's latest one.
    """
    optic.trace("components={}", components)
    if not components:
        return True, []
    from services.agent_lock import pinned_component_blockers

    blocking: list[dict] = []
    if require_public_skills:
        # Serialize public-agent review with skill privatization. Both acquire
        # skill listing locks before trusting visibility or committing approval.
        skill_ids = sorted({c.component_id for c in components if c.component_type == "skill"})
        if skill_ids:
            rows = (
                await db.execute(
                    select(SkillListing.id, SkillListing.is_private)
                    .where(SkillListing.id.in_(skill_ids))
                    .order_by(SkillListing.id)
                    .with_for_update(read=True)
                )
            ).all()
            visibility = dict(rows)
            for component in components:
                if component.component_type == "skill" and visibility.get(component.component_id, True):
                    blocking.append(
                        {
                            "component_type": "skill",
                            "component_id": str(component.component_id),
                            "name": component.component_name,
                            "version": component.resolved_version,
                            "status": "not_public",
                        }
                    )
    # Read pin status and digest only after acquiring the listing locks: a
    # concurrent skill review transition may commit while we wait for SHARE.
    # An earlier read would approve a public agent with now-pending bytes.
    blocking.extend(await pinned_component_blockers(db, components))
    return len(blocking) == 0, blocking


async def _query_pending_agents(
    db: AsyncSession, scope: ReviewScope, team_id: uuid.UUID | None = None, current_user: User | None = None
) -> list[dict]:
    # Find agents that have ANY pending version (not just latest_version_id).
    # This ensures version updates appear in the review queue after the first
    # version is approved.
    optic.debug("_query_pending_agents called")
    pending_versions_stmt = (
        select(AgentVersion).where(AgentVersion.status == AgentStatus.pending).order_by(AgentVersion.created_at.desc())
    )
    pending_versions = (await db.execute(pending_versions_stmt)).scalars().all()

    if not pending_versions:
        return []

    # Group by agent_id, take the newest pending version per agent.
    # Skip versions that are actively being edited by their owner.
    seen_agents: dict[uuid.UUID, AgentVersion] = {}
    for v in pending_versions:
        if v.agent_id not in seen_agents and not is_actively_editing(v):
            seen_agents[v.agent_id] = v

    # Load the agents, dropping the ones this caller may not review. Agents carry
    # is_private and team_id exactly like component listings do.
    agent_ids = list(seen_agents.keys())
    agents_result = await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))
    agents_map = {a.id: a for a in agents_result.scalars().all() if _in_scope(a, scope, team_id)}
    seen_agents = {aid: v for aid, v in seen_agents.items() if aid in agents_map}

    user_ids = {a.created_by for a in agents_map.values()}
    user_ids.update(v.released_by for v in seen_agents.values())
    user_map: dict[uuid.UUID, str] = {}
    if user_ids:
        rows = await db.execute(select(User.id, User.username).where(User.id.in_(user_ids)))
        user_map = {r[0]: (r[1] or "") for r in rows.all()}

    provenance = await provenance_for_many(list(agents_map.values()), current_user, db)
    items = []
    for agent_id, pending_ver in seen_agents.items():
        a = agents_map.get(agent_id)
        if not a:
            continue
        components_ready, blocking = await _check_agent_components_ready(pending_ver.components, db)
        items.append(
            {
                "type": "agent",
                "id": str(a.id),
                "name": a.name,
                "description": pending_ver.description or a.description or "",
                "version": pending_ver.version,
                "owner": a.owner or "",
                "status": pending_ver.status.value,
                "submitted_by": user_map.get(pending_ver.released_by, str(pending_ver.released_by)),
                "created_at": pending_ver.created_at.isoformat() if pending_ver.created_at else "",
                "prompt": pending_ver.prompt or "",
                "component_count": len(pending_ver.components) if pending_ver.components else 0,
                "components_ready": components_ready,
                "blocking_components": blocking,
                "gaming_flags": pending_ver.gaming_flags,
                "forked_from": provenance.get(a.id),
            }
        )
    return items


async def _query_pending_components(
    db: AsyncSession,
    scope: ReviewScope,
    type_filter: str | None = None,
    team_id: uuid.UUID | None = None,
    current_user: User | None = None,
) -> list[dict]:
    optic.trace("type_filter={}", type_filter)
    models_to_query = (
        {type_filter: LISTING_MODELS[type_filter]} if type_filter and type_filter in LISTING_MODELS else LISTING_MODELS
    )
    items = []
    user_ids: set[uuid.UUID] = set()
    for listing_type, model in models_to_query.items():
        version_model = VERSION_MODELS[listing_type]
        # Find listings that have ANY pending version (not just latest_version_id).
        # This ensures version updates appear in the queue after first approval.
        pending_versions_stmt = (
            select(version_model)
            .options(
                *(
                    defer(SkillVersion.extra_files),
                    defer(SkillVersion.skill_md_content),
                    defer(SkillVersion.script_content),
                )
                if listing_type == "skill"
                else ()
            )
            .where(version_model.status == ListingStatus.pending)
            .order_by(version_model.released_at.desc())
        )
        pending_versions = (await db.execute(pending_versions_stmt)).scalars().all()
        if not pending_versions:
            continue

        # Skill review targets are version UUIDs, even when several candidates
        # share one listing. Other component queues retain their legacy grouping.
        pending_targets = []
        seen_listings: set[uuid.UUID] = set()
        for pv in pending_versions:
            if is_actively_editing(pv):
                continue
            if listing_type == "skill" or pv.listing_id not in seen_listings:
                pending_targets.append(pv)
                seen_listings.add(pv.listing_id)

        if not pending_targets:
            continue

        # Load the listings. A listing this caller may not review is dropped here,
        # before anything about it reaches the response: a global reviewer who is
        # not in the team must not even learn a team-private item's name.
        listing_stmt = select(model).where(model.id.in_(seen_listings))
        if listing_type == "skill":
            listing_stmt = listing_stmt.options(
                defaultload(SkillListing.latest_version)
                .defer(SkillVersion.extra_files)
                .defer(SkillVersion.skill_md_content)
                .defer(SkillVersion.script_content),
                defaultload(SkillListing.versions)
                .defer(SkillVersion.extra_files)
                .defer(SkillVersion.skill_md_content)
                .defer(SkillVersion.script_content),
            )
        listings_result = await db.execute(listing_stmt)
        listings_map = {r.id: r for r in listings_result.scalars().all() if _in_scope(r, scope, team_id)}

        provenance = await provenance_for_many(list(listings_map.values()), current_user, db)
        for pv in pending_targets:
            r = listings_map.get(pv.listing_id)
            if not r or (
                listing_type == "skill"
                and pv.requires_global_review
                and (not scope.is_global_reviewer or (r.is_private and not scope.is_admin))
            ):
                continue
            user_ids.add(r.submitted_by)
            item: dict = {
                "type": listing_type,
                "id": str(r.id),
                "name": r.name,
                "description": getattr(pv, "description", None) or getattr(r, "description", None) or "",
                "version": getattr(pv, "version", None) or "",
                "owner": getattr(r, "owner", None) or "",
                "status": pv.status.value,
                "submitted_by": r.submitted_by,
                "created_at": pv.created_at.isoformat()
                if hasattr(pv, "created_at") and pv.created_at
                else r.created_at.isoformat(),
                "bundle_id": str(r.bundle_id) if isinstance(getattr(r, "bundle_id", None), uuid.UUID) else None,
            }
            if listing_type == "skill":
                item["version_id"] = str(pv.id)
                item["review_key"] = f"skill:{pv.id}"
            item["forked_from"] = provenance.get(r.id)
            # Include validation results for MCP listings
            if listing_type == "mcp" and hasattr(r, "validation_results"):
                item["mcp_validated"] = getattr(r, "mcp_validated", False)
                item["validation_results"] = [
                    {
                        "stage": vr.stage,
                        "passed": vr.passed,
                        "details": vr.details,
                        "run_at": vr.run_at.isoformat() if vr.run_at else None,
                    }
                    for vr in r.validation_results
                ]
            items.append(item)

    # Resolve bundle names
    bundle_ids = {i["bundle_id"] for i in items if i.get("bundle_id")}
    bundle_map: dict[str, str] = {}
    if bundle_ids:
        brows = await db.execute(
            select(ComponentBundle.id, ComponentBundle.name).where(
                ComponentBundle.id.in_([uuid.UUID(b) for b in bundle_ids])
            )
        )
        bundle_map = {str(r[0]): r[1] for r in brows.all()}
    for item in items:
        if item.get("bundle_id"):
            item["bundle_name"] = bundle_map.get(item["bundle_id"], "")

    # Resolve user UUIDs to display names
    user_map: dict[uuid.UUID, str] = {}
    if user_ids:
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        for u in result.scalars().all():
            user_map[u.id] = u.username or u.email

    for item in items:
        uid = item["submitted_by"]
        item["submitted_by"] = user_map.get(uid, str(uid))

    return items


@router.get("")
async def list_pending(
    type: str | None = Query(None),
    tab: str | None = Query(
        None,
        description="Filter by type: 'agents' or 'components'. Defaults to all pending items.",
    ),
    team_id: uuid.UUID | None = Query(
        None,
        description="Narrow the queue to one teamspace the caller reviews for.",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    optic.trace("type={}", type)
    scope = await _require_review_scope(db, current_user)
    _check_team_filter(team_id, scope)

    if tab == "agents":
        result = await _query_pending_agents(db, scope, team_id, current_user)
        return result

    if tab == "components":
        result = await _query_pending_components(db, scope, type, team_id, current_user)
        return result

    # Default: return both agents and components
    agents = await _query_pending_agents(db, scope, team_id, current_user)
    components = await _query_pending_components(db, scope, type, team_id, current_user)

    # Merge and sort by created_at (most recent first)
    all_items = agents + components
    all_items.sort(key=lambda x: x["created_at"], reverse=True)
    return all_items


_DETAIL_FIELDS: dict[str, list[str]] = {
    "mcp": [
        "git_url",
        "git_ref",
        "category",
        "transport",
        "framework",
        "docker_image",
        "command",
        "args",
        "url",
        "headers",
        "auto_approve",
        "tools_schema",
        "environment_variables",
        "supported_harnesses",
        "setup_instructions",
        "changelog",
        "rejection_reason",
        "bundle_id",
    ],
    "skill": [
        "git_url",
        "git_ref",
        "skill_path",
        "target_agents",
        "task_type",
        "triggers",
        "slash_command",
        "has_scripts",
        "has_templates",
        "is_power",
        "power_md",
        "mcp_server_config",
        "activation_keywords",
        "supported_harnesses",
        "rejection_reason",
        "bundle_id",
    ],
    "hook": [
        "git_url",
        "git_ref",
        "event",
        "execution_mode",
        "priority",
        "handler_type",
        "handler_config",
        "input_schema",
        "output_schema",
        "scope",
        "tool_filter",
        "file_pattern",
        "supported_harnesses",
        "rejection_reason",
        "bundle_id",
    ],
    "prompt": [
        "git_url",
        "git_ref",
        "category",
        "template",
        "variables",
        "model_hints",
        "tags",
        "supported_harnesses",
        "rejection_reason",
        "bundle_id",
    ],
    "sandbox": [
        "git_url",
        "git_ref",
        "runtime_type",
        "image",
        "dockerfile_url",
        "resource_limits",
        "network_policy",
        "allowed_mounts",
        "env_vars",
        "entrypoint",
        "supported_harnesses",
        "rejection_reason",
        "bundle_id",
    ],
}


def _safe_serialize(val: object) -> object:
    optic.trace("val={}", val)
    if isinstance(val, uuid.UUID):
        return str(val)
    if hasattr(val, "isoformat"):
        return val.isoformat()
    if isinstance(val, enum.Enum):
        return val.value
    return val


def _serialize_listing_detail(listing_type: str, listing, *, selected=None) -> dict:
    # Find the pending version if one exists (for reviews, we want pending content)
    optic.trace("listing_type={}, listing={}", listing_type, listing)
    pending_ver = selected
    if pending_ver is None and hasattr(listing, "versions"):
        pending_ver = next(
            (v for v in listing.versions if v.status == ListingStatus.pending),
            None,
        )
    # Use the reviewed version rather than fields from a different approved row.
    source = pending_ver if pending_ver else listing

    base = {
        "type": listing_type,
        "id": str(listing.id),
        "name": listing.name,
        "description": getattr(source, "description", None) or "",
        "version": getattr(source, "version", None) or "",
        "owner": getattr(listing, "owner", None) or "",
        "status": source.status.value if hasattr(source, "status") else listing.status.value,
        "submitted_by": str(listing.submitted_by),
        "created_at": listing.created_at.isoformat(),
        "updated_at": listing.updated_at.isoformat() if getattr(listing, "updated_at", None) else None,
    }
    for field in _DETAIL_FIELDS.get(listing_type, []):
        val = getattr(source, field, None)
        if val is None and (listing_type != "skill" or source is listing or field == "bundle_id"):
            val = getattr(listing, field, None)
        base[field] = _safe_serialize(val)
    if listing_type == "mcp" and hasattr(listing, "validation_results"):
        base["mcp_validated"] = getattr(listing, "mcp_validated", False)
        base["validation_results"] = [
            {
                "stage": vr.stage,
                "passed": vr.passed,
                "details": vr.details,
                "run_at": vr.run_at.isoformat() if vr.run_at else None,
            }
            for vr in listing.validation_results
        ]
    return base


@router.get("/skills/{listing_id}/versions/{version_id}")
async def get_skill_version_review(
    listing_id: str,
    version_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Authorize one pending version before loading any file bytes."""
    scope = await _require_review_scope(db, current_user)
    listing_type, listing = await _find_listing(listing_id, db)
    if listing_type != "skill" or listing is None or not can_review(listing, scope):
        raise HTTPException(status_code=404, detail="Submission not found")
    listing, version = await _authorized_version(str(listing.id), version_id, db, current_user)
    if version.status != ListingStatus.pending or (version.requires_global_review and not scope.is_global_reviewer):
        raise HTTPException(status_code=404, detail="Submission not found")
    try:
        if version.delivery_mode == "registry_direct":
            _, _, revision, files = await _authorized_files(str(listing.id), version_id, db, current_user)
            declarations = [file.declaration.model_dump() for file in files]
        else:
            revision = skill_content_revision(listing, version)
            declarations = []
    except SkillValidationError as exc:
        raise HTTPException(status_code=409, detail="Stored skill version is not a valid release") from exc
    if version.content_revision is not None and revision != version.content_revision:
        raise HTTPException(status_code=409, detail="Skill version changed; refresh before review")
    detail = _serialize_listing_detail("skill", listing, selected=version)
    base_metadata = None
    if version.base_version_id:
        base_metadata = (
            await db.execute(
                select(
                    SkillVersion.version, SkillVersion.delivery_mode, SkillVersion.git_url, SkillVersion.git_ref
                ).where(SkillVersion.id == version.base_version_id, SkillVersion.listing_id == listing.id)
            )
        ).one_or_none()
    detail.update(
        version_id=str(version.id),
        revision=revision,
        files=declarations,
        base_version_id=str(version.base_version_id) if version.base_version_id else None,
        base_version=base_metadata.version if base_metadata else None,
        base_delivery_mode=base_metadata.delivery_mode if base_metadata else None,
        base_git_url=base_metadata.git_url if base_metadata and base_metadata.delivery_mode == "git_fetch" else None,
        base_git_ref=base_metadata.git_ref if base_metadata and base_metadata.delivery_mode == "git_fetch" else None,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return detail


@router.post("/skills/{listing_id}/versions/{version_id}/decision")
async def decide_skill_version(
    listing_id: str,
    version_id: uuid.UUID,
    req: VersionReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Review an exact skill UUID; bundle decisions remain gated until delivery."""
    return await _review_version(
        listing_id, "", req, SkillListing, SkillVersion, "skill", db, current_user, selected_id=version_id
    )


@router.get("/{listing_id}")
async def get_review(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    optic.trace("listing_id={}", listing_id)
    scope = await _require_review_scope(db, current_user)
    listing_type, listing = await _find_listing(listing_id, db)

    if listing:
        # 404 rather than 403: the queue already hides items outside the caller's
        # scope, and answering 403 here would confirm a team-private item exists
        # to the very reviewers the scoping keeps away from it.
        if not can_review(listing, scope):
            raise HTTPException(status_code=404, detail="Listing not found")
        if listing_type == "skill":
            pending = [v for v in listing.versions if v.status == ListingStatus.pending]
            if len(pending) > 1:
                raise HTTPException(status_code=409, detail="Select a pending skill version by UUID")
            if pending and pending[0].requires_global_review and not scope.is_global_reviewer:
                raise HTTPException(status_code=404, detail="Submission not found")
        result = _serialize_listing_detail(listing_type, listing)
        if listing_type == "skill" and pending:
            result["version_id"] = str(pending[0].id)
        result["forked_from"] = await provenance_for(listing, current_user, db)
    else:
        # Fallback: check Agent table
        try:
            agent_uuid = uuid.UUID(listing_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Listing not found")
        agent = (await db.execute(select(Agent).where(Agent.id == agent_uuid))).scalar_one_or_none()
        if not agent or not can_review(agent, scope):
            raise HTTPException(status_code=404, detail="Listing not found")
        # Use the pending version for review (not latest_version which is the approved one)
        pending_ver = next(
            (v for v in agent.versions if v.status == AgentStatus.pending),
            None,
        )
        ver = pending_ver or agent.latest_version
        ver_components = ver.components if ver else agent.components
        components_ready, blocking = await _check_agent_components_ready(ver_components, db)
        result = {
            "type": "agent",
            "id": str(agent.id),
            "name": agent.name,
            "description": (ver.description if ver else "") or agent.description or "",
            "version": (ver.version if ver else "") or agent.version or "",
            "owner": agent.owner or "",
            "status": (ver.status.value if ver else agent.status.value),
            "submitted_by": str(ver.released_by if ver else agent.created_by),
            "created_at": agent.created_at.isoformat() if agent.created_at else None,
            "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
            "git_url": getattr(agent, "git_url", None),
            "prompt": (ver.prompt if ver else "") or "",
            "model_name": (ver.model_name if ver else "") or "",
            "model_config_json": (ver.model_config_json if ver else {}) or {},
            "external_mcps": (ver.external_mcps if ver else []) or [],
            "supported_harnesses": (ver.supported_harnesses if ver else []) or [],
            "required_capabilities": (ver.required_capabilities if ver else []) or [],
            "rejection_reason": ver.rejection_reason if ver else None,
            "component_count": len(ver_components),
            "components_ready": components_ready,
            "component_blockers": blocking,
            "gaming_flags": ver.gaming_flags if ver else None,
            "success_criteria": (ver.success_criteria if ver else None),
            "components": [
                {
                    "component_type": c.component_type,
                    "component_id": str(c.component_id),
                }
                for c in ver_components
            ],
        }

        result["forked_from"] = (
            await provenance_for(agent, current_user, db) if getattr(agent, "is_fork", False) else None
        )

        # Expand component details with resolved listing content
        expanded_components = []
        for c in ver_components:
            comp_data = {
                "component_type": c.component_type,
                "component_id": str(c.component_id),
                "name": getattr(c, "component_name", "") or "",
            }
            model = LISTING_MODELS.get(c.component_type)
            if model:
                listing = (await db.execute(select(model).where(model.id == c.component_id))).scalar_one_or_none()
                if listing:
                    comp_data["name"] = listing.name
                    if c.component_type == "prompt":
                        comp_data["template"] = getattr(listing, "template", "") or ""
                        comp_data["category"] = getattr(listing, "category", "") or ""
                    else:
                        comp_data["description"] = getattr(listing, "description", "") or ""
            expanded_components.append(comp_data)
        result["components"] = expanded_components

    # Resolve submitted_by UUID to display name
    uid_str = result.get("submitted_by", "")
    try:
        uid = uuid.UUID(uid_str)
        user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        if user:
            result["submitted_by"] = user.username or user.email
    except (ValueError, AttributeError):
        pass
    return result


async def _lock_skill_decision(db, listing, current_user, version=None, *, approval=True):
    """Read fresh bytes/status after acquiring the writer's listing/version locks."""
    target = version or listing.latest_version
    if target is None:
        raise HTTPException(status_code=409, detail="Skill has no reviewable version")
    latest_id, locked = await lock_skill_version(db, listing.id, target.id)
    await db.refresh(listing, attribute_names=["team_id", "is_private", "submitted_by", "co_authors"])
    _authorize_item(listing, await _require_review_scope(db, current_user))
    if version is None and latest_id != locked.id:
        raise HTTPException(status_code=409, detail="Skill version changed during review")
    if locked.status != ListingStatus.pending:
        raise HTTPException(status_code=409, detail="Skill version is no longer pending")
    # Legacy listing/bundle decisions have no version UUID or observed revision.
    # The initial candidate list may be stale after waiting for the listing
    # lock, so recheck ambiguity inside the serialized review transaction.
    pending_ids = (
        (
            await db.execute(
                select(SkillVersion.id)
                .where(SkillVersion.listing_id == listing.id, SkillVersion.status == ListingStatus.pending)
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if pending_ids != [locked.id]:
        raise HTTPException(status_code=409, detail="Select an exact pending skill version for review")
    if locked.requires_global_review:
        if not (await _require_review_scope(db, current_user)).is_global_reviewer:
            raise HTTPException(status_code=404, detail="Submission not found")
        raise HTTPException(status_code=409, detail="Global skill re-review is not yet available")
    _refuse_unreviewable_skill(listing, locked)
    return latest_id, locked


def _refuse_unreviewable_skill(listing, version=None):
    if isinstance(listing, SkillListing):
        candidates = [version] if version is not None else listing.versions
        for ver in candidates:
            if ver is None:
                continue
            if needs_bundle_delivery(ver):
                raise HTTPException(status_code=409, detail="Resource-bearing skill versions cannot yet be reviewed")
            if (
                ver.base_version_id is not None
                or (ver.review_epoch or 0) > 0
                or (ver.content_revision is not None and ver.delivery_mode == "registry_direct")
            ):
                raise HTTPException(
                    status_code=409, detail="Review this exact skill version with its observed revision"
                )


@router.post("/{listing_id}/approve")
async def approve(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    optic.trace("listing_id={}", listing_id)
    scope = await _require_review_scope(db, current_user)
    listing_type, listing = await _find_listing(listing_id, db)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    _authorize_item(listing, scope)

    # A listing ID is safe only for a single unambiguous legacy pending row.
    pending = [v for v in getattr(listing, "versions", ()) if v.status == ListingStatus.pending]
    if listing_type == "skill" and len(pending) != 1:
        raise HTTPException(status_code=409, detail="Select an exact pending skill version for review")
    pending_ver = pending[0] if pending else None

    latest_id = None
    if listing_type == "skill":
        latest_id, locked = await _lock_skill_decision(db, listing, current_user, pending_ver)
        if pending_ver is not None:
            pending_ver = locked
    if pending_ver:
        if is_actively_editing(pending_ver):
            raise HTTPException(status_code=409, detail="Cannot approve: the owner is currently editing this item")
        promote_skill = None
        if listing_type == "skill":
            promote_skill = await should_promote_skill_version(db, latest_id, pending_ver)
            if not promote_skill and (
                "-" not in pending_ver.version or not SKILL_SEMVER_RE.fullmatch(pending_ver.version)
            ):
                raise HTTPException(
                    status_code=409, detail="Newer approved skill release exists; refresh before review"
                )
        pending_ver.status = ListingStatus.approved
        pending_ver.rejection_reason = None
        pending_ver.reviewed_by = current_user.id
        pending_ver.reviewed_at = datetime.now(UTC)
        # Flush version changes first to avoid CircularDependencyError
        await db.flush()
        # Update latest_version_id via raw UPDATE to avoid circular dependency
        # between listing.latest_version_id and version.listing_id
        listing_cls = LISTING_MODELS[listing_type]
        if listing_type != "skill" or promote_skill:
            await db.execute(
                update(listing_cls).where(listing_cls.id == listing.id).values(latest_version_id=pending_ver.id)
            )
    else:
        # Fallback: legacy path for listings without versioning
        if listing.latest_version and is_actively_editing(listing.latest_version):
            raise HTTPException(status_code=409, detail="Cannot approve: the owner is currently editing this item")
        listing.status = ListingStatus.approved
        listing.rejection_reason = None

    # Delivered before the commit, in this same transaction: if the approval
    # rolls back, the notice rolls back with it.
    await inbox.on_review_decided(
        db,
        listing,
        subject_type=listing_type,
        approved=True,
        actor_id=current_user.id,
        version=getattr(pending_ver, "version", None),
        submitter_id=getattr(pending_ver, "released_by", None),
    )

    await db.commit()
    await db.refresh(listing)
    await invalidate_namespace("dashboard")
    asyncio.create_task(redis_publish("reviews:updated", {"listing_id": str(listing.id), "action": "approved"}))  # noqa: RUF006
    return {"type": listing_type, "id": str(listing.id), "name": listing.name, "status": listing.status.value}


@router.post("/{listing_id}/reject")
async def reject(
    listing_id: str,
    req: ReviewActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    optic.trace("listing_id={}, req={}", listing_id, req)
    scope = await _require_review_scope(db, current_user)
    listing_type, listing = await _find_listing(listing_id, db)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    _authorize_item(listing, scope)

    # Rejection is also a decision about reviewed bytes, not a listing-wide action.
    pending = [v for v in getattr(listing, "versions", ()) if v.status == ListingStatus.pending]
    if listing_type == "skill" and len(pending) != 1:
        raise HTTPException(status_code=409, detail="Select an exact pending skill version for review")
    pending_ver = pending[0] if pending else None

    if listing_type == "skill":
        _, locked = await _lock_skill_decision(db, listing, current_user, pending_ver, approval=False)
        if pending_ver is not None:
            pending_ver = locked
    if pending_ver:
        if is_actively_editing(pending_ver):
            raise HTTPException(status_code=409, detail="Cannot reject: the owner is currently editing this item")
        pending_ver.status = ListingStatus.rejected
        pending_ver.rejection_reason = req.reason
        pending_ver.reviewed_by = current_user.id
        pending_ver.reviewed_at = datetime.now(UTC)
    else:
        # Fallback: legacy path for listings without versioning
        if listing.latest_version and is_actively_editing(listing.latest_version):
            raise HTTPException(status_code=409, detail="Cannot reject: the owner is currently editing this item")
        listing.status = ListingStatus.rejected
        listing.rejection_reason = req.reason

    await inbox.on_review_decided(
        db,
        listing,
        subject_type=listing_type,
        approved=False,
        actor_id=current_user.id,
        version=getattr(pending_ver, "version", None),
        reason=req.reason,
        submitter_id=getattr(pending_ver, "released_by", None),
    )

    await db.commit()

    # Handle self-learn rejection cascade
    try:
        from services.insights.self_learn import handle_component_rejection

        await handle_component_rejection(listing_type, listing.id, db)
        await db.commit()
    except Exception:
        pass  # Non-critical: don't block rejection if cascade fails

    await db.refresh(listing)
    await invalidate_namespace("dashboard")
    asyncio.create_task(redis_publish("reviews:updated", {"listing_id": str(listing.id), "action": "rejected"}))  # noqa: RUF006
    return {"type": listing_type, "id": str(listing.id), "name": listing.name, "status": listing.status.value}


# ---------------------------------------------------------------------------
# Agent review
# ---------------------------------------------------------------------------


class AgentRejectRequest(BaseModel):
    reason: str


class AgentApproveRequest(BaseModel):
    category: str | None = None


@router.post("/agents/{agent_id}/approve")
async def approve_agent(
    agent_id: uuid.UUID,
    req: AgentApproveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    optic.trace("agent_id={}, req={}", agent_id, req)
    from services.versioning import parse_semver

    scope = await _require_review_scope(db, current_user)
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    _authorize_item(agent, scope)

    pending_versions = (
        (
            await db.execute(
                select(AgentVersion)
                .where(AgentVersion.agent_id == agent.id, AgentVersion.status == AgentStatus.pending)
                .order_by(AgentVersion.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    if not pending_versions:
        raise HTTPException(status_code=400, detail=f"Agent has no pending versions (latest is '{agent.status.value}')")

    for pv in pending_versions:
        if is_actively_editing(pv):
            raise HTTPException(status_code=409, detail="Cannot approve: the owner is currently editing this agent")

    newest_pending = pending_versions[0]
    components_ready, blocking = await _check_agent_components_ready(
        newest_pending.components, db, require_public_skills=not agent.is_private
    )
    if not components_ready:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Cannot approve: some components are not approved yet",
                "blocking_components": blocking,
            },
        )

    now = datetime.now(UTC)
    # Approve only the newest pending version; mark older ones as superseded
    newest_pending.status = AgentStatus.approved
    newest_pending.rejection_reason = None
    newest_pending.reviewed_by = current_user.id
    newest_pending.reviewed_at = now
    for pv in pending_versions[1:]:
        pv.status = AgentStatus.rejected
        pv.rejection_reason = "Superseded by newer version"
        pv.reviewed_by = current_user.id
        pv.reviewed_at = now

    # Flush version changes first to avoid CircularDependencyError
    await db.flush()

    # Approval freezes the lock: nothing writes an approved version's components.
    from services.agent_lock import lock_agent_version

    await lock_agent_version(db, agent, newest_pending)

    current_latest = agent.latest_version
    new_parsed = parse_semver(newest_pending.version)
    current_parsed = parse_semver(current_latest.version) if current_latest else None
    if not current_latest or (new_parsed is not None and current_parsed is not None and new_parsed >= current_parsed):
        agent.latest_version_id = newest_pending.id

    if req and req.category:
        agent.category = req.category

    # Addressed to whoever released the version that was approved, which is not
    # necessarily the agent's creator.
    await inbox.on_review_decided(
        db,
        agent,
        subject_type="agent",
        approved=True,
        actor_id=current_user.id,
        version=newest_pending.version,
        submitter_id=newest_pending.released_by,
    )
    # The loop above rejected every older pending version. Those are somebody's
    # submissions too, and they may not share an author with the approved one,
    # so each gets its own notice. Without this a contributor's work is rejected
    # with no inbox record of it ever happening.
    for pv in pending_versions[1:]:
        await inbox.on_review_decided(
            db,
            agent,
            subject_type="agent",
            approved=False,
            actor_id=current_user.id,
            version=pv.version,
            reason="Superseded by newer version",
            submitter_id=pv.released_by,
        )

    await db.commit()
    await invalidate_namespace("dashboard")
    asyncio.create_task(redis_publish("reviews:updated", {"listing_id": str(agent.id), "action": "approved"}))  # noqa: RUF006
    return {"id": str(agent.id), "name": agent.name, "status": "approved", "version": newest_pending.version}


@router.post("/agents/{agent_id}/reject")
async def reject_agent(
    agent_id: uuid.UUID,
    req: AgentRejectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    optic.trace("agent_id={}, req={}", agent_id, req)
    scope = await _require_review_scope(db, current_user)
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    _authorize_item(agent, scope)

    pending_versions = (
        (
            await db.execute(
                select(AgentVersion)
                .where(AgentVersion.agent_id == agent.id, AgentVersion.status == AgentStatus.pending)
                .order_by(AgentVersion.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    if not pending_versions:
        raise HTTPException(status_code=400, detail=f"Agent has no pending versions (latest is '{agent.status.value}')")
    else:
        for pv in pending_versions:
            if is_actively_editing(pv):
                raise HTTPException(status_code=409, detail="Cannot reject: the owner is currently editing this agent")
        now = datetime.now(UTC)
        for pv in pending_versions:
            pv.status = AgentStatus.rejected
            pv.rejection_reason = req.reason
            pv.reviewed_by = current_user.id
            pv.reviewed_at = now
        await db.flush()

        # Every rejected version is somebody's work, and they may not all share
        # an author, so each release gets its own notice rather than only the
        # newest one.
        for pv in pending_versions:
            await inbox.on_review_decided(
                db,
                agent,
                subject_type="agent",
                approved=False,
                actor_id=current_user.id,
                version=pv.version,
                reason=req.reason,
                submitter_id=pv.released_by,
            )

    await db.commit()
    rejected_version = pending_versions[0].version if pending_versions else ""
    await invalidate_namespace("dashboard")
    asyncio.create_task(redis_publish("reviews:updated", {"listing_id": str(agent.id), "action": "rejected"}))  # noqa: RUF006
    return {"id": str(agent.id), "name": agent.name, "status": "rejected", "version": rejected_version}


# ---------------------------------------------------------------------------
# Bundle review (atomic approve/reject)
#
# A ComponentBundle carries no visibility of its own: it is a grouping, and each
# member listing keeps its own is_private and team_id. The bundle actions are
# atomic by design, so they authorize every member listing individually and
# refuse the whole bundle when one member is outside the caller's scope. A
# wholly team-private bundle is therefore reviewable by that team's owners and
# reviewers, a wholly public one by a global reviewer, and a bundle that mixes
# the two only by an admin. Approving the reachable half and skipping the rest
# would silently half-apply an operation whose entire point is atomicity.
# ---------------------------------------------------------------------------


async def _bundle_listings(bundle_id: uuid.UUID, db: AsyncSession, scope: ReviewScope) -> list:
    """Load every listing in a bundle, refusing the bundle if one is out of scope."""
    listings = []
    for model in LISTING_MODELS.values():
        stmt = select(model).where(model.bundle_id == bundle_id)
        if model is SkillListing:
            stmt = stmt.options(*_SKILL_REVIEW_LOOKUP_OPTIONS)
        result = await db.execute(stmt)
        rows = result.scalars().all()
        # Bulk skill decisions use UUID order too; never acquire overlapping
        # bundle and bulk locks in opposing orders.
        if model is SkillListing:
            rows = sorted(rows, key=lambda listing: listing.id)
        for listing in rows:
            _authorize_item(listing, scope)
            listings.append(listing)
    return listings


@router.post("/bundles/{bundle_id}/approve")
async def approve_bundle(
    bundle_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    optic.trace("bundle_id={}", bundle_id)
    scope = await _require_review_scope(db, current_user)
    bundle = (await db.execute(select(ComponentBundle).where(ComponentBundle.id == bundle_id))).scalar_one_or_none()
    if not bundle:
        raise HTTPException(status_code=404, detail="Bundle not found")

    count = 0
    for listing in await _bundle_listings(bundle_id, db, scope):
        if (
            not isinstance(listing, SkillListing)
            and any(ver.status == ListingStatus.pending for ver in listing.versions)
            and (listing.latest_version is None or listing.latest_version.status != ListingStatus.pending)
        ):
            raise HTTPException(status_code=409, detail="Review the exact pending component version separately")
        if isinstance(listing, SkillListing):
            _, locked = await _lock_skill_decision(db, listing, current_user)
            if locked.status != ListingStatus.pending:
                raise HTTPException(status_code=409, detail="Skill version is no longer pending")
        if listing.latest_version and is_actively_editing(listing.latest_version):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot approve: '{listing.name}' is currently being edited by its owner",
            )
        listing.status = ListingStatus.approved
        listing.rejection_reason = None
        await inbox.on_review_decided(
            db,
            listing,
            subject_type=_listing_type_of(listing),
            approved=True,
            actor_id=current_user.id,
            version=getattr(listing.latest_version, "version", None),
            submitter_id=getattr(listing.latest_version, "released_by", None),
        )
        count += 1

    await db.commit()
    return {"bundle_id": str(bundle_id), "name": bundle.name, "approved_count": count}


@router.post("/bundles/{bundle_id}/reject")
async def reject_bundle(
    bundle_id: uuid.UUID,
    req: ReviewActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    optic.trace("bundle_id={}, req={}", bundle_id, req)
    scope = await _require_review_scope(db, current_user)
    bundle = (await db.execute(select(ComponentBundle).where(ComponentBundle.id == bundle_id))).scalar_one_or_none()
    if not bundle:
        raise HTTPException(status_code=404, detail="Bundle not found")

    count = 0
    for listing in await _bundle_listings(bundle_id, db, scope):
        if isinstance(listing, SkillListing):
            _, locked = await _lock_skill_decision(db, listing, current_user, approval=False)
            if locked.status != ListingStatus.pending:
                raise HTTPException(status_code=409, detail="Skill version is no longer pending")
        if listing.latest_version and is_actively_editing(listing.latest_version):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot reject: '{listing.name}' is currently being edited by its owner",
            )
        listing.status = ListingStatus.rejected
        listing.rejection_reason = req.reason
        await inbox.on_review_decided(
            db,
            listing,
            subject_type=_listing_type_of(listing),
            approved=False,
            actor_id=current_user.id,
            version=getattr(listing.latest_version, "version", None),
            reason=req.reason,
        )
        count += 1

    await db.commit()
    return {"bundle_id": str(bundle_id), "name": bundle.name, "rejected_count": count}


# ---------------------------------------------------------------------------
# Smart bulk approve: MCP + related skills
# ---------------------------------------------------------------------------


@router.get("/{listing_id}/related-skills")
async def get_related_skills(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    optic.trace("listing_id={}", listing_id)
    scope = await _require_review_scope(db, current_user)
    listing_type, listing = await _find_listing(listing_id, db)
    if not listing or listing_type != "mcp":
        return {"skills": []}
    if not can_review(listing, scope):
        raise HTTPException(status_code=404, detail="Listing not found")

    mcp_name = listing.name
    mcp_id = str(listing.id)

    stmt = (
        select(SkillListing)
        .join(SkillVersion, SkillListing.latest_version_id == SkillVersion.id)
        .where(
            SkillVersion.status == ListingStatus.pending,
            SkillVersion.mcp_server_config.isnot(None),
            or_(
                cast(SkillVersion.mcp_server_config, String).contains(escape_like(mcp_name)),
                cast(SkillVersion.mcp_server_config, String).contains(escape_like(mcp_id)),
            ),
        )
        .order_by(SkillListing.created_at.desc())
    )
    # The suggestion list is a review surface like the queue itself, so a skill
    # the caller may not review never appears in it.
    skills = [s for s in (await db.execute(stmt)).scalars().all() if can_review(s, scope)]

    user_ids = {s.submitted_by for s in skills}
    user_map: dict[uuid.UUID, str] = {}
    if user_ids:
        rows = await db.execute(select(User).where(User.id.in_(user_ids)))
        for u in rows.scalars().all():
            user_map[u.id] = u.username or u.email

    result = {
        "skills": [
            {
                "id": str(s.id),
                "type": "skill",
                "name": s.name,
                "version": s.version or "",
                "description": s.description or "",
                "task_type": s.task_type,
                "target_agents": s.target_agents or [],
                "mcp_server_config": s.mcp_server_config,
                "status": s.status.value,
                "submitted_by": user_map.get(s.submitted_by, str(s.submitted_by)),
                "created_at": s.created_at.isoformat(),
            }
            for s in skills
        ]
    }
    return result


class McpBulkApproveRequest(BaseModel):
    skill_ids: list[str] = []


# approve-with-skills approves a caller-supplied set of skill ids alongside the
# MCP, and nothing ties those skills to the MCP's teamspace, so it is scoped the
# same way as a plain approve: the MCP and every named skill are authorized on
# their own visibility. A skill outside the caller's scope fails the whole call
# rather than being dropped from the batch, because a caller who asked for it
# must be told it was refused instead of reading a smaller approved count.
@router.post("/{listing_id}/approve-with-skills")
async def approve_mcp_with_skills(
    listing_id: str,
    req: McpBulkApproveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    optic.trace("listing_id={}, req={}", listing_id, req)
    scope = await _require_review_scope(db, current_user)
    listing_type, listing = await _find_listing(listing_id, db)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing_type != "mcp":
        raise HTTPException(status_code=400, detail="Only MCP listings support bulk skill approve")
    _authorize_item(listing, scope)
    if listing.latest_version is None or listing.latest_version.status != ListingStatus.pending:
        raise HTTPException(status_code=409, detail="Review the exact pending MCP version separately")

    approved_skill_ids: list[str] = []
    locked_skills: list[SkillListing] = []
    # A canonical lock order prevents opposing batches from deadlocking.
    skill_uuids = set()
    for sid in req.skill_ids:
        try:
            parsed = uuid.UUID(sid)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Every requested skill ID must be a UUID") from exc
        if parsed in skill_uuids:
            raise HTTPException(status_code=400, detail="Duplicate skill ID in approval request")
        skill_uuids.add(parsed)
    for skill_uuid in sorted(skill_uuids):
        skill = (
            await db.execute(
                select(SkillListing).options(*_SKILL_REVIEW_LOOKUP_OPTIONS).where(SkillListing.id == skill_uuid)
            )
        ).scalar_one_or_none()
        if not skill:
            raise HTTPException(status_code=404, detail="Requested skill not found")
        _authorize_item(skill, scope)
        if skill.status != ListingStatus.pending:
            raise HTTPException(status_code=409, detail="Requested skill has no pending version")
        await _lock_skill_decision(db, skill, current_user)
        if skill.status != ListingStatus.pending:
            raise HTTPException(status_code=409, detail="Skill version is no longer pending")
        locked_skills.append(skill)

    listing.status = ListingStatus.approved
    listing.rejection_reason = None
    await inbox.on_review_decided(
        db,
        listing,
        subject_type="mcp",
        approved=True,
        actor_id=current_user.id,
        version=getattr(listing.latest_version, "version", None),
        submitter_id=getattr(listing.latest_version, "released_by", None),
    )
    for skill in locked_skills:
        skill.status = ListingStatus.approved
        skill.rejection_reason = None
        await inbox.on_review_decided(
            db,
            skill,
            subject_type="skill",
            approved=True,
            actor_id=current_user.id,
            version=getattr(skill.latest_version, "version", None),
            submitter_id=getattr(skill.latest_version, "released_by", None),
        )
        approved_skill_ids.append(str(skill.id))

    await db.commit()
    await db.refresh(listing)
    return {
        "mcp": {"id": str(listing.id), "name": listing.name, "status": listing.status.value},
        "approved_skills": len(approved_skill_ids),
        "skill_ids": approved_skill_ids,
    }
