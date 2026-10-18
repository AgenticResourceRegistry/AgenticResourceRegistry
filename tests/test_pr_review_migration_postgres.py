# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Opt-in PostgreSQL test against a pre-046-shaped review table.

The full 001-045 Alembic chain is not reproduced here; this verifies 046's
constraints, populated backfill, and reversible association on PostgreSQL.
"""

import importlib.util
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import Base
from models.review import ReviewComment, ReviewSubmission, ReviewThread
from models.skill import SkillListing, SkillVersion
from models.user import User, UserRole
from services.review.decisions import open_or_push


@pytest.mark.asyncio
async def test_request_migration_backfills_and_recovers_downgraded_threads(monkeypatch):
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"review_request_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    path = Path(__file__).resolve().parent.parent / "observal-server/alembic/versions/046_review_request_threads.py"
    spec = importlib.util.spec_from_file_location("request_migration_postgres", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr("services.review.notifications.deliver", AsyncMock(return_value=[]))
    monkeypatch.setattr("services.review.check_config.enabled", AsyncMock(return_value=False))

    def migrate(conn, direction):
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, direction)()

    try:
        async with admin.begin() as conn:
            # An opt-in database must provision the shared extension in public;
            # test schemas must never create or drop a database-wide extension.
            has_trgm = await conn.scalar(
                text("""SELECT EXISTS (
                    SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
                    WHERE e.extname = 'pg_trgm' AND n.nspname = 'public'
                )""")
            )
            if not has_trgm:
                pytest.skip("Install pg_trgm in public on the isolated PostgreSQL test database")
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            # Public may already contain the same tables; force creation in
            # this schema rather than treating those public tables as ours.
            await conn.run_sync(Base.metadata.create_all, checkfirst=False)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            author = User(
                id=uuid.uuid4(), username="author", email="author@test.local", name="Author", role=UserRole.user
            )
            reviewer = User(
                id=uuid.uuid4(),
                username="reviewer",
                email="reviewer@test.local",
                name="Reviewer",
                role=UserRole.reviewer,
            )
            db.add_all([author, reviewer])
            await db.flush()
            listing = SkillListing(
                name="Migration Skill", namespace="test", slug="migration-skill", owner="author", submitted_by=author.id
            )
            db.add(listing)
            await db.flush()
            version = SkillVersion(
                listing_id=listing.id,
                version="1.0.0",
                description="test",
                released_by=author.id,
                released_at=datetime.now(UTC),
                task_type="other",
                skill_md_content="# Skill",
            )
            db.add(version)
            await db.flush()
            review = await open_or_push(db, "skill", listing, version, author.id)
            earlier = datetime.now(UTC) - timedelta(minutes=2)
            historical = ReviewSubmission(
                review_id=review.id,
                revision_id=review.head_revision_id,
                reviewer_id=reviewer.id,
                state="submitted",
                verdict="request_changes",
                body="Old question",
                submitted_at=earlier,
            )
            live = ReviewSubmission(
                review_id=review.id,
                revision_id=review.head_revision_id,
                reviewer_id=reviewer.id,
                state="submitted",
                verdict="request_changes",
                body="Live question",
                submitted_at=earlier + timedelta(minutes=1),
            )
            db.add_all([historical, live])
            await db.flush()
            thread = ReviewThread(
                review_id=review.id,
                change_request_id=live.id,
                created_by=reviewer.id,
                created_at=live.submitted_at + timedelta(milliseconds=5),
            )
            draft_at = live.submitted_at - timedelta(minutes=5)
            draft_thread = ReviewThread(review_id=review.id, created_by=reviewer.id, created_at=draft_at)
            db.add_all([thread, draft_thread])
            await db.flush()
            db.add_all(
                [
                    ReviewComment(
                        review_id=review.id,
                        thread_id=thread.id,
                        submission_id=live.id,
                        author_id=reviewer.id,
                        body=live.body,
                        created_at=live.submitted_at + timedelta(milliseconds=10),
                    ),
                    ReviewComment(
                        review_id=review.id,
                        thread_id=draft_thread.id,
                        submission_id=live.id,
                        author_id=reviewer.id,
                        body="Separate draft note",
                        created_at=draft_at,
                    ),
                ]
            )
            await db.commit()
            live_thread_id = thread.id
            draft_thread_id = draft_thread.id

        async def verify_conversations(historical_thread_id=None):
            async with sessions() as db:
                rows = (await db.scalars(select(ReviewThread).where(ReviewThread.review_id == review.id))).all()
                assert len(rows) == 3
                by_request = {row.change_request_id: row for row in rows}
                assert set(by_request) == {historical.id, live.id, None}
                assert by_request[None].id == draft_thread_id
                assert by_request[live.id].id == live_thread_id
                assert by_request[live.id].resolved_at is None
                assert by_request[historical.id].resolved_at is not None
                if historical_thread_id is not None:
                    assert by_request[historical.id].id == historical_thread_id
                comments = (await db.scalars(select(ReviewComment).where(ReviewComment.review_id == review.id))).all()
                assert len(comments) == 3
                assert {(c.submission_id, c.thread_id, c.author_id, c.body) for c in comments} == {
                    (live.id, live_thread_id, reviewer.id, live.body),
                    (historical.id, by_request[historical.id].id, reviewer.id, historical.body),
                    (live.id, draft_thread_id, reviewer.id, "Separate draft note"),
                }
                return by_request[historical.id].id

        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE review_threads DROP COLUMN change_request_id CASCADE"))
            await conn.execute(text("DROP INDEX ix_review_comments_submission_id"))
            await conn.run_sync(migrate, "upgrade")

            def constraints(sync):
                inspector = inspect(sync)
                fks = inspector.get_foreign_keys("review_threads")
                unique = inspector.get_unique_constraints("review_threads")
                assert any(
                    fk["constrained_columns"] == ["change_request_id"]
                    and fk["referred_table"] == "review_submissions"
                    and fk["options"].get("ondelete") == "SET NULL"
                    for fk in fks
                )
                assert any(row["column_names"] == ["change_request_id"] for row in unique)
                assert any(row["column_names"] == ["submission_id"] for row in inspector.get_indexes("review_comments"))

            await conn.run_sync(constraints)
        historical_thread_id = await verify_conversations()
        async with engine.begin() as conn:
            await conn.run_sync(migrate, "downgrade")
            retained = await conn.scalar(
                text("SELECT count(*) FROM review_comments WHERE review_id = :review_id"), {"review_id": review.id}
            )
            assert retained == 3
            # The old API can edit verdict text while the conversation's
            # opening comment retains its original body.
            await conn.execute(
                text("UPDATE review_submissions SET body = 'Edited live question' WHERE id = :id"), {"id": live.id}
            )
            await conn.run_sync(migrate, "upgrade")
        await verify_conversations(historical_thread_id)
        async with sessions() as db:
            assert (await db.get(ReviewSubmission, live.id)).body == "Edited live question"
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
