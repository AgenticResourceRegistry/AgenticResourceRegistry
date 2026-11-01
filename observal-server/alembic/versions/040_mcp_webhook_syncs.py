# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Add GitHub and GitLab webhook auto-sync settings for MCP listings.

Revision ID: 040_mcp_webhook_syncs
Revises: 039_agent_pin_digest
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "040_mcp_webhook_syncs"
down_revision = "039_agent_pin_digest"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # docker/entrypoint.sh runs Base.metadata.create_all before Alembic, so on an
    # upgraded install this table can already exist.
    if _has_table("mcp_webhook_syncs"):
        # A table created by an earlier build of this revision can lack later columns.
        columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("mcp_webhook_syncs")}
        if "published_fetch_started_at" not in columns:
            op.add_column(
                "mcp_webhook_syncs",
                sa.Column("published_fetch_started_at", sa.DateTime(timezone=True), nullable=True),
            )
        return
    op.create_table(
        "mcp_webhook_syncs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False, server_default="github"),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("sync_on_push", sa.Boolean(), nullable=False),
        sa.Column("sync_on_release", sa.Boolean(), nullable=False),
        sa.Column("enabled_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event", sa.String(length=20), nullable=True),
        sa.Column("last_sync_status", sa.String(length=20), nullable=True),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_sha", sa.String(length=40), nullable=True),
        sa.Column("last_version", sa.String(length=50), nullable=True),
        sa.Column("published_fetch_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["listing_id"], ["mcp_listings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enabled_by"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("listing_id"),
    )


def downgrade() -> None:
    op.drop_table("mcp_webhook_syncs")
