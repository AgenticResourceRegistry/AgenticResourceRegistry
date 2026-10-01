# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Allow component insight reports for hooks.

Revision ID: 034_hook_component_reports
Revises: 033_skill_component_reports
"""

from alembic import op

revision = "034_hook_component_reports"
down_revision = "033_skill_component_reports"
branch_labels = None
depends_on = None

_AGENT = "(subject_type = 'agent' AND agent_id IS NOT NULL AND component_id IS NULL AND component_type IS NULL)"


def _constraint(component_types: str) -> str:
    return (
        f"{_AGENT} OR (subject_type = 'component' AND agent_id IS NULL AND component_id IS NOT NULL AND "
        f"component_type IN ({component_types}) AND project_id IS NOT NULL)"
    )


def upgrade() -> None:
    op.drop_constraint("ck_insight_report_subject", "insight_reports", type_="check")
    op.create_check_constraint("ck_insight_report_subject", "insight_reports", _constraint("'mcp', 'skill', 'hook'"))


def downgrade() -> None:
    # Hook reports cannot be represented under the previous constraint.
    op.execute("DELETE FROM insight_reports WHERE subject_type = 'component' AND component_type = 'hook'")
    op.drop_constraint("ck_insight_report_subject", "insight_reports", type_="check")
    op.create_check_constraint("ck_insight_report_subject", "insight_reports", _constraint("'mcp', 'skill'"))
