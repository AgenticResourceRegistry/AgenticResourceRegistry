# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deployment-wide data retention routes."""

from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import services.dynamic_settings as ds
from api.deps import get_db, require_role
from models.agent import Agent
from models.enterprise_config import EnterpriseConfig
from models.insight_report import InsightReport, InsightReportStatus
from models.user import User, UserRole
from observal_shared.migration.constants import DEFAULT_PROJECT_ID
from schemas.retention import RetentionConfigResponse, RetentionConfigUpdate
from services.security_events import EventType, SecurityEvent, Severity, emit_security_event

from ._router import router


async def _read_config() -> RetentionConfigResponse:
    trace_days = await ds.get_int("retention.trace_days", default=0)
    score_days = await ds.get_int("retention.score_days", default=0)
    max_trace_count = await ds.get_int("retention.max_trace_count", default=0)
    return RetentionConfigResponse(
        retention_enabled=await ds.get_bool("retention.enabled"),
        data_retention_days=trace_days or None,
        score_retention_days=score_days or None,
        max_trace_count=max_trace_count or None,
        global_retention_days=await ds.get_int("data.retention_days"),
    )


async def _write_config(db: AsyncSession, body: RetentionConfigUpdate) -> RetentionConfigResponse:
    values = {
        "retention.enabled": "true" if body.retention_enabled else "false",
        "retention.trace_days": str(body.data_retention_days or ""),
        "retention.score_days": str(body.score_retention_days or ""),
        "retention.max_trace_count": str(body.max_trace_count or ""),
    }
    rows = await db.execute(select(EnterpriseConfig).where(EnterpriseConfig.key.in_(tuple(values))))
    configs = {config.key: config for config in rows.scalars().all()}
    for key, value in values.items():
        config = configs.get(key)
        if config is None:
            db.add(EnterpriseConfig(key=key, value=value))
        else:
            config.value = value
    await db.commit()
    for key in values:
        await ds.invalidate(key)
    await ds.refresh_sync_cache()
    return await _read_config()


@router.get("/retention")
async def get_retention_config(current_user: User = Depends(require_role(UserRole.admin))) -> RetentionConfigResponse:
    del current_user
    return await _read_config()


@router.put("/retention")
async def update_retention_config(
    body: RetentionConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.super_admin)),
) -> RetentionConfigResponse:
    config = await _read_config()
    if (
        body.data_retention_days is not None
        and config.global_retention_days > 0
        and body.data_retention_days > config.global_retention_days
    ):
        raise HTTPException(
            status_code=422,
            detail=f"data_retention_days cannot exceed global ceiling of {config.global_retention_days} days",
        )

    updated = await _write_config(db, body)
    await emit_security_event(
        SecurityEvent(
            event_type=EventType.SETTING_CHANGED,
            severity=Severity.WARNING,
            outcome="success",
            actor_id=str(current_user.id),
            actor_email=current_user.email,
            actor_role=current_user.role.value,
            target_id="retention",
            target_type="setting",
            detail=f"Data retention {'enabled' if body.retention_enabled else 'disabled'}"
            f" (days={body.data_retention_days}, scores={body.score_retention_days}, max={body.max_trace_count})",
        )
    )
    return updated


@router.get("/retention/preview")
async def preview_retention(
    # The score cutoff is now minus 2 * days; a huge value lands before year 1 and raises OverflowError.
    days: int = Query(le=36_500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.super_admin)),
):
    del current_user
    if days < 7:
        raise HTTPException(status_code=422, detail="days must be >= 7")

    from services.clickhouse import _query

    response = await _query(
        "SELECT count() AS cnt FROM session_events "
        "WHERE project_id = {pid:String} AND timestamp < now() - INTERVAL {days:UInt32} DAY FORMAT JSON",
        {"param_pid": DEFAULT_PROJECT_ID, "param_days": str(days)},
    )
    counts = {"session_events": 0}
    if response.status_code == 200:
        data = response.json().get("data", [])
        counts["session_events"] = int(data[0].get("cnt", 0)) if data else 0

    score_cutoff = datetime.now(UTC) - timedelta(days=days * 2)
    report_count = (
        await db.execute(
            select(func.count())
            .select_from(InsightReport)
            .where(
                InsightReport.agent_id.in_(select(Agent.id)),
                InsightReport.completed_at < score_cutoff,
                InsightReport.status == InsightReportStatus.completed,
            )
        )
    ).scalar() or 0
    counts["insight_reports"] = report_count
    counts["_note"] = "approximate; counts may be higher if a purge ran recently"
    return counts


@router.get("/retention/stats")
async def get_retention_stats(current_user: User = Depends(require_role(UserRole.admin))):
    del current_user
    config = await _read_config()
    if not config.retention_enabled:
        return {
            "retention_enabled": False,
            "data_retention_days": config.data_retention_days,
            "score_retention_days": config.score_retention_days,
            "total_traces": 0,
            "oldest_trace_age_days": 0,
            "traces_expiring_7d": 0,
            "next_purge_approx": None,
        }

    from services.clickhouse import _query

    response = await _query(
        "SELECT count(DISTINCT session_id) AS cnt, "
        "if(cnt > 0, dateDiff('day', min(timestamp), now()), 0) AS age "
        "FROM session_events WHERE project_id = {pid:String} FORMAT JSON",
        {"param_pid": DEFAULT_PROJECT_ID},
    )
    total_traces = 0
    oldest_age_days = 0
    if response.status_code == 200:
        data = response.json().get("data", [])
        if data:
            total_traces = int(data[0].get("cnt", 0))
            oldest_age_days = int(data[0].get("age", 0)) if total_traces else 0

    traces_expiring = 0
    if config.data_retention_days:
        cutoff_soon = config.data_retention_days - 7
        if cutoff_soon > 0:
            response = await _query(
                "SELECT count(DISTINCT session_id) AS cnt FROM session_events "
                "WHERE project_id = {pid:String} "
                "AND timestamp < now() - INTERVAL {days:UInt32} DAY FORMAT JSON",
                {"param_pid": DEFAULT_PROJECT_ID, "param_days": str(cutoff_soon)},
            )
            if response.status_code == 200:
                data = response.json().get("data", [])
                traces_expiring = int(data[0].get("cnt", 0)) if data else 0

    return {
        "retention_enabled": True,
        "data_retention_days": config.data_retention_days,
        "score_retention_days": config.score_retention_days,
        "total_traces": total_traces,
        "oldest_trace_age_days": oldest_age_days,
        "traces_expiring_7d": traces_expiring,
        "next_purge_approx": "Every 6 hours (01:30, 07:30, 13:30, 19:30 UTC)",
    }


@router.get("/retention/warnings")
async def get_retention_warnings(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(require_role(UserRole.admin))
):
    del current_user
    config = await _read_config()
    if not config.retention_enabled or not config.data_retention_days:
        return {
            "warnings": [],
            "retention_days": config.data_retention_days,
            "retention_enabled": config.retention_enabled,
        }

    agents = (await db.execute(select(Agent.id, Agent.name))).all()
    if not agents:
        return {"warnings": [], "retention_days": config.data_retention_days, "retention_enabled": True}

    latest_reports: dict[object, datetime | None] = {}
    for agent_id, _ in agents:
        latest_reports[agent_id] = (
            await db.execute(
                select(InsightReport.completed_at)
                .where(InsightReport.agent_id == agent_id, InsightReport.status == InsightReportStatus.completed)
                .order_by(InsightReport.completed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    retention_start = datetime.now(UTC) - timedelta(days=config.data_retention_days)
    warnings = []
    for agent_id, agent_name in agents:
        last_report = latest_reports.get(agent_id)
        if last_report is None or last_report < retention_start:
            warnings.append(
                {
                    "agent_id": str(agent_id),
                    "agent_name": agent_name or "Unnamed Agent",
                    "traces_expiring_soon": 0,
                    "last_insight_report": last_report.isoformat() if last_report else None,
                }
            )

    return {"warnings": warnings, "retention_days": config.data_retention_days, "retention_enabled": True}
