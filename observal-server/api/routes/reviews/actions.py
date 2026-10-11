# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Review actions; no submit path is switched until the coordinated cutover."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.routes.reviews.common import get_review, notify_update, require_open
from models.inbox import InboxKind
from models.review import Review, ReviewReviewerRequest, ReviewSubscription
from models.user import User
from schemas.review import PublishRequest, Reason, ReviewerRequest, SubscriptionUpdate
from services.audit import audit_detail
from services.review import notifications
from services.review.decisions import _event, _target, close, publish, withdraw
from services.teamspace import can_review, review_scope

router = APIRouter()


@router.post("/{ref}/reviewers", status_code=201)
async def request_reviewer(
    data: ReviewerRequest,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_open(review)
    target = await db.get(User, data.user_id)
    subject, _ = await _target(db, review)
    if target is None or not can_review(subject, await review_scope(db, target)):
        raise HTTPException(422, "User is not a reviewer in scope")
    if await db.get(ReviewReviewerRequest, (review.id, target.id)):
        return {"user_id": target.id, "requested": True}
    db.add(ReviewReviewerRequest(review_id=review.id, user_id=target.id, requested_by=user.id))
    _event(db, review, "reviewer_requested", user.id, user_id=str(target.id))
    await db.flush()
    await notifications.deliver_event(db, review, "requested", user.id, reviewer_id=target.id)
    await db.commit()
    await notify_update(review, "state")
    return {"user_id": target.id, "requested": True}


@router.delete("/{ref}/reviewers/{user_id}")
async def remove_reviewer(
    user_id: uuid.UUID,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_open(review)
    request = await db.get(ReviewReviewerRequest, (review.id, user_id))
    if request is None:
        raise HTTPException(404, "Reviewer request not found")
    # A requested reviewer can decline; only the requester can retract someone else's request.
    if user.id not in (user_id, request.requested_by):
        raise HTTPException(403, "Only the requester or requested reviewer may remove the request")
    await db.delete(request)
    await notifications.resolve_review_work(
        db,
        review,
        user.id,
        kinds=(InboxKind.review_requested,),
        user_id=user_id,
        request_ids={str(user_id)},
    )
    _event(db, review, "reviewer_removed", user.id, user_id=str(user_id))
    await db.commit()
    await notify_update(review, "state")
    return {"removed": True}


@router.put("/{ref}/subscription")
async def subscription(
    data: SubscriptionUpdate,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # get_review already restricts this endpoint to participants, including authors.
    # Authors may unmute after muting their default activity notifications.
    row = await db.get(ReviewSubscription, (review.id, user.id))
    if row:
        row.mode = data.mode
    else:
        db.add(ReviewSubscription(review_id=review.id, user_id=user.id, mode=data.mode))
    await db.commit()
    return {"mode": data.mode}


@router.post("/{ref}/publish")
async def publish_review(
    data: PublishRequest,
    request: Request,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await publish(db, review, user, category=data.category, override_reason=data.override_reason)
    audit_detail(
        request,
        action="review.publish_override" if data.override_reason else "review.published",
        resource_type="review",
        resource_id=review.id,
        resource_name=f"#{review.number}",
        detail=f"subject={review.subject_type}:{review.subject_id}",
    )
    await notifications.deliver_event(db, review, "published", user.id)
    await db.commit()
    await notify_update(review, "state")
    return {"state": review.state.value, "number": review.number}


@router.post("/{ref}/close")
async def close_review(
    data: Reason,
    request: Request,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await close(db, review, user, data.reason)
    audit_detail(
        request,
        action="review.closed",
        resource_type="review",
        resource_id=review.id,
        resource_name=f"#{review.number}",
    )
    await notifications.deliver_event(db, review, "closed", user.id)
    await db.commit()
    await notify_update(review, "state")
    return {"state": review.state.value}


@router.post("/{ref}/withdraw")
async def withdraw_review(
    request: Request,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await withdraw(db, review, user)
    audit_detail(
        request,
        action="review.withdrawn",
        resource_type="review",
        resource_id=review.id,
        resource_name=f"#{review.number}",
    )
    await notifications.deliver_event(db, review, "withdrawn", user.id)
    await db.commit()
    await notify_update(review, "state")
    return {"state": review.state.value}
