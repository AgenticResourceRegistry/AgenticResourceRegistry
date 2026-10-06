# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Record whether an Insight report should include ARD discovery suggestions.

Revision ID: 055_discovery_optimization
Revises: 054_review_request_threads
"""

import sqlalchemy as sa

from alembic import op

revision = "055_discovery_optimization"
down_revision = "054_review_request_threads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "insight_reports",
        sa.Column("discovery_optimization_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("insight_reports", "discovery_optimization_requested")
