# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Pin agent components to an exact component version and content digest.

``agent_components.resolved_version`` has always recorded the version string a
component resolved to when the agent version was saved, but nothing pinned the
row itself. This adds the version row id and a content digest, then backfills
the id wherever the recorded string still names a real version row. Rows that
cannot be matched (legacy ``"latest"`` pins, or versions that no longer exist)
stay NULL and install as unlocked legacy pins with a warning.

Revision ID: 028_agent_component_pins
Revises: 027_discovery_entries
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "028_agent_component_pins"
down_revision = "027_discovery_entries"
branch_labels = None
depends_on = None

_VERSION_TABLES = {
    "mcp": "mcp_versions",
    "skill": "skill_versions",
    "hook": "hook_versions",
    "prompt": "prompt_versions",
    "sandbox": "sandbox_versions",
}


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_components")}
    if "resolved_version_id" not in columns:
        op.add_column(
            "agent_components", sa.Column("resolved_version_id", postgresql.UUID(as_uuid=True), nullable=True)
        )
    if "resolved_digest" not in columns:
        op.add_column("agent_components", sa.Column("resolved_digest", sa.String(length=80), nullable=True))

    # Table names come from the constant map above, never from input.
    for component_type, table in _VERSION_TABLES.items():
        op.execute(
            sa.text(
                f"""
                UPDATE agent_components AS ac
                SET resolved_version_id = v.id
                FROM {table} AS v
                WHERE ac.component_type = :component_type
                  AND ac.resolved_version_id IS NULL
                  AND v.listing_id = ac.component_id
                  AND v.version = ac.resolved_version
                """
            ).bindparams(component_type=component_type)
        )


def downgrade() -> None:
    op.drop_column("agent_components", "resolved_digest")
    op.drop_column("agent_components", "resolved_version_id")
