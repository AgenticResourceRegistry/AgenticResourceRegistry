# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Per-user actionable work feed.

An inbox item is one recipient's copy of one fact: a review waiting on them, a
decision on something they submitted, a newer version of something they have
installed. Fan-out happens on write, so every recipient owns their own row and
their own state.

Read state and lifecycle state are deliberately separate columns. An item the
user has read is still work until they resolve it, and a feed that empties on
read is a notification bell, not a work queue.

This is not the monitoring system. ``models/alert.py`` fires webhooks on metric
thresholds and records them in ``alert_history``; those stay where they are.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class InboxKind(str, enum.Enum):
    """What produced an item.

    Kinds whose source does not exist in the codebase yet are declared here
    anyway so the enum and its migration stay stable when those initiatives
    land. ``ownership_transfer`` waits on its offer flow, and
    ``change_requested`` waits on review conversations.
    """

    review_requested = "review_requested"
    review_approved = "review_approved"
    review_rejected = "review_rejected"
    review_comment = "review_comment"
    review_approval = "review_approval"
    review_dismissed = "review_dismissed"
    review_ready = "review_ready"
    change_requested = "change_requested"
    team_join_requested = "team_join_requested"
    team_join_decided = "team_join_decided"
    team_created_pending = "team_created_pending"
    ownership_transfer = "ownership_transfer"
    update_available = "update_available"
    insight_ready = "insight_ready"
    system_notice = "system_notice"


class InboxState(str, enum.Enum):
    """Lifecycle, independent of whether the item has been read."""

    open = "open"
    done = "done"
    dismissed = "dismissed"


class InboxItem(Base):
    """One recipient's copy of one delivered fact."""

    __tablename__ = "inbox_items"
    __table_args__ = (
        # Idempotency is a constraint, not a convention. Redelivering the same
        # fact must be a no-op even when two requests race, which a
        # check-then-insert cannot promise.
        UniqueConstraint("user_id", "dedupe_key", name="uq_inbox_items_user_dedupe"),
        Index("ix_inbox_items_user_state_created", "user_id", "state", "created_at"),
        Index("ix_inbox_items_user_action_state", "user_id", "action_required", "state"),
        Index("ix_inbox_items_user_read", "user_id", "read_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[InboxKind] = mapped_column(Enum(InboxKind, name="inbox_kind"), nullable=False)
    state: Mapped[InboxState] = mapped_column(
        Enum(InboxState, name="inbox_state"), nullable=False, default=InboxState.open
    )
    # Read is not resolved. Setting this must never remove an item from the
    # default list or the needs-action list.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    action_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "agent", "skill", "mcp", "hook", "prompt", "sandbox", "team", "insight_report"
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Stored as parts rather than a built path so canonical routes can change
    # without a backfill.
    subject_namespace: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)

    action_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    action_command: Mapped[str | None] = mapped_column(String(500), nullable=True)

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Authorization was resolved at write time, which was correct then. These two
    # carry enough to re-check it at read time, because membership can be revoked
    # afterwards and a removed member must stop seeing a private subject's name.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True
    )
    is_private_subject: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InboxItemEvent(Base):
    """Append-only history for one item.

    Never updated, only appended, and written in the same transaction as the
    state change it records. This is what "retains action history" means: an
    item that was read, reopened and finally dismissed can say so.
    """

    __tablename__ = "inbox_item_events"
    __table_args__ = (Index("ix_inbox_item_events_item", "item_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inbox_items.id", ondelete="CASCADE"), nullable=False
    )
    # "created", "read", "unread", "done", "dismissed", "reopened", "superseded"
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
