# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class InsightReportStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class InsightReport(Base):
    __tablename__ = "insight_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[InsightReportStatus] = mapped_column(
        Enum(InsightReportStatus, name="insight_report_status"), default=InsightReportStatus.pending
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Version scope for version-aware reports. `agent_version` is a denormalized
    # snapshot used for telemetry filtering/display even if the AgentVersion row
    # is later changed or removed.
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_version: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    version_scope: Mapped[str | None] = mapped_column(String(50), nullable=True, default="canonical_and_dirty")
    comparison_agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_versions.id", ondelete="SET NULL"), nullable=True
    )
    comparison_agent_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Deterministic metrics from ClickHouse
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # LLM-generated narrative sections
    narrative: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    sessions_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    llm_model_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # V2 fields
    previous_report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("insight_reports.id", ondelete="SET NULL"), nullable=True
    )
    aggregated_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    report_version: Mapped[int] = mapped_column(Integer, default=1)

    # Analysis previously computed and discarded each run. Persisted now so
    # downstream consumers (duplicate detection, pull-time recommendations,
    # governance drift signals) can read them without re-running the pipeline.
    # ``version_impact`` is the cross-user layer/config correlation analysis;
    # ``registry_offer`` is the deterministic component shortlist the model was
    # shown. Both are nullable: old reports predate the columns, and a run may
    # legitimately produce no version impact or an empty offer.
    version_impact: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    registry_offer: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Self-learn fields
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_items: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Progress fields for long-running report generation.
    progress_phase: Mapped[str | None] = mapped_column(String(50), nullable=True, default="queued")
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
