# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""Allocate globally ordered layer and activity projection attempt generations.

Revision ID: 028_projection_generation
Revises: 027_discovery_entries
"""

from alembic import op

revision = "028_projection_generation"
down_revision = "027_discovery_entries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS projection_generation_seq "
        "AS bigint MINVALUE 1 MAXVALUE 9223372036854775807 NO CYCLE CACHE 1"
    )


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS projection_generation_seq")
