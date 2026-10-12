# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Bind persisted skill review observations to a changed listing identity.

Callers hold the listing FOR UPDATE and retain this transaction through commit.
Snapshot before changing any identity field, mutate the listing and review state,
then rebind *all* version observations before making the change visible.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select

from models.skill import SkillVersion
from services.skill_revisions import skill_content_revision
from services.skill_validator import SkillValidationError


@dataclass
class SkillIdentitySnapshot:
    versions: list[SkillVersion]


async def snapshot_skill_identity(db, listing) -> SkillIdentitySnapshot:
    """Refuse corrupt snapshots and stale draft bases before changing identity."""
    versions = (
        (
            await db.execute(
                select(SkillVersion)
                .where(SkillVersion.listing_id == listing.id)
                .order_by(SkillVersion.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    revisions = {}
    for row in versions:
        try:
            revision = skill_content_revision(listing, row)
        except (SkillValidationError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="Stored skill version is not a valid release") from exc
        # Pre-folder legacy releases may lack a stored revision, but a
        # persisted folder/draft/observed review must never acquire its first
        # identity binding as a side effect of an ownership or visibility move.
        if row.content_revision is None and (
            row.extra_files
            or row.base_version_id is not None
            or row.base_revision is not None
            or row.review_epoch
            or (row.delivery_mode == "registry_direct" and row.script_filename is not None and row.script_content == "")
        ):
            raise HTTPException(status_code=409, detail="Skill version has no bound revision; repair before moving it")
        if row.content_revision is not None and row.content_revision != revision:
            raise HTTPException(status_code=409, detail="Skill version changed; refresh before moving it")
        revisions[row.id] = revision
    for row in versions:
        if row.base_version_id is not None and (
            row.base_revision is None or row.base_revision != revisions.get(row.base_version_id)
        ):
            raise HTTPException(status_code=409, detail="Skill draft base changed; rebase before moving it")
        if row.base_version_id is None and row.base_revision is not None:
            raise HTTPException(status_code=409, detail="Skill draft base is missing; repair before moving it")
    return SkillIdentitySnapshot(versions)


def rebind_skill_identity(listing, snapshot: SkillIdentitySnapshot) -> None:
    """Invalidate old observations even if a later move restores the old name."""
    # One tracked release makes this listing an exact-version identity. Bind
    # *every* sibling: a legacy release must not resurrect an old observation
    # if the listing is moved back to its former namespace or visibility.
    for row in snapshot.versions:
        row.review_epoch = (row.review_epoch or 0) + 1
    revisions = {row.id: skill_content_revision(listing, row) for row in snapshot.versions}
    for row in snapshot.versions:
        row.content_revision = revisions[row.id]
        if row.base_version_id is not None:
            row.base_revision = revisions[row.base_version_id]
