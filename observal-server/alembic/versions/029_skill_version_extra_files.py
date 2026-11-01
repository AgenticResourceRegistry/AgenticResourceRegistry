# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Store direct skill resources on each immutable skill version.

Revision ID: 029_skill_version_extra_files
Revises: 030_agent_share_manifests
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "029_skill_version_extra_files"
# Reparent after the upstream recommendation and share migrations.
# Previously stamped skill-only installations are repaired at 037/038.
down_revision = "030_agent_share_manifests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A constant server default backfills old rows and also protects old writers
    # during a rolling deploy. Keep the default for legacy inserts.
    op.add_column(
        "skill_versions",
        sa.Column("extra_files", postgresql.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )


def downgrade() -> None:
    # Refuse to destroy stored resources inadvertently; an operator must explicitly
    # remove resource-bearing versions before downgrading.
    bind = op.get_bind()
    # Serialize the check with writers until the transactional DDL commits;
    # otherwise an insert between the check and DROP could lose resources.
    bind.execute(sa.text("LOCK TABLE skill_versions IN ACCESS EXCLUSIVE MODE"))
    if bind.execute(sa.text("SELECT 1 FROM skill_versions WHERE extra_files::jsonb <> '[]'::jsonb LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade: skill_versions.extra_files contains resources")
    op.drop_column("skill_versions", "extra_files")
