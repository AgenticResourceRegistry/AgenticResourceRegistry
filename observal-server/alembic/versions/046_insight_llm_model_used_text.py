# SPDX-FileCopyrightText: 2026 Dheirav <dheirav2005@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Store every model id a report ran on, however long.

Revision ID: 046_insight_llm_model_used_text
Revises: 045_usage_ping_default_on

llm_model_used joins up to three configured model ids, and the settings do not
bound their length. Bedrock inference-profile ARNs run to about 85 characters,
so three of them overflow VARCHAR(255), and the final commit of a finished
report then fails. Truncating would record ids that do not exist. PostgreSQL
changes VARCHAR to TEXT without rewriting the table.
"""

import sqlalchemy as sa

from alembic import op

revision = "046_insight_llm_model_used_text"
down_revision = "045_usage_ping_default_on"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("insight_reports", "llm_model_used", type_=sa.Text(), existing_type=sa.String(255))


def downgrade() -> None:
    op.alter_column(
        "insight_reports",
        "llm_model_used",
        type_=sa.String(255),
        existing_type=sa.Text(),
        postgresql_using="left(llm_model_used, 255)",
    )
