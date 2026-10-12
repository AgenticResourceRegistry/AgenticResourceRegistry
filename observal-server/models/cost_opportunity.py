# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A priced cost-saving opportunity and where it is in the savings pipeline.

Opportunities are produced by deterministic detectors over observed spend
(``services.cost.opportunities``). Each row carries the formula inputs that
produced its estimate, so the identified amount is reproducible. Realized
savings are not stored here: they are measured from telemetry after the
change ships (see ``services.cost.realized``).

Lifecycle: identified -> approved -> implemented, or dismissed at any point.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

STATUS_IDENTIFIED = "identified"
STATUS_APPROVED = "approved"
STATUS_IMPLEMENTED = "implemented"
STATUS_DISMISSED = "dismissed"
OPPORTUNITY_STATUSES = (STATUS_IDENTIFIED, STATUS_APPROVED, STATUS_IMPLEMENTED, STATUS_DISMISSED)


class CostOpportunity(Base):
    __tablename__ = "cost_opportunities"
    __table_args__ = (
        Index("ix_cost_opportunities_status", "status"),
        Index("ix_cost_opportunities_agent_id", "agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # detector + subject, e.g. "model_routing:<agent>:<from>:<to>". Re-detection
    # updates the same row instead of creating duplicates.
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    detector: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    # Version the opportunity was detected on; implementation is a later version.
    baseline_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_IDENTIFIED)
    estimated_monthly_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    # Formula, inputs and observation window that produced the estimate.
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Cost per session on the baseline version, frozen when the opportunity is
    # approved, so realized savings compare against a fixed reference.
    baseline: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    still_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    implemented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    implemented_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
