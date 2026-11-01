# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Parallel section prompts for Agent Insights narrative generation.

Ground-up V4 rewrite modeled after pi /insights prompts.

Key differences from V3:
- Second-person voice ("you") throughout, not third-person analyst tone
- Prompts include session_summaries, friction_details, user_instructions
  so the LLM has real examples to cite (not just raw metrics)
- Interaction style section added (the single biggest quality differentiator)
- Suggestions split into config_additions, features_to_try, usage_patterns
  with copyable prompts and concrete code examples
- At-a-glance synthesis is coaching tone, not corporate summary
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import structlog

from ._deps import get_call_model
from .registry_match import catalog_block

if TYPE_CHECKING:
    from .registry_match import CatalogOffer

logger = structlog.get_logger(__name__)


async def _get_section_model() -> str | None:
    """Get the model for detailed section prompts (Opus by default)."""
    import services.dynamic_settings as ds

    return await ds.get("insights.model_sections") or None


async def _get_synthesis_model() -> str | None:
    """Get the model for synthesis/aggregation (Sonnet by default)."""
    import services.dynamic_settings as ds

    return await ds.get("insights.model_synthesis") or None


# ──────────────────────────────────────────────────────────────────────────────
# Section prompt templates
# ──────────────────────────────────────────────────────────────────────────────

SECTION_PREAMBLE = """You are writing part of a personal usage insights report for an AI coding agent's user.

Your reader is the developer who uses this agent every day. They want to understand their own patterns, what is working, what is failing, and what to try next.

Writing style:
- Use SECOND PERSON ("you", "your") throughout. Never "the user" or "the developer".
- Be DIRECT and SPECIFIC. Every sentence must reference actual data, specific sessions, or concrete observations.
- CONCISE. Scale output to the data volume: 1-3 sessions = brief. 10+ sessions = richer analysis.
- HONEST. If the agent failed tasks, say so clearly. Don't soften real problems.
- INSIGHTFUL. Surface patterns the reader wouldn't notice from raw numbers. Connect dots between metrics.
- NO FILLER. Never pad with generic observations or restate what metrics already show.
- Reference specific session examples from the SESSION SUMMARIES when available.
- Use **bold** for key insights in narrative text.

"""

SECTION_PROMPTS: dict[str, str] = {
    "what_they_work_on": """Analyze this usage data and identify project areas.

{data_block}

RESPOND WITH ONLY A VALID JSON OBJECT:
{{
  "what_they_work_on": {{
    "areas": [
      {{
        "name": "area name",
        "sessions": <number>,
        "description": "2-3 sentences about what was worked on and how the agent was used. Use 'you' not 'the user'."
      }}
    ]
  }}
}}

Rules:
- Maximum 6 areas, order by session count descending
- Derive areas from goal_categories, session summaries, and file paths/tools used
- Each area MUST have a real session count
- Group related goals into logical work areas (e.g. "PR review" + "code review" = one area)
- Description should mention specific tools, files, or patterns from the sessions""",
    "interaction_style": """Analyze this usage data and describe how this person interacts with their AI coding agent.

{data_block}

RESPOND WITH ONLY A VALID JSON OBJECT:
{{
  "interaction_style": {{
    "narrative": "2-3 paragraphs analyzing HOW you interact. Use second person 'you'. Describe patterns: do you iterate quickly or write detailed specs upfront? Do you interrupt often or let it run? Do you run parallel sessions? How do you handle friction: gentle correction or sharp redirection? Include specific examples from sessions. Use **bold** for key insights.",
    "key_pattern": "one sentence summary of the most distinctive interaction pattern, in quotes"
  }}
}}

Rules:
- This is the most personal section. It should feel like someone who has watched you work is describing your style.
- Reference specific friction events, repeated instructions, and session patterns.
- Note multi-session/parallel usage patterns if detected.
- Note the ratio of user messages to tool calls (sparse = directive, high = collaborative).
- Note how you respond to failures (do you course-correct, redirect, or restart?).
- Include the user's repeated instructions as evidence of their preferences.""",
    "usage_patterns": """Analyze this usage data and describe the agent's usage patterns.

{data_block}

RESPOND WITH ONLY A VALID JSON OBJECT:
{{
  "usage_patterns": {{
    "narrative": "1-3 sentences describing HOW the agent is used. Use 'you'. Reference session counts, duration patterns, and work types. No jargon like 'p90' or 'p99'.",
    "top_tasks": [
      {{"name": "task type", "count": <number>, "description": "one sentence"}}
    ],
    "tool_distribution": [
      {{"tool": "name", "calls": <number>, "error_rate": <percent as float>}}
    ],
    "session_profile": {{
      "avg_duration_minutes": <number>,
      "avg_tool_calls": <number>,
      "avg_prompts": <number>,
      "session_type": "most common type"
    }}
  }}
}}

Rules:
- Base everything on actual data, don't invent numbers
- Top tools by invocation count (max 10)
- Keep the narrative human-readable, no statistical jargon""",
    "what_works": """Analyze this usage data and identify what's working well.
Use second person ("you").

{data_block}

RESPOND WITH ONLY A VALID JSON OBJECT:
{{
  "what_works": {{
    "intro": "1 sentence summarizing the overall value delivered. Use 'you' not 'the user'.",
    "strengths": [
      {{
        "title": "short title (3-6 words)",
        "description": "2-3 sentences with specific evidence from metrics AND session examples. Use 'you'."
      }}
    ]
  }}
}}

Rules:
- Maximum 4 strengths. Empty array is valid if nothing genuinely worked well.
- Each MUST cite specific metrics, session counts, or examples from SESSION SUMMARIES.
- Focus on: tasks completed successfully, multi-file coordination, proactive help, cost efficiency on productive work.
- Reference specific things from the session summaries (e.g. "coordinated edits across 46 files", "identified a missing security gate without being asked").""",
    "friction_analysis": """Analyze this usage data and identify friction points.
Use second person ("you").

{data_block}

RESPOND WITH ONLY A VALID JSON OBJECT:
{{
  "friction_analysis": {{
    "intro": "1 sentence summarizing the overall friction pattern. Use 'you'.",
    "categories": [
      {{
        "title": "concrete category name",
        "severity": "high | medium | low",
        "description": "1-2 sentences explaining the problem. Use 'you'.",
        "examples": ["specific example with consequence from session data", "another example"],
        "evidence": "specific metrics as evidence string",
        "impact": "what this costs you in concrete terms"
      }}
    ]
  }}
}}

Rules:
- Rank by severity (high first), maximum 4 categories
- Include 2 examples per category drawn from SESSION SUMMARIES and FRICTION DETAILS
- HIGH: agent failed to complete tasks, you had to repeat instructions, or sessions ended without resolution
- MEDIUM: tool errors, wrong approaches that were corrected, inefficient patterns
- LOW: minor optimization opportunities
- Reference specific friction_detail entries and repeated_instructions when available""",
    "suggestions": """Analyze this usage data and suggest improvements.

{data_block}

RESPOND WITH ONLY A VALID JSON OBJECT:
{{
  "suggestions": {{
    "config_additions": [
      {{
        "action_type": "modify_prompt",
        "addition": "exact text to add to the agent's system prompt or AGENTS.md",
        "why": "1 sentence explaining why based on actual sessions",
        "where": "system_prompt | AGENTS.md | agent_config",
        "confidence": "high | medium | low | insufficient_data",
        "risk": "low | medium | high"
      }}
    ],
    "features_to_try": [
      {{
        "action_type": "reuse_existing_component | attach_registry_component | remove_component | create_new_skill | create_new_hook | no_action",
        "feature": "Skill | Hook | Prompt | MCP | Sandbox | Component",
        "name": "short-kebab-name (max 30 chars, e.g. 'scope-guard', 'pr-review', 'test-runner')",
        "existing_component_id": "the exact `id` copied from the reusable-components list when action_type is reuse/attach/remove, else null",
        "match_reason": "for reuse only: which observed problem this existing component solves, citing the data",
        "one_liner": "what it does in one sentence",
        "why_for_you": "why this would help YOU based on your sessions",
        "confidence": "high | medium | low | insufficient_data",
        "risk": "low | medium | high",
        "example": "required only for create_new_skill/create_new_hook; see format rules below"
      }}
    ],
    "usage_patterns": [
      {{
        "title": "short title (2-4 words)",
        "suggestion": "1-2 sentence summary",
        "detail": "3-4 sentences explaining how this applies to YOUR work, referencing specific sessions",
        "copyable_prompt": "a specific prompt to copy and try"
      }}
    ]
  }}
}}

Rules:
- config_additions: PRIORITIZE instructions from USER INSTRUCTIONS and REPEATED INSTRUCTIONS sections. These are things the user has ALREADY told the agent but it keeps forgetting. Maximum 7.
- features_to_try: 2-3 concrete suggestions.
- usage_patterns: 2-4 suggestions for how to prompt differently. Each must include a copyable prompt the user can try immediately.
- NEVER suggest vague improvements. Be specific: include the exact text, command, or config.
- If COMPONENT UTILIZATION marks a component unused/harmful, you may suggest action_type=remove_component with the existing component id/name and risk.
- Every suggested action must include confidence and risk.

REUSING WHAT ALREADY EXISTS (most important rule):
If a "Reusable Components Already In This Registry" section is present, it lists approved
components this agent is NOT yet using. Building something the user already owns wastes
their time, so:
- FIRST check whether a listed component genuinely solves an observed problem.
- If one does, use action_type=reuse_existing_component, set "existing_component_id" to that
  entry's `id` copied EXACTLY, set "feature" to its type, and explain the fit in "match_reason".
- Only suggest create_new_skill / create_new_hook when NOTHING listed fits. When you create
  something new despite the list being non-empty, say why in "why_for_you".
- ONLY suggest a match when it genuinely fits. A forced or approximate match is worse than
  no suggestion: it sends the user to install something that will not help. If nothing fits,
  do not reuse anything.
- NEVER invent an id. Any id not copied from the list is discarded, and the suggestion with it.
- MCP servers may be RECOMMENDED FOR REUSE from the list, but never invented: do not propose
  creating a new MCP server.

FEATURE FORMAT REQUIREMENTS:

For Skills, the "example" field MUST be a complete SKILL.md with frontmatter:
```
---
name: <name>
description: "<one_liner>"
version: 1.0.0
task_type: general
---

# <name>

<Step-by-step procedure the agent follows when this skill is invoked.
Be specific. Reference real tools (Read, Edit, Bash, etc.) and commands.>
```

For Hooks, the "example" field MUST be a valid shell script with this header comment:
```
# Hook: <event description>
#!/usr/bin/env bash
set -euo pipefail
<actual shell commands that accomplish the hook's purpose>
```

Valid hook events (use ONE in the "# Hook:" comment):
- "before tool use" -> PreToolUse (sync, runs before each tool call)
- "after tool use" -> PostToolUse (async, runs after each tool call)
- "session end" -> Stop (blocking, runs when session ends)
- "before prompt" -> UserPromptSubmit (sync, runs before user prompt is sent)
- "session start" -> SessionStart (async, runs when session begins)

The "name" field must be a short kebab-case identifier (max 30 chars).
Good: "scope-guard", "pre-commit-lint", "test-gate", "branch-cleanup"
Bad: "hook-that-checks-whether-files-are-in-scope", "custom-skill-for-testing"

COMPONENT-AWARE ANALYSIS:
If "Agent Configuration" is present:
- If users repeatedly attempt tasks the agent can't do, suggest a skill
- If the system prompt is missing guidance for common user requests, suggest specific prompt additions
- If tool_errors are high, suggest a validation hook
- For registry components, include: `observal agent add skill <id>`

SKILLS (include at least 1 skill suggestion, reused or new):
- Identify the user's most repetitive workflows from goal_categories and session summaries
- If a listed reusable component already covers that workflow, reuse it instead of writing a new one
- Otherwise suggest a specific skill with name, description, and the full SKILL.md content
- The skill content must be actionable steps using real tools, not pseudocode

HOOKS TO ADD (include if friction warrants it):
- If tool_errors are high, suggest a pre-execution validation hook
- If wrong_approach friction is common, suggest a confirmation gate hook
- If excessive_changes friction exists, suggest a scope-guard hook
- The hook script must be a real executable shell script, not pseudocode""",
    "usage_cost_analysis": """Analyze this agent's cost efficiency.

{data_block}

RESPOND WITH ONLY A VALID JSON OBJECT:
{{
  "usage_cost_analysis": {{
    "summary": "1-2 sentences: overall cost assessment. Use 'you'. No jargon.",
    "metrics": {{
      "total_cost_usd": <number or null>,
      "cost_per_session": <number or null>,
      "cost_per_prompt": <number or null>,
      "cache_efficiency_pct": <number 0-100 or null>,
      "most_expensive_model": "model name or null"
    }},
    "model_breakdown": [
      {{"model": "name", "cost_usd": <number>}}
    ],
    "opportunities": [
      {{
        "title": "opportunity",
        "description": "what to change",
        "estimated_savings": "e.g. '~30% reduction'"
      }}
    ]
  }}
}}

Rules:
- Keep it brief if costs are reasonable. Only go deep when there's a real problem.
- Maximum 3 opportunities, empty array is fine.
- If cache efficiency is above 90%, just note it's good and move on.
- Use plain language, not statistical jargon.""",
    "version_comparison": """Compare this selected agent version against the prior approved version.

{data_block}

Use the 'Prior Version Comparison Cohort' data if present. This cohort has its own deterministic metrics and facets; do not base improvement/degradation only on stale aggregate report numbers.

RESPOND WITH ONLY A VALID JSON OBJECT:
{{
  "version_comparison": {{
    "has_comparison": <true|false>,
    "current_version": "version string or null",
    "prior_version": "version string or null",
    "summary": "1-2 sentences stating whether the current version improved, degraded, or is inconclusive. Use 'you'.",
    "confidence": "high | medium | low | insufficient_data",
    "changes": [
      {{
        "metric": "metric name",
        "direction": "improved | degraded | stable | inconclusive",
        "current_value": "formatted value",
        "prior_value": "formatted value",
        "evidence": "specific evidence from current and prior cohorts",
        "attribution": "prompt changed | skill changed | hook changed | model changed | dirty user edit | external/environment | unknown",
        "risk": "low | medium | high | none"
      }}
    ]
  }}
}}

If no prior cohort exists, return {{"version_comparison": {{"has_comparison": false, "summary": "No prior approved version cohort was available.", "confidence": "insufficient_data", "changes": []}}}}""",
    "regression_detection": """Compare current and previous period metrics for this agent.

{data_block}

{previous_data_block}

RESPOND WITH ONLY A VALID JSON OBJECT:
{{
  "regression_detection": {{
    "has_previous_data": <true|false>,
    "summary": "1-2 sentences: what changed. Use 'you'.",
    "changes": [
      {{
        "metric": "metric name",
        "direction": "improved | degraded | stable",
        "previous_value": "formatted value",
        "current_value": "formatted value",
        "magnitude_pct": <number>,
        "significance": "meaningful | minor | noise",
        "likely_cause": "hypothesis about what caused the change"
      }}
    ]
  }}
}}

If no previous data: {{"regression_detection": {{"has_previous_data": false, "summary": "No previous period data available.", "changes": []}}}}""",
    "on_the_horizon": """Analyze this usage data and identify future opportunities as models become more capable.

{data_block}

RESPOND WITH ONLY A VALID JSON OBJECT:
{{
  "on_the_horizon": {{
    "intro": "1 sentence about the trajectory of AI-assisted development for this user",
    "opportunities": [
      {{
        "title": "short title (4-8 words)",
        "whats_possible": "2-3 ambitious sentences about autonomous agent workflows that would help this user specifically",
        "how_to_try": "1-2 sentences on how to start experimenting with this today",
        "copyable_prompt": "detailed prompt to try right now"
      }}
    ]
  }}
}}

Rules:
- Include 3 opportunities. Think ambitiously: autonomous workflows, parallel subagents, self-correcting pipelines, iterating against test suites.
- Each opportunity MUST reference the user's actual work from SESSION SUMMARIES.
- Each copyable_prompt should be a real, usable prompt the user can paste and run.
- Focus on workflows that would eliminate their top friction sources.""",
    "fun_ending": """Find ONE genuinely interesting or memorable moment from the session data.

{data_block}

RESPOND WITH ONLY A VALID JSON OBJECT:
{{
  "fun_ending": {{
    "headline": "a memorable QUALITATIVE moment from the transcripts, not a statistic. Something human, funny, or genuinely surprising.",
    "detail": "brief context about when or where this happened"
  }}
}}

Rules:
- Find something SPECIFIC and HUMAN. Not "the agent processed 1000 files" but "you discovered your WhatsApp bridge was broadcasting auth codes to all your contacts."
- If session summaries contain anything amusing, surprising, or distinctly human, use that.
- Avoid generic observations about volume or statistics.""",
    "version_impact": """Analyze configuration differences across users of this agent and their impact on outcomes.

{data_block}

If the data block contains a 'version_impact' key with groups, analyze how different
configurations (layer states) correlate with success or failure. This is CROSS-USER:
multiple users of the same agent have different configs. Find patterns.

RESPOND WITH ONLY A VALID JSON OBJECT (or null if no version_impact data):
{{
  "version_impact": {{
    "summary": "1-2 sentence overview: how many distinct configs exist, which performs best",
    "canonical_vs_dirty": {{
      "canonical_count": "<number of groups with unmodified installs>",
      "dirty_count": "<number of groups with user modifications>",
      "finding": "Which performs better and why (based on content differences)"
    }},
    "best_practices": [
      {{
        "pattern": "What the high-performing configs have in common (e.g. 'includes explicit error handling rules in CLAUDE.md')",
        "evidence": "Metric difference (e.g. '40% fewer tool errors, 0.82 success rate vs 0.61')",
        "recommendation": "Actionable suggestion for other users"
      }}
    ],
    "anti_patterns": [
      {{
        "pattern": "What the low-performing configs have in common (e.g. 'added overly restrictive rules that cause refusals')",
        "evidence": "Metric difference",
        "recommendation": "What to change or remove"
      }}
    ],
    "version_findings": [
      {{
        "component": "Component name and type",
        "finding": "e.g. 'users on skill v1.1.0 have 20% better outcomes than v1.0.0'"
      }}
    ]
  }}
}}

Rules:
- ANONYMIZE: never mention user names, emails, or identifiers. Say 'users with config X' not 'user john'.
- Focus on CONTENT differences: what rules, agents, or skills differ between high and low performers.
- If version pins differ between groups, highlight which versions correlate with better outcomes.
- canonical (unmodified from registry) vs dirty (user-edited) is a key axis of comparison.
- Be specific: quote actual rule content snippets that correlate with outcomes.
- Minimum 3 sessions per group to draw conclusions (already filtered).
- If all groups perform similarly, say so and skip the anti_patterns.""",
}

SYNTHESIS_PROMPT = """You're writing an "At a Glance" section for a usage insights report. The goal is to help the user understand their patterns and improve how they work with their AI coding agent.

Use this 4-part structure:

1. What's working
   What is the user's distinctive style and what impactful things have they done? Keep it high level. Don't be flattering or fluffy. Don't focus on which tools they use.

2. What's hindering you
   Split into two parts:
   (a) assistant-side failures: misunderstandings, wrong approaches, buggy output
   (b) user-side friction: insufficient context, environment issues, setup problems
   Be honest and constructive. Aim for patterns, not one-off incidents.

3. Quick wins to try
   Specific workflow changes or config additions they could adopt immediately. Avoid generic advice. Suggest concrete things from the suggestions section.

4. Ambitious workflows
   As models become significantly more capable, what workflows that feel out of reach today will become practical?

Keep each part to 2-3 sentences. Coaching tone, not report tone. Don't cite specific numbers or raw category names. Use "you" throughout.

RESPOND WITH ONLY A VALID JSON OBJECT:
{{
  "at_a_glance": {{
    "health": "healthy | mixed | concerning",
    "whats_working": "...",
    "whats_hindering": "...",
    "quick_win": "...",
    "ambitious_workflows": "..."
  }}
}}

DATA:
{data_block}

## Section Outputs
{sections_json}"""


# ──────────────────────────────────────────────────────────────────────────────
# Execution
# ──────────────────────────────────────────────────────────────────────────────

# Sections that require deeper reasoning get the "deep" model (Opus).
_DEEP_SECTIONS = {"suggestions", "interaction_style", "friction_analysis", "on_the_horizon"}

# Output size varies by section.
_SECTION_MAX_TOKENS: dict[str, int] = {
    "fun_ending": 1024,
}


async def _call_section(section_name: str, prompt: str, model: str | None = None) -> tuple[str, dict]:
    """Call the LLM for a single section, return (name, result)."""
    call_model = get_call_model()
    max_tokens = _SECTION_MAX_TOKENS.get(section_name, 16384)
    try:
        result = await call_model(prompt, model_override=model, max_tokens=max_tokens)
        if result and isinstance(result, dict):
            return section_name, result
        logger.warning("section_empty_response", section=section_name)
        return section_name, {}
    except RuntimeError:
        raise
    except Exception as e:
        logger.error("section_call_failed", section=section_name, error=str(e))
        return section_name, {}


def generate_component_sections(summary: dict, coverage: dict) -> dict:
    """Deterministic component subject variant; no LLM or invented evidence.

    Counts are copied only from the shared observability summary. Collision and
    unmatched counts describe the entire present-session candidate universe,
    not calls known to belong to the selected component.
    """
    calls = int(summary["observed_calls"])
    state = coverage["attribution_state"]
    if calls:
        conclusion = "Observed attributed calls in processed present sessions."
    elif state == "no_observed_calls":
        conclusion = "No attributed calls observed in processed present sessions; this does not prove no use."
    else:
        conclusion = "Activity attribution is unavailable for this cohort; no use conclusion can be drawn."
    return {
        "summary": conclusion,
        "evidence": {
            "present": summary["present_sessions"],
            "activated": summary["activation_actions"],  # configuration actions, not use
            "observed": calls,
            "inferred": "Not assessed; inferred evidence is not part of deterministic reports.",
            "not_observed": {
                "processed_present_sessions_without_attributed_calls": max(
                    0, coverage["projection"]["projection_complete_sessions"] - coverage["observed_sessions"]
                ),
                "note": "No attributed call in these processed sessions; this does not prove no use.",
            },
            "unknown": list(coverage["reasons"]),
            "cohort_collisions": coverage["calls"]["collision_calls"],
        },
        "synthesis": {
            "conclusion": conclusion,
            "coverage": {
                "attribution_state": state,
                "processed_present_sessions": coverage["projection"]["projection_complete_sessions"],
                "present_sessions": summary["present_sessions"],
                "limitations": coverage["limitations"],
            },
        },
    }


async def generate_sections(
    data_block: str,
    previous_report: dict | None = None,
    registry_offer: CatalogOffer | None = None,
) -> dict:
    """Run parallel section prompts + 1 synthesis, return combined narrative.

    Args:
        data_block: Formatted string with all metrics, facets, and session data.
        previous_report: Previous report's aggregated_data for regression comparison.
        registry_offer: Shortlist of reusable components (only for suggestions).

    Returns:
        Dict with structured section outputs for each narrative section.
    """
    call_model = get_call_model()

    # Build previous data block for regression section
    previous_data_block = ""
    if previous_report:
        previous_data_block = f"## Previous Period Metrics\n{json.dumps(previous_report, indent=2, default=str)}"
    else:
        previous_data_block = "## Previous Period Metrics\nNo previous period data available."

    offer_block = catalog_block(registry_offer) if registry_offer else ""

    # Resolve models
    deep_model = await _get_section_model()
    fast_model = await _get_synthesis_model()

    logger.info(
        "insight_sections_starting",
        deep_model=deep_model or "default",
        fast_model=fast_model or "default",
        catalog_items=registry_offer.item_count if registry_offer else 0,
    )

    # Build prompts for all sections
    section_prompts: dict[str, str] = {}
    for name, template in SECTION_PROMPTS.items():
        if name == "regression_detection":
            built = template.format(
                data_block=data_block,
                previous_data_block=previous_data_block,
            )
        elif name == "suggestions":
            built = template.format(data_block=data_block + offer_block)
        else:
            built = template.format(data_block=data_block)
        section_prompts[name] = SECTION_PREAMBLE + built

    # Route each section to the appropriate model tier
    tasks = [
        _call_section(
            name,
            prompt,
            model=deep_model if name in _DEEP_SECTIONS else fast_model,
        )
        for name, prompt in section_prompts.items()
    ]
    results = await asyncio.gather(*tasks)

    # Collect results
    narrative: dict = {}
    for name, result in results:
        if name in result:
            narrative[name] = result[name]
        elif result:
            first_value = next(iter(result.values()), None)
            narrative[name] = first_value
        else:
            narrative[name] = {} if name != "fun_ending" else {"headline": "", "detail": ""}

    # Run synthesis with all section outputs
    synthesis_prompt = SYNTHESIS_PROMPT.format(
        data_block=data_block,
        sections_json=json.dumps(narrative, indent=2, default=str),
    )
    try:
        synthesis_result = await call_model(synthesis_prompt, model_override=fast_model, max_tokens=4096)
        if synthesis_result and "at_a_glance" in synthesis_result:
            narrative["at_a_glance"] = synthesis_result["at_a_glance"]
        elif synthesis_result:
            narrative["at_a_glance"] = next(iter(synthesis_result.values()), {})
        else:
            narrative["at_a_glance"] = {}
    except Exception as e:
        logger.error("synthesis_failed", error=str(e))
        narrative["at_a_glance"] = {}

    return narrative
