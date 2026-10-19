# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger as optic
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import (
    apply_visibility_filter,
    check_listing_visibility_async,
    get_db,
    get_registry_user,
    hidden_unapproved_listing,
    require_role,
)
from models.agent import Agent
from models.feedback import Feedback
from models.hook import HookListing
from models.mcp import McpListing
from models.prompt import PromptListing
from models.sandbox import SandboxListing
from models.skill import SkillListing
from models.user import User, UserRole
from schemas.feedback import FeedbackCreateRequest, FeedbackResponse, FeedbackSummary, FeedbackUpdateRequest

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])

LISTING_MODELS = {
    "mcp": McpListing,
    "agent": Agent,
    "skill": SkillListing,
    "hook": HookListing,
    "prompt": PromptListing,
    "sandbox": SandboxListing,
}


async def _visible_listing(db: AsyncSession, listing_type: str, listing_id: uuid.UUID, current_user: User | None):
    model = LISTING_MODELS.get(listing_type)
    if not model:
        raise HTTPException(status_code=400, detail=f"Unknown listing type: {listing_type}")
    listing = await db.scalar(select(model).where(model.id == listing_id))
    if not listing or not await check_listing_visibility_async(listing, current_user, db):
        raise HTTPException(status_code=404, detail="Listing not found")
    # Agents gate non-approved listings in their own routes; components match GET /{type}/{id}.
    if model is not Agent and hidden_unapproved_listing(listing, current_user):
        raise HTTPException(status_code=404, detail="Listing not found")
    return listing


async def _visible_listing_by_id(db: AsyncSession, listing_id: uuid.UUID, current_user: User | None):
    """Resolve a listing when only its id is known, without probing every table.

    This backs the anonymous feedback summary, which the registry detail page hits
    on every render. Scanning all six listing models cost up to six round trips
    before the aggregate even ran. Feedback rows already record which type the
    listing is, so one lookup narrows it to a single model.
    """
    listing_type = await db.scalar(select(Feedback.listing_type).where(Feedback.listing_id == listing_id).limit(1))
    if listing_type is not None and listing_type in LISTING_MODELS:
        return await _visible_listing(db, listing_type, listing_id, current_user)

    # No feedback yet, so the type is unknown. An empty summary is the same answer
    # a public listing with no ratings gives, so returning it discloses nothing
    # about whether the id exists or is hidden.
    return None


def _serialize_feedback(fb: Feedback) -> FeedbackResponse:
    """Serialize feedback, redacting user_id when anonymous."""
    return FeedbackResponse(
        id=fb.id,
        listing_id=fb.listing_id,
        listing_type=fb.listing_type,
        user_id=None if fb.anonymous else fb.user_id,
        rating=fb.rating,
        comment=fb.comment,
        anonymous=fb.anonymous,
        created_at=fb.created_at,
        updated_at=fb.updated_at,
    )


@router.post("", response_model=FeedbackResponse, status_code=201)
async def create_feedback(
    req: FeedbackCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Submit a review. One review per user per listing (returns 409 if already reviewed)."""
    optic.debug("feedback create: user={}, listing={}", current_user.id, req.listing_id)

    # Validate listing exists and is visible to the caller.
    listing = await _visible_listing(db, req.listing_type, req.listing_id, current_user)
    if current_user.id in (getattr(listing, "submitted_by", None), getattr(listing, "created_by", None)):
        raise HTTPException(status_code=403, detail="You cannot review your own listing")

    # Enforce one review per user per listing
    existing = await db.scalar(
        select(Feedback.id).where(
            Feedback.user_id == current_user.id,
            Feedback.listing_id == req.listing_id,
            Feedback.listing_type == req.listing_type,
        )
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="You have already reviewed this item. Use PUT to update your review.",
        )

    fb = Feedback(
        listing_id=req.listing_id,
        listing_type=req.listing_type,
        user_id=current_user.id,
        rating=req.rating,
        comment=req.comment,
        anonymous=req.anonymous,
    )
    db.add(fb)
    await db.commit()
    await db.refresh(fb)

    return _serialize_feedback(fb)


@router.get("/mine/{listing_type}/{listing_id}", response_model=FeedbackResponse)
async def get_my_review(
    listing_type: str,
    listing_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Get the current user's review for a specific listing (if it exists)."""
    await _visible_listing(db, listing_type, listing_id, current_user)
    result = await db.execute(
        select(Feedback).where(
            Feedback.user_id == current_user.id,
            Feedback.listing_id == listing_id,
            Feedback.listing_type == listing_type,
        )
    )
    fb = result.scalar_one_or_none()
    if not fb:
        raise HTTPException(status_code=404, detail="You have not reviewed this item")
    return _serialize_feedback(fb)


@router.put("/{feedback_id}", response_model=FeedbackResponse)
async def update_feedback(
    feedback_id: uuid.UUID,
    req: FeedbackUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Update the current user's review. Only the review owner can update."""
    result = await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    fb = result.scalar_one_or_none()
    if not fb:
        raise HTTPException(status_code=404, detail="Review not found")
    if fb.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only update your own review")
    await _visible_listing(db, fb.listing_type, fb.listing_id, current_user)

    if req.rating is not None:
        fb.rating = req.rating
    if req.comment is not None:
        fb.comment = req.comment
    if req.anonymous is not None:
        fb.anonymous = req.anonymous
    fb.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(fb)
    optic.debug("feedback updated: id={}", feedback_id)
    return _serialize_feedback(fb)


@router.delete("/{feedback_id}", status_code=204)
async def delete_feedback(
    feedback_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Delete the current user's review. Only the review owner can delete."""
    result = await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    fb = result.scalar_one_or_none()
    if not fb:
        raise HTTPException(status_code=404, detail="Review not found")
    if fb.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own review")
    await _visible_listing(db, fb.listing_type, fb.listing_id, current_user)

    await db.delete(fb)
    await db.commit()
    optic.debug("feedback deleted: id={}", feedback_id)


@router.get("/me", response_model=list[FeedbackResponse])
async def my_feedback_received(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Feedback received on listings submitted/created by the current user."""
    optic.debug("my_feedback_received called")
    all_ids = []
    for model, owner_column in (
        (McpListing, McpListing.submitted_by),
        (Agent, Agent.created_by),
        (SkillListing, SkillListing.submitted_by),
        (HookListing, HookListing.submitted_by),
        (PromptListing, PromptListing.submitted_by),
        (SandboxListing, SandboxListing.submitted_by),
    ):
        stmt = apply_visibility_filter(select(model.id).where(owner_column == current_user.id), model, current_user)
        all_ids.extend((await db.execute(stmt)).scalars().all())
    if not all_ids:
        return []

    result = await db.execute(
        select(Feedback).where(Feedback.listing_id.in_(all_ids)).order_by(Feedback.created_at.desc())
    )
    feedbacks = result.scalars().all()
    return [_serialize_feedback(f) for f in feedbacks]


@router.get("/summary/{listing_id}", response_model=FeedbackSummary)
async def feedback_summary(
    listing_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    optic.trace("listing_id={}", listing_id)
    await _visible_listing_by_id(db, listing_id, current_user)
    result = await db.execute(
        select(
            func.avg(Feedback.rating).label("avg_rating"),
            func.count(Feedback.id).label("total"),
        ).where(Feedback.listing_id == listing_id)
    )
    row = result.one()
    return FeedbackSummary(
        listing_id=listing_id,
        average_rating=round(float(row.avg_rating or 0), 2),
        total_reviews=row.total,
    )


@router.get("/{listing_type}/{listing_id}", response_model=list[FeedbackResponse])
async def get_feedback(
    listing_type: str,
    listing_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_registry_user),
):
    """Get all reviews for a listing. Anonymous reviews have user_id redacted."""
    optic.trace("listing_type={}, listing_id={}", listing_type, listing_id)
    await _visible_listing(db, listing_type, listing_id, current_user)
    result = await db.execute(
        select(Feedback)
        .where(Feedback.listing_id == listing_id, Feedback.listing_type == listing_type)
        .order_by(Feedback.created_at.desc())
    )
    return [_serialize_feedback(f) for f in result.scalars().all()]
