# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Choose the version an author is editing without changing the approved install pointer."""

from packaging.version import InvalidVersion, Version
from sqlalchemy import select

from models.mcp import ListingStatus
from models.review import Review, ReviewState

_EDITABLE = (ListingStatus.draft, ListingStatus.rejected, ListingStatus.pending, ListingStatus.changes_requested)
_ACTIVE = (ReviewState.open, ReviewState.changes_requested, ReviewState.approved)


async def editable_version(db, kind, listing, version_model):
    """Prefer the newest active reviewed successor to the listing's approved default.

    Creating a version does not move latest_version_id until publication: moving it here
    would also change what unversioned installs receive. Read the review-bound version
    instead. A closed/superseded review must never become an edit target.
    """
    current = listing.latest_version
    if current is None or current.status != ListingStatus.approved:
        return current
    candidates = (
        (
            await db.execute(
                select(version_model)
                .join(Review, Review.version_id == version_model.id)
                .where(
                    Review.subject_type == kind,
                    Review.subject_id == listing.id,
                    Review.state.in_(_ACTIVE),
                    version_model.listing_id == listing.id,
                    version_model.status.in_(_EDITABLE),
                )
            )
        )
        .scalars()
        .all()
    )
    try:
        base = Version(current.version)
        successors = [v for v in candidates if Version(v.version) > base]
    except InvalidVersion:
        # Never guess a successor when historical version strings cannot be ordered.
        return current
    return max(successors, key=lambda v: Version(v.version), default=current)
