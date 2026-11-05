# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deployment-wide security, registry, and cache administration routes."""

from fastapi import Depends
from loguru import logger as optic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import services.dynamic_settings as ds
from api.deps import get_db, require_role
from models.enterprise_config import EnterpriseConfig
from models.user import User, UserRole
from services.security_events import EventType, SecurityEvent, Severity, emit_security_event
from services.user_search import clickhouse_user_conditions, resolve_user_filter_values

from ._router import router


async def _set_bool(db: AsyncSession, key: str, value: bool) -> bool:
    result = await db.execute(select(EnterpriseConfig).where(EnterpriseConfig.key == key))
    config = result.scalar_one_or_none()
    stored = "true" if value else "false"
    if config is None:
        db.add(EnterpriseConfig(key=key, value=stored))
    else:
        config.value = stored
    await db.commit()
    await ds.invalidate(key)
    await ds.refresh_sync_cache()
    return value


@router.get("/security-events")
async def get_security_events(
    event_type: str | None = None,
    severity: str | None = None,
    actor_email: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Query the deployment security event log from ClickHouse."""
    del current_user
    from services.clickhouse import _query

    conditions = ["1 = 1"]
    params: dict[str, str] = {}
    if event_type:
        conditions.append("event_type = {et:String}")
        params["param_et"] = event_type
    if severity:
        conditions.append("severity = {sev:String}")
        params["param_sev"] = severity
    if actor_email:
        values = await resolve_user_filter_values(db, actor_email)
        actor_conditions = clickhouse_user_conditions(
            id_column="actor_id",
            email_column="actor_email",
            values=values,
            prefix="actor",
            params=params,
        )
        if actor_conditions:
            conditions.append("(" + " OR ".join(actor_conditions) + ")")
        else:
            conditions.append("actor_email = {ae:String}")
            params["param_ae"] = actor_email

    limit = min(max(int(limit), 1), 1000)
    offset = max(int(offset), 0)
    where = " AND ".join(conditions)
    sql = (
        f"SELECT * FROM security_events WHERE {where} ORDER BY timestamp DESC LIMIT {limit} OFFSET {offset} FORMAT JSON"
    )
    response = await _query(sql, params)
    response.raise_for_status()
    count_response = await _query(f"SELECT count() AS total FROM security_events WHERE {where} FORMAT JSON", params)
    count_response.raise_for_status()
    return {"events": response.json().get("data", []), "total": int(count_response.json()["data"][0]["total"])}


@router.get("/trace-privacy")
async def get_trace_privacy(current_user: User = Depends(require_role(UserRole.admin))):
    del current_user
    return {"trace_privacy": ds.get_sync_bool("security.trace_privacy")}


@router.put("/trace-privacy")
async def set_trace_privacy(
    req: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    enabled = bool(req.get("trace_privacy", False))
    await _set_bool(db, "security.trace_privacy", enabled)
    await emit_security_event(
        SecurityEvent(
            event_type=EventType.SETTING_CHANGED,
            severity=Severity.WARNING,
            outcome="success",
            actor_id=str(current_user.id),
            actor_email=current_user.email,
            actor_role=current_user.role.value,
            target_id="security.trace_privacy",
            target_type="setting",
            detail=f"Trace privacy {'enabled' if enabled else 'disabled'}",
        )
    )
    return {"trace_privacy": enabled}


@router.get("/registered-agents-only")
async def get_registered_agents_only(current_user: User = Depends(require_role(UserRole.user))):
    del current_user
    return {"registered_agents_only": ds.get_sync_bool("registry.registered_agents_only")}


@router.put("/registered-agents-only")
async def set_registered_agents_only(
    req: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.super_admin)),
):
    enabled = bool(req.get("registered_agents_only", False))
    await _set_bool(db, "registry.registered_agents_only", enabled)
    await emit_security_event(
        SecurityEvent(
            event_type=EventType.SETTING_CHANGED,
            severity=Severity.WARNING,
            outcome="success",
            actor_id=str(current_user.id),
            actor_email=current_user.email,
            actor_role=current_user.role.value,
            target_id="registry.registered_agents_only",
            target_type="setting",
            detail=f"Registered-agents-only {'enabled' if enabled else 'disabled'}",
        )
    )
    return {"registered_agents_only": enabled}


@router.post("/cache/clear")
async def clear_cache(current_user: User = Depends(require_role(UserRole.admin))):
    """Clear all cached dashboard and OTEL responses."""
    optic.trace("user_id={}", current_user.id)
    from services.cache import invalidate_all

    deleted = await invalidate_all()
    return {"cleared": deleted}
