# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Mark deleted accounts; the row remains as a scrubbed shell.

Revision ID: 032_user_deleted_at
Revises: 031_component_privacy
"""

import sqlalchemy as sa

from alembic import op

revision = "032_user_deleted_at"
down_revision = "031_component_privacy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # Earlier code has no notion of a deleted-account shell: it would accept a
    # shell's outstanding access/refresh tokens, and scrubbing only prevents
    # password and SSO sign-in. Never silently drop the lockout.
    shells = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM users WHERE deleted_at IS NOT NULL OR auth_provider = 'deleted'"))
        .scalar()
    )
    if shells:
        raise RuntimeError(
            f"Refusing to downgrade: {shells} deleted-account shell(s) exist and earlier versions "
            "cannot lock them out. Downgrade is only possible before any user has been deleted."
        )
    op.drop_column("users", "deleted_at")
