# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Repair historical skill pointers by stable semver, not release timestamp.

Revision ID: 032_skill_stable_pointer_repair
Revises: 031_skill_review_epoch

Migration 030 repaired pointers from a candidate using released_at. A later
backfilled approval, an archived high version, or a prerelease could therefore
become the default in preference to a stable approved release. Prefer the
highest cleared *approved* stable version. Preserve an intentionally archived
pointer: redirecting it to an older approved row would silently unarchive a
listing, while redirecting an approved pointer to an archived row would make
that listing disappear from discovery. Preserve
listings without such a version (including drafts and grandfathered historical
prereleases) rather than guessing a default from malformed text.
"""

from alembic import op

revision = "032_skill_stable_pointer_repair"
down_revision = "031_skill_review_epoch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        WITH stable AS (
            SELECT v.id, v.listing_id, v.status,
                   row_number() OVER (
                       PARTITION BY v.listing_id
                       ORDER BY (v.status = 'approved') DESC,
                                split_part(v.version, '.', 1)::numeric DESC,
                                split_part(v.version, '.', 2)::numeric DESC,
                                split_part(v.version, '.', 3)::numeric DESC,
                                v.released_at DESC, v.id DESC
                   ) AS release_rank
            FROM skill_versions AS v
            WHERE v.status IN ('approved', 'archived')
              AND NOT v.requires_global_review
              AND v.version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)$'
        )
        UPDATE skill_listings AS listing
        SET latest_version_id = stable.id
        FROM stable
        WHERE listing.id = stable.listing_id
          AND stable.release_rank = 1
          AND listing.latest_version_id IS DISTINCT FROM stable.id
          AND NOT EXISTS (
              SELECT 1 FROM skill_versions AS current
              WHERE current.id = listing.latest_version_id
                AND NOT current.requires_global_review
                AND (current.status = 'archived'
                     OR (current.status = 'approved' AND stable.status = 'archived'))
          )
        """
    )


def downgrade() -> None:
    # Data-only, deterministic repair. The former erroneous pointer cannot be
    # reconstructed after upgrading; deliberately retain the safe stable one.
    pass
