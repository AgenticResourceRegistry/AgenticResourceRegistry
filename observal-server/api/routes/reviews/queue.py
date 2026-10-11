# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Visible review queue. Always filter before paginating to avoid leaking metadata."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, and_, cast, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.routes.reviews.common import participant
from models.agent import Agent, AgentVersion
from models.agent_component import AgentComponent
from models.review import Review, ReviewComment, ReviewReviewerRequest, ReviewState, ReviewSubmission, ReviewThread
from models.user import User
from services.agent_lock import LISTING_MODELS, VERSION_MODELS
from services.review.decisions import gate
from services.teamspace import review_scope

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])


def visible_review_predicate(user, scope, *, author_only=False):
    """Filter candidates in SQL before LIMIT, using the subject's current ACL.

    Review visibility columns are a historical snapshot; a team can become
    public after opening a review. Match participant() against live subjects.
    The final per-row check remains a defense against an ACL change mid-request.
    """
    predicates = []
    for kind, model, version_model, owner_field, version_field in (
        ("agent", Agent, AgentVersion, Agent.created_by, AgentVersion.agent_id),
        *(
            (kind, model, VERSION_MODELS[kind], model.submitted_by, VERSION_MODELS[kind].listing_id)
            for kind, model in LISTING_MODELS.items()
        ),
    ):
        own = or_(
            owner_field == user.id,
            version_model.released_by == user.id,
            cast(model.co_authors, String).like(f'%"{user.id}"%'),
        )
        if author_only:
            allowed = own
        elif scope.is_admin:
            allowed = True
        else:
            reviewer = []
            if scope.is_global_reviewer:
                reviewer.append(model.is_private.is_(False))
            if scope.team_ids:
                reviewer.append(and_(model.is_private.is_(True), model.team_id.in_(scope.team_ids)))
            if scope.public_team_ids:
                reviewer.append(and_(model.is_private.is_(False), model.team_id.in_(scope.public_team_ids)))
            allowed = or_(own, *reviewer)
        predicates.append(
            and_(
                Review.subject_type == kind,
                exists(
                    select(1)
                    .select_from(model)
                    .join(version_model, version_field == model.id)
                    .where(model.id == Review.subject_id, version_model.id == Review.version_id, allowed)
                ),
            )
        )
    return or_(*predicates)


async def summary(db, review, user):
    from api.routes.reviews.common import head

    latest = await head(db, review)
    result = await gate(db, review)
    requested = await db.get(ReviewReviewerRequest, (review.id, user.id))
    mine = await db.scalar(
        select(ReviewSubmission)
        .where(
            ReviewSubmission.review_id == review.id,
            ReviewSubmission.reviewer_id == user.id,
            ReviewSubmission.state == "submitted",
        )
        .order_by(ReviewSubmission.submitted_at.desc(), ReviewSubmission.id.desc())
        .limit(1)
    )
    thread_rows = (
        await db.execute(
            select(ReviewThread.id, ReviewThread.resolved_at, ReviewSubmission.state, ReviewSubmission.reviewer_id)
            .join(ReviewComment, ReviewComment.thread_id == ReviewThread.id)
            .outerjoin(ReviewSubmission, ReviewSubmission.id == ReviewComment.submission_id)
            .where(ReviewThread.review_id == review.id)
        )
    ).all()
    visible_threads = {
        tid: resolved for tid, resolved, state, author in thread_rows if state != "draft" or author == user.id
    }
    checks = {
        status: sum(c.get("status") == status for c in latest.checks) for status in ("pass", "fail", "warn", "skipped")
    }
    dependencies = []
    if review.subject_type == "agent":
        pins = (
            await db.scalars(
                select(AgentComponent.resolved_version_id).where(
                    AgentComponent.agent_version_id == review.version_id,
                    AgentComponent.resolved_version_id.is_not(None),
                )
            )
        ).all()
        if pins:
            linked = (await db.scalars(select(Review).where(Review.version_id.in_(pins)))).all()
            dependencies = [r.number for r in linked if await participant(db, r, user)]
    author = await db.get(User, review.opened_by) if review.opened_by else None
    return {
        "depends_on": dependencies,
        "threads": {
            "total": len(visible_threads),
            "unresolved": sum(resolved is None for resolved in visible_threads.values()),
        },
        "checks": checks,
        "id": review.id,
        "number": review.number,
        "title": review.title,
        "subject_type": review.subject_type,
        "subject_id": review.subject_id,
        "version": review.version,
        "team_id": review.team_id,
        "state": review.state.value,
        "author_id": review.opened_by,
        "author_name": author.username if author else None,
        "head_revision": latest.number,
        "updated_at": review.updated_at,
        "gate": {
            "ready": result.ready,
            "approvals": result.approvals,
            "required": result.required,
            "requirements": result.requirements,
            "policy_source": result.policy_source,
        },
        "requested_from_me": requested is not None,
        "my_last_submission": ({"verdict": mine.verdict, "revision_id": mine.revision_id} if mine else None),
    }


@router.get("")
async def list_reviews(
    state: str = "open",
    type: str | None = None,
    team_id: uuid.UUID | None = None,
    author: str | None = None,
    requested: str | None = None,
    needs: str | None = None,
    q: str | None = None,
    cursor: int | None = None,
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if state not in {"open", "changes_requested", "approved", "published", "closed", "completed", "all"}:
        raise HTTPException(422, "Unknown state")
    if type and type not in {"agent", "component", "mcp", "skill", "hook", "prompt", "sandbox"}:
        raise HTTPException(422, "Unknown subject type")
    if (
        author not in (None, "me")
        or requested not in (None, "me")
        or needs not in (None, "my_review", "ready_to_publish")
    ):
        raise HTTPException(422, "Invalid queue filter")
    scope = await review_scope(db, user)
    stmt = select(Review).where(visible_review_predicate(user, scope, author_only=author == "me"))
    if state == "open":
        stmt = stmt.where(Review.state.in_((ReviewState.open, ReviewState.changes_requested, ReviewState.approved)))
    elif state == "completed":
        stmt = stmt.where(Review.state.in_((ReviewState.published, ReviewState.closed)))
    elif state != "all":
        stmt = stmt.where(Review.state == ReviewState(state))
    if type == "component":
        stmt = stmt.where(Review.subject_type.in_(tuple(LISTING_MODELS)))
    elif type:
        stmt = stmt.where(Review.subject_type == type)
    if team_id:
        stmt = stmt.where(Review.team_id == team_id)
    if requested == "me":
        stmt = stmt.join(ReviewReviewerRequest, ReviewReviewerRequest.review_id == Review.id).where(
            ReviewReviewerRequest.user_id == user.id
        )
    if cursor is not None:
        stmt = stmt.where(Review.number < cursor)
    if needs == "my_review":
        stmt = stmt.where(
            ~exists(
                select(1).where(
                    ReviewSubmission.review_id == Review.id,
                    ReviewSubmission.reviewer_id == user.id,
                    ReviewSubmission.revision_id == Review.head_revision_id,
                    ReviewSubmission.state == "submitted",
                )
            )
        )
    if needs == "ready_to_publish":
        stmt = stmt.where(Review.state == ReviewState.approved)
    if q:
        from api.sanitize import escape_like

        stmt = stmt.where(Review.title.ilike(f"%{escape_like(q.strip())}%", escape="\\"))
    # The database, not Python, bounds the ACL-filtered page. Gate-dependent
    # filters are checked after loading at most limit+1 candidates; a sparse
    # ready page may be empty but still have a cursor to continue from.
    candidates = (await db.scalars(stmt.order_by(Review.number.desc()).limit(limit + 1))).all()
    rows = []
    for review in candidates[:limit]:
        if not await participant(db, review, user, scope=scope):
            continue
        item = await summary(db, review, user)
        if needs == "ready_to_publish" and not item["gate"]["ready"]:
            continue
        rows.append(item)
    return {
        "items": rows,
        "next_cursor": candidates[limit - 1].number if len(candidates) > limit else None,
    }
