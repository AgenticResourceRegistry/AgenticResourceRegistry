# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Remove older component analysis that may contain cross-user prompt excerpts.

Revision ID: 031_component_privacy
Revises: 030_insight_report_subjects
"""

from alembic import op

revision = "031_component_privacy"
down_revision = "030_insight_report_subjects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # V2 evidence included user goals. The remaining deterministic narrative and
    # metrics are safe to retain; old findings cannot be supported without them.
    op.execute("""
        UPDATE insight_reports
           SET narrative = (narrative::jsonb - 'component_analysis')::json
         WHERE subject_type = 'component' AND narrative IS NOT NULL
    """)


def downgrade() -> None:
    # Removed user text must not be restored on rollback.
    pass
