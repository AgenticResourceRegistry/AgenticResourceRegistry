# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Add the discovery read model.

Revision ID: 027_discovery_entries
Revises: 026_usage_ping_state
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "027_discovery_entries"
down_revision = "026_usage_ping_state"
branch_labels = None
depends_on = None

KIND = ("agent", "mcp", "skill", "hook", "prompt", "sandbox", "external")
SOURCE_KIND = ("local", "imported", "mirrored", "federated", "generated")
LIFECYCLE = ("approved", "pending", "rejected", "archived", "draft")
VISIBILITY = ("public", "team", "owner")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "discovery_entries" in inspector.get_table_names():
        return

    op.create_table(
        "discovery_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ard_identifier", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.Enum(*KIND, name="discoverykind"), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("capabilities", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("capabilities_source", sa.String(length=16), nullable=False, server_default="derived"),
        sa.Column("representative_queries", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("representative_queries_source", sa.String(length=16), nullable=False, server_default="derived"),
        sa.Column("tags", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("artifact_url", sa.String(length=1000), nullable=False),
        sa.Column("artifact_digest", sa.String(length=80), nullable=True),
        sa.Column("updated_at_source", sa.DateTime(timezone=True), nullable=True),
        sa.Column("native_ref", sa.String(length=160), nullable=True),
        sa.Column("local_entity_id", sa.UUID(), nullable=True),
        sa.Column("local_version_id", sa.UUID(), nullable=True),
        sa.Column("supported_harnesses", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("source_kind", sa.Enum(*SOURCE_KIND, name="discoverysourcekind"), nullable=False),
        sa.Column("publisher_domain", sa.String(length=255), nullable=False),
        sa.Column("source_registry_url", sa.String(length=1000), nullable=True),
        sa.Column("source_identifier", sa.String(length=255), nullable=True),
        sa.Column("visibility", sa.Enum(*VISIBILITY, name="discoveryvisibility"), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=True),
        sa.Column("owner_user_id", sa.UUID(), nullable=True),
        sa.Column("co_author_ids", sa.Text(), nullable=False, server_default=""),
        sa.Column("lifecycle_status", sa.Enum(*LIFECYCLE, name="discoverylifecycle"), nullable=False),
        sa.Column("activatable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("search_document", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_entry", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ard_identifier", name="uq_discovery_entries_ard_identifier"),
        sa.UniqueConstraint("kind", "local_entity_id", name="uq_discovery_entries_kind_entity"),
    )
    op.create_index("ix_discovery_entries_kind", "discovery_entries", ["kind"])
    op.create_index("ix_discovery_entries_lifecycle", "discovery_entries", ["lifecycle_status"])
    op.create_index("ix_discovery_entries_visibility", "discovery_entries", ["visibility", "team_id"])
    op.create_index("ix_discovery_entries_owner", "discovery_entries", ["owner_user_id"])
    op.create_index("ix_discovery_entries_tombstoned", "discovery_entries", ["tombstoned_at"])

    if bind.dialect.name == "postgresql":
        # The search prefilter is a LIKE over search_document; a trigram GIN
        # index makes that an index scan. pg_trgm was enabled by 013.
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.create_index(
            "ix_discovery_entries_search_trgm",
            "discovery_entries",
            ["search_document"],
            unique=False,
            postgresql_using="gin",
            postgresql_ops={"search_document": "gin_trgm_ops"},
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_index("ix_discovery_entries_search_trgm", table_name="discovery_entries")
    op.drop_index("ix_discovery_entries_tombstoned", table_name="discovery_entries")
    op.drop_index("ix_discovery_entries_owner", table_name="discovery_entries")
    op.drop_index("ix_discovery_entries_visibility", table_name="discovery_entries")
    op.drop_index("ix_discovery_entries_lifecycle", table_name="discovery_entries")
    op.drop_index("ix_discovery_entries_kind", table_name="discovery_entries")
    op.drop_table("discovery_entries")
    if bind.dialect.name == "postgresql":
        for enum_name in ("discoverykind", "discoverysourcekind", "discoverylifecycle", "discoveryvisibility"):
            op.execute(f"DROP TYPE IF EXISTS {enum_name}")
