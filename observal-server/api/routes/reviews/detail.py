# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Participant-only review snapshots, timeline and structured file diffs."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.routes.reviews.common import get_review, head, notify_update, participant
from api.routes.reviews.queue import summary
from models.review import (
    Review,
    ReviewEvent,
    ReviewReviewerRequest,
    ReviewRevision,
    ReviewSubmission,
    ReviewSubscription,
)
from models.user import User
from schemas.review import ReviewEdit
from services.registry_fork import provenance_for
from services.review import check_config, ci
from services.review.decisions import _lock_review, _own_work, _target, gate
from services.review.diff import diff_files
from services.review.policy import policy_for
from services.review.requests import ensure_request_threads, request_lifecycle_status, review_decisions
from services.teamspace import can_review, review_scope

router = APIRouter()


@router.get("/resolve")
async def resolve_identity(ref: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Resolve namespace/slug[@version] without putting a slash in a path parameter."""
    review = await get_review(ref, db, user)
    return {"number": review.number, "id": review.id}


@router.get("/{ref}")
async def detail(
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _lock_review(db, review)
    if await ensure_request_threads(db, review):
        await notify_update(review, "gate")
    revisions = (
        await db.scalars(
            select(ReviewRevision).where(ReviewRevision.review_id == review.id).order_by(ReviewRevision.number)
        )
    ).all()
    subscription = await db.get(ReviewSubscription, (review.id, user.id))
    requested = (
        await db.scalars(select(ReviewReviewerRequest.user_id).where(ReviewReviewerRequest.review_id == review.id))
    ).all()
    submitted_by = (
        await db.scalars(select(ReviewSubmission.reviewer_id).where(ReviewSubmission.review_id == review.id))
    ).all()
    reviewer_ids = set(requested) | set(submitted_by)
    reviewer_names = {
        str(uid): username
        for uid, username in (
            (await db.execute(select(User.id, User.username).where(User.id.in_(reviewer_ids)))).all()
            if reviewer_ids
            else []
        )
    }
    subject, version = await _target(db, review)
    base = await db.get(type(version), review.base_version_id) if review.base_version_id else None
    policy = await policy_for(review, db=db)
    self_approval_allowed = subject.is_private and policy.source == "teamspace" and policy.self_approval == "counted"
    _, latest_decisions, request_statuses = await review_decisions(db, review)
    return {
        **await summary(db, review, user),
        "body": review.body,
        "base_version_id": review.base_version_id,
        "base_version": base.version if base else None,
        "head_revision_id": review.head_revision_id,
        "opened_at": review.opened_at,
        "closed_at": review.closed_at,
        "closed_reason": review.closed_reason,
        "published_at": review.published_at,
        "published_by": review.published_by,
        "revisions": [
            {
                "id": r.id,
                "number": r.number,
                "message": r.message,
                "created_by": r.created_by,
                "created_at": r.created_at,
                "pruned": r.pruned,
            }
            for r in revisions
        ],
        "requested_reviewers": requested,
        "reviewer_names": reviewer_names,
        "reviewer_decisions": {
            str(uid): {
                "verdict": row.verdict,
                "status": request_lifecycle_status(review, row, request_statuses)
                if row.verdict == "request_changes"
                else row.state,
            }
            for uid, row in latest_decisions.items()
        },
        "my_subscription": subscription.mode if subscription else None,
        "self_approval_allowed": self_approval_allowed,
        "forked_from": await provenance_for(subject, user, db) if getattr(subject, "is_fork", True) else None,
    }


@router.patch("/{ref}")
async def edit_detail(
    changes: ReviewEdit,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    subject, version = await _target(db, review)
    if not (_own_work(subject, version, user.id) or can_review(subject, await review_scope(db, user))):
        raise HTTPException(403, "Not allowed to edit review")
    if review.state.value in ("published", "closed"):
        raise HTTPException(409, "Review is closed")
    for key, value in changes.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(review, key, value)
    await db.commit()
    await notify_update(review, "state")
    return {"title": review.title, "body": review.body}


async def _revision(db, review, selector: str):
    if selector == "base":
        from services.review.decisions import _base_files, _target

        _, version = await _target(db, review)
        return await _base_files(db, review.subject_type, version, review.base_version_id)
    if selector == "head":
        return (await head(db, review)).files
    if selector.startswith("r") and selector[1:].isdigit():
        row = await db.scalar(
            select(ReviewRevision).where(
                ReviewRevision.review_id == review.id, ReviewRevision.number == int(selector[1:])
            )
        )
        if row is None:
            raise HTTPException(404, "Revision not found")
        if row.pruned:
            raise HTTPException(410, "Revision files were pruned")
        return row.files
    raise HTTPException(422, "Use base, head or rN")


@router.get("/{ref}/files")
async def files(review: Review = Depends(get_review), db: AsyncSession = Depends(get_db)):
    changed = diff_files(await _revision(db, review, "base"), await _revision(db, review, "head"))
    return [
        {
            key: entry[key]
            for key in ("path", "status", "lang", "pinned", "generated", "additions", "deletions", "too_large")
        }
        for entry in changed
    ]


@router.get("/{ref}/diff")
async def diff(
    from_: str = Query("base", alias="from"),
    to: str = "head",
    path: str | None = None,
    context: int = Query(3, ge=0, le=100),
    full: bool = False,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
):
    before, after = await _revision(db, review, from_), await _revision(db, review, to)
    if path is not None:
        if path not in before and path not in after:
            raise HTTPException(404, "File not found")
        before, after = ({path: before[path]} if path in before else {}), ({path: after[path]} if path in after else {})
    return diff_files(before, after, context=context, full=full)


@router.get("/{ref}/revisions/{number}/files/{path:path}")
async def revision_file(
    number: int, path: str, review: Review = Depends(get_review), db: AsyncSession = Depends(get_db)
):
    files = await _revision(db, review, f"r{number}")
    if path not in files:
        raise HTTPException(404, "File not found")
    return files[path]


@router.get("/{ref}/checks")
async def checks(review: Review = Depends(get_review), db: AsyncSession = Depends(get_db)):
    revision = await head(db, review)
    return [
        {**check, "revision": revision.number} for check in ci.visible(revision.checks, await check_config.enabled())
    ]


@router.get("/{ref}/gate")
async def gate_detail(review: Review = Depends(get_review), db: AsyncSession = Depends(get_db)):
    await _lock_review(db, review)
    if await ensure_request_threads(db, review):
        await notify_update(review, "gate")
    result = await gate(db, review)
    return {
        "ready": result.ready,
        "approvals": result.approvals,
        "required": result.required,
        "policy_source": result.policy_source,
        "requirements": result.requirements,
        "outstanding_requests": result.outstanding_requests,
        "blocking_checks": result.blocking_checks,
        "blockers": result.blockers,
        "hard_blockers": result.hard,
    }


@router.get("/{ref}/timeline")
async def timeline(
    cursor: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # No draft events belong on the public timeline.
    events = (
        await db.scalars(
            select(ReviewEvent)
            .where(ReviewEvent.review_id == review.id)
            .order_by(ReviewEvent.created_at, ReviewEvent.id)
        )
    ).all()
    submissions = (
        await db.scalars(
            select(ReviewSubmission).where(ReviewSubmission.review_id == review.id, ReviewSubmission.state != "draft")
        )
    ).all()
    entries = [
        {"id": e.id, "kind": e.kind, "actor_id": e.actor_id, "created_at": e.created_at, "payload": e.payload}
        for e in events
    ] + [
        {
            "id": s.id,
            "kind": "verdict",
            "actor_id": s.reviewer_id,
            "created_at": s.submitted_at,
            "verdict": s.verdict,
            "body": s.body,
            "state": s.state,
            "revision_id": s.revision_id,
        }
        for s in submissions
    ]
    entries.sort(key=lambda e: (e["created_at"], e["id"].int))
    if cursor:
        positions = [i for i, item in enumerate(entries) if item["id"] == cursor]
        if not positions:
            raise HTTPException(404, "Cursor not found")
        entries = entries[positions[0] + 1 :]
    page = entries[:limit]
    actor_ids = {entry["actor_id"] for entry in page if entry["actor_id"]}
    actors = (
        dict((await db.execute(select(User.id, User.username).where(User.id.in_(actor_ids)))).all())
        if actor_ids
        else {}
    )
    for entry in page:
        entry["actor_name"] = actors.get(entry["actor_id"])
    return {"items": page, "next_cursor": entries[limit - 1]["id"] if len(entries) > limit else None}


@router.get("/{ref}/related")
async def related(
    review: Review = Depends(get_review), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    from models.agent_component import AgentComponent

    if review.subject_type == "agent":
        pins = (
            await db.scalars(select(AgentComponent).where(AgentComponent.agent_version_id == review.version_id))
        ).all()
        ids = [p.resolved_version_id for p in pins if p.resolved_version_id]
        related_reviews = (await db.scalars(select(Review).where(Review.version_id.in_(ids)))).all() if ids else []
        return {
            "dependencies": [
                {"number": r.number, "state": r.state.value} for r in related_reviews if await participant(db, r, user)
            ],
            "dependents": [],
        }
    dependents = (
        await db.scalars(
            select(AgentComponent.agent_version_id).where(
                AgentComponent.component_type == review.subject_type,
                AgentComponent.resolved_version_id == review.version_id,
            )
        )
    ).all()
    related_reviews = (
        (
            await db.scalars(select(Review).where(Review.subject_type == "agent", Review.version_id.in_(dependents)))
        ).all()
        if dependents
        else []
    )
    # Related review titles are private too: exclude reviews outside this caller's scope.
    return {
        "dependencies": [],
        "dependents": [
            {"number": r.number, "state": r.state.value} for r in related_reviews if await participant(db, r, user)
        ],
    }
