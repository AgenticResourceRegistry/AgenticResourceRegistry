# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""One chronological source of truth for reviewer decisions and request conversations."""

from datetime import UTC, datetime

from sqlalchemy import select

from models.review import ReviewComment, ReviewSubmission, ReviewThread


def decision_statuses(submissions):
    """Return latest per-reviewer decisions and per-request lifecycle states.

    Dismissals remain terminal decisions: filtering them out before reducing
    would silently resurrect an older approval or request.
    """
    latest = {}
    statuses = {}
    for row in sorted(
        submissions, key=lambda s: ((s.submitted_at.replace(tzinfo=None) if s.submitted_at else datetime.min), s.id)
    ):
        if row.verdict not in ("approve", "request_changes") or row.state == "draft":
            continue
        previous = latest.get(row.reviewer_id)
        if previous and previous.verdict == "request_changes" and previous.state == "submitted":
            statuses[previous.id] = "cleared" if row.verdict == "approve" else "superseded"
        latest[row.reviewer_id] = row
        if row.verdict == "request_changes":
            statuses[row.id] = "withdrawn" if row.state == "dismissed" else "blocking"
    return latest, statuses


async def review_decisions(db, review):
    rows = (
        await db.scalars(
            select(ReviewSubmission)
            .where(ReviewSubmission.review_id == review.id, ReviewSubmission.state != "draft")
            .order_by(ReviewSubmission.submitted_at, ReviewSubmission.id)
        )
    ).all()
    return rows, *decision_statuses(rows)


def request_lifecycle_status(review, request, statuses):
    """Describe why a request does or does not currently block publication."""
    if review.state.value == "closed" and review.closed_reason == "superseded":
        return "superseded"
    if (request.dismissed_reason or "").startswith("Visibility changed"):
        return "invalidated"
    if review.state.value in ("closed", "published"):
        return "closed"
    return statuses.get(request.id, "cleared")


async def reconcile_request_threads(db, review, *, actor_id=None):
    """Close only requests no longer blocking; do not resolve unrelated threads."""
    from models.inbox import InboxKind
    from services.review.notifications import resolve_review_work

    rows, _, statuses = await review_decisions(db, review)
    linked = (
        await db.scalars(
            select(ReviewThread).where(ReviewThread.review_id == review.id, ReviewThread.change_request_id.is_not(None))
        )
    ).all()
    for thread in linked:
        state = statuses.get(thread.change_request_id)
        if state == "blocking" and review.state.value not in ("published", "closed"):
            if thread.resolved_at is not None:
                thread.resolved_at = None
                thread.resolved_by = None
            continue
        if thread.resolved_at is None:
            thread.resolved_at = datetime.now(UTC)
            thread.resolved_by = actor_id
        if state != "blocking" or review.state.value in ("published", "closed"):
            await resolve_review_work(
                db, review, actor_id, kinds=(InboxKind.change_requested,), request_ids={str(thread.change_request_id)}
            )
    return rows, statuses


def _has_stale_threads(review, statuses, existing):
    """Check whether linked conversations disagree with their request lifecycle."""
    terminal = review.state.value in ("published", "closed")
    return any(
        (thread.resolved_at is None and (statuses.get(thread.change_request_id) != "blocking" or terminal))
        or (thread.resolved_at is not None and statuses.get(thread.change_request_id) == "blocking" and not terminal)
        for thread in existing
    )


def _needs_repair(review, rows, statuses, existing):
    """Detect missing request links or conversations with the wrong resolved state."""
    linked = {thread.change_request_id for thread in existing}
    return any(row.verdict == "request_changes" and row.id not in linked for row in rows) or _has_stale_threads(
        review, statuses, existing
    )


async def request_threads_need_repair(db, review):
    """Check old-worker drift without locking review, listing, or version rows."""
    rows, _, statuses = await review_decisions(db, review)
    existing = (
        await db.scalars(
            select(ReviewThread).where(ReviewThread.review_id == review.id, ReviewThread.change_request_id.is_not(None))
        )
    ).all()
    return _needs_repair(review, rows, statuses, existing)


async def ensure_request_threads(db, review, *, persist=True, update_state=True):
    """Reconcile requests written by old API workers after migration backfill."""
    from models.inbox import InboxKind
    from services.review.notifications import resolve_review_work

    rows, _, statuses = await review_decisions(db, review)
    existing = (
        await db.scalars(
            select(ReviewThread).where(ReviewThread.review_id == review.id, ReviewThread.change_request_id.is_not(None))
        )
    ).all()
    linked = {thread.change_request_id for thread in existing}
    stale = _has_stale_threads(review, statuses, existing)
    created = False
    for request in rows:
        if request.verdict != "request_changes" or request.id in linked:
            continue
        cleared = statuses.get(request.id) != "blocking" or review.state.value in ("published", "closed")
        thread = ReviewThread(
            review_id=review.id,
            change_request_id=request.id,
            created_by=request.reviewer_id,
            resolved_at=datetime.now(UTC) if cleared else None,
            resolved_by=request.dismissed_by if cleared else None,
            created_at=request.submitted_at or datetime.now(UTC),
        )
        db.add(thread)
        await db.flush()
        db.add(
            ReviewComment(
                review_id=review.id,
                thread_id=thread.id,
                submission_id=request.id,
                author_id=request.reviewer_id,
                body=request.body or "",
                created_at=request.submitted_at or datetime.now(UTC),
            )
        )
        if cleared:
            await resolve_review_work(
                db, review, request.dismissed_by, kinds=(InboxKind.change_requested,), request_ids={str(request.id)}
            )
        created = True
    if created or stale:
        if update_state:
            from services.review.decisions import gate, sync_state

            was_ready = (await gate(db, review)).ready
            was_active = review.state.value not in ("closed", "published")
        if stale:
            await reconcile_request_threads(db, review)
        if update_state:
            from services.review.notifications import deliver_gate_change

            await sync_state(db, review)
            if was_active:
                await deliver_gate_change(db, review, was_ready, None)
        if persist:
            await db.commit()
    return created or stale
