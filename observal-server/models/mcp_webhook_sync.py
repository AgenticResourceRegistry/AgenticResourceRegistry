# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Webhook auto-sync settings for one MCP listing (GitHub or GitLab)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class McpWebhookSync(Base):
    __tablename__ = "mcp_webhook_syncs"

    # The id is the unguessable part of the public webhook URL.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mcp_listings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    # github or gitlab: which webhook format the receiver accepts for this listing.
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="github", server_default="github")
    # Encrypted with services.dynamic_settings.encrypt_value. GitHub signs deliveries
    # with it (HMAC); GitLab sends it back as a token. Both need the plaintext.
    secret: Mapped[str] = mapped_column(Text, nullable=False)
    # None tracks the repository's default branch.
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sync_on_push: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sync_on_release: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Synced versions are published as this user and auto-approved on their behalf.
    enabled_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # queued, syncing, success, skipped, failed
    last_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # When the fetch behind the newest push-synced version started. A push sync whose
    # fetch started earlier holds older code, so it is not published after it.
    published_fetch_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
