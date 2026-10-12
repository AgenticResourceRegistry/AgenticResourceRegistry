# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Preserve team review attribution while historical skills await public review.

Revision ID: 033_skill_private_review
Revises: 032_skill_stable_pointer_repair
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "033_skill_private_review"
down_revision = "032_skill_stable_pointer_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("skill_versions", sa.Column("pre_public_reviewed_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("skill_versions", sa.Column("pre_public_reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_skill_versions_pre_public_reviewed_by",
        "skill_versions",
        "users",
        ["pre_public_reviewed_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE skill_versions IN ACCESS EXCLUSIVE MODE"))
    if bind.execute(
        sa.text(
            "SELECT 1 FROM skill_versions WHERE pre_public_reviewed_by IS NOT NULL "
            "OR pre_public_reviewed_at IS NOT NULL LIMIT 1"
        )
    ).first():
        raise RuntimeError("Cannot downgrade: private skill review attribution remains")
    op.drop_constraint("fk_skill_versions_pre_public_reviewed_by", "skill_versions", type_="foreignkey")
    op.drop_column("skill_versions", "pre_public_reviewed_at")
    op.drop_column("skill_versions", "pre_public_reviewed_by")
