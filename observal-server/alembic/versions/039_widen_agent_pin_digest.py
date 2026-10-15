# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Allow reviewed skill-folder v2 digests in pinned Agent components.

Revision ID: 039_agent_pin_digest
Revises: 038_share_compat
"""

import sqlalchemy as sa

from alembic import op

revision = "039_agent_pin_digest"
down_revision = "038_share_compat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing sha256 pins are <=80 chars; complete-folder digests include a
    # versioned prefix and exceed that bound. Widening is additive for old APIs.
    op.alter_column(
        "agent_components",
        "resolved_digest",
        existing_type=sa.String(length=80),
        type_=sa.String(length=128),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Narrowing a populated v2 pin would fail or destroy the exact release
    # identity. Refuse explicitly instead of silently dropping a receipt.
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM agent_components WHERE length(resolved_digest) > 80)"))
        .scalar()
    ):
        raise RuntimeError("Cannot narrow Agent pin digests while complete-folder pins exist")
    op.alter_column(
        "agent_components",
        "resolved_digest",
        existing_type=sa.String(length=128),
        type_=sa.String(length=80),
        existing_nullable=True,
    )
