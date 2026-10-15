# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The events that produce inbox items.

Review is not one choke point. Decisions land in seven endpoints across
``api/routes/review.py``, and a review can be requested from every submit and
publish path plus the visibility flip in
``teamspace.review_publication_to_public``. Every one of them calls a helper
here rather than assembling a delivery of its own, so the recipient rule, the
title and the dedupe key are decided once.

Auto-approved publishes deliberately emit nothing: a team owner publishing
team-visible work approves it for themselves, and there is no pending state for
anyone to act on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger as optic

from models.inbox import InboxKind
from services.inbox import recipients
from services.inbox.delivery import deliver, resolve_matching
from services.inbox.registry import Subject, spec_for

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


def subject_from_entity(entity, subject_type: str, *, version: str | None = None) -> Subject:
    """Build a Subject from a listing or agent row.

    Agents and all five component listings expose the same shape here
    (``namespace``, ``slug``, ``is_private``, ``team_id``), so one reader serves
    every type.
    """
    return Subject(
        type=subject_type,
        id=getattr(entity, "id", None),
        name=getattr(entity, "name", "") or "",
        namespace=getattr(entity, "namespace", None),
        slug=getattr(entity, "slug", None),
        version=version,
        team_id=getattr(entity, "team_id", None),
        is_private=bool(getattr(entity, "is_private", False)),
        # Teamspaces are addressed by handle, not id, so the URL builder needs
        # it. Listings have no handle and pass None here.
        handle=getattr(entity, "handle", None),
    )


async def on_review_requested(
    db: AsyncSession,
    entity,
    *,
    subject_type: str,
    actor_id: uuid.UUID | None,
    version: str | None = None,
    global_only: bool = False,
) -> int:
    """Something entered the review queue: tell everyone who can clear it.

    Call this only when a version is actually left pending. A publish that
    auto-approves has nothing waiting on a reviewer.
    """
    subject = subject_from_entity(entity, subject_type, version=version)
    users = await (recipients.global_reviewers(db) if global_only else recipients.reviewers_for(db, entity))
    items = await deliver(
        db,
        kind=InboxKind.review_requested,
        recipients=users,
        subject=subject,
        actor_id=actor_id,
    )
    optic.debug("inbox: review_requested for {} {} -> {} item(s)", subject_type, subject.id, len(items))
    return len(items)


async def on_review_withdrawn(
    db: AsyncSession,
    entity,
    *,
    subject_type: str,
    actor_id: uuid.UUID,
    version: str,
) -> int:
    """Close every reviewer's copy of a withdrawn version's open request."""
    subject = subject_from_entity(entity, subject_type, version=version)
    request_key = spec_for(InboxKind.review_requested).dedupe(subject, {})[:255]
    return await resolve_matching(
        db,
        kind=InboxKind.review_requested,
        dedupe_key=request_key,
        detail="Review request withdrawn by author",
        actor_id=actor_id,
    )


async def on_publish(
    db: AsyncSession,
    entity,
    *,
    subject_type: str,
    actor_id: uuid.UUID | None,
    auto_approved: bool,
    version: str | None = None,
) -> int:
    """A submit or publish finished: queue a review notice only if one is owed.

    Every publish path ends in the same fork — auto-approve, or leave pending —
    so the fork is encoded here once. A call site passing its own
    ``auto_approve`` result cannot forget the rule that self-approved work
    notifies nobody, because there is no pending state for a reviewer to act on.
    """
    if auto_approved:
        return 0
    return await on_review_requested(db, entity, subject_type=subject_type, actor_id=actor_id, version=version)


def _version_author(entity, version: str | None) -> uuid.UUID | None:
    """Find who released ``version`` of this listing or agent.

    Walks the already-loaded ``versions`` relationship rather than querying, so
    this adds no round trip to a decision that has the rows in session already.
    Falls back to the latest version when the caller named no version, and to
    None when nothing matches, which leaves the listing-level fallback in place.
    """
    versions = getattr(entity, "versions", None) or []
    if version is not None:
        for candidate in versions:
            if getattr(candidate, "version", None) == version:
                return getattr(candidate, "released_by", None)
    latest = getattr(entity, "latest_version", None)
    return getattr(latest, "released_by", None) if latest is not None else None


async def on_review_decided(
    db: AsyncSession,
    entity,
    *,
    subject_type: str,
    approved: bool,
    actor_id: uuid.UUID | None,
    version: str | None = None,
    reason: str | None = None,
    submitter_id: uuid.UUID | None = None,
) -> int:
    """A reviewer acted: tell whoever released the version that was decided.

    The recipient is the VERSION's author, not the listing's. Those differ more
    often than they look: a listing created by one person accumulates releases
    from co-authors, and ownership transfer rewrites the listing's owner without
    touching any version. Sending a rejection reason to the listing owner would
    hand one contributor's review feedback to another.

    ``submitter_id`` lets a caller that already holds the decided version pass
    its ``released_by`` directly. When it is absent this resolves the version
    itself and only falls back to the listing's own submitter when no version
    can be found.
    """
    subject = subject_from_entity(entity, subject_type, version=version)
    if submitter_id is None:
        submitter_id = _version_author(entity, version)
    users = [submitter_id] if submitter_id is not None else recipients.submitter_of(entity)
    items = await deliver(
        db,
        kind=InboxKind.review_approved if approved else InboxKind.review_rejected,
        recipients=[u for u in users if u is not None],
        subject=subject,
        actor_id=actor_id,
        body=reason,
        context={"reason": reason} if reason else None,
    )

    # The decision empties the queue for EVERY reviewer it fanned out to, not
    # only the one who acted. Their still-open review_requested copies would
    # otherwise keep counting as outstanding work and link to a queue that no
    # longer holds the submission. The request items share one deterministic
    # dedupe key per (subject, version), which is what makes them findable here.
    request_key = spec_for(InboxKind.review_requested).dedupe(subject, {})[:255]
    cleared = await resolve_matching(
        db,
        kind=InboxKind.review_requested,
        dedupe_key=request_key,
        detail=f"Review {'approved' if approved else 'rejected'}",
        actor_id=actor_id,
    )
    if cleared:
        optic.debug("inbox: cleared {} open review_requested item(s) for key {}", cleared, request_key)
    optic.debug(
        "inbox: review_{} for {} {} -> {} item(s)",
        "approved" if approved else "rejected",
        subject_type,
        subject.id,
        len(items),
    )
    return len(items)


def _team_subject(team) -> Subject:
    """A teamspace as an inbox subject.

    ``team_id`` and ``is_private`` are carried so read-time visibility can
    re-check membership. This matters for ``team_join_requested``, which goes
    only to owners: if a private team's owner is later removed, the membership
    re-check must stop their inbox from continuing to surface that team's join
    requests and decision activity. The requester's own ``team_join_decided``
    notice is unaffected because that kind sets ``recheck_visibility=False`` and
    is exempt from the re-check regardless of what the subject carries — so a
    rejected requester still reads their decision even though they are not a
    member. For a public team ``is_private`` is False, so the snapshot check
    passes for everyone and nothing is hidden.
    """
    return Subject(
        type="team",
        id=getattr(team, "id", None),
        name=getattr(team, "name", "") or "",
        handle=getattr(team, "handle", None),
        team_id=getattr(team, "id", None),
        is_private=bool(getattr(team, "is_private", False)),
    )


async def on_team_visibility_requested(
    db: AsyncSession,
    team,
    *,
    requester_id: uuid.UUID,
) -> int:
    """Tell global reviewers that a teamspace is waiting for public approval."""
    subject = _team_subject(team)
    users = await recipients.global_reviewers(db)
    items = await deliver(
        db,
        kind=InboxKind.team_created_pending,
        recipients=users,
        subject=subject,
        actor_id=requester_id,
        skip_actor=False,
    )
    optic.debug("inbox: team visibility requested for {} -> {} item(s)", team.id, len(items))
    return len(items)


async def on_team_visibility_cancelled(
    db: AsyncSession,
    team,
    *,
    actor_id: uuid.UUID | None,
) -> int:
    """Close reviewer work when an owner withdraws a visibility request."""
    subject = _team_subject(team)
    request_key = spec_for(InboxKind.team_created_pending).dedupe(subject, {})[:255]
    return await resolve_matching(
        db,
        kind=InboxKind.team_created_pending,
        dedupe_key=request_key,
        detail="Public visibility request withdrawn",
        actor_id=actor_id,
    )


async def on_team_visibility_decided(
    db: AsyncSession,
    team,
    *,
    requester_id: uuid.UUID | None,
    approved: bool,
    actor_id: uuid.UUID | None,
    reason: str | None = None,
) -> int:
    """Notify the requester and current owners, then close reviewer work."""
    subject = _team_subject(team)
    owners = await recipients.team_owners(db, team.id)
    users = owners + ([requester_id] if requester_id is not None else [])
    items = await deliver(
        db,
        kind=InboxKind.review_approved if approved else InboxKind.review_rejected,
        recipients=users,
        subject=subject,
        actor_id=actor_id,
        body=reason,
        context={"reason": reason} if reason else None,
        skip_actor=False,
    )
    request_key = spec_for(InboxKind.team_created_pending).dedupe(subject, {})[:255]
    await resolve_matching(
        db,
        kind=InboxKind.team_created_pending,
        dedupe_key=request_key,
        detail=f"Public visibility {'approved' if approved else 'rejected'}",
        actor_id=actor_id,
    )
    return len(items)


async def on_team_join_requested(
    db: AsyncSession,
    team,
    *,
    requester_id: uuid.UUID,
    message: str | None = None,
) -> int:
    """Someone asked to join a teamspace: tell every owner.

    The requester is the actor, so ``skip_actor`` keeps their own request out of
    their inbox even if they are somehow also an owner.
    """
    subject = _team_subject(team)
    owners = await recipients.team_owners(db, team.id)
    items = await deliver(
        db,
        kind=InboxKind.team_join_requested,
        recipients=owners,
        subject=subject,
        actor_id=requester_id,
        body=message,
        context={"requester_id": str(requester_id)},
    )
    optic.debug("inbox: team_join_requested for team {} -> {} item(s)", team.id, len(items))
    return len(items)


async def on_team_join_decided(
    db: AsyncSession,
    team,
    *,
    request_id: uuid.UUID,
    requester_id: uuid.UUID,
    approved: bool,
    actor_id: uuid.UUID | None,
    reason: str | None = None,
) -> int:
    """An owner decided a join request: tell the requester, clear the owners.

    The decision closes the ``team_join_requested`` copy in EVERY owner's
    inbox, not only the deciding owner's, exactly like a review decision
    empties the review queue for all of its reviewers.
    """
    subject = _team_subject(team)
    decision = "approved" if approved else "rejected"
    items = await deliver(
        db,
        kind=InboxKind.team_join_decided,
        recipients=[requester_id],
        subject=subject,
        actor_id=actor_id,
        body=reason,
        context={"request_id": str(request_id), "decision": decision},
        # The requester asked for this decision; deliver even if an admin
        # somehow decides their own request.
        skip_actor=False,
    )

    request_key = spec_for(InboxKind.team_join_requested).dedupe(subject, {"requester_id": str(requester_id)})[:255]
    cleared = await resolve_matching(
        db,
        kind=InboxKind.team_join_requested,
        dedupe_key=request_key,
        detail=f"Join request {decision}",
        actor_id=actor_id,
    )
    if cleared:
        optic.debug("inbox: cleared {} open team_join_requested item(s) for key {}", cleared, request_key)
    return len(items)


async def on_team_join_cancelled(
    db: AsyncSession,
    team,
    *,
    requester_id: uuid.UUID,
) -> int:
    """The requester withdrew: clear the owners' open request items.

    No decision notice is owed to anyone — the requester acted, and the fact
    the owners were asked to act on has stopped being true.
    """
    subject = _team_subject(team)
    request_key = spec_for(InboxKind.team_join_requested).dedupe(subject, {"requester_id": str(requester_id)})[:255]
    return await resolve_matching(
        db,
        kind=InboxKind.team_join_requested,
        dedupe_key=request_key,
        detail="Join request withdrawn",
        actor_id=requester_id,
    )


async def on_insight_ready(
    db: AsyncSession,
    report,
    *,
    agent_name: str = "",
    requester_id: uuid.UUID | None = None,
) -> int:
    """A report finished generating: tell whoever asked for it.

    The requester only. Notifying the agent owner as well was considered and
    left out: ``batch_generate_insights`` runs weekly for every agent, so owner
    notification would deliver a batch nobody requested. Add it behind an
    explicit preference if it is ever wanted.
    """
    target = requester_id if requester_id is not None else getattr(report, "triggered_by", None)
    if target is None:
        return 0
    subject = Subject(
        type="insight_report",
        id=getattr(report, "id", None),
        name=agent_name or "insight report",
    )
    items = await deliver(
        db,
        kind=InboxKind.insight_ready,
        recipients=[target],
        subject=subject,
        actor_id=None,
        skip_actor=False,  # the requester asked for this; it is the whole point
    )
    return len(items)


async def on_system_notice(
    db: AsyncSession,
    *,
    title: str,
    notice_id: str,
    body: str | None = None,
    context: dict[str, Any] | None = None,
) -> int:
    """An admin/system event worth an operator's attention.

    Deliberately not wired to anything yet. The operational events that might
    warrant one (migration failures, retention anomalies) already page through
    the alerting path in ``services/alert_evaluator.py``, and duplicating pages
    into the inbox is a decision to make per event, not a default. The helper
    exists so the first such event does not invent its own delivery.
    """
    users = await recipients.admins(db)
    ctx: dict[str, Any] = {"title": title, "notice_id": notice_id}
    if context:
        ctx.update(context)
    items = await deliver(
        db,
        kind=InboxKind.system_notice,
        recipients=users,
        subject=Subject(type="system", name=title),
        actor_id=None,
        body=body,
        context=ctx,
    )
    return len(items)
