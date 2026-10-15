# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Invalidate observed review revisions when a pending skill is withdrawn.

Revision ID: 031_skill_review_epoch
Revises: 030_skill_release_lifecycle
"""

import sqlalchemy as sa

from alembic import op

revision = "031_skill_review_epoch"
down_revision = "030_skill_release_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skill_versions",
        sa.Column("review_epoch", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE skill_versions IN ACCESS EXCLUSIVE MODE"))
    if bind.execute(sa.text("SELECT 1 FROM skill_versions WHERE review_epoch <> 0 LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade: withdrawn skill review revisions remain")
    op.drop_column("skill_versions", "review_epoch")
