# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Make webhook sync timestamps NOT NULL, as in a freshly created schema.

Revision ID: 053_mcp_webhook_sync_timestamps
Revises: 052_hook_component_reports

"""

import sqlalchemy as sa

from alembic import op

revision = "053_mcp_webhook_sync_timestamps"
down_revision = "052_hook_component_reports"
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
