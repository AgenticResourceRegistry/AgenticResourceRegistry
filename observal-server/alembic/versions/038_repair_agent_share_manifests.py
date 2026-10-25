# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Repair share tables for installations stamped by the former skill-only branch.

Revision ID: 038_share_compat
Revises: 037_recommended_compat

Fresh installations run upstream 030_agent_share_manifests before the skill
revisions. Older installations already stamped past that new ancestor never
replay it; install both missing share tables without modifying any share data.
Refuse a partial schema rather than silently completing an ambiguous state,
including when both table names exist but their upstream definitions do not.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "038_share_compat"
down_revision = "037_recommended_compat"
branch_labels = None
depends_on = None


# The existing-table path must establish the same contract as upstream 030,
# not merely the presence of two table names (which would let Alembic stamp head).
_COLUMNS = {
    "agent_share_manifests": {
        "id": (postgresql.UUID, None, False),
        "token_hash": (sa.String, 64, False),
        "created_by": (postgresql.UUID, None, False),
        "title": (sa.String, 120, True),
        "created_at": (sa.DateTime, None, False),
        "expires_at": (sa.DateTime, None, False),
        "revoked_at": (sa.DateTime, None, True),
    },
    "agent_share_items": {
        "id": (postgresql.UUID, None, False),
        "manifest_id": (postgresql.UUID, None, False),
        "agent_id": (postgresql.UUID, None, False),
        "agent_version_id": (postgresql.UUID, None, False),
        "position": (sa.Integer, None, False),
    },
}
_UNIQUES = {
    "agent_share_manifests": {("token_hash",): None},
    "agent_share_items": {
        ("manifest_id", "agent_id", "agent_version_id"): "uq_agent_share_item_version",
        ("manifest_id", "position"): "uq_agent_share_item_position",
    },
}
_FOREIGN_KEYS = {
    "agent_share_manifests": {("created_by",): ("users", ("id",))},
    "agent_share_items": {
        ("agent_id",): ("agents", ("id",)),
        ("agent_version_id",): ("agent_versions", ("id",)),
        ("manifest_id",): ("agent_share_manifests", ("id",)),
    },
}
_INDEXES = {
    "agent_share_manifests": {
        "ix_agent_share_manifests_created_by": ("created_by",),
        "ix_agent_share_manifests_expires_at": ("expires_at",),
    },
    "agent_share_items": {"ix_agent_share_items_agent_id": ("agent_id",)},
}


def _validate_existing(inspector: sa.Inspector) -> None:
    for table, expected_columns in _COLUMNS.items():
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        for name, (kind, length, nullable) in expected_columns.items():
            column = columns.get(name)
            if column is None:
                raise RuntimeError(f"Partial agent share schema: missing {table}.{name}; repair manually")
            actual = column["type"]
            if (
                not isinstance(actual, kind)
                or (length is not None and actual.length != length)
                or (kind is sa.DateTime and not actual.timezone)
                or column["nullable"] != nullable
            ):
                raise RuntimeError(f"Partial agent share schema: incompatible {table}.{name}; repair manually")

        pk = inspector.get_pk_constraint(table)
        if set(pk["constrained_columns"]) != {"id"}:
            raise RuntimeError(f"Partial agent share schema: incompatible {table} primary key; repair manually")

        uniques = {tuple(item["column_names"]): item["name"] for item in inspector.get_unique_constraints(table)}
        for columns_key, constraint_name in _UNIQUES[table].items():
            if columns_key not in uniques or (constraint_name is not None and uniques[columns_key] != constraint_name):
                raise RuntimeError(f"Partial agent share schema: missing {table} unique {columns_key}; repair manually")

        foreign_keys = {tuple(item["constrained_columns"]): item for item in inspector.get_foreign_keys(table)}
        for columns_key, (referent, referred_columns) in _FOREIGN_KEYS[table].items():
            fk = foreign_keys.get(columns_key)
            if (
                fk is None
                or fk["referred_table"] != referent
                or tuple(fk["referred_columns"]) != referred_columns
                or fk.get("options", {}).get("ondelete", "").upper() != "CASCADE"
            ):
                raise RuntimeError(
                    f"Partial agent share schema: missing {table} foreign key {columns_key}; repair manually"
                )

        indexes = {item["name"]: item for item in inspector.get_indexes(table)}
        for name, index_columns in _INDEXES[table].items():
            index = indexes.get(name)
            if (
                index is None
                or tuple(index["column_names"]) != index_columns
                or index["unique"]
                or index.get("dialect_options", {}).get("postgresql_where") is not None
                or index.get("dialect_options", {}).get("postgresql_using", "btree") != "btree"
            ):
                raise RuntimeError(f"Partial agent share schema: missing {table} index {name}; repair manually")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    manifests = inspector.has_table("agent_share_manifests")
    items = inspector.has_table("agent_share_items")
    if manifests != items:
        raise RuntimeError("Partial agent share schema; repair manually before upgrading")
    if manifests:
        _validate_existing(inspector)
        return  # Verified upstream tables and their data are left unchanged.

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
    op.create_index("ix_agent_share_manifests_created_by", "agent_share_manifests", ["created_by"])
    op.create_index("ix_agent_share_manifests_expires_at", "agent_share_manifests", ["expires_at"])

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
    op.create_index("ix_agent_share_items_agent_id", "agent_share_items", ["agent_id"])


def downgrade() -> None:
    # Upstream 030 owns the tables on a fresh schema; leave branch-stamped
    # tables intact on rollback. Downgrading past 030 will remove them there.
    pass
