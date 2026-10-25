# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent Insights API endpoints."""

from datetime import UTC, datetime, timedelta
from html import escape
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from loguru import logger as optic
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import check_listing_visibility_async, get_db, get_effective_agent_permission, require_role
from api.routes.agent.helpers import _load_agent
from api.routes.component_activity import _authorize as _authorize_component
from models.agent import Agent, AgentStatus, AgentVersion
from models.insight_meta_cache import InsightMetaCache
from models.insight_report import InsightReport, InsightReportStatus
from models.insight_session_facets import InsightSessionFacets
from models.user import User, UserRole
from observal_shared.migration.constants import DEFAULT_PROJECT_ID
from schemas.insights import (
    ApplySuggestionsRequest,
    GenerateComponentInsightRequest,
    GenerateInsightRequest,
    InsightReportListItem,
    InsightReportResponse,
)
from services.component_activity.queries import activity_summary
from services.insight_version_filters import agent_version_filter
from services.insights import INSIGHTS_AVAILABLE, render_report_html
from services.insights.batch import (
    REPORT_TIMEOUT_MINUTES,
    find_baseline_report,
    insight_report_job_id,
    lock_agent_reports,
)
from services.redis import _get_arq_pool

router = APIRouter(prefix="/api/v1/insights", tags=["insights"])


def _require_insights():
    """Raise 402 if the insights package is not installed."""
    if not INSIGHTS_AVAILABLE:
        raise HTTPException(
            status_code=402,
            detail="Insights are not available on this server.",
        )


def _require_agent_edit_access(agent: Agent, user: User) -> None:
    """Raise 403 unless user is admin or has owner/edit permission on the agent.

    Insight reports expose the agent's private session telemetry, so seeing the
    agent is not enough. Team membership is a visibility axis in the registry, not
    an ownership axis: a team member who is not the creator, a co-author, or an
    admin resolves the agent and is then refused here.
    """
    perm = get_effective_agent_permission(agent, user)
    if perm not in ("owner", "edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions for this agent")


async def _resolve_insights_agent(agent_id: str, db: AsyncSession, current_user: User) -> Agent:
    """Resolve an insights agent by UUID, ID prefix, or name."""
    agent = await _load_agent(
        db,
        agent_id,
        prefer_user_id=current_user.id,
        current_user=current_user,
        include_all_statuses=True,
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    _require_agent_edit_access(agent, current_user)
    return agent


async def _authorize_report_agent(report: InsightReport, db: AsyncSession, current_user: User) -> Agent:
    """Resolve the agent behind a report and apply visibility plus the edit gate.

    Reports carry a non-null agent_id with an ON DELETE CASCADE foreign key, so a
    missing agent row means the report is unreachable. That is reported as 404
    rather than silently skipping the authorization checks.
    """
    if getattr(report, "subject_type", "agent") != "agent":
        raise HTTPException(status_code=404, detail="Report not found")
    agent_result = await db.execute(select(Agent).where(Agent.id == report.agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent or not await check_listing_visibility_async(agent, current_user, db):
        raise HTTPException(status_code=404, detail="Report not found")
    _require_agent_edit_access(agent, current_user)
    return agent


async def _resolve_insight_agent_version(
    agent: Agent,
    db: AsyncSession,
    requested_version: str | None = None,
) -> AgentVersion:
    """Resolve the requested version or the latest approved version for a report."""
    stmt = select(AgentVersion).where(
        AgentVersion.agent_id == agent.id,
        AgentVersion.status == AgentStatus.approved,
    )
    if requested_version:
        stmt = stmt.where(AgentVersion.version == requested_version)
    else:
        stmt = stmt.order_by(AgentVersion.released_at.desc(), AgentVersion.created_at.desc()).limit(1)

    result = await db.execute(stmt)
    version = result.scalar_one_or_none()
    if not version:
        detail = (
            f"Approved version {requested_version!r} not found"
            if requested_version
            else "No approved agent version found"
        )
        raise HTTPException(status_code=404, detail=detail)
    return version


async def _previous_approved_version(
    agent: Agent, db: AsyncSession, current_version: AgentVersion
) -> AgentVersion | None:
    """Return a bounded default comparison target: the previous approved version."""
    from services.versioning import parse_semver

    current_parsed = parse_semver(current_version.version) or (0, 0, 0)
    stmt = select(AgentVersion).where(
        AgentVersion.agent_id == agent.id,
        AgentVersion.status == AgentStatus.approved,
        AgentVersion.id != current_version.id,
    )
    result = await db.execute(stmt)
    candidates = list(result.scalars().all())
    older: list[tuple[tuple[int, int, int], AgentVersion]] = []
    for candidate in candidates:
        parsed = parse_semver(candidate.version) or (0, 0, 0)
        if parsed < current_parsed:
            older.append((parsed, candidate))
    if not older:
        return None
    older.sort(key=lambda item: item[0], reverse=True)
    return older[0][1]


async def _in_flight_report(
    db: AsyncSession,
    *,
    agent: Agent,
    agent_version: AgentVersion,
    comparison_version: AgentVersion | None,
    version_scope: str,
    period_days: int,
    now: datetime,
) -> InsightReport | None:
    """Return a pending or running report for the same inputs, if there is one.

    Every report makes several model calls, so a repeated request joins the run
    already in progress instead of paying for an identical one. The per-agent
    advisory lock lasts until the caller's transaction ends, which covers the
    insert that follows a miss, so two concurrent requests cannot both miss.
    A report older than the reaper's timeout is dead, not in flight.
    """
    await lock_agent_reports(db, agent.id)
    stmt = (
        select(InsightReport)
        .where(
            InsightReport.agent_id == agent.id,
            InsightReport.agent_version_id == agent_version.id,
            InsightReport.comparison_agent_version_id.is_not_distinct_from(
                comparison_version.id if comparison_version else None
            ),
            InsightReport.version_scope == version_scope,
            InsightReport.status.in_([InsightReportStatus.pending, InsightReportStatus.running]),
            InsightReport.started_at >= now - timedelta(minutes=REPORT_TIMEOUT_MINUTES),
        )
        .order_by(InsightReport.created_at.desc())
    )
    result = await db.execute(stmt)
    period = timedelta(days=period_days)
    for report in result.scalars().all():
        if report.period_end - report.period_start == period:
            return report
    return None


async def _in_flight_component_report(
    db: AsyncSession,
    *,
    listing_id: UUID,
    component_type: str,
    component_version_id: UUID | None,
    period_days: int,
    now: datetime,
) -> InsightReport | None:
    """Return a pending or running report for the same component, version and period, if there is one.

    Same contract as ``_in_flight_report``: a repeated request joins the run in progress instead of
    paying for an identical set of model calls, and the lock covers the insert that follows a miss.
    """
    await lock_agent_reports(db, listing_id)
    result = await db.execute(
        select(InsightReport)
        .where(
            InsightReport.subject_type == "component",
            InsightReport.component_type == component_type,
            InsightReport.component_id == listing_id,
            InsightReport.component_version_id.is_not_distinct_from(component_version_id),
            InsightReport.status.in_([InsightReportStatus.pending, InsightReportStatus.running]),
            InsightReport.started_at >= now - timedelta(minutes=REPORT_TIMEOUT_MINUTES),
        )
        .order_by(InsightReport.created_at.desc())
    )
    period = timedelta(days=period_days)
    for report in result.scalars().all():
        if report.period_end - report.period_start == period:
            return report
    return None


async def _count_insight_sessions(
    *,
    agent: Agent,
    period_start: datetime,
    period_end: datetime,
    agent_version: str | None = None,
) -> int:
    """Count sessions for an agent, optionally scoped to telemetry agent_version."""
    from services.clickhouse import _query

    base_where = (
        "WHERE (agent_id = {agent_id:String} OR agent_id = {agent_name:String}) "
        "AND last_event_time >= {t_start:String} "
        "AND last_event_time <= {t_end:String} "
    )
    params = {
        "param_agent_id": str(agent.id),
        "param_agent_name": agent.name,
        "param_t_start": period_start.strftime("%Y-%m-%d %H:%M:%S"),
        "param_t_end": period_end.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if agent_version:
        base_where += f"AND {agent_version_filter()} "
        params["param_agent_version"] = agent_version

    count_sql = "SELECT count() AS cnt FROM session_stats_agg FINAL " + base_where + "FORMAT JSON"
    try:
        r = await _query(count_sql, params)
        count_data = r.json().get("data", []) if r.status_code == 200 else []
        return int(count_data[0]["cnt"]) if count_data else 0
    except Exception as e:
        optic.warning(
            "insight_session_count_agg_failed: agent={}, version={}, error={}",
            str(agent.id),
            agent_version,
            str(e),
        )

    # Safe fallback for older ClickHouse aggregates that do not yet expose agent_version.
    fallback_where = (
        "WHERE (agent_id = {agent_id:String} OR agent_id = {agent_name:String}) "
        "AND timestamp >= {t_start:String} "
        "AND timestamp <= {t_end:String} "
    )
    if agent_version:
        fallback_where += f"AND {agent_version_filter(nullable=True)} "
    fallback_sql = (
        "SELECT count(DISTINCT (project_id, user_id, harness, session_id)) AS cnt FROM session_events FINAL "
        + fallback_where
        + "FORMAT JSON"
    )
    try:
        r = await _query(fallback_sql, params)
        count_data = r.json().get("data", []) if r.status_code == 200 else []
        return int(count_data[0]["cnt"]) if count_data else 0
    except Exception as e:
        optic.warning(
            "insight_session_count_fallback_failed: agent={}, version={}, error={}",
            str(agent.id),
            agent_version,
            str(e),
        )
        return 0


@router.get("/status")
async def insights_status(current_user: User = Depends(require_role(UserRole.user))):
    """Return whether insights is available and properly configured."""
    import services.dynamic_settings as ds

    if not INSIGHTS_AVAILABLE:
        return {"available": False, "reason": "Insights service is unavailable."}

    model = await ds.get("insights.model_sections")
    if not model:
        return {"available": False, "reason": "No model configured. Set insights.model_sections in admin settings."}

    # If a direct API key is configured, skip AWS credential checks
    api_key = await ds.get("insights.api_key")

    # Check if AWS credentials are set and valid (for Bedrock models)
    if "anthropic" in model and not api_key:
        aws_key = await ds.get("insights.aws_access_key_id")
        aws_secret = await ds.get("insights.aws_secret_access_key")
        if not aws_key:
            return {
                "available": False,
                "reason": "AWS access key not configured. Set insights.aws_access_key_id in admin settings.",
            }
        if not aws_secret:
            return {
                "available": False,
                "reason": "AWS secret key not configured. Set insights.aws_secret_access_key in admin settings.",
            }

        # Validate credentials with a lightweight STS call
        try:
            import boto3

            region = await ds.get("insights.aws_region") or "us-east-1"
            sts = boto3.client(
                "sts",
                region_name=region,
                aws_access_key_id=aws_key,
                aws_secret_access_key=aws_secret,
            )
            sts.get_caller_identity()
        except Exception as e:
            error_str = str(e)
            if "InvalidClientTokenId" in error_str or "security token" in error_str:
                return {
                    "available": False,
                    "reason": "AWS access key is invalid. Update insights.aws_access_key_id in admin settings.",
                }
            if "SignatureDoesNotMatch" in error_str:
                return {
                    "available": False,
                    "reason": "AWS secret key is invalid. Update insights.aws_secret_access_key in admin settings.",
                }
            if "ExpiredToken" in error_str:
                return {
                    "available": False,
                    "reason": "AWS credentials have expired. Update credentials in admin settings.",
                }
            return {
                "available": False,
                "reason": "AWS credential check failed. Verify your access key and secret in admin settings.",
            }

    return {"available": True, "reason": None}


@router.get("/agents/{agent_id}/session-count")
async def agent_session_count(
    agent_id: str,
    agent_version: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Return the number of sessions available for insight generation."""
    agent = await _resolve_insights_agent(agent_id, db, current_user)
    version = await _resolve_insight_agent_version(agent, db, agent_version)
    now = datetime.now(UTC)
    period_start = now - timedelta(days=14)
    count = await _count_insight_sessions(
        agent=agent,
        period_start=period_start,
        period_end=now,
        agent_version=version.version,
    )

    return {"session_count": count, "agent_version": version.version, "agent_version_id": str(version.id)}


@router.post("/agents/{agent_id}/generate", response_model=InsightReportListItem)
async def generate_insight(
    agent_id: str,
    req: GenerateInsightRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Trigger generation of an insight report for an agent."""
    optic.trace("agent_id={}, req={}", agent_id, req)
    _require_insights()
    agent = await _resolve_insights_agent(agent_id, db, current_user)

    period_days = req.period_days if req else 14
    requested_version = req.agent_version if req else None
    version_scope = (req.version_scope if req else None) or "canonical_and_dirty"
    agent_version = await _resolve_insight_agent_version(agent, db, requested_version)
    comparison_version = (
        await _resolve_insight_agent_version(agent, db, req.comparison_agent_version)
        if req and req.comparison_agent_version
        else await _previous_approved_version(agent, db, agent_version)
    )
    # A version compared with itself has no prior cohort, so the report would
    # store a baseline its own version_comparison section says does not exist.
    if comparison_version and comparison_version.id == agent_version.id:
        raise HTTPException(
            status_code=422,
            detail=f"Comparison version must differ from the report version ({agent_version.version}).",
        )
    now = datetime.now(UTC)
    period_start = now - timedelta(days=period_days)

    # Check if there are any sessions for this agent/version in the period.
    session_count = await _count_insight_sessions(
        agent=agent,
        period_start=period_start,
        period_end=now,
        agent_version=agent_version.version,
    )

    if session_count == 0:
        raise HTTPException(
            status_code=422,
            detail=f"No sessions found for this agent version ({agent_version.version}) in the last {period_days} days. Cannot generate a report.",
        )

    in_flight = await _in_flight_report(
        db,
        agent=agent,
        agent_version=agent_version,
        comparison_version=comparison_version,
        version_scope=version_scope,
        period_days=period_days,
        now=now,
    )
    if in_flight:
        return InsightReportListItem.model_validate(in_flight)

    prev_report = await find_baseline_report(
        db,
        agent_id=agent.id,
        agent_version=agent_version.version,
        period_start=period_start,
        period_end=now,
    )

    report = InsightReport(
        subject_type="agent",
        agent_id=agent.id,
        triggered_by=current_user.id,
        status=InsightReportStatus.pending,
        period_start=period_start,
        period_end=now,
        started_at=now,
        previous_report_id=prev_report.id if prev_report else None,
        agent_version_id=agent_version.id,
        agent_version=agent_version.version,
        version_scope=version_scope,
        comparison_agent_version_id=comparison_version.id if comparison_version else None,
        comparison_agent_version=comparison_version.version if comparison_version else None,
        progress_phase="queued",
        progress_message="Queued for generation",
        progress_updated_at=now,
    )
    db.add(report)
    await db.flush()

    # Enqueue background job
    pool = await _get_arq_pool()
    await pool.enqueue_job("generate_insight_report", str(report.id), _job_id=insight_report_job_id(report.id))
    await db.commit()

    return InsightReportListItem.model_validate(report)


@router.get("/agents/{agent_id}/reports", response_model=list[InsightReportListItem])
async def list_reports(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """List insight reports for an agent, newest first."""
    optic.trace("agent_id={}", agent_id)
    _require_insights()
    agent = await _resolve_insights_agent(agent_id, db, current_user)

    stmt = (
        select(InsightReport)
        .where(InsightReport.agent_id == agent.id)
        .order_by(InsightReport.created_at.desc())
        .limit(20)
    )
    result = await db.execute(stmt)
    reports = result.scalars().all()
    return [InsightReportListItem.model_validate(r) for r in reports]


# Explicit allowlist per section: a future prompt or section version needs its own privacy review.
# component_analysis v3/v4 is call-only evidence; outcomes v1 holds counts; analysis v1 is checked model text
# written from counts and redacted friction notes (never prompts, arguments or transcripts).
_SAFE_SECTION_VERSIONS = {"component_analysis": (3, 4), "outcomes": (1,), "analysis": (1,)}


def _safe_component_narrative(report: InsightReport) -> dict | None:
    """Deliver the reviewed sections of a component narrative; suppress any other version."""
    narrative = report.narrative
    if not isinstance(narrative, dict):
        return None
    safe = {}
    for key, value in narrative.items():
        versions = _SAFE_SECTION_VERSIONS.get(key)
        if versions is not None and not (
            isinstance(value, dict) and type(value.get("version")) is int and value["version"] in versions
        ):
            continue
        safe[key] = value
    return safe


async def _authorize_report(report: InsightReport, db: AsyncSession, user: User) -> None:
    if getattr(report, "subject_type", "agent") == "component":
        # Resolve current visibility and ownership again, never trust a saved name.
        await _authorize_component(
            report.component_type, str(report.component_id), report.component_version_id, db, user
        )
    else:
        await _authorize_report_agent(report, db, user)


@router.post("/components/{component_type}/{identifier:path}/generate", response_model=InsightReportListItem)
async def generate_component_insight(
    component_type: str,
    identifier: str,
    req: GenerateComponentInsightRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    request = req or GenerateComponentInsightRequest()
    if not 1 <= request.period_days <= 90:
        raise HTTPException(status_code=422, detail="period_days must be between 1 and 90")
    listing, ref, version_label = await _authorize_component(
        component_type,
        identifier,
        request.component_version_id,
        db,
        current_user,
    )
    now = datetime.now(UTC)
    start = now - timedelta(days=request.period_days)
    if component_type in ("skill", "hook"):
        from services.component_activity import hook_queries, skill_queries

        read = skill_queries.skill_activity_summary if component_type == "skill" else hook_queries.hook_activity_summary
        summary = await read(
            DEFAULT_PROJECT_ID, str(listing.id), ref.component_version_id, (start, now), component_version=version_label
        )
    else:
        summary = await activity_summary(
            DEFAULT_PROJECT_ID,
            component_type,
            str(listing.id),
            ref.component_version_id,
            (start, now),
            component_version=version_label,
        )
    coverage = summary["coverage"].model_dump(mode="json")
    if summary["present_sessions"] == 0:
        raise HTTPException(status_code=422, detail={"message": "No verified present sessions", "coverage": coverage})
    in_flight = await _in_flight_component_report(
        db,
        listing_id=listing.id,
        component_type=component_type,
        component_version_id=request.component_version_id,
        period_days=request.period_days,
        now=now,
    )
    if in_flight:
        return InsightReportListItem.model_validate(in_flight)
    report = InsightReport(
        subject_type="component",
        agent_id=None,
        project_id=DEFAULT_PROJECT_ID,
        component_type=component_type,
        component_id=listing.id,
        component_version_id=request.component_version_id,
        component_version=version_label,
        component_name=ref.qualified_name,
        coverage=coverage,
        triggered_by=current_user.id,
        status=InsightReportStatus.pending,
        period_start=start,
        period_end=now,
        started_at=now,
        progress_phase="queued",
        progress_message="Queued for generation",
        progress_updated_at=now,
    )
    db.add(report)
    await db.flush()
    pool = await _get_arq_pool()
    await pool.enqueue_job("generate_insight_report", str(report.id), _job_id=insight_report_job_id(report.id))
    await db.commit()
    return InsightReportListItem.model_validate(report)


@router.get("/components/{component_type}/{identifier:path}/reports", response_model=list[InsightReportListItem])
async def list_component_reports(
    component_type: str,
    identifier: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
    before_created_at: datetime | None = Query(None),
    before_id: UUID | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    listing, _, _ = await _authorize_component(component_type, identifier, None, db, current_user)
    if (before_created_at is None) != (before_id is None) or (
        before_created_at is not None and before_created_at.tzinfo is None
    ):
        raise HTTPException(status_code=422, detail="A report cursor needs a timezone-aware date and report ID")
    stmt = select(InsightReport).where(
        InsightReport.subject_type == "component",
        InsightReport.project_id == DEFAULT_PROJECT_ID,
        InsightReport.component_type == component_type,
        InsightReport.component_id == listing.id,
    )
    if before_created_at is not None:
        stmt = stmt.where(
            or_(
                InsightReport.created_at < before_created_at,
                and_(InsightReport.created_at == before_created_at, InsightReport.id < before_id),
            )
        )
    rows = await db.execute(stmt.order_by(InsightReport.created_at.desc(), InsightReport.id.desc()).limit(limit))
    return [InsightReportListItem.model_validate(row) for row in rows.scalars().all()]


@router.delete("/components/{component_type}/{identifier:path}/reports")
async def clear_component_reports(
    component_type: str,
    identifier: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    listing, _, _ = await _authorize_component(component_type, identifier, None, db, current_user)
    result = await db.execute(
        delete(InsightReport).where(
            InsightReport.subject_type == "component",
            InsightReport.project_id == DEFAULT_PROJECT_ID,
            InsightReport.component_type == component_type,
            InsightReport.component_id == listing.id,
        )
    )
    await db.commit()
    return {"deleted_reports": result.rowcount}


@router.get("/reports/{report_id}", response_model=InsightReportResponse)
async def get_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Get a single insight report by ID."""
    optic.trace("report_id={}", report_id)
    stmt = select(InsightReport).where(InsightReport.id == report_id)
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    await _authorize_report(report, db, current_user)

    response = InsightReportResponse.model_validate(report)
    if response.subject_type == "component":
        response.narrative = _safe_component_narrative(report)
    return response


@router.get("/reports/{report_id}/export/html", response_class=HTMLResponse)
async def export_report_html(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Export an insight report as a self-contained HTML document."""
    optic.trace("report_id={}", report_id)
    stmt = select(InsightReport).where(InsightReport.id == report_id)
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    await _authorize_report(report, db, current_user)

    if report.status != InsightReportStatus.completed:
        raise HTTPException(status_code=400, detail="Report is not yet completed")
    if getattr(report, "subject_type", "agent") == "component":
        import json

        title = escape(report.component_name or "Component")
        payload = escape(
            json.dumps(
                {
                    "metrics": report.metrics,
                    "coverage": report.coverage,
                    **{
                        key: (_safe_component_narrative(report) or {}).get(key)
                        for key in ("summary", "component_analysis", "outcomes", "attention", "analysis")
                    },
                },
                indent=2,
            )
        )
        return HTMLResponse(
            content=f'<!doctype html><html lang="en"><meta charset="utf-8">'
            f"<title>{title} · Insights</title><body><h1>{title}</h1><pre>{payload}</pre></body></html>",
            headers={"Content-Disposition": f'attachment; filename="insight-report-{report_id[:8]}.html"'},
        )

    # Build report dict for the renderer
    report_data = {
        "id": str(report.id),
        "agent_id": str(report.agent_id),
        "agent_version": report.agent_version,
        "comparison_agent_version": report.comparison_agent_version,
        "status": report.status.value if hasattr(report.status, "value") else str(report.status),
        "period_start": report.period_start,
        "period_end": report.period_end,
        "metrics": report.metrics,
        "narrative": report.narrative,
        "sessions_analyzed": report.sessions_analyzed,
    }

    html_content = render_report_html(report_data)

    return HTMLResponse(
        content=html_content,
        headers={
            "Content-Disposition": f'attachment; filename="insight-report-{report_id[:8]}.html"',
        },
    )


@router.delete("/agents/{agent_id}/reports")
async def clear_agent_reports(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Delete all insight reports and cached data for an agent."""
    optic.trace("agent_id={}", agent_id)
    _require_insights()
    agent = await _resolve_insights_agent(agent_id, db, current_user)

    # Delete reports
    report_result = await db.execute(delete(InsightReport).where(InsightReport.agent_id == agent.id))

    # Delete cached session facets
    facets_result = await db.execute(delete(InsightSessionFacets).where(InsightSessionFacets.agent_id == agent.id))

    # Delete meta cache
    cache_result = await db.execute(delete(InsightMetaCache).where(InsightMetaCache.agent_id == agent.id))
    await db.commit()

    return {
        "deleted_reports": report_result.rowcount,
        "deleted_facets": facets_result.rowcount,
        "deleted_cache": cache_result.rowcount,
    }


@router.delete("/reports/{report_id}")
async def delete_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Delete a single insight report."""
    optic.trace("report_id={}", report_id)
    stmt = select(InsightReport).where(InsightReport.id == report_id)
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    await _authorize_report(report, db, current_user)

    await db.delete(report)
    await db.commit()

    return {"deleted": True, "report_id": report_id}


@router.post("/reports/{report_id}/apply")
async def apply_report_suggestions(
    report_id: str,
    body: ApplySuggestionsRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Apply selected insight suggestions as pending registry items.

    Pass index arrays to select specific suggestions. Omit or pass null
    to skip a category entirely. Pass an empty body to apply all.
    """
    optic.trace("report_id={}", report_id)

    stmt = select(InsightReport).where(InsightReport.id == report_id)
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    await _authorize_report(report, db, current_user)
    if getattr(report, "subject_type", "agent") == "component":
        raise HTTPException(status_code=409, detail="Suggestions are unavailable for component reports")

    _require_insights()
    import services.dynamic_settings as ds

    enabled = await ds.get_bool("insights.self_learn_enabled", default=True)
    if not enabled:
        raise HTTPException(
            status_code=403,
            detail="Self-learning is disabled. Enable via settings: insights.self_learn_enabled",
        )

    # Run the self-learn pipeline
    from services.insights.self_learn import apply_insight_suggestions

    selection = None
    if body and (
        body.config_indices is not None or body.feature_indices is not None or body.pattern_indices is not None
    ):
        selection = {
            "config_indices": body.config_indices,
            "feature_indices": body.feature_indices,
            "pattern_indices": body.pattern_indices,
        }

    try:
        applied = await apply_insight_suggestions(
            report_id=report_id,
            db=db,
            triggered_by=current_user.id,
            selection=selection,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "applied": True,
        "report_id": report_id,
        "items": applied,
    }
