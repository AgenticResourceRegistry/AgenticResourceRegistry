# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Add immutable provenance columns for registry forks.

Revision ID: 041_registry_forks
Revises: 040_mcp_webhook_syncs
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "041_registry_forks"
down_revision = "040_mcp_webhook_syncs"
branch_labels = None
depends_on = None

TABLES = {
    "agents": "agent_versions",
    "mcp_listings": "mcp_versions",
    "skill_listings": "skill_versions",
    "hook_listings": "hook_versions",
    "prompt_listings": "prompt_versions",
    "sandbox_listings": "sandbox_versions",
}


def _columns(table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def _fork_fks(table: str) -> dict[str, str]:
    return {
        fk["constrained_columns"][0]: fk["name"]
        for fk in sa.inspect(op.get_bind()).get_foreign_keys(table)
        if fk["constrained_columns"] and fk["constrained_columns"][0] in {"forked_from_id", "forked_from_version_id"}
    }


def upgrade() -> None:
    # The entrypoint runs create_all before migrations, so both missing columns
    # and columns already created with their constraints must be supported.
    for table, versions in TABLES.items():
        columns = _columns(table)
        for name, column in (
            ("forked_from_id", sa.Column("forked_from_id", postgresql.UUID(as_uuid=True), nullable=True)),
            (
                "forked_from_version_id",
                sa.Column("forked_from_version_id", postgresql.UUID(as_uuid=True), nullable=True),
            ),
            ("forked_from_ref", sa.Column("forked_from_ref", sa.String(200), nullable=True)),
            ("forked_at", sa.Column("forked_at", sa.DateTime(timezone=True), nullable=True)),
        ):
            if name not in columns:
                op.add_column(table, column)
        fks = _fork_fks(table)
        for column, target in (("forked_from_id", table), ("forked_from_version_id", versions)):
            if column not in fks:
                op.create_foreign_key(f"fk_{table}_{column}", table, target, [column], ["id"], ondelete="SET NULL")
        index = f"ix_{table}_forked_from_id"
        if index not in _indexes(table):
            op.create_index(index, table, ["forked_from_id"])


def downgrade() -> None:
    for table in reversed(list(TABLES)):
        index = f"ix_{table}_forked_from_id"
        if index in _indexes(table):
            op.drop_index(index, table_name=table)
        for name in _fork_fks(table).values():
            op.drop_constraint(name, table, type_="foreignkey")
        columns = _columns(table)
        for name in ("forked_at", "forked_from_ref", "forked_from_version_id", "forked_from_id"):
            if name in columns:
                op.drop_column(table, name)
