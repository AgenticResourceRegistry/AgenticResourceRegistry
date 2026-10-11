# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Transactional inbox fan-out for PR reviews; legacy notices remain untouched."""

import uuid

from sqlalchemy import select

from models.inbox import InboxItem, InboxKind, InboxState
from models.review import ReviewComment, ReviewReviewerRequest, ReviewSubmission, ReviewSubscription
from services.inbox import recipients
from services.inbox.delivery import deliver, resolve
from services.inbox.registry import Subject
from services.review.decisions import _target


async def resolve_review_work(db, review, actor_id, *, kinds=None, user_id=None, request_ids=None):
    """Resolve only this review's PR-style actionable items, in the decision transaction.

    The legacy queue uses the same kinds but does not carry review_number, so it
    must not be touched before cutover. All revision-specific keys are covered.
    """
    kinds = kinds or (InboxKind.review_requested, InboxKind.review_ready, InboxKind.change_requested)
    stmt = select(InboxItem).where(
        InboxItem.kind.in_(kinds),
        InboxItem.state == InboxState.open,
        InboxItem.payload["review_number"].as_integer() == review.number,
    )
    if user_id is not None:
        stmt = stmt.where(InboxItem.user_id == user_id)
    items = (await db.scalars(stmt.with_for_update())).all()
    for item in items:
        if request_ids is None or item.payload.get("request_id") in request_ids:
            resolve(db, item, state=InboxState.done, actor_id=actor_id)


async def resolve_change_requests(db, review, reviewer_id, actor_id):
    ids = {
        str(sid)
        for sid in (
            await db.scalars(
                select(ReviewSubmission.id).where(
                    ReviewSubmission.review_id == review.id,
                    ReviewSubmission.reviewer_id == reviewer_id,
                    ReviewSubmission.verdict == "request_changes",
                )
            )
        ).all()
    }
    if ids:
        await resolve_review_work(db, review, actor_id, kinds=(InboxKind.change_requested,), request_ids=ids)


async def deliver_gate_change(db, review, was_ready: bool, actor_id, *, revision=None, policy=None):
    """The decision engine has already synchronized state; deliver only transitions."""
    from models.review import ReviewState
    from services.review.decisions import gate

    if review.state == ReviewState.published:
        await deliver_event(db, review, "published", actor_id, revision=revision)
        return
    is_ready = (await gate(db, review, policy=policy)).ready
    if is_ready:
        await resolve_review_work(db, review, actor_id, kinds=(InboxKind.review_requested, InboxKind.change_requested))
    elif was_ready:
        await resolve_review_work(db, review, actor_id, kinds=(InboxKind.review_ready,))
    if was_ready != is_ready:
        await deliver_event(db, review, "ready" if is_ready else "lost", actor_id, revision=revision)


async def deliver_event(
    db,
    review,
    event: str,
    actor_id: uuid.UUID | None,
    *,
    thread_id=None,
    comment_id=None,
    submission_id=None,
    reviewer_id=None,
    revision=None,
):
    """Author and listing owner get every event except their own; mute exempts final outcomes."""
    if event in ("published", "closed", "withdrawn", "superseded"):
        await resolve_review_work(db, review, actor_id)
    subject, version = await _target(db, review)
    author_ids = {version.released_by, getattr(subject, "created_by", None), getattr(subject, "submitted_by", None)} - {
        None
    }
    submitted = set(
        (
            await db.scalars(
                select(ReviewSubmission.reviewer_id).where(
                    ReviewSubmission.review_id == review.id, ReviewSubmission.state != "draft"
                )
            )
        ).all()
    )
    requested = set(
        (
            await db.scalars(select(ReviewReviewerRequest.user_id).where(ReviewReviewerRequest.review_id == review.id))
        ).all()
    )
    thread_users = set()
    if thread_id:
        thread_users = set(
            (
                await db.scalars(
                    select(ReviewComment.author_id).where(
                        ReviewComment.review_id == review.id,
                        ReviewComment.thread_id == thread_id,
                        ReviewComment.deleted_at.is_(None),
                    )
                )
            ).all()
        )
    kind = {
        "opened": InboxKind.review_requested,
        "revision": InboxKind.review_requested,
        "requested": InboxKind.review_requested,
        "comment": InboxKind.review_comment,
        "resolved": InboxKind.review_comment,
        "unresolved": InboxKind.review_comment,
        "approve": InboxKind.review_approval,
        "request_changes": InboxKind.change_requested,
        "dismissed": InboxKind.review_dismissed,
        "ready": InboxKind.review_ready,
        "lost": InboxKind.review_comment,
        "policy_changed": InboxKind.review_comment,
        "published": InboxKind.review_approved,
        "closed": InboxKind.review_rejected,
        "withdrawn": InboxKind.review_comment,
        "superseded": InboxKind.review_rejected,
    }[event]
    if event == "opened":
        ids = set(await recipients.reviewers_for(db, subject))
    elif event == "revision":
        ids = submitted | requested
    elif event == "requested":
        ids = author_ids | {reviewer_id}
    elif event in ("comment", "resolved", "unresolved"):
        ids = author_ids | (thread_users if thread_id else submitted)
    elif event == "ready":
        ids = author_ids | submitted | requested
    elif event in ("lost", "withdrawn", "published", "superseded", "policy_changed"):
        ids = author_ids | submitted
    elif event == "dismissed":
        ids = author_ids | {reviewer_id}
    else:
        ids = author_ids
    watched = set(
        (
            await db.scalars(
                select(ReviewSubscription.user_id).where(
                    ReviewSubscription.review_id == review.id, ReviewSubscription.mode == "watching"
                )
            )
        ).all()
    )
    if event != "opened":
        ids |= watched
    muted = set()
    if event not in ("published", "closed", "superseded"):
        muted = set(
            (
                await db.scalars(
                    select(ReviewSubscription.user_id).where(
                        ReviewSubscription.review_id == review.id, ReviewSubscription.mode == "muted"
                    )
                )
            ).all()
        )
    ctx = {
        "review_number": review.number,
        "review_event": event,
        "revision": revision or 1,
        "comment_id": str(comment_id or uuid.uuid4()),
        "request_id": str(reviewer_id or submission_id) if event in ("requested", "request_changes") else "-",
        "submission_id": str(submission_id) if submission_id else "-",
    }
    if thread_id:
        ctx["thread_id"] = str(thread_id)
    if event == "ready":
        ctx["revision"] = revision or 1
    # Scope may change after an inbox item was sent. Resolve recipients at
    # delivery time as well; don't notify former team reviewers about private work.
    from models.user import User
    from services.teamspace import can_review, review_scope

    allowed = []
    for uid in ids - muted - {None}:
        user = await db.get(User, uid)
        if not user:
            continue
        if uid in author_ids:
            allowed.append(uid)
            continue
        scope = await review_scope(db, user)
        # A release awaiting a global decision is not a team reviewer's to act on.
        if can_review(subject, scope) and (
            scope.is_global_reviewer or not getattr(version, "requires_global_review", False)
        ):
            allowed.append(uid)
    subject_info = Subject(
        type=review.subject_type,
        id=subject.id,
        name=subject.name,
        namespace=subject.namespace,
        slug=subject.slug,
        version=review.version,
        team_id=review.team_id,
        is_private=review.is_private,
    )
    if event == "ready":
        # The author hears the gate is green but cannot publish their own work.
        # A single delivery call would mark their item action-required as well.
        result = await deliver(
            db,
            kind=kind,
            recipients=[uid for uid in allowed if uid in author_ids],
            subject=subject_info,
            actor_id=actor_id,
            context=ctx,
            action_required=False,
        )
        result += await deliver(
            db,
            kind=kind,
            recipients=[uid for uid in allowed if uid not in author_ids],
            subject=subject_info,
            actor_id=actor_id,
            context=ctx,
        )
        return result
    return await deliver(db, kind=kind, recipients=allowed, subject=subject_info, actor_id=actor_id, context=ctx)
