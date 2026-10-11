# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""The submit path must persist a review before any version can be decided."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import Base
from models.inbox import InboxItem, InboxKind
from models.mcp import ListingStatus
from models.review import Review, ReviewState
from models.skill import SkillListing, SkillVersion
from models.user import User, UserRole
from services.review.cutover import submit_for_review


@pytest.mark.asyncio
async def test_submit_creates_review_in_same_transaction_and_notifies_reviewer(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        author = User(id=uuid.uuid4(), username="author", email="a@test.io", name="Author", role=UserRole.user)
        reviewer = User(
            id=uuid.uuid4(), username="reviewer", email="r@test.io", name="Reviewer", role=UserRole.reviewer
        )
        listing = SkillListing(name="Skill", namespace="author", slug="skill", owner="author", submitted_by=author.id)
        db.add_all([author, reviewer, listing])
        await db.flush()
        version = SkillVersion(
            listing_id=listing.id,
            version="1.0.0",
            description="Test",
            task_type="other",
            skill_md_content="# Skill\n",
            status=ListingStatus.draft,
            released_by=author.id,
            released_at=datetime.now(UTC),
        )
        db.add(version)
        await db.flush()
        listing.latest_version_id = version.id
        review = await submit_for_review(db, "skill", listing, version, author.id, message="Initial release")
        assert review.number == 1
        assert listing.status == ListingStatus.pending and version.status == ListingStatus.pending
        assert (await db.scalar(select(Review).where(Review.version_id == version.id))) is review
        await db.commit()
    async with factory() as db:
        review = await db.scalar(select(Review))
        notice = await db.scalar(select(InboxItem).where(InboxItem.user_id == reviewer.id))
        assert review.version_id == version.id
        assert review.state.value == "open"
        assert notice.kind == InboxKind.review_requested
        assert notice.payload["review_number"] == review.number
    await engine.dispose()


@pytest.mark.asyncio
async def test_resubmit_does_not_write_through_unloaded_latest_version(monkeypatch):
    """A newly submitted version can exist without a loaded listing relationship."""
    import services.review.cutover as cutover

    class ListingWithoutLoadedVersion:
        @property
        def status(self):
            return ListingStatus.changes_requested

        @status.setter
        def status(self, value):
            raise AssertionError("Must set the submitted version, not the listing")

    review = SimpleNamespace(number=42, state=ReviewState.changes_requested)
    monkeypatch.setattr(cutover, "open_or_push", AsyncMock(return_value=review))
    monkeypatch.setattr(cutover, "_head", AsyncMock(return_value=SimpleNamespace(number=2)))
    monkeypatch.setattr(cutover, "deliver_event", AsyncMock())
    version = SimpleNamespace(status=ListingStatus.changes_requested)

    result = await submit_for_review(None, "skill", ListingWithoutLoadedVersion(), version, uuid.uuid4())

    assert result is review
    assert version.status == ListingStatus.pending
    cutover.deliver_event.assert_awaited_once()
