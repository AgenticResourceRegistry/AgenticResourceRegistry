# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Local delivery state for optional aggregate usage reporting."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class UsagePingState(Base):
    __tablename__ = "usage_ping_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False, default=uuid.uuid4
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Opt-in contact shared with the collector: name, email, role, team_size, consented_at.
    contact: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # "" (never asked), "later", "never", or "submitted".
    contact_prompt: Mapped[str] = mapped_column(String(16), nullable=False, default="", server_default="")
    contact_prompted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
