# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Repair recommended flags for databases that already ran the skill-only branch.

Revision ID: 037_recommended_compat
Revises: 036_discovery_trgm

Upstream added 029_recommended_flag after skill migration 029 was published on
this branch. The parent change makes fresh upgrades linear, but a database
already stamped at a skill revision never replays its new ancestor. Verify
existing columns and add only missing flags, preserving any true flags.
"""

import sqlalchemy as sa

from alembic import op

revision = "037_recommended_compat"
down_revision = "036_discovery_trgm"
branch_labels = None
depends_on = None

TABLES = (
    "agents",
    "mcp_listings",
    "skill_listings",
    "hook_listings",
    "prompt_listings",
    "sandbox_listings",
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        column = bind.execute(
            sa.text(
                "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = :table AND column_name = 'is_recommended'"
            ),
            {"table": table},
        ).one_or_none()
        if column is None:
            op.add_column(
                table,
                sa.Column("is_recommended", sa.Boolean(), nullable=False, server_default=sa.false()),
            )
        elif (
            column.data_type != "boolean"
            or column.is_nullable != "NO"
            or column.column_default
            not in (
                "false",
                "false::boolean",
            )
        ):
            raise RuntimeError(f"Incompatible {table}.is_recommended; repair manually before upgrading")


def downgrade() -> None:
    # The parent 029_recommended_flag owns these columns. This revision only
    # fills gaps left by the previously published skill-only migration chain.
    pass
