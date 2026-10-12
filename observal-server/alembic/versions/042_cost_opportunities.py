# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Add cost_opportunities for the cost intelligence savings pipeline.

Revision ID: 042_cost_opportunities
Revises: 041_registry_forks
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "042_cost_opportunities"
down_revision = "041_registry_forks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_opportunities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fingerprint", sa.String(length=255), nullable=False),
        sa.Column("detector", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("baseline_version", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="identified"),
        sa.Column("estimated_monthly_usd", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("baseline", sa.JSON(), nullable=True),
        sa.Column("still_detected", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("implemented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("implemented_version", sa.String(length=50), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_cost_opportunities_fingerprint"),
        sa.CheckConstraint(
            "status IN ('identified', 'approved', 'implemented', 'dismissed')",
            name="ck_cost_opportunities_status",
        ),
    )
    op.create_index("ix_cost_opportunities_status", "cost_opportunities", ["status"])
    op.create_index("ix_cost_opportunities_agent_id", "cost_opportunities", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_cost_opportunities_agent_id", table_name="cost_opportunities")
    op.drop_index("ix_cost_opportunities_status", table_name="cost_opportunities")
    op.drop_table("cost_opportunities")
