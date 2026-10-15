# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Persist skill draft ancestry, content revisions and public-review provenance.

Revision ID: 030_skill_release_lifecycle
Revises: 029_skill_version_extra_files
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "030_skill_release_lifecycle"
down_revision = "029_skill_version_extra_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skill_versions",
        sa.Column("base_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("skill_versions", sa.Column("base_revision", sa.String(64), nullable=True))
    op.add_column("skill_versions", sa.Column("content_revision", sa.String(64), nullable=True))
    op.add_column(
        "skill_versions",
        sa.Column("requires_global_review", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("skill_versions", sa.Column("pre_public_status", sa.String(20), nullable=True))
    op.create_foreign_key(
        "fk_skill_versions_base_version_id",
        "skill_versions",
        "skill_versions",
        ["base_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Historical rejected/pending pointers can hide an older approved release.
    # Only stable releases can become defaults. Prefer approved over archived
    # so an archived pointer never hides an available approved stable release.
    # Leave a pending pointer unchanged when only pin-only prereleases exist.
    op.execute(
        """
        UPDATE skill_listings AS listing
        SET latest_version_id = (
            SELECT approved.id FROM skill_versions AS approved
            WHERE approved.listing_id = listing.id
              AND approved.status IN ('approved', 'archived')
              AND approved.version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)$'
            ORDER BY (approved.status = 'approved') DESC,
                     approved.released_at DESC, approved.id DESC LIMIT 1
        )
        WHERE listing.latest_version_id IN (
            SELECT stale.id FROM skill_versions AS stale
            WHERE stale.listing_id = listing.id
              AND stale.status IN ('pending', 'draft', 'rejected')
        )
          AND EXISTS (
              SELECT 1 FROM skill_versions AS approved
              WHERE approved.listing_id = listing.id
                AND approved.status IN ('approved', 'archived')
                AND approved.version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)$'
          )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE skill_versions IN ACCESS EXCLUSIVE MODE"))
    if bind.execute(
        sa.text(
            "SELECT 1 FROM skill_versions WHERE requires_global_review "
            "OR pre_public_status IS NOT NULL OR base_version_id IS NOT NULL "
            "OR base_revision IS NOT NULL OR content_revision IS NOT NULL LIMIT 1"
        )
    ).first():
        raise RuntimeError("Cannot downgrade: skill drafts or global public review provenance remain")
    op.drop_constraint("fk_skill_versions_base_version_id", "skill_versions", type_="foreignkey")
    for name in ("pre_public_status", "requires_global_review", "content_revision", "base_revision", "base_version_id"):
        op.drop_column("skill_versions", name)
