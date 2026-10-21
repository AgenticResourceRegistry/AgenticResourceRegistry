# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Link change requests to conversations and backfill historical review feedback.

Revision ID: 046_review_request_threads
Revises: 045_usage_ping_default_on
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "046_review_request_threads"
down_revision = "045_usage_ping_default_on"
branch_labels = None
depends_on = None

U = pg.UUID(as_uuid=True)


def backfill() -> None:
    """May be rerun after old API workers stop writing during a rolling rollout."""
    db = op.get_bind()
    requests = (
        db.execute(
            sa.text("""
            WITH ordered AS (
                SELECT s.id, s.review_id, s.reviewer_id, s.body, s.submitted_at,
                       s.state, s.verdict, s.dismissed_at, s.dismissed_by,
                       lead(s.id) OVER (
                           PARTITION BY s.review_id, s.reviewer_id
                           ORDER BY s.submitted_at NULLS FIRST, s.id
                       ) AS later_id
                  FROM review_submissions s
                 WHERE s.verdict IN ('approve', 'request_changes') AND s.state != 'draft'
            )
            SELECT s.id, s.review_id, s.reviewer_id, s.body, s.submitted_at,
                   s.state, s.dismissed_at, s.dismissed_by, s.later_id,
                   r.state AS review_state
              FROM ordered s JOIN reviews r ON r.id = s.review_id
             WHERE s.verdict = 'request_changes'
               AND NOT EXISTS (SELECT 1 FROM review_threads t WHERE t.change_request_id = s.id)
             ORDER BY s.submitted_at, s.id
        """)
        )
        .mappings()
        .all()
    )
    has_links = sa.inspect(db).has_table("review_request_thread_links_046")
    for row in requests:
        cleared = (
            row["state"] == "dismissed" or row["later_id"] is not None or row["review_state"] in ("closed", "published")
        )
        created_at = row["submitted_at"] or datetime.now(UTC)
        link = None
        if has_links:
            # A missing thread is a tombstone, not permission to recreate its
            # deleted content. Never infer identity from comments or timestamps.
            link = db.execute(
                sa.text("SELECT thread_id FROM review_request_thread_links_046 WHERE request_id = :request_id"),
                {"request_id": row["id"]},
            ).scalar_one_or_none()
            if link is not None:
                restored = db.execute(
                    sa.text("""
                        UPDATE review_threads SET change_request_id = :request_id,
                            resolved_at = CASE WHEN :cleared THEN COALESCE(resolved_at, :resolved_at) ELSE NULL END,
                            resolved_by = CASE WHEN :cleared THEN COALESCE(resolved_by, :resolved_by) ELSE NULL END
                         WHERE id = :thread_id AND review_id = :review_id
                           AND created_by = :reviewer_id AND change_request_id IS NULL
                         RETURNING id
                    """),
                    {
                        "request_id": row["id"],
                        "thread_id": link,
                        "review_id": row["review_id"],
                        "reviewer_id": row["reviewer_id"],
                        "cleared": cleared,
                        "resolved_at": row["dismissed_at"] or created_at,
                        "resolved_by": row["dismissed_by"] or row["reviewer_id"],
                    },
                ).scalar_one_or_none()
                if restored is not None:
                    continue
                # Deleted/mismatched mapping: insert a linked, empty tombstone
                # below, never a replacement opening comment.
        thread_id = uuid.uuid4()
        db.execute(
            sa.text("""
                INSERT INTO review_threads
                    (id, review_id, change_request_id, path, side, start_line, end_line,
                     revision_id, anchor_text, outdated, resolved_at, resolved_by, created_by, created_at, updated_at)
                VALUES (:id, :review_id, :request_id, NULL, NULL, NULL, NULL, NULL, NULL,
                        false, :resolved_at, :resolved_by, :reviewer_id, :created_at, :created_at)
            """),
            {
                "id": thread_id,
                "review_id": row["review_id"],
                "request_id": row["id"],
                "resolved_at": row["dismissed_at"] or created_at if cleared else None,
                "resolved_by": row["dismissed_by"] or row["reviewer_id"] if cleared else None,
                "reviewer_id": row["reviewer_id"],
                "created_at": created_at,
            },
        )
        if link is not None:
            continue
        db.execute(
            sa.text("""
                INSERT INTO review_comments
                    (id, thread_id, review_id, submission_id, author_id, body, suggestion, created_at)
                VALUES (:id, :thread_id, :review_id, :request_id, :reviewer_id, :body, NULL, :created_at)
            """),
            {
                "id": uuid.uuid4(),
                "thread_id": thread_id,
                "review_id": row["review_id"],
                "request_id": row["id"],
                "reviewer_id": row["reviewer_id"],
                "body": row["body"] or "",
                "created_at": created_at,
            },
        )


def upgrade() -> None:
    op.add_column("review_threads", sa.Column("change_request_id", U, nullable=True))
    op.create_foreign_key(
        "fk_review_threads_change_request",
        "review_threads",
        "review_submissions",
        ["change_request_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_review_threads_change_request", "review_threads", ["change_request_id"])
    op.create_index("ix_review_comments_submission_id", "review_comments", ["submission_id"])
    op.create_index(
        "ix_review_submission_latest_decision",
        "review_submissions",
        [
            "review_id",
            "reviewer_id",
            sa.literal_column("submitted_at").desc().nulls_last(),
            sa.literal_column("id").desc(),
        ],
    )
    backfill()
    # Only a re-upgrade has links. Remove them atomically with restoring the
    # association; a fresh installation has no migration-owned table.
    if sa.inspect(op.get_bind()).has_table("review_request_thread_links_046"):
        op.drop_table("review_request_thread_links_046")


def downgrade() -> None:
    # Preserve identity even when downgraded workers edit/delete the opening
    # comment or remove a whole thread. Do not cascade thread deletion here:
    # the mapping also serves as a tombstone to prevent resurrecting content.
    op.create_table(
        "review_request_thread_links_046",
        sa.Column("request_id", U, primary_key=True),
        sa.Column("thread_id", U, nullable=False),
    )
    op.execute("""
        INSERT INTO review_request_thread_links_046 (request_id, thread_id)
        SELECT change_request_id, id FROM review_threads WHERE change_request_id IS NOT NULL
    """)
    # Preserve comments/replies as ordinary conversations on rollback.
    op.drop_index("ix_review_submission_latest_decision", table_name="review_submissions")
    op.drop_index("ix_review_comments_submission_id", table_name="review_comments")
    op.drop_constraint("uq_review_threads_change_request", "review_threads", type_="unique")
    op.drop_constraint("fk_review_threads_change_request", "review_threads", type_="foreignkey")
    op.drop_column("review_threads", "change_request_id")
