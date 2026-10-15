# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Return usage reporting to its default on every upgraded installation.

Revision ID: 045_usage_ping_default_on
Revises: 044_review_orphan_authors

Before 1.16 the settings panel read a missing usage_ping.enabled row as off,
so saving the panel stored "false" without the administrator choosing it, and
delivery stayed dormant until a company name and public URL were set. Drop
the stored enabled and frequency rows so every installation falls back to the
server defaults: reporting on, every six hours. Administrators can still turn
it off afterwards from Admin > Settings > Usage Reporting.
"""

import sqlalchemy as sa

from alembic import op

revision = "045_usage_ping_default_on"
down_revision = "044_review_orphan_authors"
branch_labels = None
depends_on = None

RESET_KEYS = ("usage_ping.enabled", "usage_ping.frequency")


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM enterprise_config WHERE key IN :keys").bindparams(sa.bindparam("keys", expanding=True)),
        {"keys": list(RESET_KEYS)},
    )


def downgrade() -> None:
    # The removed rows only restated or overrode defaults; nothing to restore.
    pass
