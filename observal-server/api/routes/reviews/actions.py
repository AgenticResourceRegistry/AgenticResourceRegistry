# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Review actions; no submit path is switched until the coordinated cutover."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.routes.reviews.common import get_review, notify_update, require_open
from models.inbox import InboxKind
from models.review import Review, ReviewReviewerRequest, ReviewSubscription
from models.team import Team, TeamMembership, TeamRole
from models.user import User, UserRole
from schemas.review import PublishRequest, Reason, ReopenRequest, ReviewerRequest, SubscriptionUpdate
from services.audit import audit_detail
from services.review import check_config, ci, notifications
from services.review.decisions import _event, _lock_review, _target, close, publish, withdraw
from services.teamspace import TEAM_REVIEW_UNLOCKED, can_review, review_scope

router = APIRouter()


@router.get("/{ref}/reviewers/eligible")
async def eligible_reviewers(
    q: str = Query(default="", max_length=100),
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
):
    """Search only people who can review this subject; never expose an org-wide directory for private items."""
    require_open(review)
    subject, _ = await _target(db, review)
    team_id = getattr(subject, "team_id", None)
    audience = User.role.in_((UserRole.admin, UserRole.super_admin))
    if not subject.is_private:
        audience = or_(audience, User.role == UserRole.reviewer)
    if team_id is not None:
        team_conditions = [
            TeamMembership.team_id == team_id,
            TeamMembership.user_id == User.id,
            TeamMembership.role.in_((TeamRole.owner, TeamRole.reviewer)),
            TEAM_REVIEW_UNLOCKED,
        ]
        if not subject.is_private:
            team_conditions.append(Team.is_private.is_(False))
        team_reviewer = exists(
            select(TeamMembership.id).join(Team, Team.id == TeamMembership.team_id).where(*team_conditions)
        )
        audience = or_(audience, team_reviewer)
    stmt = (
        select(User)
        .where(
            audience,
            User.id != review.opened_by,
            ~exists(
                select(ReviewReviewerRequest.user_id).where(
                    ReviewReviewerRequest.review_id == review.id, ReviewReviewerRequest.user_id == User.id
                )
            ),
        )
        .order_by(User.name, User.username, User.id)
        .limit(20)
    )
    if q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        stmt = stmt.where(or_(User.name.ilike(pattern, escape="\\"), User.username.ilike(pattern, escape="\\")))
    people = (await db.scalars(stmt)).all()
    return [
        {"id": person.id, "name": person.name, "username": person.username, "role": person.role.value}
        for person in people
    ]


@router.post("/{ref}/reviewers", status_code=201)
async def request_reviewer(
    data: ReviewerRequest,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_open(review)
    if data.user_id == review.opened_by:
        raise HTTPException(422, "Choose someone other than the review author")
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


@router.post("/{ref}/reopen")
async def reopen_review(
    request: Request,
    data: ReopenRequest | None = None,
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reopen a withdrawn review by resubmitting its version, from the review page or CLI.

    Delegates to the resource's own submit route, so ownership, validation and
    notifications are exactly those of submitting the draft from its listing.
    """
    from fastapi import Response

    from models.review import ReviewState

    if review.state != ReviewState.closed or review.closed_reason != "withdrawn":
        raise HTTPException(409, "Only a withdrawn review can be reopened")
    subject, version = await _target(db, review)
    message = data.message if data else None
    if review.subject_type == "skill" and version.content_revision is not None:
        from api.routes.skill_files import submit_skill_version_draft
        from schemas.skill_resources import SkillVersionRevisionRequest

        await submit_skill_version_draft(
            str(subject.id),
            version.id,
            SkillVersionRevisionRequest(observed_revision=version.content_revision, message=message),
            Response(),
            db,
            user,
        )
    else:
        if review.subject_type == "agent":
            current_id = subject.latest_version_id
        else:
            # A successor of an approved release is not the listing's latest_version (that stays on
            # the installable release until publication); the edit target is what submit acts on.
            from services.component_edit_target import editable_version
            from services.registry_fork import COMPONENT_MODELS

            target = await editable_version(db, review.subject_type, subject, COMPONENT_MODELS[review.subject_type][1])
            current_id = target.id if target is not None else None
        if current_id != version.id:
            raise HTTPException(409, "A newer version of this resource exists; submit that version instead")
        submit = _listing_submit_route(review.subject_type)
        await submit(str(subject.id), message=message, db=db, current_user=user)
    audit_detail(
        request,
        action="review.reopened",
        resource_type="review",
        resource_id=review.id,
        resource_name=f"#{review.number}",
    )
    await db.refresh(review)
    await notify_update(review, "state")
    return {"state": review.state.value, "number": review.number}


def _listing_submit_route(subject_type: str):
    if subject_type == "agent":
        from api.routes.agent.draft import submit_draft

        return submit_draft
    module = __import__(f"api.routes.{subject_type}", fromlist=[f"submit_{subject_type}_draft"])
    return getattr(module, f"submit_{subject_type}_draft")


@router.post("/{ref}/checks/rerun")
async def rerun_checks(
    review: Review = Depends(get_review),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Queue the head revision's automated checks again under the current settings."""
    if not await check_config.enabled():
        raise HTTPException(404, "Not Found")
    require_open(review)
    await _lock_review(db, review)
    queued = await ci.requeue(db, review, user.id)
    await db.commit()
    await notify_update(review, "gate")
    return {"queued": queued}
