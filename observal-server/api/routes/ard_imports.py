# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Register remote A2A agents in the discovery index (ADR 0002).

    GET    /api/v1/ard/imports                          imported entries the caller can see
    POST   /api/v1/ard/imports/a2a                      register or refresh an Agent Card by URL
    POST   /api/v1/ard/imports/{identifier}/review      approve or reject (reviewers)
    DELETE /api/v1/ard/imports/{identifier}             remove (owner or admin)

A registration is pending until a reviewer approves it, exactly like a native
submission. The approved entry pins the card that was reviewed; refreshing a
card whose content changed sends it back to review. Search, inspect and
visibility then work through the ordinary ARD endpoints.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from loguru import logger as optic
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import services.dynamic_settings as ds
from api.deps import ROLE_HIERARCHY, get_current_user, get_db
from api.ratelimit import limiter
from models.discovery_entry import DiscoveryEntry, DiscoveryLifecycle, DiscoverySourceKind, DiscoveryVisibility
from models.team import TeamMembership
from models.user import User, UserRole
from services.discovery.a2a import (
    AgentCardError,
    apply_card,
    fetch_agent_card,
    new_imported_entry,
    parse_host_allowlist,
    pinned_card,
)
from services.discovery.identity import MEDIA_TYPE_A2A, normalize_urn
from services.discovery.projection import resolve_context
from services.discovery.visibility import visible_entries_predicate
from services.teamspace import can_review, review_scope

router = APIRouter(prefix="/api/v1/ard/imports", tags=["ard"])

PRIVATE_HOSTS_SETTING = "discovery.a2a_private_hosts"
IMPORT_RATE_LIMIT = "20/minute"
_VISIBILITY = {
    "public": DiscoveryVisibility.public,
    "team": DiscoveryVisibility.team,
    "private": DiscoveryVisibility.owner,
}


class A2aImportRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    card_url: str = Field(..., alias="cardUrl", min_length=8, max_length=1000)
    visibility: Literal["public", "team", "private"] = "private"
    team_id: uuid.UUID | None = Field(default=None, alias="teamId")


class A2aReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)
    # The obs:artifactDigest the reviewer looked at, required to approve: the decision only applies to
    # that card, so an owner who refreshes it in between gets it back to review instead of approved unseen.
    digest: str | None = Field(default=None, max_length=80)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"errorCode": code, "message": message})


def _is_admin(user: User) -> bool:
    return ROLE_HIERARCHY.get(user.role, 999) <= ROLE_HIERARCHY[UserRole.admin]


def _summary(entry: DiscoveryEntry) -> dict:
    document = dict(entry.raw_entry or {})
    document["obs:lifecycle"] = entry.lifecycle_status.value
    return document


async def _imported(db: AsyncSession, identifier: str, *, for_update: bool = False) -> DiscoveryEntry | None:
    stmt = select(DiscoveryEntry).where(
        DiscoveryEntry.ard_identifier == normalize_urn(identifier),
        DiscoveryEntry.source_kind == DiscoverySourceKind.imported,
        DiscoveryEntry.media_type == MEDIA_TYPE_A2A,
        DiscoveryEntry.tombstoned_at.is_(None),
    )
    if for_update:  # a review decision and an owner's refresh must not interleave
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def _is_member(db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    stmt = select(TeamMembership.id).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    return (await db.execute(stmt)).first() is not None


@router.get("")
async def list_imports(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """Imported A2A agents the caller may see, including their own and (for reviewers) pending ones."""
    stmt = (
        select(DiscoveryEntry)
        .where(
            visible_entries_predicate(current_user),
            DiscoveryEntry.source_kind == DiscoverySourceKind.imported,
            DiscoveryEntry.media_type == MEDIA_TYPE_A2A,
        )
        .order_by(DiscoveryEntry.display_name, DiscoveryEntry.ard_identifier)
        .limit(500)
    )
    items = [
        {
            "identifier": e.ard_identifier,
            "displayName": e.display_name,
            "version": e.version,
            "url": e.artifact_url,
            "obs:lifecycle": e.lifecycle_status.value,
            "obs:visibility": e.visibility.value,
            "obs:delegable": bool((e.raw_entry or {}).get("obs:delegable")),
            "obs:artifactDigest": e.artifact_digest,
        }
        for e in (await db.execute(stmt)).scalars().all()
    ]
    return JSONResponse(content={"items": items, "total": len(items)}, headers={"Cache-Control": "no-store"})


@router.post("/a2a", status_code=201)
@limiter.limit(IMPORT_RATE_LIMIT)
async def import_a2a_agent(
    request: Request,
    body: A2aImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """Register a remote agent by its Agent Card URL, or refresh one you registered."""
    visibility = _VISIBILITY[body.visibility]
    team_id = body.team_id if visibility == DiscoveryVisibility.team else None
    if visibility == DiscoveryVisibility.team:
        if team_id is None:
            return _error(400, "INVALID_ARGUMENT", "Team visibility needs teamId.")
        if not _is_admin(current_user) and not await _is_member(db, team_id, current_user.id):
            return _error(403, "PERMISSION_DENIED", "You are not a member of that team.")

    private_hosts = parse_host_allowlist(await ds.get(PRIVATE_HOSTS_SETTING, ""))
    try:
        card, card_url = await fetch_agent_card(body.card_url, private_hosts=private_hosts)
    except AgentCardError as exc:
        return _error(422, "INVALID_AGENT_CARD", exc.message)

    ctx = await resolve_context(db)
    now = datetime.now(UTC)
    existing = (
        await db.execute(
            select(DiscoveryEntry).where(
                DiscoveryEntry.source_kind == DiscoverySourceKind.imported,
                DiscoveryEntry.source_identifier == card_url[:255],
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        if existing.tombstoned_at is not None and existing.owner_user_id != current_user.id:
            # Its owner removed it, so whoever registers the URL next owns it (and it is reviewed again).
            existing.owner_user_id = current_user.id
        elif existing.owner_user_id != current_user.id and not _is_admin(current_user):
            return _error(409, "ALREADY_EXISTS", "This Agent Card is already registered by someone else.")
        # A reviewer approved this card for this audience; a new card or a new audience needs a new review.
        unchanged = (
            existing.artifact_digest == card.digest
            and existing.tombstoned_at is None
            and existing.visibility == visibility
            and existing.team_id == team_id
        )
        lifecycle = existing.lifecycle_status if unchanged else DiscoveryLifecycle.pending
        existing.visibility = visibility
        existing.team_id = team_id
        apply_card(existing, card, card_url, lifecycle=lifecycle, now=now)
        entry, status = existing, 200
    else:
        entry = new_imported_entry(
            card=card,
            card_url=card_url,
            fallback_domain=ctx.publisher_domain,
            visibility=visibility,
            team_id=team_id,
            owner_id=current_user.id,
            now=now,
        )
        clash = (
            await db.execute(select(DiscoveryEntry.id).where(DiscoveryEntry.ard_identifier == entry.ard_identifier))
        ).first()
        if clash is not None:
            return _error(409, "ALREADY_EXISTS", f"{entry.ard_identifier} is already registered from another URL.")
        db.add(entry)
        status = 201

    await db.commit()
    optic.info(
        "a2a agent registered id={} lifecycle={} by={}",
        entry.ard_identifier,
        entry.lifecycle_status.value,
        current_user.id,
    )
    return JSONResponse(status_code=status, content=_summary(entry), headers={"Cache-Control": "no-store"})


@router.post("/{identifier:path}/review")
async def review_a2a_agent(
    identifier: str,
    body: A2aReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """Approve or reject a registered agent. Approval pins the card as reviewed.

    Authorized like every other review (see api/routes/review.py): a team-visible
    agent by its team's owners and reviewers, a public one by global reviewers,
    a private one by admins. An agent outside the caller's scope answers 404
    when it is private, so its existence is not confirmed.
    """
    scope = await review_scope(db, current_user)
    if scope.is_empty:
        return _error(403, "PERMISSION_DENIED", "Insufficient permissions")
    entry = await _imported(db, identifier, for_update=True)
    if entry is None:
        return _error(404, "NOT_FOUND", "Entry not found")
    subject = SimpleNamespace(is_private=entry.visibility != DiscoveryVisibility.public, team_id=entry.team_id)
    if not can_review(subject, scope):
        if subject.is_private:
            return _error(404, "NOT_FOUND", "Entry not found")
        return _error(403, "PERMISSION_DENIED", "Public item is outside your review scope")
    if body.action == "approve" and not body.digest:
        return _error(400, "INVALID_ARGUMENT", "Approval needs the digest of the card you reviewed.")
    if body.digest and body.digest != entry.artifact_digest:
        return _error(
            409,
            "FAILED_PRECONDITION",
            "The Agent Card changed since you looked at it. Inspect it again and review the current card.",
        )
    if body.action == "reject" and not (body.reason or "").strip():
        return _error(400, "INVALID_ARGUMENT", "A rejection needs a reason.")
    card = pinned_card(entry)
    if card is None:
        return _error(409, "FAILED_PRECONDITION", "The stored Agent Card is unreadable; refresh it first.")
    lifecycle = DiscoveryLifecycle.approved if body.action == "approve" else DiscoveryLifecycle.rejected
    apply_card(entry, card, entry.artifact_url, lifecycle=lifecycle, reviewer_note=(body.reason or "").strip() or None)
    await db.commit()
    optic.info("a2a agent reviewed id={} action={} by={}", entry.ard_identifier, body.action, current_user.id)
    return JSONResponse(content=_summary(entry), headers={"Cache-Control": "no-store"})


@router.delete("/{identifier:path}")
async def remove_a2a_agent(
    identifier: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """Remove a registered agent from discovery. Owners and admins only."""
    entry = await _imported(db, identifier)
    if entry is None:
        return _error(404, "NOT_FOUND", "Entry not found")
    if entry.owner_user_id != current_user.id and not _is_admin(current_user):
        return _error(403, "PERMISSION_DENIED", "Only the owner or an admin can remove this agent.")
    entry.tombstoned_at = datetime.now(UTC)
    await db.commit()
    optic.info("a2a agent removed id={} by={}", entry.ard_identifier, current_user.id)
    return JSONResponse(content={"identifier": entry.ard_identifier, "removed": True})


__all__ = ["router"]
