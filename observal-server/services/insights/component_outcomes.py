# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""How sessions went with a component present, against sessions without it.

Outcomes come from the same per-session facets agent reports use (goal outcome, satisfaction, friction),
read from the cache when a session already has them and extracted for a bounded number that do not.
The two cohorts differ in more than the component (task, user, agent), so every figure is an association
and the report says so. Nothing here shows the component caused anything.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from loguru import logger as optic

from services.layer_components.queries import absence_sample, presence_sample
from services.secrets_redactor import redact_secrets

from .facets import load_cached_facets_batch
from .scope import SessionKey
from .transcript import build_session_transcript

if TYPE_CHECKING:
    from models.insight_report import InsightReport

OUTCOMES_VERSION = 1
MAX_COHORT = 30  # sessions sampled per side
MAX_NEW_FACETS = 40  # model calls this one report may spend on sessions with no facets yet
MIN_PER_SIDE = 10  # a difference is only reported when both sides have this many known outcomes
MIN_GAP = 0.15  # and the achieved rates differ by at least this much
Z_THRESHOLD = 1.96
MAX_FRICTION_NOTES = 12
MAX_NOTE_CHARS = 160

COHORT_SCOPE = (
    "Sessions in this project in the same period, on the same harnesses, whose layer snapshot lacks this component."
)

_ACHIEVED = {"fully_achieved", "mostly_achieved"}
_NOT_ACHIEVED = {"partially_achieved", "not_achieved"}
_SATISFIED = {"satisfied", "happy", "likely_satisfied"}
_DISSATISFIED = {"frustrated", "dissatisfied"}
_SEVERITY_ORDER = {"blocking": 0, "major": 1, "minor": 2}


def summarize_cohort(total: int, facets: list[dict]) -> dict:
    """Counts for one side. ``total`` is the cohort size; ``facets`` are those of the sampled sessions that have any."""
    outcomes = [f.get("outcome") for f in facets]
    satisfaction = [f.get("user_satisfaction") for f in facets]
    friction_types: dict[str, int] = {}
    with_friction = blocking = tool_failed = 0
    for facet in facets:
        points = [p for p in facet.get("friction_points") or [] if isinstance(p, dict)]
        with_friction += bool(points)
        blocking += any(p.get("severity") == "blocking" for p in points)
        tool_failed += any(p.get("type") == "tool_failed" for p in points)
        for kind in {p.get("type") or "unknown" for p in points}:
            friction_types[kind] = friction_types.get(kind, 0) + 1
    return {
        "sessions": total,
        "analyzed": len(facets),
        "achieved": sum(o in _ACHIEVED for o in outcomes),
        "not_achieved": sum(o in _NOT_ACHIEVED for o in outcomes),
        "unclear": sum(o not in _ACHIEVED | _NOT_ACHIEVED for o in outcomes),
        "satisfied": sum(s in _SATISFIED for s in satisfaction),
        "dissatisfied": sum(s in _DISSATISFIED for s in satisfaction),
        "with_friction": with_friction,
        "blocking_friction": blocking,
        "tool_failed": tool_failed,
        "friction_types": [list(item) for item in sorted(friction_types.items(), key=lambda i: (-i[1], i[0]))[:5]],
    }


def known_outcomes(side: dict) -> int:
    return side["achieved"] + side["not_achieved"]


def outcome_gap(with_component: dict, without: dict) -> dict:
    """Whether the achieved rates differ enough to mention: both sides large enough, a real gap, and z >= 1.96."""
    known_with, known_without = known_outcomes(with_component), known_outcomes(without)
    if min(known_with, known_without) < MIN_PER_SIDE:
        return {"state": "insufficient", "min_per_side": MIN_PER_SIDE}
    rate_with = with_component["achieved"] / known_with
    rate_without = without["achieved"] / known_without
    pooled = (with_component["achieved"] + without["achieved"]) / (known_with + known_without)
    spread = math.sqrt(pooled * (1 - pooled) * (1 / known_with + 1 / known_without))
    difference = rate_with - rate_without
    significant = abs(difference) >= MIN_GAP and spread > 0 and abs(difference / spread) >= Z_THRESHOLD
    state = "no_difference" if not significant else ("higher" if difference > 0 else "lower")
    return {
        "state": state,
        "rate_with": round(rate_with, 3),
        "rate_without": round(rate_without, 3),
        "difference": round(difference, 3),
    }


def friction_notes(facets: list[dict]) -> list[dict]:
    """A few redacted friction descriptions, worst first, for the model to read. Never stored in the report."""
    notes = []
    for facet in facets:
        for point in facet.get("friction_points") or []:
            if isinstance(point, dict) and isinstance(point.get("description"), str) and point["description"].strip():
                notes.append(
                    {
                        "type": str(point.get("type") or "unknown")[:40],
                        "severity": point.get("severity") if point.get("severity") in _SEVERITY_ORDER else "minor",
                        "description": redact_secrets(point["description"].strip())[:MAX_NOTE_CHARS],
                    }
                )
    notes.sort(key=lambda note: (_SEVERITY_ORDER[note["severity"]], note["type"], note["description"]))
    return notes[:MAX_FRICTION_NOTES]


async def _with_facets(
    keys: list[SessionKey], extract: int, db, concurrency: int
) -> tuple[dict[SessionKey, dict], int]:
    """Cached facets for ``keys`` plus up to ``extract`` new ones. Returns them and how many were new."""
    from .generator import _extract_facets_batch

    facets = await load_cached_facets_batch(keys, db)
    missing = [key for key in keys if key not in facets][: max(extract, 0)]
    transcripts = {}
    for key in missing:
        text = await build_session_transcript(key)
        if text.strip():
            transcripts[key] = text
    if not transcripts:
        return facets, 0
    new = await _extract_facets_batch(
        transcripts=transcripts, session_metas={}, agent_id="", db=db, max_concurrent=concurrency
    )
    return facets | new, len(new)


async def component_outcomes(report: InsightReport, db) -> tuple[dict, dict]:
    """(stored outcomes, private context for the model). The context holds redacted friction notes and is never saved.

    A report with no requester (a scheduled one) reads cached facets only: nobody asked for it, so it spends no
    model calls. ``state`` is ``unavailable`` when a read or an extraction failed, so the owner regenerates
    instead of reading it as "no data".
    """
    import services.dynamic_settings as ds

    project, kind, component = report.project_id, report.component_type, str(report.component_id)
    version = str(report.component_version_id) if report.component_version_id else None
    period = (report.period_start, report.period_end)
    try:
        present_rows, present_total = await presence_sample(project, kind, component, version, period, limit=MAX_COHORT)
        harnesses = sorted({row["harness"] for row in present_rows})
        absent_rows, absent_total = await absence_sample(project, kind, component, period, harnesses, limit=MAX_COHORT)
        present = [SessionKey(project, r["user_id"], r["harness"], r["session_id"]) for r in present_rows]
        absent = [SessionKey(project, r["user_id"], r["harness"], r["session_id"]) for r in absent_rows]
        budget = MAX_NEW_FACETS if report.triggered_by else 0
        concurrency = int(await ds.get("insights.facet_concurrency") or 5)
        present_facets, spent = await _with_facets(present, budget, db, concurrency)
        absent_facets, _ = await _with_facets(absent, budget - spent, db, concurrency)
    except Exception as error:
        optic.warning(
            "component outcomes unavailable: component_id={} error_type={}", report.component_id, type(error).__name__
        )
        return {"version": OUTCOMES_VERSION, "state": "unavailable"}, {}
    with_facets = [present_facets[key] for key in present if key in present_facets]
    without_facets = [absent_facets[key] for key in absent if key in absent_facets]
    side_with = summarize_cohort(present_total, with_facets)
    side_without = summarize_cohort(absent_total, without_facets)
    outcomes = {
        "version": OUTCOMES_VERSION,
        "state": "assessed" if with_facets else "insufficient",
        "with": side_with | {"sampled": len(present)},
        "without": side_without | {"sampled": len(absent)},
        "gap": outcome_gap(side_with, side_without),
        "comparison": COHORT_SCOPE,
    }
    return outcomes, {"friction_notes": friction_notes(with_facets)}
