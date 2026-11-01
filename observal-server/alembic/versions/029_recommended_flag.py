# SPDX-FileCopyrightText: 2026 Chandhini Veerabuthiran <Chandhini03@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Add is_recommended flag to agents and component listings.

Revision ID: 029_recommended_flag
Revises: 028_agent_component_pins
"""

import sqlalchemy as sa

from alembic import op

revision = "029_recommended_flag"
down_revision = "028_agent_component_pins"
branch_labels = None
depends_on = None

TABLES = [
    "agents",
    "mcp_listings",
    "skill_listings",
    "hook_listings",
    "prompt_listings",
    "sandbox_listings",
]


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("is_recommended", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "is_recommended")
