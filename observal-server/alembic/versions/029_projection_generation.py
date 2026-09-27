# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Allocate globally ordered layer and activity projection attempt generations.

Revision ID: 029_projection_generation
Revises: 028_agent_component_pins
"""

from alembic import op

revision = "029_projection_generation"
down_revision = "028_agent_component_pins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS projection_generation_seq "
        "AS bigint MINVALUE 1 MAXVALUE 9223372036854775807 NO CYCLE CACHE 1"
    )


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS projection_generation_seq")
