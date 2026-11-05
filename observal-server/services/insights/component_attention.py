# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic "what to look at" items for a component report.

Each rule reads counts the report already holds and fires on a fixed threshold, so the same data always gives
the same items. An item states a fact with its numbers and, where there is a known remedy, one suggestion.
None claims the component caused an outcome or that a component with no recorded use was never used.
"""

from __future__ import annotations

from .component_outcomes import MIN_PER_SIDE, known_outcomes

MIN_CALLS_FOR_ERROR_RATE = 5
ERROR_RATE = 0.2
MIN_SESSIONS_FOR_RATE = 5
LOW_USE_RATE = 0.2
MIN_FAILED_RUNS = 3
FAILED_RUN_RATE = 0.5
HEADLESS_SHARE = 0.25
FRICTION_MIN_COUNT = 3
FRICTION_RATIO = 2.0
FRICTION_MIN_GAP = 0.15
# Friction a component cannot cause: the cause is outside the agent, or the request was unclear.
NOT_ATTRIBUTABLE_FRICTION = {"external_issue", "user_unclear"}


def _item(item_id: str, severity: str, title: str, detail: str, evidence: dict, suggestion: str | None = None) -> dict:
    return {
        "id": item_id,
        "severity": severity,
        "title": title,
        "detail": detail,
        "evidence": evidence,
        "suggestion": suggestion,
    }


def _processed(coverage: dict) -> int:
    return int(coverage["projection"]["projection_complete_sessions"])


def _mcp_rules(summary: dict, coverage: dict) -> list[dict]:
    items = []
    calls = int(summary["observed_calls"])
    errors = int(summary["result_states"].get("error", 0))
    processed = _processed(coverage)
    sessions = int(summary["observed_sessions"])
    if calls >= MIN_CALLS_FOR_ERROR_RATE and errors / calls >= ERROR_RATE:
        items.append(
            _item(
                "mcp_call_errors",
                "attention",
                "Many calls returned an error",
                f"{errors} of {calls} observed calls returned an error.",
                {"calls": calls, "errors": errors},
                "Find which tool fails and check that its description states the arguments it needs and their format.",
            )
        )
    if processed >= MIN_SESSIONS_FOR_RATE and sessions == 0 and coverage["attribution_state"] == "no_observed_calls":
        items.append(
            _item(
                "mcp_no_calls",
                "info",
                "No calls recorded where it was present",
                f"It was present in {processed} processed sessions and none had an attributed call. "
                "That does not show it was never used.",
                {"processed_sessions": processed, "sessions_with_calls": 0},
                "Check that the tool names and descriptions say when to use them. If the server is not needed, "
                "remove it from the agent.",
            )
        )
    elif processed >= 2 * MIN_SESSIONS_FOR_RATE and 0 < sessions / processed < LOW_USE_RATE:
        items.append(
            _item(
                "mcp_rarely_called",
                "info",
                "Calls were recorded in few sessions",
                f"{sessions} of {processed} processed sessions had an attributed call.",
                {"processed_sessions": processed, "sessions_with_calls": sessions},
                "Check that the tool descriptions say when each tool is the right one to use.",
            )
        )
    return items


def _skill_rules(summary: dict, coverage: dict) -> list[dict]:
    items = []
    processed = _processed(coverage)
    loads = int(summary["confirmed_loads"])
    available, loaded = summary["available_sessions"], int(summary["loaded_sessions"])
    if processed >= MIN_SESSIONS_FOR_RATE and loads == 0 and coverage["attribution_state"] == "no_observed_skill_use":
        items.append(
            _item(
                "skill_never_loaded",
                "attention",
                "Present but never loaded",
                f"It was present in {processed} processed sessions and none had a confirmed load or invocation. "
                "That does not show it was never used.",
                {"processed_sessions": processed, "confirmed_loads": 0},
                "Make the skill's description say when to use it, in the words a request would use.",
            )
        )
    elif available and available >= 2 * MIN_SESSIONS_FOR_RATE and 0 < loaded / available < LOW_USE_RATE:
        items.append(
            _item(
                "skill_rarely_loaded",
                "info",
                "Loaded in few of the sessions that offered it",
                f"It was offered in {available} sessions and loaded in {loaded}.",
                {"offered_sessions": available, "loaded_sessions": loaded},
                "Check that the skill's description says when to use it.",
            )
        )
    return items


def _hook_rules(summary: dict, coverage: dict) -> list[dict]:
    items = []
    eligibility = coverage["eligibility"]
    failures, blocks = int(summary["failures"]), int(summary["blocks"])
    recorded = int(summary["runs_with_output"]) + int(summary["silent_runs"] or 0) + failures + blocks
    if failures >= MIN_FAILED_RUNS and recorded and failures / recorded >= FAILED_RUN_RATE:
        items.append(
            _item(
                "hook_failing",
                "attention",
                "Most recorded runs failed",
                f"{failures} of {recorded} recorded runs failed.",
                {"recorded_runs": recorded, "failures": failures},
                "Check that its command exits 0 when nothing is wrong, and prints why when it fails.",
            )
        )
    headless, present = int(eligibility["headless_sessions"]), int(summary["present_sessions"])
    if headless and present and headless / present >= HEADLESS_SHARE:
        items.append(
            _item(
                "hook_headless",
                "attention",
                "Could not run in headless sessions",
                f"In {headless} of {present} present sessions the agent ran headless, where this hook's "
                "placement cannot run.",
                {"headless_sessions": headless, "present_sessions": present},
                "If it should also run headless, pull the agent with `observal agent pull --hooks=settings`: "
                "hooks kept in the agent file run only in interactive sessions.",
            )
        )
    unverified = int(eligibility.get("version_unverified_sessions") or 0)
    if unverified:
        items.append(
            _item(
                "hook_version_unverified",
                "info",
                "Some sessions are excluded for an unverified harness version",
                f"{unverified} sessions were recorded by a harness version whose hook records are not verified, "
                "so whether the hook ran there is unknown.",
                {"unverified_sessions": unverified},
            )
        )
    eligible = int(summary["eligible_sessions"])
    if eligible >= MIN_SESSIONS_FOR_RATE and not int(summary["sessions_with_recorded_run"]):
        items.append(
            _item(
                "hook_no_recorded_runs",
                "info",
                "No run recorded where it could run",
                f"None of {eligible} sessions where it could run has a recorded run. A hook that succeeds without "
                "printing leaves no record on some harnesses, so this does not show it never ran.",
                {"eligible_sessions": eligible, "sessions_with_recorded_run": 0},
            )
        )
    return items


def _percent(part: int, whole: int) -> int:
    return round(100 * part / whole)


def _outcome_rules(outcomes: dict | None) -> list[dict]:
    if not outcomes or outcomes.get("state") != "assessed":
        return []
    items = []
    side_with, side_without, gap = outcomes["with"], outcomes["without"], outcomes["gap"]
    if gap["state"] in ("lower", "higher"):
        known_with, known_without = known_outcomes(side_with), known_outcomes(side_without)
        lower = gap["state"] == "lower"
        items.append(
            _item(
                "outcomes_lower" if lower else "outcomes_higher",
                "attention" if lower else "info",
                "Sessions with it achieved their goal less often"
                if lower
                else "Sessions with it achieved their goal more often",
                f"Sessions with this component achieved their goal in {side_with['achieved']} of {known_with} with a "
                f"known outcome ({_percent(side_with['achieved'], known_with)}%), against {side_without['achieved']} "
                f"of {known_without} ({_percent(side_without['achieved'], known_without)}%) for other sessions in "
                "this project. The sessions differ in more than this component, so this is an association.",
                {
                    "achieved_with": side_with["achieved"],
                    "known_with": known_with,
                    "achieved_without": side_without["achieved"],
                    "known_without": known_without,
                },
            )
        )
    analyzed_with, analyzed_without = side_with["analyzed"], side_without["analyzed"]
    if min(analyzed_with, analyzed_without) >= MIN_PER_SIDE:
        theirs = dict(side_without["friction_types"])
        for kind, count in side_with["friction_types"]:
            if kind in NOT_ATTRIBUTABLE_FRICTION:
                continue
            other = theirs.get(kind, 0)
            share_with, share_without = count / analyzed_with, other / analyzed_without
            if (
                count >= FRICTION_MIN_COUNT
                and share_with - share_without >= FRICTION_MIN_GAP
                and share_with >= FRICTION_RATIO * share_without
            ):
                label = kind.replace("_", " ")
                items.append(
                    _item(
                        f"friction_{kind}",
                        "info",
                        f"More '{label}' friction in sessions with it",
                        f"'{label}' friction appeared in {count} of {analyzed_with} analyzed sessions with this "
                        f"component, against {other} of {analyzed_without} without it. This is an association.",
                        {
                            "friction_with": count,
                            "analyzed_with": analyzed_with,
                            "friction_without": other,
                            "analyzed_without": analyzed_without,
                        },
                    )
                )
    return items


_TYPE_RULES = {"mcp": _mcp_rules, "skill": _skill_rules, "hook": _hook_rules}


def attention_items(component_type: str, summary: dict, coverage: dict, outcomes: dict | None) -> list[dict]:
    """Items worth the owner's attention, those marked ``attention`` first, otherwise in rule order."""
    items = _TYPE_RULES[component_type](summary, coverage) + _outcome_rules(outcomes)
    return sorted(items, key=lambda item: item["severity"] != "attention")
