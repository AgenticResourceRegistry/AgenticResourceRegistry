# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The per-user inbox API.

Every route is scoped to ``current_user`` and there is no admin view of another
user's inbox. That is a different feature and a privacy decision nobody has
made; adding it here by accident would be the wrong way to make it.
"""

# No `from __future__ import annotations` here, matching the other route
# modules: FastAPI resolves path and query parameter annotations at runtime.
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger as optic
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, require_role
from api.ratelimit import limiter
from api.sanitize import escape_like
from models.inbox import InboxItem, InboxItemEvent, InboxKind, InboxState
from models.user import User, UserRole
from schemas.inbox import (
    BulkReadResponse,
    InboxCountResponse,
    InboxItemDetailResponse,
    InboxItemEventResponse,
    InboxItemResponse,
    InboxListResponse,
    OutdatedReportRequest,
    OutdatedReportResponse,
)
from services.inbox import delivery, visibility
from services.inbox.registry import Subject, review_action_url

router = APIRouter(prefix="/api/v1/inbox", tags=["inbox"])

# Visibility is applied inside the SQL query, so a page is exactly the rows the
# caller may see. The cap only bounds how much one request can pull; anything
# beyond it and the caller should be paginating rather than scrolling.
_MAX_PAGE_SIZE = 100


def _to_response(item: InboxItem) -> InboxItemResponse:
    return InboxItemResponse(
        id=item.id,
        kind=item.kind.value,
        state=item.state.value,
        read=item.read_at is not None,
        read_at=item.read_at,
        action_required=item.action_required,
        title=item.title,
        body=item.body,
        subject_type=item.subject_type,
        subject_id=item.subject_id,
        subject_namespace=item.subject_namespace,
        subject_slug=item.subject_slug,
        action_url=review_action_url(item.kind, item.payload or {}) or item.action_url,
        action_command=item.action_command,
        actor_id=item.actor_id,
        team_id=item.team_id,
        payload=item.payload or {},
        created_at=item.created_at,
        resolved_at=item.resolved_at,
    )


def _apply_filters(
    stmt,
    *,
    state: InboxState | None,
    kind: InboxKind | None,
    action_required: bool | None,
    unread: bool | None,
    subject_type: str | None,
    q: str | None = None,
):
    if state is not None:
        stmt = stmt.where(InboxItem.state == state)
    if kind is not None:
        stmt = stmt.where(InboxItem.kind == kind)
    if action_required is not None:
        stmt = stmt.where(InboxItem.action_required == action_required)
    if unread is not None:
        stmt = stmt.where(InboxItem.read_at.is_(None) if unread else InboxItem.read_at.is_not(None))
    if subject_type is not None:
        stmt = stmt.where(InboxItem.subject_type == subject_type)
    if q:
        # Searched server-side rather than by filtering the page in the browser.
        # A client-side filter can only see the 25 rows it already has, so it
        # answers "no results" for a match sitting on page two.
        #
        # The columns are the ones an item actually shows: its title, the
        # namespace/slug it names, and the body carrying a reviewer's reason.
        # `escape_like` is what stops a literal % from matching everything.
        needle = f"%{escape_like(q.strip())}%"
        stmt = stmt.where(
            or_(
                InboxItem.title.ilike(needle, escape="\\"),
                InboxItem.body.ilike(needle, escape="\\"),
                InboxItem.subject_namespace.ilike(needle, escape="\\"),
                InboxItem.subject_slug.ilike(needle, escape="\\"),
            )
        )
    return stmt


async def _load_own_item(db: AsyncSession, item_id: uuid.UUID, user: User) -> InboxItem:
    """Fetch one item belonging to this caller, or 404.

    Another user's item answers 404 rather than 403: the caller has no way to
    learn an id they do not own, and confirming that one exists would be a
    disclosure in itself.
    """
    item = (
        await db.execute(select(InboxItem).where(InboxItem.id == item_id, InboxItem.user_id == user.id))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    if not await visibility.visible_to(db, item, user):
        # Same answer as a missing row. The subject has become invisible to this
        # user, so the item must not confirm it exists either.
        raise HTTPException(status_code=404, detail="Inbox item not found")
    return item


@router.get("", response_model=InboxListResponse)
async def list_inbox(
    state: InboxState | None = Query(None),
    kind: InboxKind | None = Query(None),
    action_required: bool | None = Query(None),
    unread: bool | None = Query(None),
    subject_type: str | None = Query(None, max_length=32),
    q: str | None = Query(None, max_length=200),
    sort: str = Query("newest", pattern="^(newest|oldest)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=_MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """List this caller's items, newest first by default.

    Visibility is applied in SQL, so ``total``, the page size, and the rows all
    describe the same set. Counting first and filtering afterwards would leak:
    the total would still include items whose subject the caller can no longer
    see, and a page could come back short with no explanation.
    """
    optic.trace("inbox list for user {}", current_user.id)
    base = select(InboxItem).where(
        InboxItem.user_id == current_user.id,
        visibility.visible_filter(current_user),
    )
    base = _apply_filters(
        base,
        state=state,
        kind=kind,
        action_required=action_required,
        unread=unread,
        subject_type=subject_type,
        q=q,
    )

    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # `id` breaks ties in the same direction as the timestamp. Two items
    # delivered in one fan-out share a `created_at` to the microsecond, and an
    # unstable tiebreak lets the same row appear on two consecutive pages.
    order = (
        (InboxItem.created_at.asc(), InboxItem.id.asc())
        if sort == "oldest"
        else (InboxItem.created_at.desc(), InboxItem.id.desc())
    )
    offset = (page - 1) * page_size
    rows = (await db.execute(base.order_by(*order).offset(offset).limit(page_size))).scalars().all()

    return InboxListResponse(
        items=[_to_response(item) for item in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/count", response_model=InboxCountResponse)
async def inbox_count(
    facets: bool = Query(False),
    facet_state: InboxState | None = Query(InboxState.open),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Badge counts, and optionally the per-facet breakdown behind them.

    Unread counts every unread item regardless of state, because an item that
    was resolved without ever being opened is still something the user has not
    seen. Action-required counts only OPEN items: work already done is not
    outstanding.

    Every count carries the visibility filter. A badge that ticks up for an item
    the caller cannot open would disclose the item just as surely as listing it.

    ``facets`` is opt-in because the two callers want different things. The nav
    badge polls this on a timer for one number; the inbox sidebar loads it once
    per view and needs the breakdown. ``facet_state`` scopes that breakdown to
    the bucket the sidebar is showing, so its per-kind numbers add up to the
    list beside them instead of describing a different set of rows.
    """
    mine = (InboxItem.user_id == current_user.id, visibility.visible_filter(current_user))

    unread = (
        await db.execute(select(func.count(InboxItem.id)).where(*mine, InboxItem.read_at.is_(None)))
    ).scalar() or 0
    action = (
        await db.execute(
            select(func.count(InboxItem.id)).where(
                *mine,
                InboxItem.action_required == True,  # noqa: E712
                InboxItem.state == InboxState.open,
            )
        )
    ).scalar() or 0

    # One grouped scan for all three lifecycle totals rather than three counts.
    by_state = dict(
        (
            await db.execute(select(InboxItem.state, func.count(InboxItem.id)).where(*mine).group_by(InboxItem.state))
        ).all()
    )

    response = InboxCountResponse(
        unread=unread,
        action_required=action,
        open=by_state.get(InboxState.open, 0),
        done=by_state.get(InboxState.done, 0),
        dismissed=by_state.get(InboxState.dismissed, 0),
    )
    if not facets:
        return response

    scoped = (*mine, InboxItem.state == facet_state) if facet_state is not None else mine
    kinds = (
        await db.execute(select(InboxItem.kind, func.count(InboxItem.id)).where(*scoped).group_by(InboxItem.kind))
    ).all()
    subjects = (
        await db.execute(
            select(InboxItem.subject_type, func.count(InboxItem.id)).where(*scoped).group_by(InboxItem.subject_type)
        )
    ).all()
    response.by_kind = {kind.value: count for kind, count in kinds}
    response.by_subject_type = {subject_type: count for subject_type, count in subjects}
    return response


@router.get("/{item_id}", response_model=InboxItemDetailResponse)
async def get_inbox_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    item = await _load_own_item(db, item_id, current_user)
    history = (
        (
            await db.execute(
                select(InboxItemEvent)
                .where(InboxItemEvent.item_id == item.id)
                .order_by(InboxItemEvent.created_at.asc(), InboxItemEvent.id.asc())
            )
        )
        .scalars()
        .all()
    )
    base = _to_response(item)
    return InboxItemDetailResponse(
        **base.model_dump(),
        history=[
            InboxItemEventResponse(
                id=row.id,
                event=row.event,
                actor_id=row.actor_id,
                detail=row.detail,
                created_at=row.created_at,
            )
            for row in history
        ],
    )


@router.post("/{item_id}/read", response_model=InboxItemResponse)
async def mark_item_read(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Mark read. This does NOT change ``state`` and does not resolve anything."""
    item = await _load_own_item(db, item_id, current_user)
    delivery.mark_read(db, item, actor_id=current_user.id)
    await db.commit()
    await db.refresh(item)
    return _to_response(item)


@router.post("/{item_id}/unread", response_model=InboxItemResponse)
async def mark_item_unread(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    item = await _load_own_item(db, item_id, current_user)
    delivery.mark_unread(db, item, actor_id=current_user.id)
    await db.commit()
    await db.refresh(item)
    return _to_response(item)


@router.post("/{item_id}/done", response_model=InboxItemResponse)
async def mark_item_done(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    item = await _load_own_item(db, item_id, current_user)
    delivery.resolve(db, item, state=InboxState.done, actor_id=current_user.id)
    await db.commit()
    await db.refresh(item)
    return _to_response(item)


@router.post("/{item_id}/dismiss", response_model=InboxItemResponse)
async def dismiss_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    item = await _load_own_item(db, item_id, current_user)
    delivery.resolve(db, item, state=InboxState.dismissed, actor_id=current_user.id)
    await db.commit()
    await db.refresh(item)
    return _to_response(item)


@router.post("/{item_id}/reopen", response_model=InboxItemResponse)
async def reopen_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Undo a done or dismiss. Resolving by mistake must be recoverable."""
    item = await _load_own_item(db, item_id, current_user)
    delivery.resolve(db, item, state=InboxState.open, actor_id=current_user.id)
    await db.commit()
    await db.refresh(item)
    return _to_response(item)


@router.post("/read-all", response_model=BulkReadResponse)
async def read_all(
    state: InboxState | None = Query(None),
    kind: InboxKind | None = Query(None),
    action_required: bool | None = Query(None),
    subject_type: str | None = Query(None, max_length=32),
    q: str | None = Query(None, max_length=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Mark everything matching the active filter as read.

    Filter-scoped, not global. A blanket read-all over an actionable feed is a
    footgun: it clears the unread signal on work the user has never looked at.
    Passing no filters still means "everything currently unread", which is the
    caller's explicit choice rather than a hidden default.

    ``q`` is accepted for the same reason: the button sits above a searched
    list, so it has to mean the rows the user is looking at.
    """
    # Items whose subject the caller can no longer see are excluded in SQL, so
    # they are never silently marked read on the caller's behalf.
    stmt = select(InboxItem).where(
        InboxItem.user_id == current_user.id,
        InboxItem.read_at.is_(None),
        visibility.visible_filter(current_user),
    )
    stmt = _apply_filters(
        stmt,
        state=state,
        kind=kind,
        action_required=action_required,
        unread=None,
        subject_type=subject_type,
        q=q,
    )
    rows = (await db.execute(stmt)).scalars().all()

    updated = 0
    for item in rows:
        if delivery.mark_read(db, item, actor_id=current_user.id):
            updated += 1
    await db.commit()
    return BulkReadResponse(updated=updated)


@router.post("/outdated-report", response_model=OutdatedReportResponse)
@limiter.limit("10/minute")
async def outdated_report(
    request: Request,
    req: OutdatedReportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Record outdated findings the CLI computed from the local lock file.

    The server cannot compute this itself: ``AgentDownloadRecord`` knows who has
    an agent but not which version, and ``ComponentDownloadRecord`` knows the
    version but not the user. The lock file is the only source with both, so the
    exact comparison stays in ``observal outdated`` and the result is reported
    here.

    Reported versions are NOT validated against the registry. Items land only in
    the reporting user's own inbox, so a fabricated report harms nobody else.
    That stops being true the moment these rows feed anything shared — an
    aggregate, an admin view — and the trade must be revisited then.

    Rate-limited because open items are never purged on age: without a cap, an
    authenticated caller scripting fabricated reports could grow their own inbox
    without bound, and table growth is shared even when the rows are not.
    """
    created = 0
    superseded = 0

    # One notice per component, whichever harness reported it first. The same
    # component installed in two harnesses arrives as two entries sharing one
    # dedupe key; letting the second run through would rewrite the first's
    # content every request and make the item look freshly updated forever.
    seen_components: set[uuid.UUID] = set()

    for entry in req.items:
        if entry.component_id in seen_components:
            continue
        seen_components.add(entry.component_id)
        subject = Subject(
            type=entry.type,
            id=entry.component_id,
            name=entry.name or (entry.slug or ""),
            namespace=entry.namespace,
            slug=entry.slug,
            version=entry.latest_version,
        )
        context = {
            "current_version": entry.current_version,
            "latest_version": entry.latest_version,
            "harness": entry.harness,
        }
        item = await delivery.deliver_one(
            db,
            kind=InboxKind.update_available,
            user_id=current_user.id,
            subject=subject,
            context=context,
        )
        if item is None:
            continue
        created += 1

        # Older notices for the same component are about a version that is no
        # longer the latest. They are closed with a history entry rather than
        # deleted, so the trail of what was offered survives.
        stale = (
            (
                await db.execute(
                    select(InboxItem).where(
                        InboxItem.user_id == current_user.id,
                        InboxItem.kind == InboxKind.update_available,
                        InboxItem.subject_id == entry.component_id,
                        InboxItem.state == InboxState.open,
                        InboxItem.id != item.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        superseded += await delivery.supersede(db, user_id=current_user.id, keep=item, others=list(stale))

    await db.commit()
    optic.debug("inbox: outdated report from {} -> {} new, {} superseded", current_user.id, created, superseded)
    return OutdatedReportResponse(created=created, superseded=superseded)
