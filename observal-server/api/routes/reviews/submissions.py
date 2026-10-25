# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Verdicts, private drafts and dismissal permissions."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.routes.reviews.common import get_review, notify_update
from models.inbox import InboxKind
from models.review import Review, ReviewComment, ReviewSubmission, ReviewThread
from models.user import User
from schemas.review import CommentEdit, Reason, VerdictCreate
from services.audit import audit_detail
from services.review import notifications
from services.review.decisions import dismiss, gate, submit_verdict

router = APIRouter()


async def _my_draft(db, review, user):
    return await db.scalar(
        select(ReviewSubmission).where(
            ReviewSubmission.review_id == review.id,
            ReviewSubmission.reviewer_id == user.id,
            ReviewSubmission.state == "draft",
        )
    )


@router.get("/{ref}/submissions/draft")
async def draft(
    review: Review = Depends(get_review), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    row = await _my_draft(db, review, user)
    if row is None:
        return {"comments": [], "submission_id": None}
    comments = (await db.scalars(select(ReviewComment).where(ReviewComment.submission_id == row.id))).all()
    return {
        "submission_id": row.id,
        "comments": [
            {"id": c.id, "thread_id": c.thread_id, "body": c.body, "suggestion": c.suggestion} for c in comments
        ],
    }


@router.delete("/{ref}/submissions/draft")
async def discard(
    review: Review = Depends(get_review), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    row = await _my_draft(db, review, user)
    if row:
        comments = (await db.scalars(select(ReviewComment).where(ReviewComment.submission_id == row.id))).all()
        thread_ids = [c.thread_id for c in comments]
        await db.execute(delete(ReviewComment).where(ReviewComment.submission_id == row.id))
        await db.delete(row)
        await db.flush()
        # Only delete threads with no remaining published or other draft comments.
        for tid in set(thread_ids):
            if not await db.scalar(select(ReviewComment.id).where(ReviewComment.thread_id == tid).limit(1)):
                await db.execute(delete(ReviewThread).where(ReviewThread.id == tid))
        await db.commit()
    return {"discarded": True}


@router.post("/{ref}/submissions", status_code=201)
async def submit(
    data: VerdictCreate,
    request: Request,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if data.verdict == "request_changes" and not data.body.strip():
        raise HTTPException(422, "Explain what needs changing")
    was_ready = (await gate(db, review)).ready
    row = await submit_verdict(db, review, user, data.verdict, data.body)
    await db.flush()
    await notifications.resolve_review_work(
        db, review, user.id, kinds=(InboxKind.review_requested, InboxKind.review_ready), user_id=user.id
    )
    if data.verdict == "approve":
        await notifications.resolve_change_requests(db, review, user.id, user.id)
    audit_detail(
        request,
        action=f"review.{data.verdict}",
        resource_type="review",
        resource_id=review.id,
        resource_name=f"#{review.number}",
    )
    await notifications.deliver_event(
        db, review, data.verdict if data.verdict != "comment" else "comment", user.id, submission_id=row.id
    )
    await notifications.deliver_gate_change(db, review, was_ready, user.id)
    await db.commit()
    await notify_update(review, "submission")
    return {"id": row.id, "state": review.state.value, "verdict": row.verdict}


@router.patch("/{ref}/submissions/{submission_id}")
async def edit_submission(
    submission_id: uuid.UUID,
    data: CommentEdit,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = await db.get(ReviewSubmission, submission_id)
    if not row or row.review_id != review.id:
        raise HTTPException(404, "Submission not found")
    if row.reviewer_id != user.id:
        raise HTTPException(403, "Not your submission")
    row.body = data.body
    await db.commit()
    await notify_update(review, "submission")
    return {"id": row.id, "body": row.body}


@router.post("/{ref}/submissions/{submission_id}/dismiss")
async def dismiss_submission(
    submission_id: uuid.UUID,
    data: Reason,
    request: Request,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = await db.get(ReviewSubmission, submission_id)
    if not row or row.review_id != review.id:
        raise HTTPException(404, "Submission not found")
    was_ready = (await gate(db, review)).ready
    await dismiss(db, review, row, user, data.reason)
    if row.verdict == "request_changes":
        await notifications.resolve_review_work(
            db, review, user.id, kinds=(InboxKind.change_requested,), request_ids={str(row.id)}
        )
    audit_detail(
        request,
        action="review.dismissed",
        resource_type="review",
        resource_id=review.id,
        resource_name=f"#{review.number}",
    )
    await notifications.deliver_event(
        db, review, "dismissed", user.id, submission_id=row.id, reviewer_id=row.reviewer_id
    )
    await notifications.deliver_gate_change(db, review, was_ready, user.id)
    await db.commit()
    await notify_update(review, "submission")
    return {"state": review.state.value, "dismissed": True}
