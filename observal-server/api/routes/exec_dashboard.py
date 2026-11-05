# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Executive Dashboard API endpoints."""

import json
import uuid
from datetime import UTC, timedelta
from datetime import datetime as dt

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, require_role
from api.routes.dashboard import _ch_json, _range_days
from models.agent import Agent, AgentStatus, AgentVersion
from models.download import AgentDownloadRecord
from models.exec_config import ExecDashboardConfig
from models.feedback import Feedback
from models.user import User, UserRole
from models.user_group import UserGroup
from services.redis import get_redis

logger = structlog.get_logger(__name__)


router = APIRouter(prefix="/api/v1/exec", tags=["exec-dashboard"])


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def compute_trend_percent(current: int, previous: int) -> float:
    """Compute period-over-period trend as a percentage."""
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 1)


def _period_bounds(range_: str | None) -> tuple[dt, dt, dt, dt]:
    """Return (current_start, current_end, previous_start, previous_end) for a range."""
    days = _range_days(range_)
    now = dt.now(UTC)
    current_start = now - timedelta(days=days)
    current_end = now
    previous_start = current_start - timedelta(days=days)
    previous_end = current_start
    return current_start, current_end, previous_start, previous_end


async def resolve_user_departments(db: AsyncSession) -> dict[str, list[str]]:
    """Return a deployment-wide department to user ID mapping."""
    dept_map: dict[str, list[str]] = {}
    assigned_user_ids: set[uuid.UUID] = set()

    group_rows = (await db.execute(select(UserGroup.group_name, UserGroup.user_id))).all()
    for row in group_rows:
        dept_map.setdefault(row.group_name, []).append(str(row.user_id))
        assigned_user_ids.add(row.user_id)

    department_filter = User.department.isnot(None)
    if assigned_user_ids:
        department_filter = department_filter & User.id.notin_(assigned_user_ids)
    dept_rows = (await db.execute(select(User.id, User.department).where(department_filter))).all()
    for row in dept_rows:
        dept_map.setdefault(row.department, []).append(str(row.id))
        assigned_user_ids.add(row.id)

    all_users = (await db.execute(select(User.id))).scalars().all()
    unassigned = [str(uid) for uid in all_users if uid not in assigned_user_ids]
    if unassigned:
        dept_map["Unassigned"] = unassigned
    return dept_map


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ExecConfigResponse(BaseModel):
    id: uuid.UUID
    hourly_dev_cost: float
    pre_ai_baselines: dict
    department_budgets: dict
    target_adoption_pct: int
    target_adoption_date: str | None


class ExecConfigUpdate(BaseModel):
    hourly_dev_cost: float | None = None
    pre_ai_baselines: dict | None = None
    department_budgets: dict | None = None
    target_adoption_pct: int | None = None
    target_adoption_date: str | None = None


# ---------------------------------------------------------------------------
# Config endpoint
# ---------------------------------------------------------------------------


@router.get("/config", response_model=ExecConfigResponse | None)
async def get_exec_config(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Get the deployment executive dashboard configuration."""
    result = await db.execute(select(ExecDashboardConfig).limit(1))
    config = result.scalar_one_or_none()
    if not config:
        return None

    return _config_to_response(config)


@router.put("/config", response_model=ExecConfigResponse)
async def update_exec_config(
    req: ExecConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Create or update the deployment executive dashboard configuration."""
    result = await db.execute(select(ExecDashboardConfig).limit(1))
    config = result.scalar_one_or_none()

    if not config:
        config = ExecDashboardConfig()
        db.add(config)

    if req.hourly_dev_cost is not None:
        config.hourly_dev_cost = req.hourly_dev_cost
    if req.pre_ai_baselines is not None:
        config.pre_ai_baselines = req.pre_ai_baselines
    if req.department_budgets is not None:
        config.department_budgets = req.department_budgets
    if req.target_adoption_pct is not None:
        config.target_adoption_pct = req.target_adoption_pct
    if req.target_adoption_date is not None:
        from datetime import date

        config.target_adoption_date = date.fromisoformat(req.target_adoption_date)

    await db.commit()
    await db.refresh(config)

    return _config_to_response(config)


def _config_to_response(config: ExecDashboardConfig) -> ExecConfigResponse:
    return ExecConfigResponse(
        id=config.id,
        hourly_dev_cost=float(config.hourly_dev_cost),
        pre_ai_baselines=config.pre_ai_baselines or {},
        department_budgets=config.department_budgets or {},
        target_adoption_pct=config.target_adoption_pct,
        target_adoption_date=str(config.target_adoption_date) if config.target_adoption_date else None,
    )


# ---------------------------------------------------------------------------
# Adoption Tab
# ---------------------------------------------------------------------------


class AdoptionPoint(BaseModel):
    month: str
    adoption_pct: float


class AdoptionResponse(BaseModel):
    monthly: list[AdoptionPoint]
    current_pct: float
    total_users: int
    active_users: int
    departments_covered: int


@router.get("/adoption", response_model=AdoptionResponse)
async def get_adoption(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Monthly AI adoption percentage from active session users."""

    # Total users in the deployment
    total_users = await db.scalar(select(func.count(User.id))) or 0

    # Monthly active users from session aggregates (last 12 months)
    rows = await _ch_json(
        "SELECT toStartOfMonth(first_event_time) AS month, "
        "count(DISTINCT user_id) AS active "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL 12 MONTH "
        "GROUP BY month ORDER BY month",
    )

    monthly = []
    for r in rows:
        active = int(r.get("active", 0))
        pct = round((active / total_users) * 100, 1) if total_users > 0 else 0.0
        monthly.append(AdoptionPoint(month=str(r["month"])[:7], adoption_pct=pct))

    # Current month active users
    current_rows = await _ch_json(
        "SELECT count(DISTINCT user_id) AS active "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND first_event_time >= toStartOfMonth(now())",
    )
    active_users = int(current_rows[0]["active"]) if current_rows else 0
    current_pct = round((active_users / total_users) * 100, 1) if total_users > 0 else 0.0

    # Departments covered (groups with at least one active user)
    dept_map = await resolve_user_departments(db)
    departments_covered = sum(1 for k in dept_map if k != "Unassigned")

    return AdoptionResponse(
        monthly=monthly,
        current_pct=current_pct,
        total_users=total_users,
        active_users=active_users,
        departments_covered=departments_covered,
    )


class AgentCountBreakdown(BaseModel):
    total: int
    active: int
    published: int
    in_development: int
    by_category: list[dict]


@router.get("/agent-counts", response_model=AgentCountBreakdown)
async def get_agent_counts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Agent count breakdown by status and category."""
    base = select(Agent)
    # Total
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0

    # Published (approved latest version)
    pub_stmt = (
        select(func.count(Agent.id))
        .join(AgentVersion, Agent.latest_version_id == AgentVersion.id)
        .where(AgentVersion.status == AgentStatus.approved)
    )
    published = await db.scalar(pub_stmt) or 0

    # In development (pending/draft)
    dev_stmt = (
        select(func.count(Agent.id))
        .join(AgentVersion, Agent.latest_version_id == AgentVersion.id)
        .where(AgentVersion.status.in_([AgentStatus.pending, AgentStatus.draft]))
    )
    in_development = await db.scalar(dev_stmt) or 0

    # Active agents with sessions in the last 7 days
    active_rows = await _ch_json(
        "SELECT count(DISTINCT agent_id) AS cnt FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' AND agent_id != '' "
        "AND first_event_time >= now() - INTERVAL 7 DAY",
    )
    active = int(active_rows[0]["cnt"]) if active_rows else 0

    # By category
    cat_stmt = select(Agent.category, func.count(Agent.id)).group_by(Agent.category)
    cat_rows = (await db.execute(cat_stmt)).all()
    by_category = [{"category": row[0] or "Uncategorized", "count": row[1]} for row in cat_rows]

    return AgentCountBreakdown(
        total=total,
        active=active,
        published=published,
        in_development=in_development,
        by_category=by_category,
    )


class UsageByCategoryItem(BaseModel):
    category: str
    sessions: int
    growth_pct: float


@router.get("/usage-by-category", response_model=list[UsageByCategoryItem])
async def get_usage_by_category(
    range_: str | None = Query(None, alias="range"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Agent usage grouped by category with period-over-period growth."""
    days = _range_days(range_)

    # Current period sessions by agent
    current_rows = await _ch_json(
        "SELECT agent_id, count() AS cnt FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' AND agent_id != '' "
        "AND first_event_time >= now() - INTERVAL {days:UInt32} DAY "
        "GROUP BY agent_id",
        {"param_days": str(days)},
    )

    # Previous period
    prev_rows = await _ch_json(
        "SELECT agent_id, count() AS cnt FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' AND agent_id != '' "
        "AND first_event_time >= now() - INTERVAL {days2:UInt32} DAY "
        "AND first_event_time < now() - INTERVAL {days:UInt32} DAY "
        "GROUP BY agent_id",
        {"param_days": str(days), "param_days2": str(days * 2)},
    )

    # Resolve agent_id → category from PG
    agent_ids = list({r["agent_id"] for r in current_rows + prev_rows if r.get("agent_id")})
    cat_map: dict[str, str] = {}
    if agent_ids:
        import uuid as _uuid

        valid_ids = []
        for aid in agent_ids:
            try:
                valid_ids.append(_uuid.UUID(aid))
            except (ValueError, AttributeError):
                pass
        if valid_ids:
            rows = (await db.execute(select(Agent.id, Agent.category).where(Agent.id.in_(valid_ids)))).all()
            cat_map = {str(r.id): r.category or "Uncategorized" for r in rows}

    # Aggregate by category
    current_by_cat: dict[str, int] = {}
    for r in current_rows:
        cat = cat_map.get(r["agent_id"], "Uncategorized")
        current_by_cat[cat] = current_by_cat.get(cat, 0) + int(r["cnt"])

    prev_by_cat: dict[str, int] = {}
    for r in prev_rows:
        cat = cat_map.get(r["agent_id"], "Uncategorized")
        prev_by_cat[cat] = prev_by_cat.get(cat, 0) + int(r["cnt"])

    result = []
    for cat, sessions in sorted(current_by_cat.items(), key=lambda x: -x[1]):
        prev = prev_by_cat.get(cat, 0)
        growth = compute_trend_percent(sessions, prev)
        result.append(UsageByCategoryItem(category=cat, sessions=sessions, growth_pct=growth))

    return result


class PlatformCoverageItem(BaseModel):
    platform: str
    users: int
    sessions: int


@router.get("/platform-coverage", response_model=list[PlatformCoverageItem])
async def get_platform_coverage(
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Harness/platform coverage — distinct users and sessions per platform."""
    rows = await _ch_json(
        "SELECT harness, count(DISTINCT user_id) AS users, count() AS sessions "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND harness != '' "
        "GROUP BY harness ORDER BY sessions DESC",
    )
    return [
        PlatformCoverageItem(platform=r["harness"], users=int(r["users"]), sessions=int(r["sessions"])) for r in rows
    ]


# ---------------------------------------------------------------------------
# Investments Tab (Platform Comparison)
# ---------------------------------------------------------------------------


class PlatformScore(BaseModel):
    platform: str
    composite_score: float
    sessions: int
    avg_cost: float
    avg_latency_ms: float
    success_rate: float | None
    error_rate: float | None
    users: int


@router.get("/platforms", response_model=list[PlatformScore])
async def get_platforms(
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Per-Harness platform comparison with composite scores."""
    rows = await _ch_json(
        "SELECT harness, count() AS sessions, "
        "count(DISTINCT user_id) AS users, "
        "round(avg(dateDiff('millisecond', first_event_time, last_event_time)), 1) AS avg_latency_ms "
        "FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' AND harness != '' "
        "GROUP BY harness ORDER BY sessions DESC",
    )

    if not rows:
        return []

    results = []
    for r in rows:
        sessions = int(r.get("sessions", 0))
        users = int(r.get("users", 0))
        # ponytail: session aggregates do not expose monetary cost or structured errors;
        # populate these when session ingestion gains those fields.
        avg_cost = 0.0
        avg_latency = float(r.get("avg_latency_ms") or 0)
        error_rate = None
        success_rate = None

        results.append(
            PlatformScore(
                platform=r["harness"],
                composite_score=0,
                sessions=sessions,
                avg_cost=avg_cost,
                avg_latency_ms=avg_latency,
                success_rate=success_rate,
                error_rate=error_rate,
                users=users,
            )
        )

    # Score = session-based rank (most adopted = highest score, no opaque weighting)
    if results:
        max_sessions = results[0].sessions or 1
        for r in results:
            r.composite_score = round((r.sessions / max_sessions) * 100, 1)

    return results


# ---------------------------------------------------------------------------
# Velocity Tab
# ---------------------------------------------------------------------------


class VelocityPoint(BaseModel):
    week: str
    traces: int


class VelocityResponse(BaseModel):
    weekly: list[VelocityPoint]
    current_weekly_avg: float
    baseline_weekly_avg: float
    multiplier: float


@router.get("/velocity", response_model=VelocityResponse)
async def get_velocity(
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Weekly trace counts with baseline comparison."""
    rows = await _ch_json(
        "SELECT toStartOfWeek(first_event_time) AS week, count() AS traces "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL 12 WEEK "
        "GROUP BY week ORDER BY week",
    )

    weekly = [VelocityPoint(week=str(r["week"])[:10], traces=int(r["traces"])) for r in rows]

    if len(weekly) >= 4:
        baseline_weeks = weekly[:4]
        current_weeks = weekly[-4:]
        baseline_avg = sum(w.traces for w in baseline_weeks) / len(baseline_weeks)
        current_avg = sum(w.traces for w in current_weeks) / len(current_weeks)
    elif weekly:
        baseline_avg = weekly[0].traces
        current_avg = weekly[-1].traces
    else:
        baseline_avg = 0
        current_avg = 0

    multiplier = round(current_avg / baseline_avg, 1) if baseline_avg > 0 else 0.0

    return VelocityResponse(
        weekly=weekly,
        current_weekly_avg=round(current_avg, 1),
        baseline_weekly_avg=round(baseline_avg, 1),
        multiplier=multiplier,
    )


class TopAgentScored(BaseModel):
    id: str
    name: str
    category: str
    composite_score: float
    sessions: int
    downloads: int
    avg_rating: float | None
    weekly_trend: list[int]


@router.get("/top-agents", response_model=list[TopAgentScored])
async def get_top_agents(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Top agents by composite score (downloads + sessions + rating)."""
    # Downloads from PG
    dl_stmt = select(AgentDownloadRecord.agent_id, func.count(AgentDownloadRecord.id).label("downloads")).group_by(
        AgentDownloadRecord.agent_id
    )
    dl_rows = (await db.execute(dl_stmt)).all()
    dl_map = {str(r.agent_id): r.downloads for r in dl_rows}

    # Ratings from PG
    rating_stmt = (
        select(Feedback.listing_id, func.avg(Feedback.rating).label("avg_rating"))
        .where(Feedback.listing_type == "agent")
        .group_by(Feedback.listing_id)
    )
    rating_rows = (await db.execute(rating_stmt)).all()
    rating_map = {str(r.listing_id): round(float(r.avg_rating), 2) for r in rating_rows}

    # Sessions from ClickHouse (last 30 days)
    session_rows = await _ch_json(
        "SELECT agent_id, count() AS sessions "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND agent_id != '' AND first_event_time >= now() - INTERVAL 30 DAY "
        "GROUP BY agent_id ORDER BY sessions DESC LIMIT 50",
    )
    session_map = {r["agent_id"]: int(r["sessions"]) for r in session_rows}

    # Weekly trend (last 6 weeks) per agent
    trend_rows = await _ch_json(
        "SELECT agent_id, toStartOfWeek(first_event_time) AS week, count() AS cnt "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND agent_id != '' AND first_event_time >= now() - INTERVAL 6 WEEK "
        "GROUP BY agent_id, week ORDER BY agent_id, week",
    )
    trend_map: dict[str, list[int]] = {}
    for r in trend_rows:
        aid = r["agent_id"]
        trend_map.setdefault(aid, []).append(int(r["cnt"]))

    # Get all candidate agent_ids
    all_agent_ids = set(session_map.keys()) | set(dl_map.keys())
    if not all_agent_ids:
        return []

    # Resolve names + categories from PG
    import uuid as _uuid

    valid_ids = []
    for aid in all_agent_ids:
        try:
            valid_ids.append(_uuid.UUID(aid))
        except (ValueError, AttributeError):
            pass

    agent_info: dict[str, tuple[str, str]] = {}
    if valid_ids:
        info_stmt = select(Agent.id, Agent.name, Agent.category)
        info_stmt = info_stmt.where(Agent.id.in_(valid_ids))
        info_rows = (await db.execute(info_stmt)).all()
        agent_info = {str(r.id): (r.name, r.category or "Uncategorized") for r in info_rows}

    # Compute composite and rank
    max_downloads = max(dl_map.values(), default=1) or 1
    max_sessions = max(session_map.values(), default=1) or 1

    scored = []
    for aid, (name, category) in agent_info.items():
        downloads = dl_map.get(aid, 0)
        sessions = session_map.get(aid, 0)
        rating = rating_map.get(aid)

        dl_norm = (downloads / max_downloads) * 100
        sess_norm = (sessions / max_sessions) * 100
        rating_norm = ((rating or 0) / 5) * 100

        composite = round(dl_norm * 0.3 + sess_norm * 0.4 + rating_norm * 0.3, 1)

        scored.append(
            TopAgentScored(
                id=aid,
                name=name,
                category=category,
                composite_score=composite,
                sessions=sessions,
                downloads=downloads,
                avg_rating=rating,
                weekly_trend=trend_map.get(aid, []),
            )
        )

    scored.sort(key=lambda x: -x.composite_score)
    return scored[:limit]


# ---------------------------------------------------------------------------
# Departments Tab
# ---------------------------------------------------------------------------


class DepartmentItem(BaseModel):
    department: str
    user_count: int
    agent_count: int
    utilization_pct: float
    sessions_per_user: float


class DepartmentsResponse(BaseModel):
    departments: list[DepartmentItem]


@router.get("/departments", response_model=DepartmentsResponse)
async def get_departments(
    range_: str | None = Query(None, alias="range"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Per-department breakdown: users, agents, utilization, sessions/user."""
    days = _range_days(range_)

    dept_map = await resolve_user_departments(db)
    if not dept_map:
        return DepartmentsResponse(departments=[])

    # Agent count per department: count agents created by users in each group
    agent_count_by_dept: dict[str, int] = {}
    all_dept_user_ids: list[str] = []
    for uids in dept_map.values():
        all_dept_user_ids.extend(uids)
    if all_dept_user_ids:
        from models.agent import Agent

        agent_rows = (await db.execute(select(Agent.created_by, func.count(Agent.id)).group_by(Agent.created_by))).all()
        user_agent_count: dict[str, int] = {str(r[0]): r[1] for r in agent_rows}
        for dept_name, user_ids in dept_map.items():
            agent_count_by_dept[dept_name] = sum(user_agent_count.get(uid, 0) for uid in user_ids)

    # Get session counts per user from ClickHouse
    all_user_ids = []
    for uids in dept_map.values():
        all_user_ids.extend(uids)

    user_sessions: dict[str, int] = {}
    if all_user_ids:
        # Batch query: get session count per user in period
        session_rows = await _ch_json(
            "SELECT user_id, count() AS sessions "
            "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
            "AND first_event_time >= now() - INTERVAL {days:UInt32} DAY "
            "GROUP BY user_id",
            {"param_days": str(days)},
        )
        user_sessions = {r["user_id"]: int(r["sessions"]) for r in session_rows}

    departments = []
    for dept_name, user_ids in sorted(dept_map.items()):
        user_count = len(user_ids)
        agent_count = agent_count_by_dept.get(dept_name, 0)

        # Utilization: users with >= 1 session in period
        active_in_dept = sum(1 for uid in user_ids if user_sessions.get(uid, 0) > 0)
        utilization = round((active_in_dept / user_count) * 100, 1) if user_count > 0 else 0.0

        # Sessions per user
        total_sessions = sum(user_sessions.get(uid, 0) for uid in user_ids)
        sessions_per_user = round(total_sessions / user_count, 1) if user_count > 0 else 0.0

        departments.append(
            DepartmentItem(
                department=dept_name,
                user_count=user_count,
                agent_count=agent_count,
                utilization_pct=utilization,
                sessions_per_user=sessions_per_user,
            )
        )

    return DepartmentsResponse(departments=departments)


class DeptTokenItem(BaseModel):
    department: str
    tokens_used: int
    cost_per_task: float
    sessions_per_user: float
    trend_pct: float


@router.get("/dept-tokens", response_model=list[DeptTokenItem])
async def get_dept_tokens(
    range_: str | None = Query(None, alias="range"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Token usage and cost per department with trend."""
    days = _range_days(range_)

    dept_map = await resolve_user_departments(db)
    if not dept_map:
        return []

    # Current period: tokens and sessions per user. Session telemetry has no
    # monetary cost field, so cost remains zero.
    current_rows = await _ch_json(
        "SELECT user_id, sum(input_tokens + output_tokens) AS tokens, "
        "count() AS traces "
        "FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL {days:UInt32} DAY "
        "GROUP BY user_id",
        {"param_days": str(days)},
    )
    current_by_user: dict[str, dict] = {
        r["user_id"]: {"tokens": int(r["tokens"]), "traces": int(r["traces"]), "cost": 0.0} for r in current_rows
    }

    # Previous period: tokens per user (for trend)
    prev_rows = await _ch_json(
        "SELECT user_id, sum(input_tokens + output_tokens) AS tokens "
        "FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL {days2:UInt32} DAY "
        "AND first_event_time < now() - INTERVAL {days:UInt32} DAY "
        "GROUP BY user_id",
        {"param_days": str(days), "param_days2": str(days * 2)},
    )
    prev_by_user: dict[str, int] = {r["user_id"]: int(r["tokens"]) for r in prev_rows}

    result = []
    for dept_name, user_ids in sorted(dept_map.items()):
        user_count = len(user_ids)
        tokens = sum(current_by_user.get(uid, {}).get("tokens", 0) for uid in user_ids)
        traces = sum(current_by_user.get(uid, {}).get("traces", 0) for uid in user_ids)
        cost = sum(current_by_user.get(uid, {}).get("cost", 0) for uid in user_ids)
        prev_tokens = sum(prev_by_user.get(uid, 0) for uid in user_ids)

        cost_per_task = round(cost / traces, 4) if traces > 0 else 0.0
        sessions_per_user = round(traces / user_count, 1) if user_count > 0 else 0.0
        trend = compute_trend_percent(tokens, prev_tokens)

        result.append(
            DeptTokenItem(
                department=dept_name,
                tokens_used=tokens,
                cost_per_task=cost_per_task,
                sessions_per_user=sessions_per_user,
                trend_pct=trend,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Cost Intelligence Tab
# ---------------------------------------------------------------------------


class MonthlyCostPoint(BaseModel):
    month: str
    ai_spend: float
    savings: float


class CostByCategory(BaseModel):
    category: str
    baseline_cost: float
    actual_cost: float
    saved_pct: float


class CostSummaryResponse(BaseModel):
    monthly_savings: float
    cost_reduction_pct: float
    projected_annual_savings: float
    cost_per_task: float
    monthly_trend: list[MonthlyCostPoint]
    by_category: list[CostByCategory]
    configured: bool


@router.get("/cost-summary", response_model=CostSummaryResponse)
async def get_cost_summary(
    range_: str | None = Query(None, alias="range"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Cost savings, spend, and ROI. Requires exec_dashboard_config baselines."""
    # Load config
    config = (await db.execute(select(ExecDashboardConfig).limit(1))).scalar_one_or_none()

    if not config:
        return CostSummaryResponse(
            monthly_savings=0,
            cost_reduction_pct=0,
            projected_annual_savings=0,
            cost_per_task=0,
            monthly_trend=[],
            by_category=[],
            configured=False,
        )

    monthly_rows = await _ch_json(
        "SELECT toStartOfMonth(first_event_time) AS month "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL 12 MONTH "
        "GROUP BY month ORDER BY month",
    )

    # ponytail: session telemetry has no monetary cost field. Report zero until
    # ingestion provides a real currency value instead of estimating one.
    monthly_trend = [MonthlyCostPoint(month=str(r["month"])[:7], ai_spend=0, savings=0) for r in monthly_rows]
    monthly_savings = 0.0
    cost_per_task = 0.0
    cost_reduction_pct = 0.0
    projected_annual = 0.0
    by_category: list[CostByCategory] = []

    return CostSummaryResponse(
        monthly_savings=round(monthly_savings, 2),
        cost_reduction_pct=cost_reduction_pct,
        projected_annual_savings=projected_annual,
        cost_per_task=cost_per_task,
        monthly_trend=monthly_trend,
        by_category=by_category,
        configured=True,
    )


# ---------------------------------------------------------------------------
# ROI Projections
# ---------------------------------------------------------------------------


class ROIProjectionPoint(BaseModel):
    quarter: str
    projected_savings: float
    cumulative_savings: float
    confidence: float


class ROIProjectionsResponse(BaseModel):
    projections: list[ROIProjectionPoint]
    growth_rate_pct: float
    time_to_breakeven_months: int | None
    total_invested: float
    total_saved: float
    roi_multiple: float


@router.get("/roi-projections", response_model=ROIProjectionsResponse)
async def get_roi_projections(
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Return no projections until session telemetry includes monetary cost."""
    # ponytail: currency cost is unavailable in session telemetry; add projections
    # when ingestion stores a real monetary value.
    return ROIProjectionsResponse(
        projections=[],
        growth_rate_pct=0,
        time_to_breakeven_months=None,
        total_invested=0,
        total_saved=0,
        roi_multiple=0,
    )


# Strategic Insights (deployment analysis from real telemetry)
# ---------------------------------------------------------------------------


class ModelComparisonItem(BaseModel):
    model: str
    sessions: int
    avg_cost: float
    avg_tokens: int
    success_rate: float
    best_at: str


class DepartmentGap(BaseModel):
    department: str
    adoption_pct: float
    sessions: int
    opportunity: str


class QuickWin(BaseModel):
    title: str
    detail: str
    estimated_savings: float
    effort: str


class PlatformComparison(BaseModel):
    platform: str
    avg_task_time_ms: float
    sessions: int
    success_rate: float


class StrategicInsightsResponse(BaseModel):
    model_comparison: list[ModelComparisonItem]
    department_gaps: list[DepartmentGap]
    quick_wins: list[QuickWin]
    platform_comparison: list[PlatformComparison]
    power_user_pct: float
    power_user_value_pct: float
    total_active_users: int
    automatable_pct: float


@router.get("/strategic-insights", response_model=StrategicInsightsResponse)
async def get_strategic_insights(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Strategic insights derived from deployment telemetry data."""

    # 1. Model comparison from session_stats_agg
    model_rows = await _ch_json(
        "SELECT model, "
        "count() AS sessions, "
        "round(avg(input_tokens + output_tokens)) AS avg_tokens "
        "FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' AND model != '' "
        "GROUP BY model "
        "HAVING sessions >= 5 "
        "ORDER BY sessions DESC "
        "LIMIT 10",
    )

    # Completion proxy per model from session aggregates.
    model_success_rows = await _ch_json(
        "SELECT model, "
        "countIf(event_count > 5 AND prompt_count >= 1) AS successes, "
        "count() AS total "
        "FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' AND model != '' "
        "AND first_event_time >= now() - INTERVAL 30 DAY "
        "GROUP BY model HAVING total >= 5",
    )
    model_success_map = {
        r["model"]: round(int(r["successes"]) / int(r["total"]) * 100, 1)
        for r in model_success_rows
        if int(r.get("total", 0)) > 0
    }

    model_comparison = []
    if model_rows:
        most_used = model_rows[0] if model_rows else None

        for r in model_rows:
            model_name = r["model"]
            avg_cost = 0.0
            sessions = int(r.get("sessions", 0))
            avg_tokens = int(float(r.get("avg_tokens") or 0))
            success = model_success_map.get(model_name, 0)

            if sessions == int(most_used.get("sessions", 0)):
                best_at = "Most popular, proven reliability"
            elif avg_tokens > 5000:
                best_at = "Complex/long-context tasks"
            else:
                best_at = "General purpose"

            model_comparison.append(
                ModelComparisonItem(
                    model=model_name,
                    sessions=sessions,
                    avg_cost=avg_cost,
                    avg_tokens=avg_tokens,
                    success_rate=success,
                    best_at=best_at,
                )
            )

    # 2. Department gaps
    dept_map = await resolve_user_departments(db)
    all_user_ids = []
    for uids in dept_map.values():
        all_user_ids.extend(uids)

    user_session_rows = await _ch_json(
        "SELECT user_id, count() AS sessions FROM session_stats_agg FINAL WHERE project_id = '{project_id}' GROUP BY user_id",
    )
    user_sessions = {r["user_id"]: int(r["sessions"]) for r in user_session_rows}

    department_gaps = []
    for dept_name, user_ids in sorted(dept_map.items()):
        if dept_name == "Unassigned":
            continue
        user_count = len(user_ids)
        active_count = sum(1 for uid in user_ids if user_sessions.get(uid, 0) > 0)
        adoption = round((active_count / user_count) * 100, 1) if user_count > 0 else 0
        total_sessions = sum(user_sessions.get(uid, 0) for uid in user_ids)

        if adoption < 50:
            opportunity = f"{user_count - active_count} users not using AI — potential for automation"
        elif adoption < 80:
            opportunity = "Moderate adoption, room for deeper integration"
        else:
            opportunity = "High adoption — focus on optimization"

        department_gaps.append(
            DepartmentGap(
                department=dept_name,
                adoption_pct=adoption,
                sessions=total_sessions,
                opportunity=opportunity,
            )
        )

    department_gaps.sort(key=lambda d: d.adoption_pct)

    # Session telemetry does not expose monetary cost or structured errors, so
    # cost-saving quick wins cannot be computed without inventing values.
    quick_wins = []

    # 4. Platform comparison (task completion speed)
    platform_rows = await _ch_json(
        "SELECT harness, "
        "round(avg(dateDiff('millisecond', first_event_time, last_event_time))) AS avg_time_ms, "
        "count() AS sessions, "
        "countIf(event_count > 2) AS completed "
        "FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' AND harness != '' "
        "AND first_event_time != last_event_time "
        "GROUP BY harness "
        "HAVING sessions >= 5 "
        "ORDER BY sessions DESC",
    )
    platform_comparison = [
        PlatformComparison(
            platform=r["harness"],
            avg_task_time_ms=float(r.get("avg_time_ms") or 0),
            sessions=int(r["sessions"]),
            success_rate=round(int(r["completed"]) / int(r["sessions"]) * 100, 1) if int(r["sessions"]) > 0 else 0,
        )
        for r in platform_rows
    ]

    # 5. Power user analysis
    user_value_rows = await _ch_json(
        "SELECT user_id, count() AS sessions, sum(input_tokens + output_tokens) AS value "
        "FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL 30 DAY "
        "GROUP BY user_id "
        "ORDER BY value DESC",
    )

    total_active = len(user_value_rows)
    if total_active > 0:
        top_20_count = max(1, total_active // 5)
        total_value = sum(float(r.get("value") or 0) for r in user_value_rows)
        top_20_value = sum(float(r.get("value") or 0) for r in user_value_rows[:top_20_count])
        power_user_value_pct = round((top_20_value / total_value) * 100, 1) if total_value > 0 else 0
    else:
        power_user_value_pct = 0

    # 6. Automatable task estimation (simple tasks = low tokens + high success)
    auto_rows = await _ch_json(
        "SELECT "
        "countIf((input_tokens + output_tokens) < 3000 AND event_count <= 5) AS simple, "
        "count() AS total "
        "FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL 30 DAY",
    )
    simple = int(auto_rows[0]["simple"]) if auto_rows else 0
    total_tasks = int(auto_rows[0]["total"]) if auto_rows else 0
    automatable_pct = round((simple / total_tasks) * 100, 1) if total_tasks > 0 else 0

    return StrategicInsightsResponse(
        model_comparison=model_comparison,
        department_gaps=department_gaps,
        quick_wins=quick_wins,
        platform_comparison=platform_comparison,
        power_user_pct=20.0,
        power_user_value_pct=power_user_value_pct,
        total_active_users=total_active,
        automatable_pct=automatable_pct,
    )


# ---------------------------------------------------------------------------
# Developer Breakdown
# ---------------------------------------------------------------------------


class DeveloperActivityItem(BaseModel):
    user_id: str
    name: str
    department: str
    sessions: int
    tokens_consumed: int
    cost: float
    percentile: int


class DeveloperBreakdownResponse(BaseModel):
    total_developers: int
    active_developers: int
    top_20_value_pct: float
    developers: list[DeveloperActivityItem]


@router.get("/developer-breakdown", response_model=DeveloperBreakdownResponse)
async def get_developer_breakdown(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Per-developer activity breakdown with percentile ranking."""

    # Total users in the deployment
    total_developers = await db.scalar(select(func.count(User.id))) or 0

    # Per-user activity from ClickHouse (last 30 days)
    user_rows = await _ch_json(
        "SELECT user_id, "
        "count() AS sessions, "
        "sumIf(input_tokens + output_tokens, input_tokens IS NOT NULL) AS tokens "
        "FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL 30 DAY "
        "GROUP BY user_id "
        "ORDER BY sessions DESC",
    )

    active_developers = len(user_rows)

    # Top 20% value calculation
    total_value = sum(int(r.get("sessions", 0)) for r in user_rows)
    top_20_count = max(1, len(user_rows) // 5)
    top_20_value = sum(int(r.get("sessions", 0)) for r in user_rows[:top_20_count])
    top_20_value_pct = round((top_20_value / total_value) * 100, 1) if total_value > 0 else 0

    # Resolve user names + departments from PG
    user_ids = [r["user_id"] for r in user_rows[:limit]]
    user_info: dict[str, tuple[str, str]] = {}
    if user_ids:
        import uuid as _uuid

        valid_ids = []
        for uid in user_ids:
            try:
                valid_ids.append(_uuid.UUID(uid))
            except (ValueError, AttributeError):
                pass
        if valid_ids:
            info_rows = (
                await db.execute(select(User.id, User.name, User.department).where(User.id.in_(valid_ids)))
            ).all()
            user_info = {str(r.id): (r.name, r.department or "Unassigned") for r in info_rows}

    # Also check user_groups for SSO department
    dept_map = await resolve_user_departments(db)
    uid_to_dept: dict[str, str] = {}
    for dept_name, uids in dept_map.items():
        for uid in uids:
            uid_to_dept[uid] = dept_name

    developers = []
    for i, r in enumerate(user_rows[:limit]):
        uid = r["user_id"]
        name, fallback_dept = user_info.get(uid, ("Unknown", "Unassigned"))
        department = uid_to_dept.get(uid, fallback_dept)
        percentile = max(1, 100 - int((i / max(len(user_rows), 1)) * 100))

        developers.append(
            DeveloperActivityItem(
                user_id=uid,
                name=name,
                department=department,
                sessions=int(r.get("sessions", 0)),
                tokens_consumed=int(r.get("tokens", 0)),
                cost=0.0,
                percentile=percentile,
            )
        )

    return DeveloperBreakdownResponse(
        total_developers=total_developers,
        active_developers=active_developers,
        top_20_value_pct=top_20_value_pct,
        developers=developers,
    )


# ---------------------------------------------------------------------------
# Inactivity / Churn Alerts
# ---------------------------------------------------------------------------


class InactiveAgentItem(BaseModel):
    id: str
    name: str
    category: str
    last_session_days_ago: int
    previous_sessions: int


class InactiveUserItem(BaseModel):
    user_id: str
    name: str
    department: str
    last_session_days_ago: int
    previous_sessions: int


class InactivityAlertsResponse(BaseModel):
    inactive_agents: list[InactiveAgentItem]
    inactive_users: list[InactiveUserItem]


@router.get("/inactivity-alerts", response_model=InactivityAlertsResponse)
async def get_inactivity_alerts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Agents and users that were active in days 15-28 but inactive in last 14 days."""

    # Agents active 15-28 days ago but NOT in last 14 days
    prev_agent_rows = await _ch_json(
        "SELECT agent_id, count() AS sessions "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND agent_id != '' "
        "AND first_event_time >= now() - INTERVAL 28 DAY "
        "AND first_event_time < now() - INTERVAL 14 DAY "
        "GROUP BY agent_id HAVING sessions >= 5",
    )

    recent_agent_rows = await _ch_json(
        "SELECT agent_id "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND agent_id != '' "
        "AND first_event_time >= now() - INTERVAL 14 DAY "
        "GROUP BY agent_id",
    )
    recently_active_agents = {r["agent_id"] for r in recent_agent_rows}

    churned_agent_ids = [r for r in prev_agent_rows if r["agent_id"] not in recently_active_agents]

    # Resolve agent names
    import uuid as _uuid

    agent_ids_to_resolve = []
    for r in churned_agent_ids:
        try:
            agent_ids_to_resolve.append(_uuid.UUID(r["agent_id"]))
        except (ValueError, AttributeError):
            pass

    agent_info: dict[str, tuple[str, str]] = {}
    if agent_ids_to_resolve:
        info_stmt = select(Agent.id, Agent.name, Agent.category)
        info_stmt = info_stmt.where(Agent.id.in_(agent_ids_to_resolve))
        rows = (await db.execute(info_stmt)).all()
        agent_info = {str(r.id): (r.name, r.category or "Uncategorized") for r in rows}

    inactive_agents = []
    for r in churned_agent_ids[:10]:
        aid = r["agent_id"]
        if aid in {str(k) for k in agent_ids_to_resolve} and aid in agent_info:
            name, category = agent_info[aid]
            inactive_agents.append(
                InactiveAgentItem(
                    id=aid,
                    name=name,
                    category=category,
                    last_session_days_ago=14,
                    previous_sessions=int(r["sessions"]),
                )
            )

    # Users active 15-28 days ago but NOT in last 14 days
    prev_user_rows = await _ch_json(
        "SELECT user_id, count() AS sessions "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL 28 DAY "
        "AND first_event_time < now() - INTERVAL 14 DAY "
        "GROUP BY user_id HAVING sessions >= 5",
    )

    recent_user_rows = await _ch_json(
        "SELECT user_id "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL 14 DAY "
        "GROUP BY user_id",
    )
    recently_active_users = {r["user_id"] for r in recent_user_rows}

    churned_users = [r for r in prev_user_rows if r["user_id"] not in recently_active_users]

    # Resolve user names + departments
    dept_map = await resolve_user_departments(db)
    uid_to_dept: dict[str, str] = {}
    for dept_name, uids in dept_map.items():
        for uid in uids:
            uid_to_dept[uid] = dept_name

    user_ids_to_resolve = []
    for r in churned_users[:10]:
        try:
            user_ids_to_resolve.append(_uuid.UUID(r["user_id"]))
        except (ValueError, AttributeError):
            pass

    user_names: dict[str, str] = {}
    if user_ids_to_resolve:
        rows = (await db.execute(select(User.id, User.name).where(User.id.in_(user_ids_to_resolve)))).all()
        user_names = {str(r.id): r.name for r in rows}

    inactive_users = []
    for r in churned_users[:10]:
        uid = r["user_id"]
        name = user_names.get(uid, "Unknown")
        dept = uid_to_dept.get(uid, "Unassigned")
        inactive_users.append(
            InactiveUserItem(
                user_id=uid,
                name=name,
                department=dept,
                last_session_days_ago=14,
                previous_sessions=int(r["sessions"]),
            )
        )

    return InactivityAlertsResponse(
        inactive_agents=inactive_agents,
        inactive_users=inactive_users,
    )


# ---------------------------------------------------------------------------
# Time to Value
# ---------------------------------------------------------------------------


class TimeToValueItem(BaseModel):
    id: str
    name: str
    category: str
    created_at: str
    days_to_100: int | None
    current_sessions: int


class TimeToValueResponse(BaseModel):
    agents: list[TimeToValueItem]
    avg_days_to_100: float | None


@router.get("/time-to-value", response_model=TimeToValueResponse)
async def get_time_to_value(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Days from agent deployment to reaching 100 sessions."""
    # Get all agents with their created_at
    agent_stmt = select(Agent.id, Agent.name, Agent.category, Agent.created_at)
    agent_rows = (await db.execute(agent_stmt)).all()

    if not agent_rows:
        return TimeToValueResponse(agents=[], avg_days_to_100=None)

    # Get cumulative session counts per agent per day
    session_rows = await _ch_json(
        "SELECT agent_id, min(first_event_time) AS first_session, count() AS total_sessions "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND agent_id != '' "
        "GROUP BY agent_id",
    )
    session_map: dict[str, dict] = {
        r["agent_id"]: {"first_session": r["first_session"], "total": int(r["total_sessions"])} for r in session_rows
    }

    # For agents with >=100 sessions, find when they hit 100
    agents_over_100 = [aid for aid, data in session_map.items() if data["total"] >= 100]
    day_100_map: dict[str, str] = {}
    if agents_over_100:
        # Get the date of the 100th session for each agent
        milestone_rows = await _ch_json(
            "SELECT agent_id, first_event_time AS start_time "
            "FROM ("
            "  SELECT agent_id, first_event_time, "
            "    row_number() OVER (PARTITION BY agent_id ORDER BY first_event_time) AS rn "
            "  FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
            "  AND agent_id IN ({aids:String})"
            ") WHERE rn = 100",
            {"param_aids": ",".join(f"'{a}'" for a in agents_over_100[:20])},
        )
        for r in milestone_rows:
            day_100_map[r["agent_id"]] = r["start_time"]

    import datetime as _dt

    items = []
    days_list = []
    for agent in agent_rows:
        aid = str(agent.id)
        data = session_map.get(aid)
        current_sessions = data["total"] if data else 0

        days_to_100: int | None = None
        if aid in day_100_map and agent.created_at:
            try:
                milestone_dt = _dt.datetime.fromisoformat(str(day_100_map[aid]).replace("Z", "+00:00"))
                created = agent.created_at if agent.created_at.tzinfo else agent.created_at.replace(tzinfo=_dt.UTC)
                days_to_100 = max(0, (milestone_dt - created).days)
                days_list.append(days_to_100)
            except (ValueError, TypeError):
                pass

        items.append(
            TimeToValueItem(
                id=aid,
                name=agent.name,
                category=agent.category or "Uncategorized",
                created_at=str(agent.created_at)[:10] if agent.created_at else "",
                days_to_100=days_to_100,
                current_sessions=current_sessions,
            )
        )

    items.sort(key=lambda x: x.current_sessions, reverse=True)
    avg_days = round(sum(days_list) / len(days_list), 1) if days_list else None

    return TimeToValueResponse(agents=items[:20], avg_days_to_100=avg_days)


# AI Insights (LLM-generated strategic recommendations)
# ---------------------------------------------------------------------------


class AIInsightsResponse(BaseModel):
    quick_wins: list[dict]
    adoption_gaps: list[dict]
    platform_insight: dict
    model_insight: dict
    automation_opportunity: dict
    usage_pattern: dict
    generated: bool
    generated_at: str | None = None


def _ai_insights_cache_key(current_user: User) -> str:
    return "exec.ai_insights"


def _empty_ai_insights_response() -> AIInsightsResponse:
    detail = "Generate an executive insight report to see LLM-powered strategic recommendations."
    return AIInsightsResponse(
        quick_wins=[],
        adoption_gaps=[],
        platform_insight={"title": "No cached report", "detail": detail},
        model_insight={"title": "No cached report", "detail": detail},
        automation_opportunity={"title": "No cached report", "detail": detail},
        usage_pattern={"title": "No cached report", "detail": detail},
        generated=False,
    )


@router.get("/ai-insights", response_model=AIInsightsResponse)
async def get_ai_insights(
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Return the cached executive AI insight report without generating a new one."""
    try:
        cached = await get_redis().get(_ai_insights_cache_key(current_user))
    except RedisError as e:
        logger.error("exec_ai_insights_cache_read_failed", error=str(e))
        raise HTTPException(status_code=503, detail="Executive AI insights cache is unavailable") from e

    if not cached:
        return _empty_ai_insights_response()

    try:
        return AIInsightsResponse.model_validate(json.loads(cached))
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.error("exec_ai_insights_cache_invalid", error=str(e))
        raise HTTPException(status_code=500, detail="Cached executive AI insights report is invalid") from e


@router.post("/ai-insights", response_model=AIInsightsResponse)
async def generate_ai_insights(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Generate and cache LLM-powered strategic insights from deployment telemetry."""
    from services.strategic_insights import generate_strategic_insights

    # Collect all metrics needed for the LLM
    # 1. Adoption
    total_users = await db.scalar(select(func.count(User.id))) or 0

    active_rows = await _ch_json(
        "SELECT count(DISTINCT user_id) AS active "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL 30 DAY",
    )
    active_users = int(active_rows[0]["active"]) if active_rows else 0
    adoption_pct = round((active_users / total_users) * 100, 1) if total_users > 0 else 0

    # 2. Model comparison
    model_rows = await _ch_json(
        "SELECT model, count() AS sessions, "
        "round(avg(input_tokens + output_tokens)) AS avg_tokens "
        "FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' AND model != '' "
        "GROUP BY model HAVING sessions >= 3 "
        "ORDER BY sessions DESC LIMIT 10",
    )

    # 3. Platform comparison
    platform_rows = await _ch_json(
        "SELECT harness, count() AS sessions, "
        "count(DISTINCT user_id) AS users, "
        "round(avg(dateDiff('millisecond', first_event_time, last_event_time)) / 1000) AS avg_task_seconds "
        "FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' AND harness != '' "
        "AND first_event_time != last_event_time "
        "GROUP BY harness HAVING sessions >= 3 "
        "ORDER BY sessions DESC",
    )

    # 4. Department gaps
    dept_map = await resolve_user_departments(db)
    user_session_rows = await _ch_json(
        "SELECT user_id, count() AS sessions "
        "FROM session_stats_agg FINAL WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL 30 DAY "
        "GROUP BY user_id",
    )
    user_sessions = {r["user_id"]: int(r["sessions"]) for r in user_session_rows}

    dept_data = []
    for dept_name, user_ids in sorted(dept_map.items()):
        if dept_name == "Unassigned":
            continue
        user_count = len(user_ids)
        active_count = sum(1 for uid in user_ids if user_sessions.get(uid, 0) > 0)
        dept_adoption = round((active_count / user_count) * 100, 1) if user_count > 0 else 0
        total_sessions = sum(user_sessions.get(uid, 0) for uid in user_ids)
        dept_data.append(
            {
                "department": dept_name,
                "users": user_count,
                "active_users": active_count,
                "adoption_pct": dept_adoption,
                "sessions": total_sessions,
            }
        )

    # Monetary cost is unavailable in session telemetry.
    expensive_rows: list[dict] = []

    # 6. Automatable estimate
    auto_rows = await _ch_json(
        "SELECT "
        "countIf((input_tokens + output_tokens) < 3000 AND event_count <= 5) AS simple, "
        "count() AS total "
        "FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL 30 DAY",
    )
    simple_count = int(auto_rows[0]["simple"]) if auto_rows else 0
    total_count = int(auto_rows[0]["total"]) if auto_rows else 0

    # 7. Developer activity summary
    dev_rows = await _ch_json(
        "SELECT user_id, count() AS sessions "
        "FROM session_stats_agg FINAL "
        "WHERE project_id = '{project_id}' "
        "AND first_event_time >= now() - INTERVAL 30 DAY "
        "GROUP BY user_id ORDER BY sessions DESC",
    )

    # Build metrics dict for LLM
    metrics_data = {
        "adoption": {
            "total_users": total_users,
            "active_users": active_users,
            "adoption_pct": adoption_pct,
        },
        "model_comparison": [
            {
                "model": r["model"],
                "sessions": int(r["sessions"]),
                "avg_cost": 0.0,
                "avg_tokens": int(float(r.get("avg_tokens") or 0)),
            }
            for r in model_rows
        ],
        "platform_comparison": [
            {
                "platform": r["harness"],
                "sessions": int(r["sessions"]),
                "users": int(r["users"]),
                "avg_task_seconds": float(r.get("avg_task_seconds") or 0),
            }
            for r in platform_rows
        ],
        "department_gaps": dept_data,
        "quick_win_candidates": [
            {
                "model": r["model"],
                "sessions": int(r["sessions"]),
                "total_cost": 0.0,
                "avg_tokens": int(float(r.get("avg_tokens") or 0)),
            }
            for r in expensive_rows
        ],
        "automatable": {
            "simple_sessions": simple_count,
            "total_sessions": total_count,
            "automatable_pct": round((simple_count / total_count) * 100, 1) if total_count > 0 else 0,
        },
        "developer_breakdown": {
            "total_active": len(dev_rows),
            "total_sessions": sum(int(r.get("sessions", 0)) for r in dev_rows),
            "total_cost": 0.0,
            "top_20_sessions": sum(int(r.get("sessions", 0)) for r in dev_rows[: max(1, len(dev_rows) // 5)]),
        },
    }

    result = await generate_strategic_insights(metrics_data)

    if not result:
        raise HTTPException(
            status_code=503,
            detail="Insights model is not configured or failed to generate a report",
        )

    response = AIInsightsResponse(
        quick_wins=result.get("quick_wins", []),
        adoption_gaps=result.get("adoption_gaps", []),
        platform_insight=result.get("platform_insight", {}),
        model_insight=result.get("model_insight", {}),
        automation_opportunity=result.get("automation_opportunity", {}),
        usage_pattern=result.get("usage_pattern", {}),
        generated=True,
        generated_at=dt.now(UTC).isoformat(),
    )

    try:
        await get_redis().set(_ai_insights_cache_key(current_user), json.dumps(response.model_dump(mode="json")))
    except RedisError as e:
        logger.error("exec_ai_insights_cache_write_failed", error=str(e))
        raise HTTPException(status_code=503, detail="Executive AI insights cache is unavailable") from e

    return response
