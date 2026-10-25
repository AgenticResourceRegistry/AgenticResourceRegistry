# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Insight report generation pipeline.

Ground-up V5 rewrite modeled after pi /insights.

Pipeline:
1. Extract deterministic session metadata from raw JSONL in ClickHouse
2. Build transcripts for top sessions (for facet extraction)
3. Extract facets via Haiku (goal, outcome, satisfaction, friction, instructions)
4. Aggregate metas + facets into a focused data block
5. Run 7 parallel section prompts + 1 synthesis via Opus/Sonnet
6. Return structured report
"""

from __future__ import annotations

import asyncio
import json

import structlog

from observal_shared.migration.constants import DEFAULT_PROJECT_ID

from ._deps import get_db_session
from .facets import aggregate_facets, extract_facets, load_cached_facets_batch, store_facets
from .registry_match import (
    RegistryScope,
    build_catalog,
    build_signals,
    count_reused,
    validate_reuse_suggestions,
)
from .sections import _get_section_model, _get_synthesis_model, generate_sections
from .session_meta_extractor import aggregate_metas, extract_all_session_metas
from .transcript import build_session_transcript

logger = structlog.get_logger(__name__)

REPORT_VERSION = "5.0"

# How many sessions get full transcript + facet extraction
MAX_FACET_SESSIONS = 50


async def generate_report_content(
    agent_name: str,
    agent_id: str | None = None,
    agent_version: str | None = None,
    comparison_agent_version: str | None = None,
    period_start: str = "",
    period_end: str = "",
    previous_metrics: dict | None = None,
    agent_config: dict | None = None,
    registry_scope: RegistryScope | None = None,
    db=None,
    progress_callback=None,
) -> dict:
    """Generate a complete insight report for an agent.

    This is the main entry point. The host app (observal-server) handles
    DB persistence of the result.
    """
    owns_session = False
    if db is None:
        session_factory = get_db_session()
        db = session_factory()
        owns_session = True

    try:
        return await _run_pipeline(
            agent_name=agent_name,
            agent_id=agent_id,
            agent_version=agent_version,
            comparison_agent_version=comparison_agent_version,
            period_start=period_start,
            period_end=period_end,
            previous_metrics=previous_metrics,
            agent_config=agent_config,
            registry_scope=registry_scope,
            db=db,
            progress_callback=progress_callback,
        )
    finally:
        if owns_session:
            await db.close()


async def _run_pipeline(
    agent_name: str,
    agent_id: str | None,
    agent_version: str | None,
    comparison_agent_version: str | None,
    period_start: str,
    period_end: str,
    previous_metrics: dict | None,
    agent_config: dict | None,
    registry_scope: RegistryScope | None,
    db=None,
    progress_callback=None,
) -> dict:
    """Core pipeline execution."""
    registry_scope = registry_scope or RegistryScope()

    logger.info(
        "insight_pipeline_started",
        agent=agent_name,
        agent_id=agent_id,
        agent_version=agent_version,
        period=f"{period_start} to {period_end}",
    )

    await _emit_progress(progress_callback, "extracting_metadata", 1, 9, "Extracting deterministic session metadata")

    # ── Step 1: Deterministic metadata extraction from raw JSONL ──────────
    # This reads actual session content from ClickHouse and computes:
    # lines added/removed, git commits, languages, tool errors, response
    # times, subagent usage, cost, etc.
    session_metas = await extract_all_session_metas(
        agent_id=agent_id or "",
        period_start=period_start,
        period_end=period_end,
        agent_name=agent_name,
        agent_version=agent_version,
    )

    if not session_metas:
        logger.warning("insight_no_sessions", agent_id=agent_id)
        return _empty_report()

    logger.info("insight_metas_extracted", count=len(session_metas))
    await _emit_progress(progress_callback, "building_transcripts", 2, 9, "Building session transcripts")

    # Aggregate deterministic stats
    agg = aggregate_metas(session_metas)

    session_ids = [m["session_id"] for m in session_metas]
    facets_by_session = await load_cached_facets_batch(session_ids, db)
    logger.info("insight_cached_facets_loaded", count=len(facets_by_session))

    # Build transcripts only for top sessions that still need facets.
    ranked = sorted(
        session_metas,
        key=lambda m: m.get("duration_seconds", 0) * sum(m.get("tool_counts", {}).values()),
        reverse=True,
    )
    top_sessions = [m for m in ranked if m["session_id"] not in facets_by_session][:MAX_FACET_SESSIONS]

    transcripts: dict[str, str] = {}
    if top_sessions:
        transcript_tasks = [build_session_transcript(m["session_id"]) for m in top_sessions]
        results = await asyncio.gather(*transcript_tasks, return_exceptions=True)
        for meta, result in zip(top_sessions, results, strict=False):
            if isinstance(result, str) and result.strip():
                transcripts[meta["session_id"]] = result

    logger.info("insight_transcripts_built", count=len(transcripts))
    await _emit_progress(progress_callback, "extracting_facets", 3, 9, "Extracting qualitative session facets")

    import services.dynamic_settings as ds

    max_concurrent = int(await ds.get("insights.facet_concurrency") or 5)

    # The facet model only runs when some session still needed facets, so it
    # belongs in models_used only then.
    facet_model_called = bool(transcripts)
    if transcripts:
        facets_by_session.update(
            await _extract_facets_batch(
                transcripts=transcripts,
                session_metas={m["session_id"]: m for m in session_metas},
                agent_id=agent_id or "",
                db=db,
                max_concurrent=max_concurrent,
            )
        )

    all_facets = [facets_by_session[sid] for sid in session_ids if facets_by_session.get(sid)]

    logger.info("insight_facets_extracted", count=len(all_facets))
    await _emit_progress(progress_callback, "analyzing_versions", 4, 9, "Analyzing versions, layers, and dirty cohorts")

    model_efficiency: list[dict] = []
    estimated_waste = 0.0
    model_tiers = agg.get("model_tiers", {})

    for meta in session_metas:
        session_model_usage = meta.get("model_usage", {})
        if not session_model_usage:
            continue

        facet = facets_by_session.get(meta["session_id"], {})
        if not facet:
            continue

        # Determine primary model (highest cost or most messages)
        primary = max(
            session_model_usage.items(),
            key=lambda x: (x[1].get("cost", 0), x[1].get("messages", 0)),
        )
        model_name, model_stats = primary
        tier = model_tiers.get(model_name, "mid")

        session_type = facet.get("session_type", "single_task")
        complexity = facet.get("complexity", "medium")
        outcome = facet.get("outcome", "unclear")

        is_simple = (
            session_type in ("quick_question", "single_task")
            or complexity in ("trivial", "low")
            or (meta.get("user_message_count", 0) <= 3 and meta.get("duration_seconds", 0) < 300)
        )
        is_complex = (
            session_type in ("multi_task", "iterative_refinement")
            or complexity in ("high", "very_high")
            or meta.get("user_message_count", 0) > 8
            or meta.get("files_modified", 0) > 5
        )
        poor_outcome = outcome in ("not_achieved", "partially_achieved")
        good_outcome = outcome in ("fully_achieved", "mostly_achieved")

        flag = "ok"
        reason = ""

        if tier == "subscription":
            # Quota pressure: heavy subscription model on trivial tasks
            model_lower = model_name.lower()
            is_heavy = bool(
                any(p in model_lower for p in ("opus", "pro", "medium", "large", "o1", "o3"))
                and not any(p in model_lower for p in ("small", "mini", "flash", "haiku", "lite"))
            )
            if is_simple and good_outcome and is_heavy and model_stats.get("messages", 0) > 3:
                flag = "quota_pressure"
                reason = (
                    f"Used {model_name} (subscription) for a {complexity} {session_type}. "
                    f"This model consumes more quota than lighter alternatives within the same plan."
                )
        elif tier == "high" and is_simple and good_outcome:
            flag = "overspend"
            reason = (
                f"Used {model_name} for a {complexity} {session_type} that succeeded. "
                f"A lower-tier model would likely suffice."
            )
            estimated_waste += model_stats.get("cost", 0) * 0.8
        elif tier == "low" and is_complex and poor_outcome:
            flag = "underspend"
            reason = (
                f"Used {model_name} for a {complexity} {session_type} that ended with {outcome}. "
                f"A more capable model may have succeeded."
            )
            estimated_waste += model_stats.get("cost", 0)
        elif tier == "high" and poor_outcome and model_stats.get("cost", 0) > 0.10:
            flag = "overspend"
            reason = (
                f"Spent ${model_stats.get('cost', 0):.2f} on {model_name} but outcome was {outcome}. "
                f"Tokens were consumed without reaching the goal."
            )
            estimated_waste += model_stats.get("cost", 0) * 0.5

        if flag != "ok":
            model_efficiency.append(
                {
                    "model": model_name,
                    "session_id": meta["session_id"],
                    "date": meta.get("start_time", "")[:10],
                    "cost": model_stats.get("cost", 0),
                    "outcome": outcome,
                    "session_type": session_type,
                    "complexity": complexity,
                    "flag": flag,
                    "reason": reason,
                }
            )

    model_efficiency.sort(key=lambda x: -x.get("cost", 0))
    model_efficiency = model_efficiency[:20]

    component_utilization = _analyze_component_utilization(agent_config or {}, session_metas, all_facets)

    # ── Step 3c: Facet a bounded prior-version cohort for A/B comparison ──
    comparison_cohort: dict | None = None
    if comparison_agent_version and comparison_agent_version != agent_version:
        try:
            prior_metas = await extract_all_session_metas(
                agent_id=agent_id or "",
                period_start=period_start,
                period_end=period_end,
                agent_name=agent_name,
                agent_version=comparison_agent_version,
            )
            if prior_metas:
                prior_agg = aggregate_metas(prior_metas)
                prior_ids = [m["session_id"] for m in prior_metas]
                prior_facets_by_session = await load_cached_facets_batch(prior_ids, db)
                prior_ranked = [
                    m
                    for m in sorted(
                        prior_metas,
                        key=lambda m: m.get("duration_seconds", 0) * sum(m.get("tool_counts", {}).values()),
                        reverse=True,
                    )
                    if m["session_id"] not in prior_facets_by_session
                ][: min(MAX_FACET_SESSIONS, 25)]
                prior_transcripts: dict[str, str] = {}
                prior_results = await asyncio.gather(
                    *[build_session_transcript(m["session_id"]) for m in prior_ranked],
                    return_exceptions=True,
                )
                for meta, result in zip(prior_ranked, prior_results, strict=False):
                    if isinstance(result, str) and result.strip():
                        prior_transcripts[meta["session_id"]] = result
                if prior_transcripts:
                    facet_model_called = True
                    prior_facets_by_session.update(
                        await _extract_facets_batch(
                            transcripts=prior_transcripts,
                            session_metas={m["session_id"]: m for m in prior_metas},
                            agent_id=agent_id or "",
                            db=db,
                            max_concurrent=max_concurrent,
                        )
                    )
                prior_facets = [prior_facets_by_session[sid] for sid in prior_ids if prior_facets_by_session.get(sid)]
                comparison_cohort = {
                    "current_version": agent_version,
                    "prior_version": comparison_agent_version,
                    "prior_sessions": len(prior_metas),
                    "prior_metrics": prior_agg,
                    "prior_facets_summary": aggregate_facets(prior_facets),
                    "prior_faceted_sessions": len(prior_facets),
                }
                logger.info(
                    "insight_prior_version_cohort_faceted",
                    current_version=agent_version,
                    prior_version=comparison_agent_version,
                    sessions=len(prior_metas),
                    facets=len(prior_facets),
                )
        except Exception as e:
            logger.warning("prior_version_comparison_failed", prior_version=comparison_agent_version, error=str(e))

    # ── Step 4: Build the data block (pi-style focused format) ────────────
    facets_summary = aggregate_facets(all_facets)
    cache_read_tokens = agg.get("total_cache_read_tokens", 0)
    cache_write_tokens = agg.get("total_cache_write_tokens", 0)
    input_tokens = agg.get("total_input_tokens", 0)
    cache_denominator = input_tokens + cache_read_tokens + cache_write_tokens
    cache_hit_rate_pct = round((cache_read_tokens / cache_denominator) * 100, 1) if cache_denominator else None
    data_block = _build_data_block(
        agent_name=agent_name,
        agg=agg,
        facets_summary=facets_summary,
        all_facets=all_facets,
        period_start=period_start,
        period_end=period_end,
        agent_config=agent_config,
        model_efficiency=model_efficiency,
        estimated_waste=estimated_waste,
        component_utilization=component_utilization,
    )
    if comparison_cohort:
        data_block += f"\n\n## Prior Version Comparison Cohort\n{json.dumps(comparison_cohort, indent=2, default=str)}"

    # ── Step 4b: Version impact analysis ─────────────────────────────────
    version_impact = None
    try:
        from .version_impact import build_version_impact_data

        version_impact = await build_version_impact_data(
            agent_id=agent_id or "",
            period_start=period_start,
            period_end=period_end,
            agent_name=agent_name,
            agent_version=agent_version,
            project_id=DEFAULT_PROJECT_ID,
        )
        if version_impact:
            data_block += f"\n\n## Version Impact\n{json.dumps(version_impact, indent=2)}"
            logger.info("version_impact_detected", groups=version_impact["group_count"])
    except Exception as e:
        logger.warning("version_impact_analysis_failed", error=str(e))

    # ── Step 4c: Shortlist reusable registry components ───────────────────
    # Driven by this report's own signals, so the model is offered things
    # that actually relate to the observed work rather than the whole
    # registry. Never fatal: an empty offer just omits the catalog block.
    # build_signals runs before build_catalog's own guard, so it needs its
    # own: a malformed facet shape must not take down the whole report.
    try:
        registry_signals = build_signals(agg=agg, facets_summary=facets_summary, agent_config=agent_config)
    except Exception as e:
        logger.warning("insight_signal_build_failed", error=str(e))
        registry_signals = ""
    registry_offer = await build_catalog(db, registry_scope, registry_signals)

    # ── Step 5: Generate narrative sections (7 parallel + 1 synthesis) ────
    await _emit_progress(progress_callback, "generating_sections", 7, 9, "Generating report sections")
    narrative = await generate_sections(
        data_block=data_block,
        previous_report=previous_metrics,
        registry_offer=registry_offer,
    )

    # Ground every reuse suggestion before anything renders it. An id the
    # model invented must never reach the UI as a real component link.
    try:
        narrative = await validate_reuse_suggestions(narrative, registry_offer, db, registry_scope)
    except Exception as e:
        logger.warning("insight_reuse_validation_failed", error=str(e))

    # Record what the reuse search actually did, so the report can say
    # "nothing matched" rather than leaving the reader to guess whether it
    # even ran.
    if isinstance(narrative, dict):
        narrative["registry_match"] = registry_offer.to_summary(reused=count_reused(narrative))

    await _emit_progress(progress_callback, "synthesizing", 8, 9, "Synthesizing report")

    logger.info(
        "insight_pipeline_complete",
        sessions=len(session_metas),
        facets=len(all_facets),
    )

    # ── Build final report structure ──────────────────────────────────────
    # metrics.rich is what the frontend reads for stat cards
    metrics = {
        "rich": {
            "total_sessions": agg.get("total_sessions", 0),
            "total_messages": agg.get("total_messages", 0),
            "active_hours": round(agg.get("total_duration_hours", 0), 1),
            "days_active": agg.get("days_active", 0),
            "lines_added": agg.get("total_lines_added", 0),
            "lines_removed": agg.get("total_lines_removed", 0),
            "files_modified": agg.get("total_files_modified", 0),
            "git_commits": agg.get("git_commits", 0),
            "git_pushes": agg.get("git_pushes", 0),
            "tool_errors": agg.get("total_tool_errors", 0),
            "interruptions": agg.get("total_interruptions", 0),
            "subagent_sessions": agg.get("sessions_using_subagent", 0),
            "mcp_sessions": agg.get("sessions_using_mcp", 0),
            "total_cost_usd": round(agg.get("total_cost", 0), 2),
            "total_credits": round(agg.get("total_credits", 0), 4),
            "total_input_tokens": agg.get("total_input_tokens", 0),
            "total_output_tokens": agg.get("total_output_tokens", 0),
            "total_cache_read_tokens": cache_read_tokens,
            "total_cache_write_tokens": cache_write_tokens,
            "cache_hit_rate_pct": cache_hit_rate_pct,
            "estimated_uncached_input_tokens": input_tokens + cache_read_tokens,
            "cache_tokens_saved": cache_read_tokens,
            "top_tools": agg.get("top_tools", [])[:15],
            "top_languages": agg.get("top_languages", [])[:10],
            "tool_error_categories": agg.get("tool_error_categories", {}),
            "projects": agg.get("projects", {}),
            "harnesses": agg.get("harnesses", agg.get("ides", [])),
            "ides": agg.get("ides", []),
            "sessions_with_tokens": agg.get("sessions_with_tokens", 0),
            "sessions_with_credits": agg.get("sessions_with_credits", 0),
            "model_usage": {
                m: {
                    "cost": u["cost"],
                    "messages": u["messages"],
                    "sessions": u["sessions"],
                    "tier": u.get("tier", "mid"),
                }
                for m, u in sorted(agg.get("model_usage", {}).items(), key=lambda x: -x[1]["cost"])
            },
            "model_efficiency": model_efficiency[:10],
            "estimated_waste_usd": round(estimated_waste, 2),
            "version_comparison_baseline": comparison_cohort,
            "canonical_dirty_summary": (version_impact or {}).get("canonical_dirty_summary"),
            "inspiration_candidates": (version_impact or {}).get("inspiration_candidates", []),
            "isolated_regressions": (version_impact or {}).get("isolated_regressions", []),
            "component_utilization": component_utilization,
        },
        "overview": {
            "total_sessions": agg.get("total_sessions", 0),
            "unique_users": 1,  # single-user context for now
        },
    }

    return {
        "metrics": metrics,
        "narrative": narrative,
        "sessions_analyzed": len(session_metas),
        "models_used": await _models_used(facet_model_called),
        "report_version": REPORT_VERSION,
        "regressions": [],
        "facets_summary": facets_summary,
        "cross_user_patterns": {},
    }


async def _models_used(facet_model_called: bool) -> list[str]:
    """Model ids the report's LLM calls ran on, in pipeline order.

    An unset synthesis or facet model is not "no model": call_model falls back
    to insights.model_sections, so that is the id recorded for the stage.
    """
    import services.dynamic_settings as ds

    section_model = await _get_section_model()
    models = [section_model, await _get_synthesis_model() or section_model]
    if facet_model_called:
        models.append(await ds.get("insights.model_facets") or section_model)
    return list(dict.fromkeys(m for m in models if m))


async def _emit_progress(progress_callback, phase: str, current: int, total: int, message: str) -> None:
    if not progress_callback:
        return
    try:
        await progress_callback(phase, current, total, message)
    except Exception as e:
        logger.debug("insight_progress_callback_failed", phase=phase, error=str(e))


def _analyze_component_utilization(agent_config: dict, session_metas: list[dict], all_facets: list[dict]) -> list[dict]:
    """Best-effort deterministic utilization for currently attached components."""
    components = []
    for name in agent_config.get("configured_skills", []) or []:
        components.append(("skill", str(name)))
    for name in agent_config.get("configured_hooks", []) or []:
        components.append(("hook", str(name)))
    if not components:
        return []

    searchable = "\n".join(
        [str(m.get("first_prompt", "")) for m in session_metas]
        + [str(f.get("brief_summary", "")) for f in all_facets if f]
        + [" ".join(m.get("tool_counts", {}).keys()) for m in session_metas]
    ).lower()
    results = []
    for comp_type, name in components:
        key = name.lower().replace("-", " ")
        compact = name.lower()
        mentions = searchable.count(key) + searchable.count(compact)
        status = "used" if mentions > 0 else "unused_or_unobserved"
        results.append(
            {
                "type": comp_type,
                "name": name,
                "observed_mentions": mentions,
                "status": status,
                "confidence": "medium" if mentions > 0 else "low",
            }
        )
    return results


async def _extract_facets_batch(
    transcripts: dict[str, str],
    session_metas: dict[str, dict],
    agent_id: str,
    db,
    max_concurrent: int = 5,
) -> dict[str, dict]:
    """Extract facets with concurrency limit.

    Only the LLM calls run concurrently. ``db`` is one AsyncSession, which
    does not allow concurrent use, so results are stored sequentially after
    the gather. Callers load cached facets beforehand and pass only the
    sessions that still need extraction.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _one(sid: str, transcript: str) -> dict:
        async with semaphore:
            return await extract_facets(sid, transcript, session_metas.get(sid, {}))

    session_ids = list(transcripts)
    tasks = [_one(sid, transcripts[sid]) for sid in session_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    facets_by_session: dict[str, dict] = {}
    for sid, result in zip(session_ids, results, strict=False):
        if isinstance(result, Exception):
            logger.warning("facet_task_exception", session_id=sid, error=str(result), type=type(result).__name__)
        elif isinstance(result, dict) and result:
            await store_facets(sid, agent_id, result, db)
            facets_by_session[sid] = result

    return facets_by_session


def _build_data_block(
    agent_name: str,
    agg: dict,
    facets_summary: dict,
    all_facets: list[dict],
    period_start: str,
    period_end: str,
    agent_config: dict | None = None,
    model_efficiency: list[dict] | None = None,
    estimated_waste: float = 0.0,
    component_utilization: list[dict] | None = None,
) -> str:
    """Build the DATA_BLOCK for section prompts.

    Modeled after pi's buildSharedDataBlock: a focused JSON summary plus
    SESSION SUMMARIES, FRICTION DETAILS, and USER INSTRUCTIONS as text.
    This is what the LLM actually reads. Keep it tight and information-dense.
    """

    # Top 8 helper
    def top8(rec: dict) -> list[list]:
        return sorted(rec.items(), key=lambda x: -x[1])[:8]

    # Core summary (like pi's buildSharedDataBlock JSON)
    summary = {
        "agent": agent_name,
        "period": f"{period_start} to {period_end}",
        "sessions": agg.get("total_sessions", 0),
        "sessions_with_facets": facets_summary.get("sessions_with_facets", 0),
        "date_range": agg.get("date_range", {}),
        "messages": agg.get("total_messages", 0),
        "hours": round(agg.get("total_duration_hours", 0)),
        "days_active": agg.get("days_active", 0),
        "commits": agg.get("git_commits", 0),
        "pushes": agg.get("git_pushes", 0),
        "cost_usd": round(agg.get("total_cost", 0), 2),
        "cache_efficiency": {
            "cache_read_tokens": agg.get("total_cache_read_tokens", 0),
            "cache_write_tokens": agg.get("total_cache_write_tokens", 0),
            "input_tokens": agg.get("total_input_tokens", 0),
            "cache_hit_rate_pct": (
                round(
                    agg.get("total_cache_read_tokens", 0)
                    / max(
                        1,
                        agg.get("total_input_tokens", 0)
                        + agg.get("total_cache_read_tokens", 0)
                        + agg.get("total_cache_write_tokens", 0),
                    )
                    * 100,
                    1,
                )
                if (
                    agg.get("total_input_tokens", 0)
                    or agg.get("total_cache_read_tokens", 0)
                    or agg.get("total_cache_write_tokens", 0)
                )
                else None
            ),
            "cache_tokens_saved": agg.get("total_cache_read_tokens", 0),
        },
        "lines_added": agg.get("total_lines_added", 0),
        "lines_removed": agg.get("total_lines_removed", 0),
        "files_modified": agg.get("total_files_modified", 0),
        "tool_errors": agg.get("total_tool_errors", 0),
        "interruptions": agg.get("total_interruptions", 0),
        "subagent_sessions": agg.get("sessions_using_subagent", 0),
        "mcp_sessions": agg.get("sessions_using_mcp", 0),
        "top_tools": agg.get("top_tools", [])[:10],
        "top_languages": agg.get("top_languages", [])[:10],
        "tool_error_categories": agg.get("tool_error_categories", {}),
        "projects": agg.get("projects", {}),
        # From facets aggregation
        "top_goals": facets_summary.get("goal_categories", [])[:10],
        "outcomes": facets_summary.get("outcomes", {}),
        "satisfaction": facets_summary.get("satisfaction", {}),
        "helpfulness": facets_summary.get("helpfulness", {}),
        "friction": facets_summary.get("friction_types", [])[:10],
        "success": facets_summary.get("success_factors", [])[:10],
        "session_types": facets_summary.get("session_types", {}),
        "complexity": facets_summary.get("complexity_distribution", {}),
    }

    # Model usage with pre-classified tiers
    model_usage = agg.get("model_usage", {})
    if model_usage:
        model_lines = []
        for m, u in sorted(model_usage.items(), key=lambda x: -x[1].get("cost", 0)):
            cpt = f"${u.get('cost_per_1k_tokens', 0):.4f}" if u.get("cost_per_1k_tokens") else "$0"
            model_lines.append(
                f"  {m}: {u.get('sessions', 0)} sessions, {u.get('messages', 0)} msgs, "
                f"${u.get('cost', 0):.2f} total, tier={u.get('tier', 'mid')}, $/1k-tok={cpt}"
            )
        summary["model_breakdown"] = model_lines

    # Multi-session detection
    if agg.get("multi_clauding"):
        summary["multi_clauding"] = agg["multi_clauding"]

    sections = [json.dumps(summary, indent=2)]

    # Agent configuration (for component-aware suggestions)
    if agent_config:
        sections.append(f"\n## Agent Configuration\n{json.dumps(agent_config, indent=2)}")

    # SESSION SUMMARIES (the key differentiator from old evals approach)
    if all_facets:
        summaries = []
        for f in all_facets[-50:]:
            if not f:
                continue
            brief = f.get("brief_summary", "")
            outcome = f.get("outcome", "unclear")
            helpfulness = f.get("agent_helpfulness", "unknown")
            if brief:
                summaries.append(f"- {brief} ({outcome}, {helpfulness})")

        if summaries:
            sections.append("\nSESSION SUMMARIES:\n" + "\n".join(summaries))

        # FRICTION DETAILS (specific examples the LLM can cite)
        friction_details = []
        for f in all_facets:
            if not f:
                continue
            for fp in f.get("friction_points", []):
                desc = fp.get("description", "")
                ftype = fp.get("type", "")
                if desc:
                    friction_details.append(f"- [{ftype}] {desc}")

        if friction_details:
            sections.append("\nFRICTION DETAILS:\n" + "\n".join(friction_details[:30]))

        # USER INSTRUCTIONS TO ASSISTANT (repeated patterns)
        user_instructions = []
        for f in all_facets:
            if not f:
                continue
            for instr in f.get("repeated_instructions", []):
                if instr:
                    user_instructions.append(f"- {instr}")

        if user_instructions:
            sections.append("\nUSER INSTRUCTIONS TO ASSISTANT:\n" + "\n".join(user_instructions[:20]))

    # Repeated instructions summary (aggregated)
    repeated = facets_summary.get("repeated_instructions", [])
    if repeated:
        sections.append(
            "\nREPEATED INSTRUCTIONS (by frequency):\n"
            + "\n".join(f'- "{r["instruction"]}" (frequency: {r["frequency"]})' for r in repeated[:10])
        )

    # Component utilization analysis (pre-computed, constrains suggestions)
    if component_utilization:
        sections.append("\nCOMPONENT UTILIZATION:\n" + json.dumps(component_utilization, indent=2))

    # Model efficiency analysis (pre-computed, helps LLM write cost section)
    if model_efficiency:
        eff_lines = []
        for e in model_efficiency[:10]:
            eff_lines.append(
                f"- [{e['flag']}] {e['model']} on {e['date']}: "
                f"${e.get('cost', 0):.2f}, {e['complexity']} {e['session_type']}, "
                f"outcome={e['outcome']}. {e['reason']}"
            )
        sections.append("\nMODEL EFFICIENCY FLAGS:\n" + "\n".join(eff_lines))
        if estimated_waste > 0:
            sections.append(f"\nEstimated waste from model mismatch: ${estimated_waste:.2f}")

    return "\n".join(sections)


def _empty_report() -> dict:
    """Return an empty report structure when no sessions exist."""
    return {
        "metrics": {},
        "narrative": {
            "at_a_glance": {
                "health": "unknown",
                "whats_working": "No session data available for this period.",
                "whats_hindering": "N/A",
                "quick_win": "N/A",
            },
        },
        "sessions_analyzed": 0,
        "models_used": [],
        "report_version": REPORT_VERSION,
        "regressions": [],
        "facets_summary": {},
        "cross_user_patterns": {},
    }
