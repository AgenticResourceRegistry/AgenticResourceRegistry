# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Writing inbox items and moving them through their lifecycle.

Delivery is transactional. ``deliver()`` writes rows in the caller's own
transaction, so inbox items commit exactly when the thing they describe commits
and roll back with it. It is deliberately NOT routed through
``services.events.EventBus``: that bus awaits handlers inline and swallows their
exceptions, logging that "this handler's side-effects are lost". A lost handler
would be a lost inbox item, which is the one thing this feature may not do.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger as optic
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from models.inbox import InboxItem, InboxItemEvent, InboxKind, InboxState
from services.inbox.registry import KindSpec, Subject, spec_for

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


def record_event(
    db: AsyncSession,
    item: InboxItem,
    event: str,
    *,
    actor_id: uuid.UUID | None = None,
    detail: str | None = None,
) -> InboxItemEvent:
    """Append one history row. Callers must be inside the item's transaction."""
    row = InboxItemEvent(item_id=item.id, event=event, actor_id=actor_id, detail=detail)
    db.add(row)
    return row


async def deliver_one(
    db: AsyncSession,
    *,
    kind: InboxKind,
    user_id: uuid.UUID,
    subject: Subject,
    actor_id: uuid.UUID | None = None,
    body: str | None = None,
    context: dict[str, Any] | None = None,
    action_required: bool | None = None,
) -> InboxItem | None:
    """Deliver one item to one recipient. Returns None if already delivered.

    Idempotency is enforced by the ``(user_id, dedupe_key)`` unique constraint
    and recovered from inside a SAVEPOINT. The savepoint is load-bearing: this
    runs in the originating request's transaction, so an ``IntegrityError``
    escaping to a plain ``db.rollback()`` would discard the caller's own
    uncommitted work — a redelivered approval notice would undo the approval —
    and every sibling recipient already flushed in this batch. Postgres also
    refuses further statements after an error unless a savepoint absorbs it, so
    there is no "roll back and carry on" without one.

    Do not copy the recovery in ``services/user_recommendations.py`` here. It
    rolls the whole session back, which is safe only because it owns its
    transaction and commits at top level.
    """
    ctx = context or {}
    spec = spec_for(kind)

    item = InboxItem(
        user_id=user_id,
        kind=kind,
        state=InboxState.open,
        action_required=spec.action_required if action_required is None else action_required,
        title=spec.title(subject, ctx)[:255],
        body=body,
        subject_type=subject.type,
        subject_id=subject.id,
        subject_namespace=subject.namespace,
        subject_slug=subject.slug,
        action_url=_truncate(spec.url(subject), 500),
        action_command=_truncate(spec.command(subject, ctx), 500),
        actor_id=actor_id,
        team_id=subject.team_id,
        is_private_subject=bool(subject.is_private),
        dedupe_key=spec.dedupe(subject, ctx)[:255],
        payload=dict(ctx),
    )

    try:
        async with db.begin_nested():  # SAVEPOINT: portable across SQLite and Postgres
            db.add(item)
            await db.flush()
    except IntegrityError as exc:
        # ONLY a dedupe collision means "already delivered". This table also has
        # foreign keys to users (twice) and teams, and a recipient deleted
        # between resolution and flush raises IntegrityError too. Swallowing
        # that would lose an item silently, which is the one outcome this
        # module exists to prevent, so anything else is re-raised.
        if not _is_dedupe_collision(exc):
            optic.error("inbox delivery failed for user {} kind {}: {}", user_id, kind.value, exc)
            raise
        return await _on_duplicate(db, fresh=item, spec=spec, actor_id=actor_id)

    record_event(db, item, "created", actor_id=actor_id)
    return item


def _is_dedupe_collision(exc: IntegrityError) -> bool:
    """Whether this error is the idempotency constraint and nothing else.

    Matched on the constraint name, which both Postgres and SQLite put in the
    message, rather than on the exception type. A foreign key violation is the
    same type and must not be read as a duplicate.
    """
    text = str(getattr(exc, "orig", exc)).lower()
    if "uq_inbox_items_user_dedupe" in text:
        return True
    # SQLite words it as the column list instead of the constraint name.
    return "unique constraint failed" in text and "dedupe_key" in text


def _refresh_content(existing: InboxItem, fresh: InboxItem) -> bool:
    """Bring a colliding item's presentation up to date with the redelivery.

    The dedupe key names the fact, not its wording. A second rejection of the
    same version carries a new reason, and a still-outdated install reports a
    new current version; leaving the old text in place would show the recipient
    a decision that is no longer the one on record.
    """
    changed = False
    # Visibility and ownership can change between review rounds. Reopening a
    # private team request for a global reviewer must not retain the old private
    # snapshot (or stale namespace/team link) from the previous delivery.
    for attr in (
        "title",
        "body",
        "action_url",
        "action_command",
        "payload",
        "subject_namespace",
        "subject_slug",
        "team_id",
        "is_private_subject",
    ):
        new_value = getattr(fresh, attr)
        if getattr(existing, attr) != new_value:
            setattr(existing, attr, new_value)
            changed = True
    return changed


async def _on_duplicate(
    db: AsyncSession,
    *,
    fresh: InboxItem,
    spec: KindSpec,
    actor_id: uuid.UUID | None,
) -> InboxItem | None:
    """Handle a redelivery colliding with an item the recipient already holds.

    Redelivering an OPEN item is a no-op for delivery counts, but its content is
    refreshed (and it goes unread again) when the redelivery says something new.

    A key arriving again after the recipient RESOLVED their copy is not a
    duplicate — it is the fact becoming true a second time. Resubmitting the
    same version after a rejection is the case that matters: the reviewer has
    already marked their copy done, and without this nobody would be told the
    work is back. Reopening preserves the audit trail that a redelivery
    happened, which creating a second row could not.

    Kinds with ``reopen_on_redelivery=False`` opt out of the resurrection:
    ``update_available`` is re-reported by every ``observal outdated`` run for
    as long as the user stays outdated, so the same still-true fact would
    otherwise reopen a dismissed notice forever. A NEWER version is a new
    dedupe key and a new item regardless of this flag.
    """
    user_id, dedupe_key = fresh.user_id, fresh.dedupe_key
    existing = (
        await db.execute(select(InboxItem).where(InboxItem.user_id == user_id, InboxItem.dedupe_key == dedupe_key))
    ).scalar_one_or_none()
    if existing is None:
        # The colliding row is gone (purged, or another transaction removed it).
        # Nothing to reopen and nothing to report.
        return None

    changed = _refresh_content(existing, fresh)

    if existing.state == InboxState.open:
        if changed:
            existing.read_at = None  # the new content has not been seen
            record_event(db, existing, "updated", actor_id=actor_id, detail="Redelivered with updated details")
        optic.trace("inbox item already delivered for user {} key {}", user_id, dedupe_key)
        return None

    if not spec.reopen_on_redelivery:
        # Content stays current so a manual reopen shows today's facts, but a
        # resolved item resolved stays: no event, no unread, no resurrection.
        return None

    existing.state = InboxState.open
    existing.resolved_at = None
    existing.read_at = None  # a fact that came back is unread again
    record_event(db, existing, "reopened", actor_id=actor_id, detail="Redelivered after resolution")
    optic.debug("inbox item reopened on redelivery for user {} key {}", user_id, dedupe_key)
    return existing


async def deliver(
    db: AsyncSession,
    *,
    kind: InboxKind,
    recipients: Iterable[uuid.UUID],
    subject: Subject,
    actor_id: uuid.UUID | None = None,
    body: str | None = None,
    context: dict[str, Any] | None = None,
    action_required: bool | None = None,
    skip_actor: bool = True,
) -> list[InboxItem]:
    """Fan out one fact to its recipients, in the caller's transaction.

    ``skip_actor`` keeps a user from being told about their own action. A team
    reviewer who approves an item is in the reviewer set for it; delivering to
    them would put their own click in their queue.
    """
    delivered: list[InboxItem] = []
    for user_id in _unique(recipients):
        if skip_actor and actor_id is not None and user_id == actor_id:
            continue
        item = await deliver_one(
            db,
            kind=kind,
            user_id=user_id,
            subject=subject,
            actor_id=actor_id,
            body=body,
            context=context,
            action_required=action_required,
        )
        if item is not None:
            delivered.append(item)
    return delivered


async def supersede(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    keep: InboxItem,
    others: Sequence[InboxItem],
) -> int:
    """Resolve older items that a newer fact has replaced.

    A newer version is a new ``dedupe_key`` and therefore a new item; the
    previous one is not a duplicate to be swallowed but a fact that is no longer
    true. It is closed with its own history entry rather than deleted, because
    the point of this feature is that work does not silently disappear.

    ``user_id`` is enforced, not decorative. This closes rows and writes history
    against them, so a caller that assembled ``others`` from a query missing its
    own user filter would silently resolve somebody else's inbox. Rows that do
    not belong to ``user_id`` are skipped and logged rather than touched.
    """
    now = datetime.now(UTC)
    count = 0
    for stale in others:
        if stale.user_id != user_id:
            optic.warning(
                "inbox supersede skipped item {} owned by {} while scoped to {}",
                stale.id,
                stale.user_id,
                user_id,
            )
            continue
        if stale.id == keep.id or stale.state != InboxState.open:
            continue
        stale.state = InboxState.done
        stale.resolved_at = now
        record_event(db, stale, "superseded", detail=f"Superseded by {keep.dedupe_key}")
        count += 1
    return count


async def resolve_matching(
    db: AsyncSession,
    *,
    kind: InboxKind,
    dedupe_key: str,
    detail: str,
    actor_id: uuid.UUID | None = None,
) -> int:
    """Close EVERY recipient's open copy of one fact that stopped being true.

    A review decision empties the queue for all of the reviewers it fanned out
    to, not only the one who clicked. Leaving the other copies open would keep
    ``action_required`` counting work that no longer exists and send a reviewer
    into a queue that does not contain the thing their inbox promised.

    This is the one deliberate exception to per-user scoping: the rows belong to
    different users, but the state change is the system recording that the
    underlying fact is gone, not one user acting on another's inbox. Items are
    resolved with their own history entry, never deleted.
    """
    rows = (
        (
            await db.execute(
                select(InboxItem).where(
                    InboxItem.kind == kind,
                    InboxItem.dedupe_key == dedupe_key,
                    InboxItem.state == InboxState.open,
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    for item in rows:
        item.state = InboxState.done
        item.resolved_at = now
        record_event(db, item, "done", actor_id=actor_id, detail=detail)
    return len(rows)


def mark_read(db: AsyncSession, item: InboxItem, *, actor_id: uuid.UUID | None = None) -> bool:
    """Mark an item read WITHOUT touching its lifecycle state.

    The two axes are independent by design. An item that is read is still open
    work, still counted by the needs-action filter, and still in the default
    list. Collapsing these into one boolean is the failure this feature exists
    to prevent.
    """
    if item.read_at is not None:
        return False
    item.read_at = datetime.now(UTC)
    record_event(db, item, "read", actor_id=actor_id)
    return True


def mark_unread(db: AsyncSession, item: InboxItem, *, actor_id: uuid.UUID | None = None) -> bool:
    if item.read_at is None:
        return False
    item.read_at = None
    record_event(db, item, "unread", actor_id=actor_id)
    return True


def resolve(
    db: AsyncSession,
    item: InboxItem,
    *,
    state: InboxState,
    actor_id: uuid.UUID | None = None,
) -> bool:
    """Move an item to done or dismissed, or reopen it."""
    if item.state == state:
        return False
    item.state = state
    if state == InboxState.open:
        item.resolved_at = None
        record_event(db, item, "reopened", actor_id=actor_id)
    else:
        item.resolved_at = datetime.now(UTC)
        record_event(db, item, state.value, actor_id=actor_id)
    return True


def _unique(values: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []
    for value in values:
        if value is not None and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]
