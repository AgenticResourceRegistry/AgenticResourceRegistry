# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""The single submit-to-review path. The caller commits all rows together."""

from fastapi import HTTPException
from sqlalchemy import select

from models.agent import AgentStatus
from models.mcp import ListingStatus
from models.review import Review, ReviewState
from services.review.decisions import DRAFT_EDIT_MESSAGE, SYSTEM_REVISION_MESSAGES, _head, open_or_push, sync_state
from services.review.notifications import deliver_event


async def lock_review_for_edit(db, subject_type, version):
    """Serialize an in-place draft save with publication of its review.

    A save on a version under review is pushed as its next revision (see
    ``push_draft_edit``). Publication locks the review row first; after waiting
    for it, refresh the version status so an edit that began before publication
    cannot overwrite the newly approved release.
    """
    review_id = await db.scalar(
        select(Review.id).where(Review.subject_type == subject_type, Review.version_id == version.id).with_for_update()
    )
    if review_id is not None:
        await db.refresh(version, attribute_names=["status"])


async def submit_for_review(db, subject_type, subject, version, actor_id, *, message=None, system_generated=False):
    if message in SYSTEM_REVISION_MESSAGES and not system_generated:
        raise HTTPException(422, "This review message is reserved for system-generated revisions")
    already_current = await _draft_already_under_review(db, subject_type, version)
    if already_current is not None:
        # A draft save already pushed this content as the head revision; submitting it
        # just marks the version ready for review again instead of failing as "no changes".
        if version.status in (ListingStatus.draft, ListingStatus.rejected, ListingStatus.changes_requested):
            version.status = AgentStatus.pending if subject_type == "agent" else ListingStatus.pending
        # The author now submits this revision: it answers outstanding change requests.
        head = await _head(db, already_current)
        head.message = message
        await sync_state(db, already_current, actor_id=actor_id)
        return already_current
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


async def _draft_already_under_review(db, subject_type, version):
    """The open review whose head revision already holds this exact content, pushed by a draft save."""
    from services.review.decisions import _hash
    from services.review.files import render_files

    review = await db.scalar(
        select(Review).where(Review.subject_type == subject_type, Review.version_id == version.id).with_for_update()
    )
    if review is None or review.state in (ReviewState.closed, ReviewState.published):
        return None
    head = await _head(db, review)
    if head.message != DRAFT_EDIT_MESSAGE:
        return None  # Only an automatic draft push makes a resubmission a no-op.
    if _hash(await render_files(subject_type, version, db)) != head.content_hash:
        return None
    return review


async def resubmit_if_changed(db, subject_type, subject, version, message, *, actor_id=None, system_generated=False):
    """Push a new revision when the version changed after submission.

    Used for system-derived fields (MCP validation) and for author draft saves.
    Returns the review, or None when there is no live review or nothing changed.
    """
    from services.review.decisions import _hash
    from services.review.files import render_files

    review = await db.scalar(
        select(Review).where(Review.subject_type == subject_type, Review.version_id == version.id).with_for_update()
    )
    if review is None or review.state in (ReviewState.closed, ReviewState.published):
        return None
    head = await _head(db, review)
    if _hash(await render_files(subject_type, version, db)) == head.content_hash:
        return None
    return await submit_for_review(
        db,
        subject_type,
        subject,
        version,
        actor_id or version.released_by,
        message=message,
        system_generated=system_generated,
    )


async def push_draft_edit(db, subject_type, subject, version, actor_id) -> dict | None:
    """Submit an author's draft save as a new revision of its open review.

    Without this, a save after submission leaves reviewers approving an older
    revision than the one that would be published. Approvals then follow the
    review's stale-approval policy. Returns response fields describing the new
    revision, or None when there is no open review or the content is unchanged.
    """
    from services.review.decisions import _hash
    from services.review.files import render_files

    await db.flush()
    review = await db.scalar(
        select(Review).where(Review.subject_type == subject_type, Review.version_id == version.id).with_for_update()
    )
    if review is None or review.state in (ReviewState.closed, ReviewState.published):
        return None
    if _hash(await render_files(subject_type, version, db)) == (await _head(db, review)).content_hash:
        return None
    # open_or_push (not submit_for_review): the version keeps its status, so an author answering
    # requested changes can keep editing; each save is a revision reviewers can see.
    review = await open_or_push(db, subject_type, subject, version, actor_id, message=DRAFT_EDIT_MESSAGE)
    revision = await _head(db, review)
    await deliver_event(db, review, "revision", actor_id, revision=revision.number)
    return {
        "review_number": review.number,
        "review_url": f"/review/{review.number}",
        "review_revision": revision.number,
    }
