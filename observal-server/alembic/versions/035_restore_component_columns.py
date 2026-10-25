# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Repair ORM columns absent from some historical migration-built installs.

Revision ID: 035_component_columns
Revises: 034_migration_op_enum

The v1 model upgraded through 034 lacks several nullable hook/sandbox
columns that current ORM queries always SELECT. Fresh model-created schemas
already contain them. Never drop historical columns or rewrite release data.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "035_component_columns"
down_revision = "034_migration_op_enum"
branch_labels = None
depends_on = None

_COLUMNS = {
    "hook_versions": (
        ("source_url", sa.String(500)),
        ("source_ref", sa.String(255)),
        ("source_path", sa.String(500)),
        ("resolved_sha", sa.String(40)),
        ("script_content", sa.Text()),
        ("script_filename", sa.String(255)),
        ("requirements", postgresql.JSON()),
    ),
    "sandbox_versions": (
        ("sandbox_path", sa.String(500)),
        ("validated_at", sa.DateTime(timezone=True)),
    ),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, columns in _COLUMNS.items():
        existing = {column["name"]: column for column in inspector.get_columns(table)}
        for name, datatype in columns:
            if name in existing:
                current = existing[name]
                if (
                    current["type"].compile(dialect=bind.dialect) != datatype.compile(dialect=bind.dialect)
                    or not current["nullable"]
                ):
                    raise RuntimeError(f"Incompatible existing {table}.{name}; manual inspection required")
                continue
            op.add_column(table, sa.Column(name, datatype, nullable=True))


def downgrade() -> None:
    # The preceding ORM models also read these fields. Dropping them loses
    # manually patched or post-upgrade data without improving compatibility.
    pass
