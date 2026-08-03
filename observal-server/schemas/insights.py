# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import uuid
from datetime import datetime

from pydantic import BaseModel

from models.insight_report import InsightReportStatus


class GenerateInsightRequest(BaseModel):
    period_days: int = 14
    agent_version: str | None = None
    comparison_agent_version: str | None = None
    version_scope: str | None = "canonical_and_dirty"


class ApplySuggestionsRequest(BaseModel):
    """Select which suggestions to apply. Omit a field or pass None to skip that category."""

    config_indices: list[int] | None = None
    feature_indices: list[int] | None = None
    pattern_indices: list[int] | None = None


class InsightReportListItem(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID | None = None
    agent_version: str | None = None
    version_scope: str | None = None
    status: InsightReportStatus
    period_start: datetime
    period_end: datetime
    sessions_analyzed: int
    created_at: datetime
    completed_at: datetime | None
    progress_phase: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    progress_percent: int = 0
    progress_message: str | None = None
    progress_updated_at: datetime | None = None
    model_config = {"from_attributes": True}


class InsightReportResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID | None = None
    agent_version: str | None = None
    version_scope: str | None = None
    comparison_agent_version_id: uuid.UUID | None = None
    comparison_agent_version: str | None = None
    triggered_by: uuid.UUID | None
    status: InsightReportStatus
    period_start: datetime
    period_end: datetime
    metrics: dict | None
    narrative: dict | None
    sessions_analyzed: int
    llm_model_used: str | None
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    progress_phase: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    progress_percent: int = 0
    progress_message: str | None = None
    progress_updated_at: datetime | None = None
    # V2+ fields
    previous_report_id: uuid.UUID | None = None
    aggregated_data: dict | None = None
    report_version: int = 3
    # Persisted analysis (previously discarded each run). Nullable: old
    # reports predate the columns, and a run may produce neither.
    version_impact: dict | None = None
    registry_offer: dict | None = None
    # Self-learn fields
    applied_at: datetime | None = None
    applied_items: dict | None = None
    model_config = {"from_attributes": True}
