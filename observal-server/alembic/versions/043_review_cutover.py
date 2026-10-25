# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Remove abandoned bundle and legacy submission storage.

Revision ID: 043_review_cutover
Revises: 042_pr_reviews

The PR review backfill runs after this DDL and before API startup. Bundle
membership was never a publish gate; each pending listing gets its own review.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "043_review_cutover"
down_revision = "042_pr_reviews"
branch_labels = None
depends_on = None

_TABLES = ("mcp_listings", "skill_listings", "hook_listings", "prompt_listings", "sandbox_listings")


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for table in _TABLES:
        if "bundle_id" in {column["name"] for column in inspector.get_columns(table)}:
            # Dropping the column also removes its FK; batch mode supports SQLite
            # migration checks and PostgreSQL upgrades with the same DDL.
            with op.batch_alter_table(table) as batch:
                batch.drop_column("bundle_id")
    for table in ("component_bundles", "submissions"):
        if table in sa.inspect(op.get_bind()).get_table_names():
            op.drop_table(table)


def downgrade():
    # The old objects were unused, but their data cannot be restored after a
    # destructive upgrade. Recreate empty structures to permit schema rollback.
    uuid = pg.UUID(as_uuid=True).with_variant(sa.Uuid(), "sqlite")
    op.create_table(
        "component_bundles",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("submitted_by", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    labels = ("draft", "pending", "changes_requested", "approved", "rejected", "archived")
    status = pg.ENUM(*labels, name="listingstatus", create_type=False).with_variant(
        sa.Enum(*labels, name="listingstatus"), "sqlite"
    )
    op.create_table(
        "submissions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("listing_type", sa.String(50), nullable=False),
        sa.Column("listing_id", uuid, nullable=False),
        sa.Column("status", status),
        sa.Column("rejection_reason", sa.Text),
        sa.Column("submitted_by", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewed_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
    )
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("bundle_id", uuid, sa.ForeignKey("component_bundles.id", name=f"fk_{table}_bundle_id"))
            )
