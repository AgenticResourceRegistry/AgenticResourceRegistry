# SPDX-FileCopyrightText: 2026 The Observal Authors
# SPDX-License-Identifier: Apache-2.0

"""Typed schema for the insights pipeline's analytical payload.

The pipeline computes a rich set of analysis *before* it writes any narrative
prose: deterministic metrics, LLM-extracted facets, cross-user version-impact
analysis, and a deterministic registry shortlist. Historically most of this
was stored as untyped JSON blobs on the ``InsightReport`` (``metrics``,
``narrative``, ``aggregated_data``), while ``version_impact`` and
``registry_offer`` were computed and then discarded every run.

This module defines the canonical top-level shape of that payload so there is
a single source of truth for what one pipeline run produces, and so structural
drift (a whole section going missing, a dict arriving as a list) is detectable
at write time instead of silently persisted.

The schema is intentionally permissive on nested fields: ``metrics``,
``narrative`` and ``aggregated_data`` carry LLM-generated content whose exact
shape varies, so they are typed as ``dict`` rather than fully modelled.
Validation catches gross structural errors, not minor LLM output variance — a
report must never fail to persist because the model added an unexpected field.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = structlog.get_logger(__name__)


class InsightAnalysisPayload(BaseModel):
    """The analytical output of one insights pipeline run.

    Fields are grouped by how they were produced so a reader can tell
    deterministic data apart from LLM-inferred content:

    * Deterministic: ``metrics``, ``aggregated_data``, ``version_impact``,
      ``registry_offer``, ``facets_summary`` (the last is an aggregate of
      per-session LLM facets, but the aggregation itself is deterministic).
    * Generated: ``narrative`` (the LLM-written report sections, including
      ``suggestions``).
    """

    model_config = ConfigDict(extra="allow")

    # Deterministic metrics from ClickHouse (has ``rich`` / ``overview``).
    metrics: dict = Field(default_factory=dict)
    # LLM-generated narrative sections (including ``suggestions``).
    narrative: dict = Field(default_factory=dict)
    # Pre-existing roll-up: metrics + facets_summary + regressions +
    # cross_user_patterns. Kept as a dict for back-compat with readers.
    aggregated_data: dict = Field(default_factory=dict)
    # Cross-user layer/config correlation analysis. Previously discarded
    # after being folded into the LLM prompt; now persisted.
    version_impact: dict | None = None
    # Deterministic shortlist of registry components the agent does not yet
    # use. Previously discarded after being shown to the model; now persisted
    # so future consumers (duplicate detection, pull-time recs) can read it
    # without re-running the pipeline.
    registry_offer: dict | None = None
    # Aggregate of per-session facets.
    facets_summary: dict = Field(default_factory=dict)
    # Number of sessions the run analysed.
    sessions_analyzed: int = 0


def validate_payload(content: dict) -> dict | None:
    """Structurally validate a pipeline run's output.

    Returns a validated ``InsightAnalysisPayload`` dict on success, or ``None``
    on structural mismatch. Failures are *never* fatal: callers persist the
    raw ``content`` dict as a fallback so a schema mismatch cannot break
    report generation. The mismatch is logged so drift is observable.
    """
    try:
        payload = InsightAnalysisPayload.model_validate(content)
    except ValidationError as e:
        # Log only loc/type/msg — never the raw input, which can carry
        # LLM-generated narrative or session-derived text from ``content``.
        safe = [{"loc": err["loc"], "type": err["type"], "msg": err["msg"]} for err in e.errors(include_input=False)]
        logger.warning("insight_payload_validation_failed", errors=safe)
        return None
    return payload.model_dump()


__all__ = ["InsightAnalysisPayload", "validate_payload"]
