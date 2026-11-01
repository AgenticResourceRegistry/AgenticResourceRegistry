# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Permit unattributed historical reviews when deleted submitters cannot be recovered.

Revision ID: 044_review_orphan_authors
Revises: 043_review_cutover
"""

import sqlalchemy as sa

from alembic import op

revision = "044_review_orphan_authors"
down_revision = "043_review_cutover"
branch_labels = None
depends_on = None


def upgrade():
    # Existing rows all have authors; only legacy backfill needs nullable fields.
    for table, column in (("reviews", "opened_by"), ("review_revisions", "created_by")):
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=sa.Uuid(), nullable=True)


def downgrade():
    # Rolling back to a schema that forbids orphans cannot be lossless. Fail
    # explicitly instead of inventing an author or deleting review history.
    bind = op.get_bind()
    for table, column in (("reviews", "opened_by"), ("review_revisions", "created_by")):
        count = bind.scalar(sa.text(f"SELECT count(*) FROM {table} WHERE {column} IS NULL"))
        if count:
            raise RuntimeError(f"Cannot downgrade: {count} {table} rows lack {column}")
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=sa.Uuid(), nullable=False)
