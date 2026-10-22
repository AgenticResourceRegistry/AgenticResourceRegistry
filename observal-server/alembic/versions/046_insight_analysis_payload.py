# SPDX-FileCopyrightText: 2026 The Observal Authors
# SPDX-License-Identifier: Apache-2.0

"""Persist insights analysis previously discarded each run.

Adds ``version_impact`` and ``registry_offer`` (JSON, nullable) to
``insight_reports``. The pipeline already computes both every run — the
cross-user layer/config correlation analysis and the deterministic registry
component shortlist shown to the model — but historically folded them into the
LLM prompt and then discarded them. These columns persist that analysis so
downstream consumers (duplicate detection, pull-time recommendations,
governance drift signals) can read it without re-running the pipeline.

Both columns are nullable: pre-existing reports predate the columns (and never
had the data), and a run may legitimately produce no version impact or an
empty offer. No backfill is needed — null is the correct value for old rows.

Revision ID: 046_insight_analysis_payload
Revises: 045_usage_ping_default_on
Create Date: 2026-10-22
"""

import sqlalchemy as sa

from alembic import op

revision = "046_insight_analysis_payload"
down_revision = "045_usage_ping_default_on"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("insight_reports", sa.Column("version_impact", sa.JSON(), nullable=True))
    op.add_column("insight_reports", sa.Column("registry_offer", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("insight_reports", "registry_offer")
    op.drop_column("insight_reports", "version_impact")
