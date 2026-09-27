# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Scope insight report subjects and invalidate unscoped facet cache entries.

Revision ID: 030_insight_report_subjects
Revises: 029_projection_generation
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "030_insight_report_subjects"
down_revision = "029_projection_generation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("insight_reports", sa.Column("subject_type", sa.String(20), nullable=False, server_default="agent"))
    op.alter_column("insight_reports", "agent_id", existing_type=UUID(as_uuid=True), nullable=True)
    for name, column in (
        ("project_id", sa.String(255)),
        ("component_type", sa.String(20)),
        ("component_id", UUID(as_uuid=True)),
        ("component_version_id", UUID(as_uuid=True)),
        ("component_name", sa.String(255)),
        ("component_version", sa.String(64)),
        ("coverage", sa.JSON()),
    ):
        op.add_column("insight_reports", sa.Column(name, column, nullable=True))
    op.create_check_constraint(
        "ck_insight_report_subject",
        "insight_reports",
        "(subject_type = 'agent' AND agent_id IS NOT NULL AND component_id IS NULL AND component_type IS NULL) OR "
        "(subject_type = 'component' AND agent_id IS NULL AND component_id IS NOT NULL AND "
        "component_type = 'mcp' AND project_id IS NOT NULL)",
    )
    op.create_index(
        "ix_insight_reports_component_subject",
        "insight_reports",
        ["project_id", "component_type", "component_id", "component_version_id"],
    )
    # Registry listing routes currently archive rather than hard-delete. A
    # database trigger handles any eventual ORM, bulk SQL or administrative
    # listing deletion, in the listing's transaction, without a polymorphic FK.
    op.execute("""
        CREATE FUNCTION insight_report_cleanup_on_listing_delete() RETURNS trigger AS $$
        BEGIN
            DELETE FROM insight_reports
             WHERE subject_type = 'component' AND component_type = TG_ARGV[0] AND component_id = OLD.id;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
    """)
    for kind in ("mcp", "skill", "hook"):
        op.execute(
            f"CREATE TRIGGER trg_{kind}_insight_report_cleanup "
            f"AFTER DELETE ON {kind}_listings FOR EACH ROW "
            f"EXECUTE FUNCTION insight_report_cleanup_on_listing_delete('{kind}')"
        )

    # No historical row has a provable (project, user, harness) identity. Never
    # silently assign it to another user's identically named session.
    op.execute("DELETE FROM insight_session_facets")
    op.drop_constraint("uq_session_facets_agent_session", "insight_session_facets", type_="unique")
    op.add_column("insight_session_facets", sa.Column("project_id", sa.String(255), nullable=False))
    op.add_column("insight_session_facets", sa.Column("user_id", sa.String(255), nullable=False))
    op.add_column("insight_session_facets", sa.Column("harness", sa.String(100), nullable=False))
    op.add_column(
        "insight_session_facets", sa.Column("facet_version", sa.Integer(), nullable=False, server_default="1")
    )
    op.alter_column("insight_session_facets", "agent_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.create_unique_constraint(
        "uq_session_facets_scoped", "insight_session_facets", ["project_id", "user_id", "harness", "session_id"]
    )


def downgrade() -> None:
    for kind in ("mcp", "skill", "hook"):
        op.execute(f"DROP TRIGGER trg_{kind}_insight_report_cleanup ON {kind}_listings")
    op.execute("DROP FUNCTION insight_report_cleanup_on_listing_delete()")
    # Component rows have no agent_id; they cannot fit the previous NOT NULL FK.
    op.execute("DELETE FROM insight_session_facets")
    op.drop_constraint("uq_session_facets_scoped", "insight_session_facets", type_="unique")
    op.alter_column("insight_session_facets", "agent_id", existing_type=UUID(as_uuid=True), nullable=False)
    for name in ("facet_version", "harness", "user_id", "project_id"):
        op.drop_column("insight_session_facets", name)
    op.create_unique_constraint("uq_session_facets_agent_session", "insight_session_facets", ["agent_id", "session_id"])
    op.drop_index("ix_insight_reports_component_subject", table_name="insight_reports")
    op.drop_constraint("ck_insight_report_subject", "insight_reports", type_="check")
    op.execute("DELETE FROM insight_reports WHERE subject_type = 'component'")
    for name in (
        "coverage",
        "component_version",
        "component_name",
        "component_version_id",
        "component_id",
        "component_type",
        "project_id",
        "subject_type",
    ):
        op.drop_column("insight_reports", name)
    op.alter_column("insight_reports", "agent_id", existing_type=UUID(as_uuid=True), nullable=False)
