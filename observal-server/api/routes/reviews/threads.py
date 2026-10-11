# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Inline conversations with server-side drafts and line validation."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.routes.reviews.common import get_review, head, notify_update, require_open
from api.routes.reviews.detail import _revision
from models.review import Review, ReviewComment, ReviewSubmission, ReviewThread
from models.user import User, UserRole
from schemas.review import CommentCreate, CommentEdit, ThreadCreate
from services.audit import audit_detail
from services.review import notifications
from services.review.decisions import _event, gate, sync_state

router = APIRouter()


async def _draft(db, review, user):
    draft = await db.scalar(
        select(ReviewSubmission).where(
            ReviewSubmission.review_id == review.id,
            ReviewSubmission.reviewer_id == user.id,
            ReviewSubmission.state == "draft",
        )
    )
    if draft is None:
        draft = ReviewSubmission(
            review_id=review.id, reviewer_id=user.id, revision_id=review.head_revision_id, state="draft"
        )
        db.add(draft)
        await db.flush()
    return draft


async def _comments(db, review, threads, user):
    if not threads:
        return []
    rows = (
        await db.scalars(
            select(ReviewComment)
            .where(ReviewComment.review_id == review.id, ReviewComment.thread_id.in_([t.id for t in threads]))
            .order_by(ReviewComment.created_at, ReviewComment.id)
        )
    ).all()
    drafts = {
        s.id: s
        for s in (
            await db.scalars(
                select(ReviewSubmission).where(
                    ReviewSubmission.review_id == review.id, ReviewSubmission.state == "draft"
                )
            )
        ).all()
    }
    return [c for c in rows if c.submission_id not in drafts or drafts[c.submission_id].reviewer_id == user.id]


def _render_comment(comment):
    return {
        "id": comment.id,
        "thread_id": comment.thread_id,
        "author_id": comment.author_id,
        "body": "[comment deleted]" if comment.deleted_at else comment.body,
        "suggestion": None if comment.deleted_at else comment.suggestion,
        "created_at": comment.created_at,
        "edited_at": comment.edited_at,
        "deleted_at": comment.deleted_at,
        "submission_id": comment.submission_id,
    }


@router.get("/{ref}/threads")
async def threads(
    resolved: bool | None = None,
    path: str | None = None,
    outdated: bool | None = None,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(ReviewThread).where(ReviewThread.review_id == review.id)
    if resolved is not None:
        stmt = stmt.where(ReviewThread.resolved_at.is_not(None) if resolved else ReviewThread.resolved_at.is_(None))
    if path is not None:
        stmt = stmt.where(ReviewThread.path == path)
    if outdated is not None:
        stmt = stmt.where(ReviewThread.outdated == outdated)
    rows = (await db.scalars(stmt.order_by(ReviewThread.created_at, ReviewThread.id))).all()
    comments = await _comments(db, review, rows, user)
    by_thread = {row.id: [] for row in rows}
    for comment in comments:
        by_thread[comment.thread_id].append(_render_comment(comment))
    return [
        {
            "id": row.id,
            "path": row.path,
            "side": row.side,
            "start_line": row.start_line,
            "end_line": row.end_line,
            "outdated": row.outdated,
            "resolved_at": row.resolved_at,
            "revision_id": row.revision_id,
            "comments": by_thread[row.id],
        }
        for row in rows
        if by_thread[row.id]
    ]


async def _thread(db, review, thread_id, user):
    row = await db.get(ReviewThread, thread_id)
    if row is None or row.review_id != review.id:
        raise HTTPException(404, "Thread not found")
    visible = await _comments(db, review, [row], user)
    if not visible:
        raise HTTPException(404, "Thread not found")
    return row


@router.post("/{ref}/threads", status_code=201)
async def create_thread(
    data: ThreadCreate,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_open(review)
    revision = await head(db, review)
    anchor_text = None
    if data.path:
        files = await _revision(db, review, data.side)
        file = files.get(data.path)
        if file is None:
            raise HTTPException(422, "File is not in this revision")
        lines = file["content"].splitlines()
        end = data.end_line or data.start_line
        if end > len(lines):
            raise HTTPException(422, "Line is outside this file")
        anchor_text = "\n".join(lines[data.start_line - 1 : end])
    elif data.suggestion:
        raise HTTPException(422, "Suggestions require a file anchor")
    was_ready = (await gate(db, review)).ready if not data.as_draft else False
    draft = await _draft(db, review, user) if data.as_draft else None
    thread = ReviewThread(
        review_id=review.id,
        path=data.path,
        side=data.side,
        start_line=data.start_line,
        end_line=(data.end_line or data.start_line) if data.path else None,
        revision_id=revision.id if data.path else None,
        anchor_text=anchor_text,
        created_by=user.id,
    )
    db.add(thread)
    await db.flush()
    comment = ReviewComment(
        thread_id=thread.id,
        review_id=review.id,
        author_id=user.id,
        submission_id=draft.id if draft else None,
        body=data.body,
        suggestion=data.suggestion,
    )
    db.add(comment)
    await db.flush()
    if not draft:
        _event(db, review, "comment", user.id, thread_id=str(thread.id))
        await notifications.deliver_event(db, review, "comment", user.id, thread_id=thread.id, comment_id=comment.id)
        await sync_state(db, review, actor_id=user.id)
        await notifications.deliver_gate_change(db, review, was_ready, user.id)
    await db.commit()
    if not draft:
        await notify_update(review, "thread")
    return _render_comment(comment)


@router.post("/{ref}/threads/{thread_id}/comments", status_code=201)
async def reply(
    thread_id: uuid.UUID,
    data: CommentCreate,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_open(review)
    thread = await _thread(db, review, thread_id, user)
    if data.suggestion and thread.path is None:
        raise HTTPException(422, "Suggestions require a file anchor")
    draft = await _draft(db, review, user) if data.as_draft else None
    # A draft-only conversation cannot receive a public reply until it is submitted.
    if not draft:
        public = await db.scalar(
            select(ReviewComment.id)
            .outerjoin(ReviewSubmission, ReviewComment.submission_id == ReviewSubmission.id)
            .where(
                ReviewComment.thread_id == thread_id,
                (ReviewComment.submission_id.is_(None) | (ReviewSubmission.state != "draft")),
            )
            .limit(1)
        )
        if public is None:
            raise HTTPException(409, "Submit your draft before replying publicly")
    comment = ReviewComment(
        thread_id=thread_id,
        review_id=review.id,
        author_id=user.id,
        submission_id=draft.id if draft else None,
        body=data.body,
        suggestion=data.suggestion,
    )
    db.add(comment)
    await db.flush()
    if not draft:
        _event(db, review, "comment", user.id, thread_id=str(thread_id))
        await notifications.deliver_event(db, review, "comment", user.id, thread_id=thread_id, comment_id=comment.id)
    await db.commit()
    if not draft:
        await notify_update(review, "comment")
    return _render_comment(comment)


@router.patch("/{ref}/comments/{comment_id}")
async def edit_comment(
    comment_id: uuid.UUID,
    data: CommentEdit,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    comment = await db.get(ReviewComment, comment_id)
    if not comment or comment.review_id != review.id:
        raise HTTPException(404, "Comment not found")
    if comment.author_id != user.id:
        if comment.submission_id:
            draft = await db.get(ReviewSubmission, comment.submission_id)
            if draft and draft.state == "draft":
                raise HTTPException(404, "Comment not found")
        raise HTTPException(403, "Only the author may edit")
    if comment.deleted_at:
        raise HTTPException(409, "Comment was deleted")
    comment.body, comment.edited_at = data.body, datetime.now(UTC)
    draft = await db.get(ReviewSubmission, comment.submission_id) if comment.submission_id else None
    await db.commit()
    if not draft or draft.state != "draft":
        await notify_update(review, "comment")
    return _render_comment(comment)


@router.delete("/{ref}/comments/{comment_id}")
async def delete_comment(
    comment_id: uuid.UUID,
    request: Request,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    comment = await db.get(ReviewComment, comment_id)
    if not comment or comment.review_id != review.id:
        raise HTTPException(404, "Comment not found")
    draft = await db.get(ReviewSubmission, comment.submission_id) if comment.submission_id else None
    if draft and draft.state == "draft" and draft.reviewer_id != user.id:
        raise HTTPException(404, "Comment not found")
    if comment.author_id != user.id and user.role not in (UserRole.admin, UserRole.super_admin):
        raise HTTPException(403, "Only the author or an admin may delete")
    comment.deleted_at = datetime.now(UTC)
    comment.body, comment.suggestion = "", None
    if not draft or draft.state != "draft":
        _event(db, review, "comment_deleted", user.id, comment_id=str(comment.id))
        audit_detail(
            request,
            action="review.comment_deleted",
            resource_type="review",
            resource_id=review.id,
            resource_name=f"#{review.number}",
        )
    await db.commit()
    if not draft or draft.state != "draft":
        await notify_update(review, "comment")
    return {"deleted": True}


async def _resolve(db, review, thread_id, user, *, resolved):
    require_open(review)
    thread = await _thread(db, review, thread_id, user)
    public_comment = await db.scalar(
        select(ReviewComment.id)
        .outerjoin(ReviewSubmission, ReviewComment.submission_id == ReviewSubmission.id)
        .where(
            ReviewComment.thread_id == thread_id,
            (ReviewComment.submission_id.is_(None) | (ReviewSubmission.state != "draft")),
        )
        .limit(1)
    )
    if public_comment is None:
        raise HTTPException(409, "Submit your draft before resolving this thread")
    was_ready = (await gate(db, review)).ready
    thread.resolved_at = datetime.now(UTC) if resolved else None
    thread.resolved_by = user.id if resolved else None
    _event(db, review, "thread_resolved" if resolved else "thread_unresolved", user.id, thread_id=str(thread_id))
    await notifications.deliver_event(
        db, review, "resolved" if resolved else "unresolved", user.id, thread_id=thread_id
    )
    await sync_state(db, review, actor_id=user.id)
    await notifications.deliver_gate_change(db, review, was_ready, user.id)
    await db.commit()
    await notify_update(review, "thread")
    return {"resolved": resolved}


@router.post("/{ref}/threads/{thread_id}/resolve")
async def resolve(
    thread_id: uuid.UUID,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await _resolve(db, review, thread_id, user, resolved=True)


@router.post("/{ref}/threads/{thread_id}/unresolve")
async def unresolve(
    thread_id: uuid.UUID,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await _resolve(db, review, thread_id, user, resolved=False)
