# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Idempotent pending-version backfill, run after migrations before API traffic.

Batches commit atomically; retrying skips versions with an existing review.
"""

from loguru import logger as optic
from sqlalchemy import select

from database import async_session
from models.agent import Agent, AgentStatus, AgentVersion
from models.mcp import ListingStatus
from models.user import User
from services.agent_lock import LISTING_MODELS, VERSION_MODELS
from services.review.decisions import open_or_push


async def backfill_pending(*, batch_size: int = 100) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    created = 0
    targets = {
        "agent": (Agent, AgentVersion),
        **{kind: (LISTING_MODELS[kind], model) for kind, model in VERSION_MODELS.items()},
    }
    for kind, (listing_model, version_model) in targets.items():
        last_id = None
        while True:
            async with async_session() as db:
                query = (
                    select(version_model)
                    .where(version_model.status == (AgentStatus.pending if kind == "agent" else ListingStatus.pending))
                    .order_by(version_model.id)
                    .limit(batch_size)
                )
                if last_id:
                    query = query.where(version_model.id > last_id)
                versions = (await db.execute(query)).scalars().all()
                if not versions:
                    break
                for version in versions:
                    subject_id = version.agent_id if kind == "agent" else version.listing_id
                    subject = await db.get(listing_model, subject_id)
                    if not subject:
                        continue
                    from models.review import Review

                    existing = await db.scalar(
                        select(Review.id).where(Review.subject_type == kind, Review.version_id == version.id)
                    )
                    if existing:
                        continue
                    actor = version.released_by
                    message = None
                    if actor is None or await db.get(User, actor) is None:
                        original = actor
                        owner = getattr(subject, "created_by", None) or getattr(subject, "submitted_by", None)
                        actor = owner if owner and await db.get(User, owner) is not None else None
                        message = f"Historical review: original submitter {original} no longer exists."
                        optic.warning(
                            "Review backfill: missing submitter for {} version {}; attributed to {}",
                            kind,
                            version.id,
                            actor,
                        )
                    await open_or_push(db, kind, subject, version, actor, backfill=True, message=message)
                    created += 1
                last_id = versions[-1].id
                await db.commit()
    return created


if __name__ == "__main__":
    import asyncio

    print(f"Backfilled {asyncio.run(backfill_pending())} pending review(s)")
