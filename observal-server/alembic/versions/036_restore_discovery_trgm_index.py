# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Add missing discovery search index on historical versioned databases.

Revision ID: 036_discovery_trgm
Revises: 035_component_columns

The real revision-027 database retained the extension and table, but lacked
its trigram search index. Fresh model-created databases already have it.
"""

import re

import sqlalchemy as sa

from alembic import op

revision = "036_discovery_trgm"
down_revision = "035_component_columns"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_discovery_entries_search_trgm"
_VALID_DEFINITION = re.compile(r"\(search_document (?:[a-zA-Z_][\w]*\.)?gin_trgm_ops\)$")


def upgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            "SELECT am.amname, i.indnkeyatts, i.indnatts, i.indisvalid, i.indisunique, "
            "pg_get_indexdef(idx.oid) AS definition "
            "FROM pg_class idx JOIN pg_namespace ns ON ns.oid=idx.relnamespace "
            "JOIN pg_index i ON i.indexrelid=idx.oid "
            "JOIN pg_class tbl ON tbl.oid=i.indrelid "
            "JOIN pg_am am ON am.oid=idx.relam "
            "WHERE ns.nspname=current_schema() AND tbl.relname='discovery_entries' "
            "AND idx.relname=:name"
        ),
        {"name": _INDEX_NAME},
    ).one_or_none()
    if row:
        if (
            row.amname != "gin"
            or row.indnkeyatts != 1
            or row.indnatts != 1
            or not row.indisvalid
            or row.indisunique
            or not _VALID_DEFINITION.search(row.definition)
        ):
            raise RuntimeError("Existing discovery trigram index is incompatible; inspect manually")
        return
    # The 027 migration creates pg_trgm on normal installations. Explicitly
    # ensure it exists when 027 was stamped over a pre-created table.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        _INDEX_NAME,
        "discovery_entries",
        ["search_document"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"search_document": "gin_trgm_ops"},
    )


def downgrade() -> None:
    # Fresh installs and some historical databases already owned this index
    # before 036. Dropping it would regress search and destroy prior DDL.
    pass
