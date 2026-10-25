# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Choose the version an author is editing without changing the approved install pointer."""

from packaging.version import InvalidVersion, Version
from sqlalchemy import and_, or_, select

from models.mcp import ListingStatus
from models.review import Review, ReviewState

_EDITABLE = (ListingStatus.draft, ListingStatus.rejected, ListingStatus.pending, ListingStatus.changes_requested)
_ACTIVE = (ReviewState.open, ReviewState.changes_requested, ReviewState.approved)


async def editable_version(db, kind, listing, version_model):
    """Prefer the newest active reviewed successor to the listing's approved default.

    Creating a version does not move latest_version_id until publication: moving it here
    would also change what unversioned installs receive. Read the review-bound version
    instead. A review closed as rejected or superseded never becomes an edit target; a
    withdrawn one does, because withdrawing returns the version to the author as a draft.
    """
    return (await editable_versions(db, kind, [listing], version_model)).get(listing.id, listing.latest_version)


async def editable_versions(db, kind, listings, version_model) -> dict:
    """Batch form of ``editable_version``: one query for every listing with an approved default."""
    result = {listing.id: listing.latest_version for listing in listings}
    approved = {
        listing.id: listing.latest_version
        for listing in listings
        if listing.latest_version is not None and listing.latest_version.status == ListingStatus.approved
    }
    if not approved:
        return result
    candidates = (
        (
            await db.execute(
                select(version_model)
                .join(Review, Review.version_id == version_model.id)
                .where(
                    Review.subject_type == kind,
                    Review.subject_id.in_(list(approved)),
                    or_(
                        Review.state.in_(_ACTIVE),
                        # A withdrawn successor is back in draft: the author edits and reopens it.
                        and_(
                            Review.state == ReviewState.closed,
                            Review.closed_reason == "withdrawn",
                            version_model.status == ListingStatus.draft,
                        ),
                    ),
                    version_model.listing_id.in_(list(approved)),
                    version_model.status.in_(_EDITABLE),
                )
            )
        )
        .scalars()
        .all()
    )
    by_listing: dict = {}
    for candidate in candidates:
        by_listing.setdefault(candidate.listing_id, []).append(candidate)
    for listing_id, current in approved.items():
        try:
            base = Version(current.version)
            successors = [v for v in by_listing.get(listing_id, []) if Version(v.version) > base]
        except InvalidVersion:
            # Never guess a successor when historical version strings cannot be ordered.
            continue
        result[listing_id] = max(successors, key=lambda v: Version(v.version), default=current)
    return result


def reject_listing_edits_on_successor(listing, version, req, fields) -> None:
    """Refuse listing-level changes while editing a reviewed successor of an approved release.

    Listing fields (name, category, owner) apply to the live, approved listing at once; a
    pending successor's review would never cover them. Repeating the current value passes.
    """
    if version is None or version.id == listing.latest_version_id:
        return
    from fastapi import HTTPException

    changed = [f for f in fields if getattr(req, f, None) is not None and getattr(req, f) != getattr(listing, f)]
    if changed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{', '.join(changed)} cannot change while editing pending version {version.version}: "
                "it would change the published listing without review. Edit it after this version is published."
            ),
        )
