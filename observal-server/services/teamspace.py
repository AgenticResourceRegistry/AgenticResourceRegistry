# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Teamspace membership and handle reservation helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select

from models.team import Team, TeamMembership, TeamRole
from models.user import User, UserRole
from services.registry_namespace import namespace_for_user, slugify, validate_namespace

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

_HANDLE_STRIP_RE = re.compile(r"[^a-z0-9-]+")


def slugify_handle(raw: str, *, fallback: str = "team") -> str:
    """Reduce raw text to a namespace-valid handle (3-32 lowercase chars)."""
    base = _HANDLE_STRIP_RE.sub("-", (raw or "").strip().lower()).strip("-")
    if not base:
        base = fallback
    # Namespaces require 3-32 chars. Keep short names recognizable instead of
    # appending an opaque character.
    if len(base) > 32:
        base = base[:32].rstrip("-")
    if len(base) < 3:
        base = f"{base}-team"
    return validate_namespace(base, allow_reserved=True)


def is_admin(user: User) -> bool:
    return user.role in (UserRole.admin, UserRole.super_admin)


def is_global_reviewer(user: User) -> bool:
    return user.role in (UserRole.reviewer, UserRole.admin, UserRole.super_admin)


def is_team_role(role: TeamRole | str, expected: TeamRole) -> bool:
    value = role.value if isinstance(role, TeamRole) else str(role)
    return value == expected.value


async def team_membership(db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID) -> TeamMembership | None:
    return (
        await db.execute(
            select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
        )
    ).scalar_one_or_none()


# The team roles that clear review for their own teamspace. A plain member
# publishes into the queue; it is these two that empty it.
REVIEWING_TEAM_ROLES = (TeamRole.owner, TeamRole.reviewer)
# A teamspace whose visibility change is under review is locked: its roles review nothing meanwhile.
TEAM_REVIEW_UNLOCKED = or_(Team.visibility_request_status.is_(None), Team.visibility_request_status != "pending")


@dataclass(frozen=True)
class ReviewScope:
    """What one caller is allowed to review.

    ``is_admin`` reviews everything. ``is_global_reviewer`` without ``is_admin``
    is the plain global reviewer role. ``team_ids`` contains unlocked teamspaces
    where the caller owns or reviews private content. ``public_team_ids`` is the
    subset already approved for public visibility, whose public content those
    same team roles may also review.
    """

    is_admin: bool
    is_global_reviewer: bool
    team_ids: frozenset[uuid.UUID]
    public_team_ids: frozenset[uuid.UUID] = frozenset()

    @property
    def is_empty(self) -> bool:
        """True when the caller may not review anything at all."""
        return not self.is_global_reviewer and not self.team_ids


async def review_scope(db: AsyncSession, user: User) -> ReviewScope:
    """Resolve the caller's review capability once, for both listing and acting.

    The review queue and the approve and reject actions must agree on what a
    caller may touch, so both read this single answer instead of re-deriving it.
    """
    if is_admin(user):
        # An admin reviews every item regardless of teamspace, so their own
        # memberships would not widen anything and the query is skipped.
        return ReviewScope(is_admin=True, is_global_reviewer=True, team_ids=frozenset())

    rows = (
        await db.execute(
            select(TeamMembership.team_id, Team.is_private)
            .join(Team, Team.id == TeamMembership.team_id)
            .where(
                TeamMembership.user_id == user.id,
                TeamMembership.role.in_(REVIEWING_TEAM_ROLES),
                TEAM_REVIEW_UNLOCKED,
            )
        )
    ).all()
    return ReviewScope(
        is_admin=False,
        is_global_reviewer=is_global_reviewer(user),
        team_ids=frozenset(team_id for team_id, _is_private in rows),
        public_team_ids=frozenset(team_id for team_id, private in rows if not private),
    )


def can_review(entity, scope: ReviewScope) -> bool:
    """Whether this caller may review one listing, agent version, or agent.

    Decided from the item's own visibility, never from what the caller asked for.
    A team-private item belongs to its teamspace: only that teamspace's owners and
    reviewers, plus admins, may act on it. Public items may also be handled by
    owners and reviewers when their teamspace has passed public visibility review.
    Reviewing their own submission is allowed: team publishes never auto-approve,
    so self-approval is the recorded decision replacing the old silent skip.

    A private item with no teamspace is a personal listing. Nobody holds a team
    role over it, so it stays admin-only.
    """
    if scope.is_admin:
        return True
    team_id = getattr(entity, "team_id", None)
    if getattr(entity, "is_private", False):
        return team_id is not None and team_id in scope.team_ids
    return scope.is_global_reviewer or (team_id is not None and team_id in scope.public_team_ids)


async def user_team_ids(db: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    rows = await db.execute(select(TeamMembership.team_id).where(TeamMembership.user_id == user_id))
    return list(rows.scalars().all())


def team_visible_to(team: Team, membership: TeamMembership | None, user: User) -> bool:
    """Whether this caller may see this teamspace at all.

    Public teamspaces are visible to every signed-in user. A private one is
    visible only to its members and deployment admins. Global reviewers may
    also inspect a pending public-visibility request. Other private teamspaces
    remain hidden, and callers that fail this check receive 404 so a private
    handle's existence is not confirmed.
    """
    if not team.is_private:
        return True
    if getattr(team, "visibility_request_status", None) == "pending":
        return membership is not None or is_global_reviewer(user)
    return membership is not None or is_admin(user)


@dataclass(frozen=True)
class PublishTarget:
    namespace: str
    slug: str
    team_id: uuid.UUID | None
    visibility: str
    owner: str
    auto_approve: bool


async def resolve_publish_target(
    db: AsyncSession,
    user: User,
    name: str,
    *,
    team_id: uuid.UUID | None = None,
    visibility: str | None = None,
) -> PublishTarget:
    """Resolve and authorize the namespace and visibility for a new listing."""
    target_visibility = (visibility or "public").strip().lower()
    if target_visibility not in {"public", "team"}:
        raise HTTPException(status_code=422, detail="visibility must be 'public' or 'team'")

    if team_id is None:
        if target_visibility == "team":
            raise HTTPException(status_code=422, detail="Team visibility requires a teamspace")
        return PublishTarget(
            namespace=namespace_for_user(user),
            slug=slugify(name),
            team_id=None,
            visibility="public",
            owner=user.username or user.email,
            auto_approve=False,
        )

    team = await db.get(Team, team_id, with_for_update=True)
    if not team:
        raise HTTPException(status_code=404, detail="Teamspace not found")
    if getattr(team, "visibility_request_status", None) == "pending":
        raise HTTPException(status_code=409, detail="Teamspace is locked pending public visibility review")
    if team.is_private and target_visibility == "public":
        raise HTTPException(status_code=409, detail="Private teamspaces can only publish team-private items")
    membership = await team_membership(db, team.id, user.id)
    if getattr(team, "is_personal", False) and (
        team.created_by != user.id or membership is None or membership.role != TeamRole.owner or not team.is_private
    ):
        raise HTTPException(status_code=403, detail="Personal publishing requires its active creator-owner")
    # Publishing into a teamspace you do not belong to is an admin capability, not a
    # reviewer one. A global reviewer cannot read another team's private listings, so
    # letting one publish a team-private item here would create a listing its author
    # can no longer see. Admins keep it because they can still read the result.
    if not membership and not is_admin(user):
        raise HTTPException(status_code=403, detail="You are not a member of this teamspace")

    # NOTHING published into a teamspace auto-approves. Every submission enters
    # the review queue; team owners and team reviewers may then explicitly
    # approve — including their own submissions — so every release carries a
    # recorded reviewed_by decision instead of a silent skip.
    return PublishTarget(
        namespace=team.handle,
        slug=slugify(name),
        team_id=team.id,
        visibility=target_visibility,
        owner=team.handle,
        auto_approve=False,
    )


async def publish_auto_approves_for_entity(entity, user: User, db: AsyncSession) -> bool:
    """Return whether this actor can publish an already saved team item without review.

    Always False: teamspace publishing never auto-approves. Team owners and
    team reviewers clear their own queue explicitly (self-approval is allowed),
    so approval is a recorded decision rather than a side effect of publishing.
    The helper survives so every publish path keeps one policy chokepoint.
    """
    del entity, user, db
    return False


async def skill_transition_needs_rollout_gate(db: AsyncSession, listing_id: uuid.UUID) -> bool:
    """Old workers do not understand tracked reviews, extras, or empty script files."""
    from models.skill import SkillVersion

    return (
        await db.scalar(
            select(SkillVersion.id)
            .where(
                SkillVersion.listing_id == listing_id,
                or_(
                    SkillVersion.content_revision.is_not(None),
                    SkillVersion.base_revision.is_not(None),
                    SkillVersion.base_version_id.is_not(None),
                    SkillVersion.review_epoch != 0,
                    func.json_array_length(SkillVersion.extra_files) > 0,
                    and_(
                        SkillVersion.delivery_mode == "registry_direct",
                        SkillVersion.script_filename.is_not(None),
                        SkillVersion.script_content == "",
                    ),
                ),
            )
            .limit(1)
        )
        is not None
    )


async def _review_skill_visibility(
    entity, user: User, db: AsyncSession, *, was_private: bool, identity_transfer: bool = False
) -> bool:
    """Serialize every release's review provenance with publication and reversal.

    The caller holds the listing FOR UPDATE before changing visibility; version
    locks follow the same listing-then-version order as review and installation.
    Notifications are version-specific, and are part of the same transaction.
    """
    from models.mcp import ListingStatus
    from models.skill import SkillVersion
    from services.inbox import sources as inbox
    from services.versioning import parse_semver

    if was_private == entity.is_private and not identity_transfer:
        return False
    rows = (
        (
            await db.execute(
                select(SkillVersion)
                .where(SkillVersion.listing_id == entity.id)
                .order_by(SkillVersion.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    if was_private or identity_transfer:
        requeued = False
        for row in rows:
            # Close prior notices before re-delivery: dedupe reopens them for
            # new recipients, otherwise former reviewers see the old work.
            if row.status == ListingStatus.pending:
                await inbox.on_review_withdrawn(db, entity, subject_type="skill", actor_id=user.id, version=row.version)
            row.requires_global_review = True
            if row.status in (ListingStatus.approved, ListingStatus.archived):
                row.pre_public_status = row.status.value
                row.pre_public_reviewed_by = row.reviewed_by
                row.pre_public_reviewed_at = row.reviewed_at
                row.status = ListingStatus.pending
                row.reviewed_by = None
                row.reviewed_at = None
                requeued = True
            if row.status == ListingStatus.pending:
                await inbox.on_review_requested(
                    db, entity, subject_type="skill", actor_id=user.id, version=row.version, global_only=True
                )
        return requeued

    # A public approval is a genuine new review and survives the return to team
    # visibility. A rejected publication is not a rejection of the older team
    # approval: restore its immutable bytes and original archived/approved state.
    for row in rows:
        if not row.requires_global_review:
            continue
        if row.status == ListingStatus.pending:
            await inbox.on_review_withdrawn(db, entity, subject_type="skill", actor_id=user.id, version=row.version)
        if row.pre_public_status is not None:
            row.status = ListingStatus(row.pre_public_status)
            row.rejection_reason = None
            row.reviewed_by = row.pre_public_reviewed_by
            row.reviewed_at = row.pre_public_reviewed_at
            row.pre_public_status = None
            row.pre_public_reviewed_by = None
            row.pre_public_reviewed_at = None
        row.requires_global_review = False
        if row.status == ListingStatus.pending:
            await inbox.on_review_requested(db, entity, subject_type="skill", actor_id=user.id, version=row.version)

    # A former pointer can still point at a pending candidate. Never demote an
    # already-cleared newer release when choosing the private default.
    candidates = [
        row
        for row in rows
        if row.status in (ListingStatus.approved, ListingStatus.archived) and not row.requires_global_review
    ]
    if candidates:
        stable = [row for row in candidates if parse_semver(row.version) is not None]
        if stable:
            chosen = max(stable, key=lambda row: (parse_semver(row.version), str(row.id)))
        elif len(candidates) == 1:
            chosen = candidates[0]
        else:
            raise HTTPException(status_code=409, detail="Cannot select a default from historical skill versions")
        current = next((row for row in candidates if row.id == entity.latest_version_id), None)
        if current is None or (
            parse_semver(chosen.version)
            and parse_semver(current.version)
            and parse_semver(chosen.version) > parse_semver(current.version)
        ):
            entity.latest_version_id = chosen.id
    return False


async def review_skill_identity_transfer(entity, user: User, db: AsyncSession) -> bool:
    """Re-review every public release after its owner and namespace change.

    This is not a new release: retain historical approval/archival provenance
    for exact UUID decisions, while installs exclude every marked row.
    """
    return await _review_skill_visibility(entity, user, db, was_private=False, identity_transfer=True)


async def review_publication_to_public(entity, user: User, db: AsyncSession, *, was_private: bool) -> bool:
    """Requeue team-reviewed releases when published; restore skill provenance on reversal.

    Skill publication always requires a new global decision for *every* team-
    reviewed release, regardless of the visibility actor's role. Other component
    types retain their historical transition behavior.
    """
    from models.skill import SkillListing

    if isinstance(entity, SkillListing):
        return await _review_skill_visibility(entity, user, db, was_private=was_private)
    if not was_private or entity.is_private or is_global_reviewer(user):
        return False

    version = getattr(entity, "latest_version", None)
    if version is None:
        raise RuntimeError(f"{type(entity).__name__} has no latest_version; cannot re-enter review")
    if version.status != _approved_status(entity):
        return False

    version_model, listing_column = _version_model_for(entity)
    approved = _approved_status(entity)
    pending = _pending_status(entity)
    rows = (
        await db.execute(select(version_model).where(listing_column == entity.id, version_model.status == approved))
    ).scalars()
    for row in rows:
        row.status = pending
        row.reviewed_by = None
        row.reviewed_at = None

    entity.status = pending
    version.reviewed_by = None
    version.reviewed_at = None
    return True


def _version_model_for(entity):
    """Return (version model, the column linking a version back to its listing)."""
    from models.agent import Agent, AgentVersion

    if isinstance(entity, Agent):
        return AgentVersion, AgentVersion.agent_id
    version_model = type(entity).__mapper__.relationships["latest_version"].entity.class_
    return version_model, version_model.listing_id


def _approved_status(entity):
    from models.agent import Agent, AgentStatus
    from models.mcp import ListingStatus

    return AgentStatus.approved if isinstance(entity, Agent) else ListingStatus.approved


def _pending_status(entity):
    from models.agent import Agent, AgentStatus
    from models.mcp import ListingStatus

    return AgentStatus.pending if isinstance(entity, Agent) else ListingStatus.pending


async def reserve_handle(
    db: AsyncSession,
    handle: str,
    *,
    exclude_team_id: uuid.UUID | None = None,
    exclude_user_id: uuid.UUID | None = None,
) -> str:
    """Slugify, validate, and ensure handle is free across users and teams.

    A handle is the namespace identity for both a user and a team, so it must
    resolve unambiguously. Raises ValueError on validation failure or collision.
    """
    value = validate_namespace(slugify_handle(handle))

    user_stmt = select(User.id).where(User.username == value)
    if exclude_user_id is not None:
        user_stmt = user_stmt.where(User.id != exclude_user_id)
    if (await db.execute(user_stmt.limit(1))).scalar_one_or_none() is not None:
        raise ValueError(f"Handle '{value}' is already taken")

    team_stmt = select(Team.id).where(Team.handle == value)
    if exclude_team_id is not None:
        team_stmt = team_stmt.where(Team.id != exclude_team_id)
    if (await db.execute(team_stmt.limit(1))).scalar_one_or_none() is not None:
        raise ValueError(f"Handle '{value}' is already taken")

    return value


async def count_owners(db: AsyncSession, team_id: uuid.UUID, *, for_update: bool = False) -> int:
    stmt = select(TeamMembership.id).where(TeamMembership.team_id == team_id, TeamMembership.role == TeamRole.owner)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalars().all().__len__()
