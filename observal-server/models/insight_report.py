# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, Text
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
    __table_args__ = (
        CheckConstraint(
            "(subject_type = 'agent' AND agent_id IS NOT NULL AND component_id IS NULL AND component_type IS NULL) OR "
            "(subject_type = 'component' AND agent_id IS NULL AND component_id IS NOT NULL AND "
            "component_type = 'mcp' AND project_id IS NOT NULL)",
            name="ck_insight_report_subject",
        ),
        Index(
            "ix_insight_reports_component_subject",
            "project_id",
            "component_type",
            "component_id",
            "component_version_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False, default="agent")
    project_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    component_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    component_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    component_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    component_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    component_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    coverage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
    llm_model_used: Mapped[str | None] = mapped_column(Text, nullable=True)
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
