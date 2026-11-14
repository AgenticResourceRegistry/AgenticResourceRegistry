# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Component reports pair published activity facts with bounded interpretation.

Interpretation (outcomes against sessions without the component, attention items, a model-written reading) is
separately labelled and cannot change observed counts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.component_activity.queries import activity_summary

from .component_analysis import component_analysis
from .component_attention import attention_items
from .component_outcomes import component_outcomes
from .scope import InsightScope
from .sections import generate_component_sections

if TYPE_CHECKING:
    from models.insight_report import InsightReport


async def _interpret(report: InsightReport, db, summary: dict, coverage: dict, metrics: dict, narrative: dict) -> None:
    """Add what sessions looked like, what to look at, and the model's reading. The counts above are untouched."""
    outcomes, context = await component_outcomes(report, db)
    narrative["outcomes"] = outcomes
    narrative["attention"] = attention_items(report.component_type, summary, coverage, outcomes)
    narrative["analysis"] = await component_analysis(report, metrics, narrative["attention"], outcomes, context)


async def generate_component_content(report: InsightReport, db) -> dict:
    if (
        report.subject_type != "component"
        or report.component_type not in ("mcp", "skill", "hook")
        or not report.project_id
        or not report.component_id
    ):
        raise ValueError("Unsupported report subject")
    if report.component_type == "skill":
        return await _generate_skill_content(report, db)
    if report.component_type == "hook":
        return await _generate_hook_content(report, db)
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
    metrics = {
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
    }
    await _interpret(report, db, summary, coverage, metrics, narrative)
    return {
        "metrics": metrics,
        "narrative": narrative,
        "coverage": coverage,
        "sessions_analyzed": summary["present_sessions"],
    }


async def _generate_skill_content(report: InsightReport, db) -> dict:
    """Deterministic skill report: published skill evidence and its coverage only.

    No MCP call figures. Codex selection is not a separately confirmed load,
    and neither selection nor a load establishes that the skill was followed.
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
    metrics = {
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
    }
    narrative = generate_skill_sections(summary, coverage)
    await _interpret(report, db, summary, coverage, metrics, narrative)
    return {
        "metrics": metrics,
        "narrative": narrative,
        "coverage": coverage,
        "sessions_analyzed": summary["present_sessions"],
    }


def generate_skill_sections(summary: dict, coverage: dict) -> dict:
    """Plain-language conclusion and evidence breakdown, from counts alone."""
    state = coverage["attribution_state"]
    codex_only = set(summary["harness_distribution"]) == {"codex"}
    if state == "observed":
        if codex_only and summary["invocations"]:
            conclusion = (
                "A native Codex skill selection was recorded in measured sessions. "
                "This is not a separately confirmed skill load or proof that the skill was followed or helped."
            )
        elif summary["invocations"] and not summary["confirmed_loads"]:
            conclusion = (
                "A native skill selection or invocation was recorded in measured sessions. "
                "This does not establish a separately confirmed load or show the skill was followed or helped."
            )
        else:
            conclusion = (
                "The skill's instructions entered context in processed present sessions "
                "(a confirmed load or an invocation). This does not show the skill was followed or helped."
            )
        if "skill_scope_or_offer_not_verified" in coverage["reasons"]:
            conclusion += " Other installs or unverified offers remain unknown."
    elif state == "no_observed_skill_use":
        if "skill_scope_or_offer_not_verified" in coverage["reasons"]:
            conclusion = (
                "No observed selection, invocation or confirmed load in measured sessions; "
                "other installs or unverified offers remain unknown. This does not prove the skill was never used."
            )
        else:
            conclusion = (
                "No observed selection, invocation or confirmed load in measured present sessions; "
                "this does not prove the skill was never used."
            )
    elif summary["available_sessions"] and coverage["projection"]["projection_complete_sessions"]:
        conclusion = (
            "The skill was offered in processed, verified-present sessions, but those sessions do not establish "
            "a selection or separately confirmed load. No use conclusion can be drawn."
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


async def _generate_hook_content(report: InsightReport, db) -> dict:
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
    metrics = {
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
    }
    narrative = generate_hook_sections(summary, coverage)
    await _interpret(report, db, summary, coverage, metrics, narrative)
    return {
        "metrics": metrics,
        "narrative": narrative,
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
            f" In {count} {'session' if count == 1 else 'sessions'} the hook's agent ran headless, but this "
            f"hook's placement cannot run there; {'it is' if count == 1 else 'they are'} excluded."
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
