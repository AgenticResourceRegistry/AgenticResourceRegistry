# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cost Intelligence endpoints for the executive dashboard.

    GET  /api/v1/exec/cost/intelligence                       full dashboard payload (read-only)
    POST /api/v1/exec/cost/refresh                            re-run detection now (also runs every 30 min)
    GET  /api/v1/exec/cost/settings                           pricing and billing settings
    PUT  /api/v1/exec/cost/settings/{key}                     update one cost.* setting
    POST /api/v1/exec/cost/opportunities/{id}/approve         freeze baseline, move to approved
    POST /api/v1/exec/cost/opportunities/{id}/implemented     mark shipped on a version
    POST /api/v1/exec/cost/opportunities/{id}/dismiss         drop from the pipeline
    POST /api/v1/exec/cost/opportunities/{id}/reopen          back to identified

Spend is priced from per-call token telemetry; savings estimates come from
deterministic detectors; realized savings are measured after a change ships.
See ``services/cost``.
"""

from __future__ import annotations

import uuid  # noqa: TC003 - FastAPI resolves path parameter types at runtime
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger as optic
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002 - FastAPI resolves this at runtime

from api.deps import get_db, require_role
from models.agent import AgentVersion
from models.cost_opportunity import (
    STATUS_APPROVED,
    STATUS_DISMISSED,
    STATUS_IDENTIFIED,
    STATUS_IMPLEMENTED,
    CostOpportunity,
)
from models.user import User, UserRole
from services.cost import service
from services.cost.ledger import CostDataUnavailableError

router = APIRouter(prefix="/api/v1/exec/cost", tags=["cost-intelligence"])


class DismissRequest(BaseModel):
    reason: str = Field("", max_length=2000)


class ImplementedRequest(BaseModel):
    # Required for agent changes (the released version); omitted for billing
    # changes such as removing seats, which are not tied to an agent release.
    version: str | None = Field(None, min_length=1, max_length=50)


COST_SETTING_KEYS = (
    "cost.billing",
    "cost.price_overrides",
    "cost.price_multipliers",
    "cost.credit_usd",
    "cost.timezone",
    "cost.opportunity_min_monthly_usd",
)


class SettingUpdate(BaseModel):
    value: str = Field("", max_length=20000)


@router.get("/intelligence")
async def get_cost_intelligence(
    refresh: bool = Query(False, description="Rebuild from telemetry instead of serving the 5-minute cache"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
) -> dict:
    try:
        return await service.cached_cost_intelligence(db, refresh=refresh)
    except CostDataUnavailableError as exc:
        optic.warning("cost intelligence unavailable: {}", exc)
        raise HTTPException(
            status_code=503, detail="Cost data is temporarily unavailable: telemetry could not be read."
        ) from exc


@router.post("/refresh")
async def refresh_cost_intelligence(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
) -> dict:
    """Detect opportunities and mark shipped ones implemented, then rebuild the dashboard."""
    try:
        result = await service.refresh(db)
    except CostDataUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Cost data is temporarily unavailable: telemetry could not be read."
        ) from exc
    optic.info("cost intelligence refreshed by={} opportunities={}", current_user.id, result["opportunities"])
    return result


@router.get("/settings")
async def get_cost_settings(current_user: User = Depends(require_role(UserRole.admin))) -> dict:
    import services.dynamic_settings as ds

    return {
        "settings": [
            {"key": key, "value": await ds.get(key), "help": ds.SETTING_SUBTITLES.get(key, "")}
            for key in COST_SETTING_KEYS
        ]
    }


@router.put("/settings/{key}")
async def put_cost_setting(
    key: str,
    req: SettingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
) -> dict:
    """Admins manage cost inputs here; other deployment settings stay super-admin only."""
    import services.dynamic_settings as ds
    from models.enterprise_config import EnterpriseConfig
    from services.cost.pricing import validate_setting

    if key not in COST_SETTING_KEYS:
        raise HTTPException(status_code=404, detail="Not a cost setting")
    value = req.value.strip()
    try:
        value = validate_setting(key, value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    cfg = (await db.execute(select(EnterpriseConfig).where(EnterpriseConfig.key == key))).scalar_one_or_none()
    if cfg is None:
        db.add(EnterpriseConfig(key=key, value=value))
    else:
        cfg.value = value
    await db.commit()
    await ds.invalidate(key)
    await ds.refresh_sync_cache()
    await service.invalidate_cache()
    optic.info("cost setting updated key={} by={}", key, current_user.id)
    # Pricing and billing change what each saving is worth and whether it lowers
    # the bill, so re-run detection now rather than at the next scheduled refresh.
    try:
        await service.refresh(db)
    except CostDataUnavailableError:
        optic.warning("cost refresh after setting change skipped: telemetry unavailable")
    return {"key": key, "value": value}


async def _load(db: AsyncSession, opportunity_id: uuid.UUID) -> CostOpportunity:
    opp = (await db.execute(select(CostOpportunity).where(CostOpportunity.id == opportunity_id))).scalar_one_or_none()
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return opp


def _require(opp: CostOpportunity, *allowed: str) -> None:
    if opp.status not in allowed:
        raise HTTPException(
            status_code=409, detail=f"Opportunity is {opp.status}; this action needs one of: {', '.join(allowed)}"
        )


@router.post("/opportunities/{opportunity_id}/approve")
async def approve_opportunity(
    opportunity_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
) -> dict:
    opp = await _load(db, opportunity_id)
    _require(opp, STATUS_IDENTIFIED)
    try:
        opp = await service.approve(db, opp, current_user.id, datetime.now(UTC))
    except CostDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Cannot freeze the baseline: telemetry could not be read.") from exc
    optic.info("cost opportunity approved id={} by={}", opp.id, current_user.id)
    return {"id": str(opp.id), "status": opp.status, "baseline": opp.baseline}


@router.post("/opportunities/{opportunity_id}/implemented")
async def mark_implemented(
    opportunity_id: uuid.UUID,
    req: ImplementedRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
) -> dict:
    opp = await _load(db, opportunity_id)
    _require(opp, STATUS_APPROVED)
    if opp.agent_id is None:
        opp.status = STATUS_IMPLEMENTED
        opp.implemented_at = datetime.now(UTC)
        await db.commit()
        await service.invalidate_cache()
        optic.info("cost opportunity implemented id={} by={}", opp.id, current_user.id)
        return {"id": str(opp.id), "status": opp.status, "implemented_version": None}
    if not req.version:
        raise HTTPException(status_code=422, detail="Give the agent version that shipped this change")
    version = (
        await db.execute(
            select(AgentVersion).where(AgentVersion.agent_id == opp.agent_id, AgentVersion.version == req.version)
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=422, detail=f"Version {req.version} does not exist for this agent")
    if req.version == opp.baseline_version:
        raise HTTPException(status_code=422, detail="The implementing version must differ from the baseline version")
    opp.status = STATUS_IMPLEMENTED
    opp.implemented_version = req.version
    opp.implemented_at = version.released_at or datetime.now(UTC)
    await db.commit()
    await service.invalidate_cache()
    optic.info("cost opportunity implemented id={} version={} by={}", opp.id, req.version, current_user.id)
    return {"id": str(opp.id), "status": opp.status, "implemented_version": opp.implemented_version}


@router.post("/opportunities/{opportunity_id}/dismiss")
async def dismiss_opportunity(
    opportunity_id: uuid.UUID,
    req: DismissRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
) -> dict:
    opp = await _load(db, opportunity_id)
    _require(opp, STATUS_IDENTIFIED, STATUS_APPROVED)
    opp.status = STATUS_DISMISSED
    opp.dismissed_at = datetime.now(UTC)
    opp.note = req.reason.strip() or None
    await db.commit()
    await service.invalidate_cache()
    optic.info("cost opportunity dismissed id={} by={}", opp.id, current_user.id)
    return {"id": str(opp.id), "status": opp.status}


@router.post("/opportunities/{opportunity_id}/reopen")
async def reopen_opportunity(
    opportunity_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
) -> dict:
    opp = await _load(db, opportunity_id)
    _require(opp, STATUS_DISMISSED, STATUS_APPROVED)
    opp.status = STATUS_IDENTIFIED
    opp.approved_at = None
    opp.approved_by = None
    opp.baseline = None
    opp.dismissed_at = None
    await db.commit()
    await service.invalidate_cache()
    optic.info("cost opportunity reopened id={} by={}", opp.id, current_user.id)
    return {"id": str(opp.id), "status": opp.status}
