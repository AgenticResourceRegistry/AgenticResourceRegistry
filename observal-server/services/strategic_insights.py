# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Strategic AI Insights generator.

Collects deployment-wide telemetry metrics, builds a data block, and calls the
configured LLM to produce business-language strategic recommendations similar
to a consulting report.
"""

from __future__ import annotations

import json

from loguru import logger as optic

from services.insights import call_model

SYSTEM_PREAMBLE = """You are the AI Strategy Advisor for an engineering organization that uses AI coding agents. You produce strategic insight reports for engineering leadership.

Your reader is a VP of Engineering, CTO, or Head of Platform who needs to understand:
- Where is AI delivering ROI and where is it wasting money?
- Which teams are underserving themselves by not adopting AI?
- What specific actions would save money or improve productivity this month?

Writing style:
- EXECUTIVE TONE. Clear, confident, direct. Like a McKinsey consultant who actually understands engineering.
- SPECIFIC. Every recommendation must cite actual numbers from the data. Never be vague.
- ACTIONABLE. Every insight must end with a concrete action someone can take this week.
- HONEST. If adoption is low, say so. If spend is wasteful, call it out.
- QUANTIFIED. Always include dollar amounts, percentages, or time savings.

Output valid JSON only. No markdown, no code fences."""

STRATEGIC_PROMPT = """Analyze this deployment's AI usage telemetry and produce strategic recommendations.

## Deployment Telemetry Data
{data_block}

Produce a JSON object with this EXACT structure:
{{
  "quick_wins": [
    {{
      "title": "<imperative headline, e.g. 'Stop routing simple tasks to Claude Opus'>",
      "detail": "<2-3 sentences with specific numbers explaining the problem and the fix>",
      "estimated_savings": "<dollar amount per month, e.g. '$3,800/mo'>",
      "effort": "low"
    }}
  ],
  "adoption_gaps": [
    {{
      "title": "<headline about the gap>",
      "detail": "<2-3 sentences with specific adoption %, user counts, and what they're missing>",
      "impact": "high"
    }}
  ],
  "platform_insight": {{
    "title": "<headline comparing harness/platform performance>",
    "detail": "<2-3 sentences comparing platforms with specific metrics like task time, success rate, sessions>"
  }},
  "model_insight": {{
    "title": "<headline about model cost-efficiency>",
    "detail": "<2-3 sentences comparing models with costs, success rates, and a clear recommendation>"
  }},
  "automation_opportunity": {{
    "title": "<headline about what % of work could be automated>",
    "detail": "<2-3 sentences about routine tasks that could run autonomous with approval gates>"
  }},
  "usage_pattern": {{
    "title": "<headline about power users vs underutilizers>",
    "detail": "<2-3 sentences about user distribution and what training the middle tier could unlock>"
  }}
}}

Rules:
- quick_wins: 2-4 items, each MUST have a dollar savings estimate. Only include if the data supports it.
- adoption_gaps: 1-3 items, only departments/teams below 50% adoption. If all are high, return empty array.
- platform_insight: compare harnesses/platforms by task completion time and success rate. If only one platform exists, focus on its performance.
- model_insight: compare model costs and recommend the best default. If only one model, say so.
- automation_opportunity: estimate what % of sessions are routine (low tokens, few events) and could run unattended.
- usage_pattern: describe the power user distribution and what the middle tier is missing.
- If data is insufficient for any section, still return the key with title "Insufficient data" and a brief explanation of what's needed.
- All numbers must come from the provided data. Do NOT invent statistics."""


async def generate_strategic_insights(metrics_data: dict) -> dict:
    """Generate LLM-powered strategic insights from deployment telemetry metrics.

    Args:
        metrics_data: Dict containing all the computed metrics from the
            strategic-insights and other exec endpoints.

    Returns:
        Dict with structured strategic recommendations.
    """
    optic.debug("generating")
    import services.dynamic_settings as ds

    configured_model = await ds.get("insights.model_synthesis") or await ds.get("insights.model_sections")
    if not configured_model:
        optic.warning("strategic_insights_no_model: no insights model configured")
        return {}

    data_block = _build_data_block(metrics_data)
    prompt = SYSTEM_PREAMBLE + "\n\n" + STRATEGIC_PROMPT.format(data_block=data_block)

    synthesis_model = await ds.get("insights.model_synthesis")
    model = synthesis_model or configured_model
    try:
        result = await call_model(prompt, model_override=model, max_tokens=4096)
        if result and isinstance(result, dict):
            optic.info("strategic_insights_generated: keys={}", list(result.keys()))
            return result
        optic.warning("strategic_insights_empty_response")
        return {}
    except Exception as e:
        optic.error("strategic_insights_failed: error={}", str(e))
        return {}


def _build_data_block(metrics: dict) -> str:
    """Build the data block string from collected metrics."""
    optic.trace("computing strategic insights from metrics")
    sections = []

    if metrics.get("adoption"):
        sections.append("## Adoption")
        sections.append(json.dumps(metrics["adoption"], indent=2))

    if metrics.get("agents"):
        sections.append("\n## Agent Inventory")
        sections.append(json.dumps(metrics["agents"], indent=2))

    if metrics.get("model_comparison"):
        sections.append("\n## Model Usage Comparison")
        sections.append(json.dumps(metrics["model_comparison"], indent=2))

    if metrics.get("department_gaps"):
        sections.append("\n## Department Adoption")
        sections.append(json.dumps(metrics["department_gaps"], indent=2))

    if metrics.get("platform_comparison"):
        sections.append("\n## Platform/harness Performance")
        sections.append(json.dumps(metrics["platform_comparison"], indent=2))

    if metrics.get("quick_win_candidates"):
        sections.append("\n## Cost Optimization Candidates")
        sections.append(json.dumps(metrics["quick_win_candidates"], indent=2))

    if metrics.get("developer_breakdown"):
        sections.append("\n## Developer Activity")
        sections.append(json.dumps(metrics["developer_breakdown"], indent=2))

    if metrics.get("velocity"):
        sections.append("\n## Development Velocity")
        sections.append(json.dumps(metrics["velocity"], indent=2))

    if metrics.get("cost_summary"):
        sections.append("\n## Cost Summary")
        sections.append(json.dumps(metrics["cost_summary"], indent=2))

    if metrics.get("automatable"):
        sections.append("\n## Automation Potential")
        sections.append(json.dumps(metrics["automatable"], indent=2))

    return "\n".join(sections)
