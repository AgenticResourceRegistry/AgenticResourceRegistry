# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Align old migration job enum labels with the current SQLAlchemy model.

Revision ID: 034_migration_op_enum
Revises: 033_skill_private_review

Migration 014 created the label ``import``; the Python Enum member is
``import_``, which SQLAlchemy persists by *member name*. The pre-existing
rows retain their PostgreSQL enum value OID when its label is renamed.
Fresh model-created schemas already use ``import_`` and need no DDL.
"""

import sqlalchemy as sa

from alembic import op

revision = "034_migration_op_enum"
down_revision = "033_skill_private_review"
branch_labels = None
depends_on = None


_LABELS = ("export", "import", "validate")
_NORMALIZED = ("export", "import_", "validate")


def upgrade() -> None:
    bind = op.get_bind()
    # Hold the table lock from observation through rename/transaction commit.
    # Do not guess if an operator manually created an incompatible enum.
    bind.execute(sa.text("LOCK TABLE migration_jobs IN ACCESS EXCLUSIVE MODE"))
    labels = tuple(
        bind.execute(
            sa.text(
                "SELECT e.enumlabel FROM pg_enum e "
                "JOIN pg_type t ON t.oid=e.enumtypid "
                "JOIN pg_namespace n ON n.oid=t.typnamespace "
                "WHERE t.typname='migration_operation' AND n.nspname=current_schema() "
                "ORDER BY e.enumsortorder"
            )
        ).scalars()
    )
    if labels == _LABELS:
        op.execute("ALTER TYPE migration_operation RENAME VALUE 'import' TO 'import_'")
    elif labels != _NORMALIZED:
        raise RuntimeError("Cannot normalize unknown migration_operation enum labels; inspect manually")


def downgrade() -> None:
    # Prior application models also bind MigrationOperation.import_ as the enum
    # member *name*; reverting to the broken old label would strand their rows.
    # Keep the safe label when rolling back only this schema revision.
    pass
