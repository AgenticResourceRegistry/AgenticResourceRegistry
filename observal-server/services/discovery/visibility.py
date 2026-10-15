# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Who may see which discovery entries.

Applies the caller's namespace scope and the registry's lifecycle rules
(ADR 0001, Decision 6) as a SQL predicate:

1. Scope — ordinary signed-in users see their own personal listings and items
   in teamspaces they currently belong to, regardless of public/private status.
   Anonymous callers see public items; reviewers retain their public review
   scope, and admins and super-admins retain access to everything.
2. Lifecycle — approved entries to everyone who passes (1); pending, rejected
   and draft entries only to the owner or a co-author (the owner fallback that
   install already honours); whoever may review a pending entry
   (``services.teamspace.can_review``) sees it too, which is their queue, but
   not other people's drafts or rejections; archived entries only when asked
   for explicitly.

Anonymous callers see public approved entries, and only when the deployment
has switched public search on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, or_, select, true

from models.discovery_entry import DiscoveryEntry, DiscoveryLifecycle, DiscoveryVisibility
from models.team import Team, TeamMembership
from models.user import UserRole
from services.teamspace import REVIEWING_TEAM_ROLES, TEAM_REVIEW_UNLOCKED

if TYPE_CHECKING:
    import uuid

_ADMIN_ROLES = {UserRole.admin, UserRole.super_admin}

# Lifecycle states returned when the caller does not filter on obs:lifecycle.
DEFAULT_LIFECYCLES: tuple[DiscoveryLifecycle, ...] = (
    DiscoveryLifecycle.approved,
    DiscoveryLifecycle.pending,
    DiscoveryLifecycle.rejected,
    DiscoveryLifecycle.draft,
)


def _is_admin(user: Any | None) -> bool:
    return user is not None and getattr(user, "role", None) in _ADMIN_ROLES


def _is_reviewer(user: Any | None) -> bool:
    return user is not None and getattr(user, "role", None) == UserRole.reviewer


def privacy_predicate(user: Any | None):
    """Scope ARD reads by current ownership/membership, not the public catalog.

    For personal listings, owner_user_id follows the native owner through
    transfers (unlike a parsed namespace/slug reference). A team's submitter
    cannot retain access after losing membership, even when the item is public.
    """
    public = DiscoveryEntry.visibility == DiscoveryVisibility.public
    if user is None:
        return public
    if _is_admin(user):
        return true()
    user_id: uuid.UUID = user.id
    own = and_(
        DiscoveryEntry.team_id.is_(None),
        DiscoveryEntry.owner_user_id == user_id,
        DiscoveryEntry.visibility.in_((DiscoveryVisibility.public, DiscoveryVisibility.owner)),
    )
    member = (
        select(TeamMembership.id)
        .where(TeamMembership.team_id == DiscoveryEntry.team_id, TeamMembership.user_id == user_id)
        .correlate(DiscoveryEntry)
        .exists()
    )
    team = and_(
        DiscoveryEntry.team_id.is_not(None),
        DiscoveryEntry.visibility.in_((DiscoveryVisibility.public, DiscoveryVisibility.team)),
        member,
    )
    if _is_reviewer(user):
        # Reviewers still browse the public catalog to work their global queue.
        return or_(public, own, team)
    return or_(own, team)


def _review_queue(user: Any):
    """Pending entries the caller may review: the SQL form of services.teamspace.can_review."""
    reviews_team = (
        select(TeamMembership.id)
        .join(Team, Team.id == TeamMembership.team_id)
        .where(
            TeamMembership.team_id == DiscoveryEntry.team_id,
            TeamMembership.user_id == user.id,
            TeamMembership.role.in_(REVIEWING_TEAM_ROLES),
            TEAM_REVIEW_UNLOCKED,
        )
        .correlate(DiscoveryEntry)
    )
    public = DiscoveryEntry.visibility == DiscoveryVisibility.public
    public_scope = true() if _is_reviewer(user) else reviews_team.where(Team.is_private.is_(False)).exists()
    return and_(
        DiscoveryEntry.lifecycle_status == DiscoveryLifecycle.pending,
        or_(and_(public, public_scope), and_(~public, reviews_team.exists())),
    )


def lifecycle_predicate(user: Any | None, lifecycles: tuple[DiscoveryLifecycle, ...] = DEFAULT_LIFECYCLES):
    """Which lifecycle states the caller may see among the requested ones."""
    wanted = set(lifecycles)
    approved_wanted = DiscoveryLifecycle.approved in wanted
    unapproved_wanted = wanted - {DiscoveryLifecycle.approved}

    clauses = []
    if approved_wanted:
        clauses.append(DiscoveryEntry.lifecycle_status == DiscoveryLifecycle.approved)
    if unapproved_wanted and user is not None:
        in_unapproved = DiscoveryEntry.lifecycle_status.in_(list(unapproved_wanted))
        owner = DiscoveryEntry.owner_user_id == user.id
        co_author = DiscoveryEntry.co_author_ids.like(f"%{user.id}%")
        own = and_(in_unapproved, or_(owner, co_author))
        if _is_admin(user):
            clauses.append(in_unapproved)
        elif DiscoveryLifecycle.pending in wanted:
            clauses.append(or_(own, _review_queue(user)))  # their own work, plus what they may review
        else:
            clauses.append(own)
    if not clauses:
        # Nothing the caller is allowed to see in the requested states.
        return DiscoveryEntry.id.is_(None)
    return or_(*clauses)


def visible_entries_predicate(
    user: Any | None,
    *,
    lifecycles: tuple[DiscoveryLifecycle, ...] = DEFAULT_LIFECYCLES,
):
    """Complete predicate: live, allowed to see, and in a permitted lifecycle."""
    return and_(
        DiscoveryEntry.tombstoned_at.is_(None),
        privacy_predicate(user),
        lifecycle_predicate(user, lifecycles),
    )
