# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Additive PR review schema; legacy routes and bundles remain until the phase 2 cutover.

Revision ID: 042_pr_reviews
Revises: 041_registry_forks
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "042_pr_reviews"
down_revision = "041_registry_forks"
branch_labels = None
depends_on = None

U = pg.UUID(as_uuid=True)
J = sa.JSON().with_variant(pg.JSONB, "postgresql")
D = sa.DateTime(timezone=True)


# Must match models/review.py exactly: fresh installs build these tables from the
# models (docker/entrypoint.sh), upgrades build them here.
_ON_DELETE = {"reviews.id": "CASCADE", "review_threads.id": "CASCADE"}


def uid(name, fk=None, *, nullable=False, ondelete=None):
    parts = [U, sa.ForeignKey(fk, ondelete=ondelete or _ON_DELETE.get(fk))] if fk else [U]
    return sa.Column(name, *parts, nullable=nullable)


def col(name, type_, *, nullable=False):
    return sa.Column(name, type_, nullable=nullable)


def create(name, *columns, indexes=()):
    if name not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(name, *columns)
    for index_name, fields, options in indexes:
        if index_name not in {idx["name"] for idx in sa.inspect(op.get_bind()).get_indexes(name)}:
            op.create_index(index_name, name, fields, **options)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum in ("agentstatus", "listingstatus"):
            op.execute(f"ALTER TYPE {enum} ADD VALUE IF NOT EXISTS 'changes_requested'")
        for label in ("review_approval", "review_dismissed", "review_ready"):
            op.execute(f"ALTER TYPE inbox_kind ADD VALUE IF NOT EXISTS '{label}'")
        op.execute("CREATE SEQUENCE IF NOT EXISTS review_number_seq")
        state = pg.ENUM(
            "open", "changes_requested", "approved", "published", "closed", name="review_state", create_type=False
        )
        state.create(bind, checkfirst=True)
    else:
        state = sa.Enum("open", "changes_requested", "approved", "published", "closed", name="review_state")

    create(
        "reviews",
        uid("id"),
        col("number", sa.Integer()),
        col("subject_type", sa.String(16)),
        uid("subject_id"),
        uid("version_id"),
        col("version", sa.String(50)),
        uid("base_version_id", nullable=True),
        col("state", state),
        col("closed_reason", sa.String(16), nullable=True),
        col("title", sa.String(255)),
        col("body", sa.Text()),
        uid("head_revision_id", nullable=True),
        uid("opened_by", "users.id"),
        col("opened_at", D),
        col("updated_at", D),
        col("closed_at", D, nullable=True),
        uid("closed_by", "users.id", nullable=True),
        col("published_at", D, nullable=True),
        uid("published_by", "users.id", nullable=True),
        col("publish_override_reason", sa.Text(), nullable=True),
        uid("team_id", "teams.id", nullable=True),
        col("is_private", sa.Boolean()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
        sa.UniqueConstraint("subject_type", "version_id", name="uq_reviews_subject_version"),
        indexes=(
            ("ix_reviews_state_updated", ["state", "updated_at"], {}),
            ("ix_reviews_team_state", ["team_id", "state"], {}),
            ("ix_reviews_opened_by_state", ["opened_by", "state"], {}),
        ),
    )
    create(
        "review_revisions",
        uid("id"),
        uid("review_id", "reviews.id"),
        col("number", sa.Integer()),
        col("files", J),
        col("content_hash", sa.String(64)),
        col("checks", J),
        col("message", sa.Text(), nullable=True),
        col("pruned", sa.Boolean()),
        uid("created_by", "users.id"),
        col("created_at", D),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id", "number", name="uq_review_revision_number"),
    )
    create(
        "review_threads",
        uid("id"),
        uid("review_id", "reviews.id"),
        col("path", sa.String(500), nullable=True),
        col("side", sa.String(4), nullable=True),
        col("start_line", sa.Integer(), nullable=True),
        col("end_line", sa.Integer(), nullable=True),
        uid("revision_id", "review_revisions.id", nullable=True, ondelete="SET NULL"),
        col("anchor_text", sa.Text(), nullable=True),
        col("outdated", sa.Boolean()),
        col("resolved_at", D, nullable=True),
        uid("resolved_by", "users.id", nullable=True),
        uid("created_by", "users.id"),
        col("created_at", D),
        col("updated_at", D),
        sa.PrimaryKeyConstraint("id"),
    )
    create(
        "review_submissions",
        uid("id"),
        uid("review_id", "reviews.id"),
        uid("reviewer_id", "users.id"),
        col("state", sa.String(16)),
        col("verdict", sa.String(16), nullable=True),
        col("body", sa.Text(), nullable=True),
        uid("revision_id", "review_revisions.id"),
        col("self_review", sa.Boolean()),
        col("submitted_at", D, nullable=True),
        uid("dismissed_by", "users.id", nullable=True),
        col("dismissed_at", D, nullable=True),
        col("dismissed_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        indexes=(
            (
                "uq_review_submission_draft",
                ["review_id", "reviewer_id"],
                {
                    "unique": True,
                    "postgresql_where": sa.text("state = 'draft'"),
                    "sqlite_where": sa.text("state = 'draft'"),
                },
            ),
        ),
    )
    create(
        "review_comments",
        uid("id"),
        uid("thread_id", "review_threads.id"),
        uid("review_id", "reviews.id"),
        uid("submission_id", "review_submissions.id", nullable=True, ondelete="SET NULL"),
        uid("author_id", "users.id"),
        col("body", sa.Text()),
        col("suggestion", sa.Text(), nullable=True),
        col("created_at", D),
        col("edited_at", D, nullable=True),
        col("deleted_at", D, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    create(
        "review_reviewer_requests",
        uid("review_id", "reviews.id"),
        uid("user_id", "users.id"),
        uid("requested_by", "users.id"),
        col("created_at", D),
        sa.PrimaryKeyConstraint("review_id", "user_id"),
    )
    create(
        "review_subscriptions",
        uid("review_id", "reviews.id"),
        uid("user_id", "users.id"),
        col("mode", sa.String(16)),
        col("created_at", D),
        sa.PrimaryKeyConstraint("review_id", "user_id"),
    )
    create(
        "review_events",
        uid("id"),
        uid("review_id", "reviews.id"),
        col("kind", sa.String(32)),
        uid("actor_id", "users.id", nullable=True),
        col("created_at", D),
        col("payload", J),
        sa.PrimaryKeyConstraint("id"),
        indexes=(("ix_review_events_review_id", ["review_id"], {}),),
    )


def downgrade():
    for table in (
        "review_events",
        "review_subscriptions",
        "review_reviewer_requests",
        "review_comments",
        "review_submissions",
        "review_threads",
        "review_revisions",
        "reviews",
    ):
        if table in sa.inspect(op.get_bind()).get_table_names():
            op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP SEQUENCE IF EXISTS review_number_seq")
        pg.ENUM(name="review_state").drop(op.get_bind(), checkfirst=True)
    # PG enum values cannot be removed if another row is using them.
