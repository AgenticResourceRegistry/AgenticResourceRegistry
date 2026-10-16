# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Join Component Insights and registry review migration histories.

Revision ID: 046_component_insights_merge
Revises: 045_usage_ping_default_on, 034_hook_component_reports

Keep both already-published revision chains intact: upgrades from either head
must apply the other chain before the combined schema is considered current.
"""

import sqlalchemy as sa

from alembic import op

revision = "046_component_insights_merge"
down_revision = ("045_usage_ping_default_on", "034_hook_component_reports")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 040 created nullable timestamps on upgraded installations; a fresh
    # schema from model metadata has NOT NULL columns. Bring both paths into
    # agreement without losing existing webhook sync records.
    op.execute(
        sa.text(
            "UPDATE mcp_webhook_syncs SET created_at = COALESCE(created_at, now()), "
            "updated_at = COALESCE(updated_at, created_at, now()) "
            "WHERE created_at IS NULL OR updated_at IS NULL"
        )
    )
    op.alter_column("mcp_webhook_syncs", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("mcp_webhook_syncs", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    op.alter_column("mcp_webhook_syncs", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column("mcp_webhook_syncs", "created_at", existing_type=sa.DateTime(timezone=True), nullable=True)
