# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Re-checking who may still read an item.

Authorization is resolved when an item is written, which is correct at that
instant. Two things can change afterwards, and both have to be caught:

* the reader's membership can be revoked, so someone removed from a teamspace
  must stop reading a private component's name out of their inbox;
* the SUBJECT can be made private, so an item written when a listing was public
  must stop being readable by people who cannot see the listing now.

The second is why ``is_private_subject`` on the row is not consulted for
anything that has a listing table. It records what was true at write time, and a
listing flipped to private afterwards would still be described by its rows as
public. Visibility is therefore resolved against the CURRENT listing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select, true

from api.deps import can_see_private_listings
from models.agent import Agent
from models.hook import HookListing
from models.inbox import InboxItem, InboxKind
from models.mcp import McpListing
from models.prompt import PromptListing
from models.sandbox import SandboxListing
from models.skill import SkillListing
from models.team import TeamMembership
from services.inbox.registry import REVIEWER_AUDIENCE, SPECS, spec_for

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from models.user import User

# Kinds that opt out of the membership re-check, resolved once from the registry
# so the SQL predicate and the row-level check cannot name different sets.
_NO_RECHECK_KINDS: tuple[InboxKind, ...] = tuple(kind for kind, spec in SPECS.items() if not spec.recheck_visibility)

# Subject types whose visibility is owned by a listing row. Anything absent here
# (``team``, ``insight_report``, ``system``) has no listing to consult and keeps
# the write-time snapshot.
_SUBJECT_MODELS: dict[str, type] = {
    "agent": Agent,
    "mcp": McpListing,
    "skill": SkillListing,
    "hook": HookListing,
    "prompt": PromptListing,
    "sandbox": SandboxListing,
}


def _listing_visible(model, user: User):
    """ "May this user see this listing", as a predicate over ``model``.

    Mirrors ``api.deps.apply_visibility_filter``: public is visible to everyone,
    private-with-a-team needs a membership row, and private-without-a-team is a
    personal subject that only its recipient holds a row for.
    """
    member = (
        select(TeamMembership.id)
        .where(TeamMembership.team_id == model.team_id, TeamMembership.user_id == user.id)
        .correlate(model)
        .exists()
    )
    return or_(
        model.is_private == False,  # noqa: E712
        model.team_id.is_(None),
        member,
    )


def _snapshot_visible(user: User):
    """Fallback for subjects with no listing table.

    ``team``, ``insight_report`` and ``system`` items are not listings, so there
    is nothing to re-read. The write-time snapshot is all there is.
    """
    member = (
        select(TeamMembership.id)
        .where(TeamMembership.team_id == InboxItem.team_id, TeamMembership.user_id == user.id)
        .correlate(InboxItem)
        .exists()
    )
    return or_(
        InboxItem.is_private_subject == False,  # noqa: E712
        InboxItem.team_id.is_(None),
        member,
    )


def visible_filter(user: User):
    """SQL predicate matching :func:`visible_to`, for counts and pagination.

    This is the set-level twin of ``visible_to`` and must stay identical to it,
    the same way ``apply_visibility_filter`` and
    ``check_listing_visibility_async`` mirror each other in ``api.deps``.

    It exists because post-filtering rows is not enough on its own: a ``total``
    or a badge count computed over unfiltered rows still discloses that hidden
    items exist. Someone removed from a teamspace could watch the count move and
    infer activity on a private component they can no longer read. Filtering in
    SQL keeps the aggregates, the page size, and the rows telling one story.

    The per-type EXISTS arms are correlated subqueries on a primary key, so each
    is an index lookup; only the arm matching a row's ``subject_type`` runs.
    """
    if can_see_private_listings(user):
        return true()

    audience = InboxItem.payload["audience"].as_string()
    arms = [
        and_(
            InboxItem.kind.in_(_NO_RECHECK_KINDS),
            or_(audience.is_(None), audience != REVIEWER_AUDIENCE),
        )
    ]
    for subject_type, model in _SUBJECT_MODELS.items():
        current = (
            select(model.id)
            .where(model.id == InboxItem.subject_id, _listing_visible(model, user))
            .correlate(InboxItem)
            .exists()
        )
        arms.append(and_(InboxItem.subject_type == subject_type, current))
    arms.append(and_(InboxItem.subject_type.notin_(tuple(_SUBJECT_MODELS)), _snapshot_visible(user)))
    return or_(*arms)


async def visible_to(db: AsyncSession, item: InboxItem, user: User) -> bool:
    """Whether this recipient may still see this item.

    The question here is "may this user still SEE the subject", which is
    membership — the same rule as ``api.deps.check_listing_visibility_async``.
    It is deliberately NOT ``teamspace.can_review``/``ReviewScope``: that
    answers "may they ACT on it", and a plain team member may see a private
    listing they have no authority to review. Using the review predicate here
    would hide a member's own decision notices from them.

    Items that fail this are omitted by the caller, never redacted. A redacted
    placeholder still discloses that something exists.
    """
    # A decision addressed to its own submitter reports what happened to work
    # they wrote. That stays theirs to read even if the listing has since moved
    # somewhere they cannot follow. A reviewer's copy of the same kind is not
    # theirs in that sense, so it is re-checked like any other item.
    if not spec_for(item.kind).recheck_visibility and (item.payload or {}).get("audience") != REVIEWER_AUDIENCE:
        return True

    if can_see_private_listings(user):
        return True

    model = _SUBJECT_MODELS.get(item.subject_type)
    if model is None:
        # No listing to consult; the snapshot is the only record of what this
        # subject was.
        if not item.is_private_subject or item.team_id is None:
            return True
        membership = await db.scalar(
            select(TeamMembership.id).where(
                TeamMembership.team_id == item.team_id,
                TeamMembership.user_id == user.id,
            )
        )
        return membership is not None

    # Deliberately reads the listing rather than the row's own snapshot: the
    # listing may have been made private after this item was written.
    row = (await db.execute(select(model.is_private, model.team_id).where(model.id == item.subject_id))).one_or_none()
    if row is None:
        # The subject is gone. Fail closed: there is nothing left to prove this
        # recipient may read it, and an item naming a listing nobody can fetch
        # is not worth disclosing.
        return False

    is_private, team_id = row
    if not is_private:
        return True
    if team_id is None:
        # Private with no teamspace is a personal subject: only the recipient,
        # who is already the only person holding this row.
        return True

    membership = await db.scalar(
        select(TeamMembership.id).where(
            TeamMembership.team_id == team_id,
            TeamMembership.user_id == user.id,
        )
    )
    return membership is not None


async def filter_visible(db: AsyncSession, items: list[InboxItem], user: User) -> list[InboxItem]:
    """Drop items the recipient may no longer see."""
    kept: list[InboxItem] = []
    for item in items:
        if await visible_to(db, item, user):
            kept.append(item)
    return kept
