# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""The single submit-to-review path. The caller commits all rows together."""

from sqlalchemy import select

from models.agent import AgentStatus
from models.mcp import ListingStatus
from models.review import Review, ReviewState
from services.review.decisions import _head, open_or_push, sync_state
from services.review.notifications import deliver_event


async def lock_review_for_edit(db, subject_type, version):
    """Serialize an in-place draft save with publication of its review.

    The author may save without submitting a new revision. Publication locks the
    review row first; after waiting for it, refresh the version status so an edit
    that began before publication cannot overwrite the newly approved release.
    """
    review_id = await db.scalar(
        select(Review.id).where(Review.subject_type == subject_type, Review.version_id == version.id).with_for_update()
    )
    if review_id is not None:
        await db.refresh(version, attribute_names=["status"])


async def submit_for_review(db, subject_type, subject, version, actor_id, *, message=None):
    review = await open_or_push(db, subject_type, subject, version, actor_id, message=message)
    # open_or_push starts the revision and may then mark an outstanding change
    # request as blocking. A resubmitted version remains pending until review;
    # the review gate still tracks those outstanding requests separately.
    # Write through the version, not the listing's latest_version relationship:
    # a newly created listing may not have that relationship loaded yet.
    if version.status in (ListingStatus.draft, ListingStatus.rejected, ListingStatus.changes_requested):
        version.status = AgentStatus.pending if subject_type == "agent" else ListingStatus.pending
    revision = await _head(db, review)
    if revision.number == 1:
        await sync_state(db, review, actor_id=actor_id)
    event = "published" if review.state == ReviewState.published else "opened" if revision.number == 1 else "revision"
    await deliver_event(db, review, event, actor_id, revision=revision.number)
    return review
