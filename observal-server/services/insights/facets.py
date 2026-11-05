# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Per-session facet extraction via LLM (Haiku).

Ported from pi /insights FACET_EXTRACT_PROMPT. Extracts structured metadata
from session transcripts: goals, outcomes, satisfaction, friction, instructions.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from ._deps import get_call_model, get_facets_model
from .scope import SessionKey

logger = structlog.get_logger(__name__)

FACET_VERSION = 1

FACET_PROMPT = """\
Analyze this session and extract structured facets.

CRITICAL GUIDELINES:

1. goal_categories: Count ONLY what the USER explicitly asked for.
   - DO NOT count autonomous exploration the assistant decided to do
   - ONLY count when user says "can you...", "please...", "I need...", "let's..."

2. user_satisfaction: Base ONLY on explicit user signals.
   - "Yay!", "great!", "perfect!" -> happy
   - "thanks", "looks good", "that works" -> satisfied
   - "ok, now let's..." (continuing without complaint) -> likely_satisfied
   - "that's not right", "try again" -> dissatisfied
   - "this is broken", "I give up" -> frustrated

3. friction_points: Be specific about what went wrong.
   - misunderstood_request: assistant interpreted the request incorrectly
   - wrong_approach: right goal, wrong solution method
   - buggy_code: code didn't work correctly
   - user_rejected_action: user said no/stop to a proposed action
   - excessive_changes: over-engineered or changed too much
   - slow_or_verbose: took too long or output too much text
   - tool_failed: a tool call errored
   - user_unclear: user's instructions were ambiguous
   - external_issue: problem outside the agent's control

4. repeated_instructions: direct instructions the user gave that should be remembered,
   e.g. "always show diffs before editing". Include only reusable instructions (not one-off requests).

5. If very short or just a warmup, use "warmup_minimal" for goal_categories.

SESSION:
{transcript}

RESPOND WITH ONLY A VALID JSON OBJECT:
{{
  "underlying_goal": "<what the user fundamentally wanted to accomplish>",
  "goal_categories": ["<from: debug_investigate, implement_feature, fix_bug, write_script_tool, refactor_code, configure_system, create_pr_commit, analyze_data, understand_codebase, write_tests, write_docs, deploy_infra, warmup_minimal>"],
  "outcome": "<fully_achieved | mostly_achieved | partially_achieved | not_achieved | unclear>",
  "user_satisfaction": "<frustrated | dissatisfied | likely_satisfied | satisfied | happy | unsure>",
  "agent_helpfulness": "<unhelpful | slightly_helpful | moderately_helpful | very_helpful | essential>",
  "session_type": "<single_task | multi_task | iterative_refinement | exploration | quick_question>",
  "complexity": "<trivial | low | medium | high | very_high>",
  "friction_points": [
    {{
      "type": "<type from list above>",
      "description": "<specific description of what happened>",
      "severity": "<blocking | major | minor>"
    }}
  ],
  "primary_success_factors": ["<from: fast_accurate_search, correct_code_edits, good_explanations, proactive_help, multi_file_changes, good_debugging>"],
  "tools_effective": ["<tool names that worked well>"],
  "tools_problematic": [{{"tool": "<name>", "reason": "<why>"}}],
  "repeated_instructions": ["<instructions the user repeats to the agent>"],
  "brief_summary": "<one sentence: what user wanted and whether they got it>"
}}"""


async def extract_facets(
    session_id: str,
    transcript: str,
    meta: dict,
) -> dict:
    """Extract structured facets from a session transcript."""
    if not transcript or len(transcript.strip()) < 50:
        return {}

    call_model = get_call_model()
    import services.dynamic_settings as ds

    model_override = await ds.get("insights.model_facets") or None

    prompt = FACET_PROMPT.format(transcript=transcript)

    try:
        result = await call_model(prompt, model_override=model_override, max_tokens=4096)
        if result and isinstance(result, dict):
            return result
        return {}
    except Exception as e:
        logger.error("facets_extraction_failed", session_id=session_id, error=str(e))
        return {}


async def store_facets(session: SessionKey, agent_id: str, facets: dict, db) -> None:
    """Persist only to the row for this exact scoped session."""
    import uuid as _uuid

    from sqlalchemy import select

    model = get_facets_model()
    stmt = select(model).where(
        model.project_id == session.project_id,
        model.user_id == session.user_id,
        model.harness == session.harness,
        model.session_id == session.session_id,
        model.facet_version == FACET_VERSION,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        existing.facets = facets
        existing.extracted_at = datetime.now(UTC)
    else:
        db.add(
            model(
                project_id=session.project_id,
                user_id=session.user_id,
                harness=session.harness,
                session_id=session.session_id,
                agent_id=_uuid.UUID(agent_id) if agent_id else None,
                facet_version=FACET_VERSION,
                facets=facets,
            )
        )
    await db.flush()


async def load_cached_facets_batch(sessions: list[SessionKey], db) -> dict[SessionKey, dict]:
    """Legacy unscoped cache entries are intentionally never eligible for reuse."""
    if not sessions:
        return {}

    from sqlalchemy import select, tuple_

    model = get_facets_model()
    stmt = select(model).where(
        tuple_(model.project_id, model.user_id, model.harness, model.session_id).in_(
            [session.as_tuple() for session in sessions]
        ),
        model.facet_version == FACET_VERSION,
    )
    result = await db.execute(stmt)
    return {
        SessionKey(row.project_id, row.user_id, row.harness, row.session_id): row.facets
        for row in result.scalars().all()
        if row.facets
    }


def aggregate_facets(all_facets: list[dict]) -> dict:
    """Aggregate per-session facets into summary statistics.

    Returns a dict suitable for the report's facets_summary field and
    for inclusion in the LLM data block.
    """
    if not all_facets:
        return {}

    goal_categories: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    satisfaction: dict[str, int] = {}
    helpfulness: dict[str, int] = {}
    session_types: dict[str, int] = {}
    friction_types: dict[str, int] = {}
    success_factors: dict[str, int] = {}
    tools_effective: dict[str, int] = {}
    tools_problematic: dict[str, int] = {}
    complexities: dict[str, int] = {}
    repeated_instructions_all: list[str] = []

    for f in all_facets:
        if not f:
            continue

        # Goal categories (list)
        for cat in f.get("goal_categories", []):
            goal_categories[cat] = goal_categories.get(cat, 0) + 1

        # Outcome
        oc = f.get("outcome", "unclear")
        outcomes[oc] = outcomes.get(oc, 0) + 1

        # Satisfaction
        sat = f.get("user_satisfaction", "unsure")
        satisfaction[sat] = satisfaction.get(sat, 0) + 1

        # Helpfulness
        hlp = f.get("agent_helpfulness", "moderately_helpful")
        helpfulness[hlp] = helpfulness.get(hlp, 0) + 1

        # Session type
        st = f.get("session_type", "single_task")
        session_types[st] = session_types.get(st, 0) + 1

        # Complexity
        cx = f.get("complexity", "medium")
        complexities[cx] = complexities.get(cx, 0) + 1

        # Friction points
        for fp in f.get("friction_points", []):
            ft = fp.get("type", "unknown")
            friction_types[ft] = friction_types.get(ft, 0) + 1

        # Success factors
        for sf in f.get("primary_success_factors", []):
            success_factors[sf] = success_factors.get(sf, 0) + 1

        # Tools
        for tool in f.get("tools_effective", []):
            name = tool if isinstance(tool, str) else str(tool)
            tools_effective[name] = tools_effective.get(name, 0) + 1
        for tp in f.get("tools_problematic", []):
            tool_name = tp.get("tool", tp) if isinstance(tp, dict) else str(tp)
            tools_problematic[tool_name] = tools_problematic.get(tool_name, 0) + 1

        # Repeated instructions
        for instr in f.get("repeated_instructions", []):
            if instr:
                repeated_instructions_all.append(instr)

    # Deduplicate instructions by frequency
    instruction_counts: dict[str, int] = {}
    for instr in repeated_instructions_all:
        key = instr.strip().lower()
        instruction_counts[key] = instruction_counts.get(key, 0) + 1

    repeated_summary = [
        {"instruction": instr, "frequency": count}
        for instr, count in sorted(instruction_counts.items(), key=lambda x: -x[1])
        if count >= 2
    ][:10]

    return {
        "sessions_with_facets": len(all_facets),
        "goal_categories": sorted(goal_categories.items(), key=lambda x: -x[1]),
        "outcomes": outcomes,
        "satisfaction": satisfaction,
        "helpfulness": helpfulness,
        "session_types": session_types,
        "complexity_distribution": complexities,
        "friction_types": sorted(friction_types.items(), key=lambda x: -x[1]),
        "success_factors": sorted(success_factors.items(), key=lambda x: -x[1]),
        "tools_effective": sorted(tools_effective.items(), key=lambda x: -x[1])[:10],
        "tools_problematic": sorted(tools_problematic.items(), key=lambda x: -x[1])[:10],
        "repeated_instructions": repeated_summary,
    }
