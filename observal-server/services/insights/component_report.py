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
        or report.component_type not in ("mcp", "skill", "hook")
        or not report.project_id
        or not report.component_id
    ):
        raise ValueError("Unsupported report subject")
    if report.component_type == "skill":
        return await _generate_skill_content(report)
    if report.component_type == "hook":
        return await _generate_hook_content(report)
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


async def _generate_skill_content(report: InsightReport) -> dict:
    """Deterministic skill report: published skill evidence and its coverage only.

    No MCP call figures and no model interpretation: a load or invocation shows
    the skill's instructions entered context, not that the skill helped.
    """
    from services.component_activity.skill_queries import skill_activity_summary

    summary = await skill_activity_summary(
        report.project_id,
        str(report.component_id),
        str(report.component_version_id) if report.component_version_id else None,
        (report.period_start, report.period_end),
        component_version=report.component_version,
    )
    coverage = summary["coverage"].model_dump(mode="json")
    return {
        "metrics": {
            key: summary[key]
            for key in (
                "present_sessions",
                "present_users",
                "available_sessions",
                "loaded_sessions",
                "confirmed_loads",
                "load_attempts",
                "invoked_sessions",
                "invocations",
                "harness_distribution",
                "version_distribution",
                "activation_actions",
            )
        },
        "narrative": generate_skill_sections(summary, coverage),
        "coverage": coverage,
        "sessions_analyzed": summary["present_sessions"],
    }


def generate_skill_sections(summary: dict, coverage: dict) -> dict:
    """Plain-language conclusion and evidence breakdown, from counts alone."""
    state = coverage["attribution_state"]
    if state == "observed":
        conclusion = (
            "The skill's instructions entered context in processed present sessions "
            "(a confirmed load or an invocation). This does not show the skill was followed or helped."
        )
    elif state == "no_observed_skill_use":
        conclusion = (
            "No confirmed load or invocation in processed present sessions; this does not prove the skill "
            "was never used."
        )
    else:
        conclusion = "Skill evidence is unavailable for this cohort; no use conclusion can be drawn."
    processed = coverage["projection"]["projection_complete_sessions"]
    return {
        "summary": conclusion,
        "evidence": {
            "present": summary["present_sessions"],
            "available": summary["available_sessions"],
            "confirmed_loads": summary["confirmed_loads"],
            "unconfirmed_load_attempts": summary["load_attempts"],
            "invoked": summary["invocations"],  # None: the harness records no invocation origin
            "activated": summary["activation_actions"],  # configuration actions, not use
            "unknown": list(coverage["reasons"]),
        },
        "synthesis": {
            "conclusion": conclusion,
            "coverage": {
                "attribution_state": state,
                "processed_present_sessions": processed,
                "present_sessions": summary["present_sessions"],
                "limitations": coverage["limitations"],
            },
        },
    }


async def _generate_hook_content(report: InsightReport) -> dict:
    """Deterministic hook report: recorded runs, whether the hook could run, and coverage.

    No MCP figures and no model interpretation. A recorded run shows the hook
    executed; where a harness leaves silent successes unrecorded, runs are a lower bound.
    """
    from services.component_activity.hook_queries import hook_activity_summary

    summary = await hook_activity_summary(
        report.project_id,
        str(report.component_id),
        str(report.component_version_id) if report.component_version_id else None,
        (report.period_start, report.period_end),
        component_version=report.component_version,
    )
    coverage = summary["coverage"].model_dump(mode="json")
    return {
        "metrics": {
            key: summary[key]
            for key in (
                "present_sessions",
                "present_users",
                "eligible_sessions",
                "sessions_with_recorded_run",
                "runs_with_output",
                "silent_runs",
                "failures",
                "blocks",
                "harness_distribution",
                "version_distribution",
                "activation_actions",
            )
        },
        "narrative": generate_hook_sections(summary, coverage),
        "coverage": coverage,
        "sessions_analyzed": summary["present_sessions"],
    }


def generate_hook_sections(summary: dict, coverage: dict) -> dict:
    """Plain-language conclusion from counts alone."""
    state = coverage["attribution_state"]
    eligibility = coverage["eligibility"]
    if state == "observed":
        conclusion = (
            "The hook has recorded runs in sessions where it could run. A recorded run shows it executed, "
            "not what it changed or whether it helped."
        )
    elif state == "no_recorded_runs":
        conclusion = "No recorded runs in sessions where the hook could run."
        if summary.get("silent_runs") is None or "silent_runs_not_recorded_on_some_harnesses" in coverage["reasons"]:
            conclusion += (
                " A harness in this cohort leaves no record of hooks that succeed without printing output, "
                "so this does not show the hook never ran."
            )
        else:
            conclusion += " The harnesses here record silent runs too, and none was recorded."
    else:
        conclusion = "There were no processed sessions where this hook could run; no conclusion can be drawn."
    if eligibility["headless_sessions"]:
        count = eligibility["headless_sessions"]
        conclusion += (
            f" In {count} {'session' if count == 1 else 'sessions'} the hook's agent ran headless, where agent "
            f"hooks do not run; {'it is' if count == 1 else 'they are'} excluded."
        )
    if eligibility.get("agent_unknown_sessions"):
        count = eligibility["agent_unknown_sessions"]
        conclusion += (
            f" In {count} subagent {'session' if count == 1 else 'sessions'} neither the subagent nor its parent "
            "session recorded which agent ran, so whether the hook could run there is unknown; "
            f"{'it is' if count == 1 else 'they are'} excluded."
        )
    if eligibility.get("version_unverified_sessions"):
        count = eligibility["version_unverified_sessions"]
        conclusion += (
            f" {count} {'session was' if count == 1 else 'sessions were'} recorded by a harness version whose hook "
            f"records have not been verified yet, so whether the hook ran there is unknown; "
            f"{'it is' if count == 1 else 'they are'} excluded."
        )
    return {
        "summary": conclusion,
        "evidence": {
            "present": summary["present_sessions"],
            "eligible": summary["eligible_sessions"],
            "sessions_with_recorded_run": summary["sessions_with_recorded_run"],
            "runs_with_output": summary["runs_with_output"],
            "silent_runs": summary.get("silent_runs"),
            "failures": summary["failures"],
            "blocks": summary["blocks"],
            "could_not_run": {key: eligibility[key] for key in ("headless_sessions", "agent_inactive_sessions")},
            "unknown": list(coverage["reasons"]),
        },
        "synthesis": {
            "conclusion": conclusion,
            "coverage": {
                "attribution_state": state,
                "eligible_sessions": summary["eligible_sessions"],
                "present_sessions": summary["present_sessions"],
                "limitations": coverage["limitations"],
            },
        },
    }
