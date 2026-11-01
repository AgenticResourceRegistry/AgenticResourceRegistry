# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Add expiring Agent share manifests.

Revision ID: 030_agent_share_manifests
Revises: 029_recommended_flag
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "030_agent_share_manifests"
down_revision = "029_recommended_flag"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_index(table: str, name: str) -> bool:
    return any(index["name"] == name for index in sa.inspect(op.get_bind()).get_indexes(table))


def upgrade() -> None:
    # docker/entrypoint.sh runs Base.metadata.create_all before Alembic, so on an
    # upgraded install these tables and indexes can already exist.
    if not _has_table("agent_share_manifests"):
        op.create_table(
            "agent_share_manifests",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("title", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_hash"),
        )
    if not _has_index("agent_share_manifests", "ix_agent_share_manifests_created_by"):
        op.create_index("ix_agent_share_manifests_created_by", "agent_share_manifests", ["created_by"])
    if not _has_index("agent_share_manifests", "ix_agent_share_manifests_expires_at"):
        op.create_index("ix_agent_share_manifests_expires_at", "agent_share_manifests", ["expires_at"])

    if not _has_table("agent_share_items"):
        op.create_table(
            "agent_share_items",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent_version_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["manifest_id"], ["agent_share_manifests.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("manifest_id", "agent_id", "agent_version_id", name="uq_agent_share_item_version"),
            sa.UniqueConstraint("manifest_id", "position", name="uq_agent_share_item_position"),
        )
    if not _has_index("agent_share_items", "ix_agent_share_items_agent_id"):
        op.create_index("ix_agent_share_items_agent_id", "agent_share_items", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_share_items_agent_id", table_name="agent_share_items")
    op.drop_table("agent_share_items")
    op.drop_index("ix_agent_share_manifests_expires_at", table_name="agent_share_manifests")
    op.drop_index("ix_agent_share_manifests_created_by", table_name="agent_share_manifests")
    op.drop_table("agent_share_manifests")
