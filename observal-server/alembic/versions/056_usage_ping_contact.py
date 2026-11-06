# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Store the opt-in usage-report contact and its prompt state.

Revision ID: 056_usage_ping_contact
Revises: 055_discovery_optimization

A super administrator can choose to share a contact person with the usage
collector. The contact and the state of the "Stay in touch" prompt live on the
single usage_ping_state row rather than in enterprise settings, so the generic
settings API can neither list nor edit them.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "056_usage_ping_contact"
down_revision = "055_discovery_optimization"
branch_labels = None
depends_on = None

COLUMNS = (
    ("contact", pg.JSON(), True, None),
    ("contact_prompt", sa.String(16), False, sa.text("''")),
    ("contact_prompted_at", sa.DateTime(timezone=True), True, None),
)


def upgrade() -> None:
    """Add the contact columns to usage_ping_state when missing."""
    inspector = sa.inspect(op.get_bind())
    if "usage_ping_state" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("usage_ping_state")}
    for name, column_type, nullable, server_default in COLUMNS:
        if name not in existing:
            op.add_column(
                "usage_ping_state",
                sa.Column(name, column_type, nullable=nullable, server_default=server_default),
            )


def downgrade() -> None:
    """Drop the contact columns from usage_ping_state."""
    inspector = sa.inspect(op.get_bind())
    if "usage_ping_state" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("usage_ping_state")}
    for name, *_ in reversed(COLUMNS):
        if name in existing:
            op.drop_column("usage_ping_state", name)
