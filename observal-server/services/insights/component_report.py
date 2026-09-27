# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Component reports pair published activity facts with bounded interpretation.

Interpretation is separately labelled and cannot change observed counts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.component_activity.queries import activity_summary

from .scope import InsightScope
from .sections import generate_component_sections

if TYPE_CHECKING:
    from models.insight_report import InsightReport


async def generate_component_content(report: InsightReport) -> dict:
    if (
        report.subject_type != "component"
        or report.component_type != "mcp"
        or not report.project_id
        or not report.component_id
    ):
        raise ValueError("Unsupported report subject")
    scope = InsightScope(
        subject_type="component",
        project_id=report.project_id,
        component_type=report.component_type,
        component_id=str(report.component_id),
        component_version_id=str(report.component_version_id) if report.component_version_id else None,
    )
    summary = await activity_summary(
        scope.project_id,
        scope.component_type,
        scope.component_id,
        scope.component_version_id,
        (report.period_start, report.period_end),
        component_version=report.component_version,
    )
    coverage = summary["coverage"].model_dump(mode="json")
    narrative = generate_component_sections(summary, coverage)
    from .component_evidence import component_findings

    narrative["component_analysis"] = await component_findings(report)
    return {
        "metrics": {
            "present_sessions": summary["present_sessions"],
            "present_users": summary["present_users"],
            "observed_sessions": summary["observed_sessions"],
            "observed_calls": summary["observed_calls"],
            "result_states": summary["result_states"],
            "harness_distribution": summary["harness_distribution"],
            "version_distribution": summary["version_distribution"],
            # Publication counters describe ALL candidate calls in the present
            # session cohort, not collisions assigned to this specific MCP.
            "cohort_collision_calls": coverage["calls"]["collision_calls"],
            "cohort_unmatched_calls": coverage["calls"]["unmatched_calls"],
            "activation_actions": summary["activation_actions"],
        },
        "narrative": narrative,
        "coverage": coverage,
        "sessions_analyzed": summary["present_sessions"],
    }
