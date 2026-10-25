# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""One authorization boundary for every review endpoint, including raw files."""

import uuid

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from models.review import Review, ReviewRevision
from models.user import User
from services.review.decisions import _own_work, _target
from services.teamspace import can_review, review_scope


async def participant(db: AsyncSession, review: Review, user: User, *, scope=None) -> bool:
    if scope is None:
        scope = await review_scope(db, user)
    subject, version = await _target(db, review)
    if can_review(subject, scope):
        return True
    return _own_work(subject, version, user.id)


async def get_review(
    ref: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Review:
    try:
        number = int(ref.removeprefix("#"))
    except ValueError:
        number = None
    if number is not None and not 1 <= number <= 2**31 - 1:
        raise HTTPException(404, "Review not found")  # review.number is a 32-bit sequence
    if number is not None:
        review = await db.scalar(select(Review).where(Review.number == number))
    else:
        try:
            uid = uuid.UUID(ref)
        except ValueError:
            if "/" not in ref:
                raise HTTPException(404, "Review not found") from None
            from models.agent import Agent
            from services.agent_lock import LISTING_MODELS

            identity, _, version = ref.partition("@")
            namespace, slug = identity.split("/", 1)
            if not namespace or not slug:
                raise HTTPException(404, "Review not found") from None
            matches = []
            for subject_type, model in (("agent", Agent), *LISTING_MODELS.items()):
                subject_ids = select(model.id).where(model.namespace == namespace, model.slug == slug)
                statement = select(Review).where(
                    Review.subject_type == subject_type, Review.subject_id.in_(subject_ids)
                )
                if version:
                    statement = statement.where(Review.version == version)
                else:
                    statement = statement.where(Review.state.in_(("open", "changes_requested", "approved")))
                for candidate in (await db.scalars(statement)).all():
                    if await participant(db, candidate, user):
                        matches.append(candidate)
            if len(matches) != 1:
                raise HTTPException(
                    409 if matches else 404, "Ambiguous review" if matches else "Review not found"
                ) from None
            return matches[0]
        review = await db.scalar(select(Review).where(Review.id == uid))
        if review is None:
            review = await db.scalar(
                select(Review)
                .where(Review.subject_id == uid, Review.state.in_(("open", "changes_requested", "approved")))
                .order_by(Review.updated_at.desc(), Review.number.desc())
                .limit(1)
            )
    if review is None or not await participant(db, review, user):
        raise HTTPException(404, "Review not found")
    return review


async def head(db: AsyncSession, review: Review) -> ReviewRevision:
    revision = await db.get(ReviewRevision, review.head_revision_id)
    if revision is None:
        raise HTTPException(409, "Review has no head revision")
    return revision


def require_open(review: Review) -> None:
    if review.state.value in ("published", "closed"):
        raise HTTPException(409, "Review is closed")


async def notify_update(review: Review, kind: str) -> None:
    """Notifications are best-effort after commit; database changes must not roll back."""
    from loguru import logger as optic

    from services.redis import publish

    payload = {"number": review.number, "state": review.state.value, "kind": kind}
    try:
        # Global legacy queue subscribers have no per-review authorization.
        # Never put private review metadata on that channel.
        from database import async_session
        from services.review.decisions import _target

        async with async_session() as db:
            subject, _ = await _target(db, review)
            if not subject.is_private:
                await publish("reviews:updated", {"listing_id": str(review.subject_id), "action": kind})
        await publish(f"review:{review.id}:updated", payload)
    except Exception:
        optic.warning("Review {} live update unavailable", review.number)
