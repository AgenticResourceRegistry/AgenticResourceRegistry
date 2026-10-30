# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Opt-in PostgreSQL test against a pre-request-migration-shaped review table.

The full Alembic chain is not reproduced here; this verifies 054's
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

from api.routes.reviews.detail import timeline
from api.routes.reviews.threads import threads
from models import Base
from models.review import ReviewComment, ReviewSubmission, ReviewThread
from models.skill import SkillListing, SkillVersion
from models.user import User, UserRole
from services.review.decisions import gate, open_or_push
from services.review.requests import ensure_request_threads


@pytest.mark.asyncio
async def test_request_migration_backfills_and_recovers_downgraded_threads(monkeypatch):
    url = os.environ.get("OBSERVAL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OBSERVAL_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    schema = f"review_request_{uuid.uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
    path = Path(__file__).resolve().parent.parent / "observal-server/alembic/versions/054_review_request_threads.py"
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
            undated = ReviewSubmission(
                review_id=review.id,
                revision_id=review.head_revision_id,
                reviewer_id=reviewer.id,
                state="submitted",
                verdict="request_changes",
                body="Undated question",
                submitted_at=None,
            )
            db.add_all([historical, live, undated])
            await db.flush()
            draft_at = live.submitted_at - timedelta(minutes=5)
            draft_thread = ReviewThread(review_id=review.id, created_by=author.id, created_at=draft_at)
            undated_draft = ReviewThread(
                review_id=review.id, created_by=reviewer.id, created_at=earlier - timedelta(minutes=20)
            )
            db.add_all([draft_thread, undated_draft])
            await db.flush()
            db.add_all(
                [
                    ReviewComment(
                        review_id=review.id,
                        thread_id=draft_thread.id,
                        submission_id=live.id,
                        author_id=reviewer.id,
                        body="Separate draft note",
                        created_at=draft_at,
                    ),
                    ReviewComment(
                        review_id=review.id,
                        thread_id=undated_draft.id,
                        submission_id=undated.id,
                        author_id=reviewer.id,
                        body=undated.body,
                        created_at=earlier - timedelta(minutes=20) + timedelta(milliseconds=5),
                    ),
                ]
            )
            await db.commit()
            draft_thread_id = draft_thread.id
            undated_draft_id = undated_draft.id

        async def verify_conversations(deleted_opening=False, deleted_thread=False, mismatched=False):
            async with sessions() as db:
                rows = (await db.scalars(select(ReviewThread).where(ReviewThread.review_id == review.id))).all()
                assert len(rows) == 5 + int(mismatched)
                by_request = {row.change_request_id: row for row in rows if row.change_request_id is not None}
                assert set(by_request) == {historical.id, live.id, undated.id}
                unlinked = {draft_thread_id, undated_draft_id}
                if mismatched:
                    unlinked.add(undated_thread_id)
                    assert by_request[undated.id].id != undated_thread_id
                else:
                    assert by_request[undated.id].id == undated_thread_id
                assert {row.id for row in rows if row.change_request_id is None} == unlinked
                if deleted_thread:
                    assert by_request[live.id].id != live_thread_id
                else:
                    assert by_request[live.id].id == live_thread_id
                assert by_request[live.id].resolved_at is None
                assert by_request[historical.id].resolved_at is not None
                assert by_request[historical.id].id == historical_thread_id
                comments = (await db.scalars(select(ReviewComment).where(ReviewComment.review_id == review.id))).all()
                assert len(comments) == 5 - int(deleted_opening) - int(deleted_thread)
                expected = {
                    (live.id, live_thread_id, reviewer.id, live.body),
                    (historical.id, historical_thread_id, reviewer.id, historical.body),
                    (live.id, draft_thread_id, reviewer.id, "Separate draft note"),
                    (undated.id, undated_thread_id, reviewer.id, undated.body),
                    (undated.id, undated_draft_id, reviewer.id, undated.body),
                }
                if deleted_opening:
                    expected.remove((undated.id, undated_thread_id, reviewer.id, undated.body))
                if deleted_thread:
                    expected.remove((live.id, live_thread_id, reviewer.id, live.body))
                assert {(c.submission_id, c.thread_id, c.author_id, c.body) for c in comments} == expected

        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE review_threads DROP COLUMN change_request_id CASCADE"))
            await conn.execute(text("DROP INDEX ix_review_comments_submission_id"))
            await conn.execute(text("DROP INDEX ix_review_submission_latest_decision"))
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
                index_def = sync.execute(
                    text("SELECT pg_get_indexdef('ix_review_submission_latest_decision'::regclass)")
                )
                assert "(review_id, reviewer_id, submitted_at DESC NULLS LAST, id DESC)" in index_def.scalar_one()

            await conn.run_sync(constraints)
        async with sessions() as db:
            rows = (await db.scalars(select(ReviewThread).where(ReviewThread.review_id == review.id))).all()
            by_request = {row.change_request_id: row.id for row in rows if row.change_request_id is not None}
            historical_thread_id = by_request[historical.id]
            live_thread_id = by_request[live.id]
            undated_thread_id = by_request[undated.id]
        await verify_conversations()
        async with engine.begin() as conn:
            await conn.run_sync(migrate, "downgrade")
            assert not await conn.scalar(text("SELECT to_regclass('ix_review_submission_latest_decision')"))
            retained = await conn.scalar(
                text("SELECT count(*) FROM review_comments WHERE review_id = :review_id"), {"review_id": review.id}
            )
            assert retained == 5
            assert await conn.scalar(text("SELECT to_regclass('review_request_thread_links_046')"))
            assert dict(
                (await conn.execute(text("SELECT request_id, thread_id FROM review_request_thread_links_046"))).all()
            ) == {
                historical.id: historical_thread_id,
                live.id: live_thread_id,
                undated.id: undated_thread_id,
            }
            # The old API can edit verdict text while the conversation's
            # opening comment retains its original body.
            await conn.execute(
                text("UPDATE review_submissions SET body = 'Edited live question' WHERE id = :id"), {"id": live.id}
            )
            # The opening is physically deleted while the same-body draft
            # remains. Neither the draft nor a synthesized copy may replace it.
            await conn.execute(text("DELETE FROM review_comments WHERE thread_id = :id"), {"id": undated_thread_id})
            await conn.run_sync(migrate, "upgrade")
            assert not await conn.scalar(text("SELECT to_regclass('review_request_thread_links_046')"))
        await verify_conversations(deleted_opening=True)
        async with engine.begin() as conn:
            await conn.run_sync(migrate, "downgrade")
            # The second rollback removes the *blocking* conversation. Preserve
            # the verdict but change its body so the removed text is unambiguous.
            await conn.execute(text("DELETE FROM review_threads WHERE id = :id"), {"id": live_thread_id})
            await conn.execute(
                text("UPDATE review_submissions SET body = 'Retained verdict text' WHERE id = :id"), {"id": live.id}
            )
            # A mapping pointing at an unrelated draft must not claim it or
            # synthesize the opening comment deleted on the first rollback.
            await conn.execute(
                text("UPDATE review_request_thread_links_046 SET thread_id = :thread WHERE request_id = :request"),
                {"thread": draft_thread_id, "request": undated.id},
            )
            await conn.run_sync(migrate, "upgrade")
            assert not await conn.scalar(text("SELECT to_regclass('review_request_thread_links_046')"))
        await verify_conversations(deleted_opening=True, deleted_thread=True, mismatched=True)
        async with sessions() as db:
            assert (await db.get(ReviewSubmission, live.id)).body == "Retained verdict text"
            assert not await ensure_request_threads(db, review)
            visible = await threads(review=review, db=db, user=author, resolved=None, path=None, outdated=None)
            tombstone = next(
                row for row in visible if row["change_request"] and row["change_request"]["submission_id"] == live.id
            )
            assert tombstone["comments"] == []
            assert tombstone["change_request"]["status"] == "open"
            assert tombstone["id"] != live_thread_id
            assert (await gate(db, review)).outstanding_requests == 1
            assert not (await gate(db, review)).ready
            assert not await db.scalar(text("SELECT 1 FROM review_comments WHERE body = 'Live question'"))
            # Timeline ordering requires dated verdicts; the NULL-submitted_at
            # migration case above has already been exercised.
            await db.execute(
                text("UPDATE review_submissions SET submitted_at = :at WHERE id = :id"),
                {"at": earlier - timedelta(minutes=1), "id": undated.id},
            )
            items = (await timeline(review=review, db=db, user=author, cursor=None, limit=50))["items"]
            assert all(item.get("body") != "Live question" for item in items)
            assert any(item.get("body") == "Retained verdict text" for item in items)
            assert not await ensure_request_threads(db, review)
            assert (
                await db.scalar(text("SELECT count(*) FROM review_threads WHERE review_id = :id"), {"id": review.id})
                == 6
            )
            assert (
                await db.scalar(text("SELECT count(*) FROM review_comments WHERE review_id = :id"), {"id": review.id})
                == 3
            )
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
