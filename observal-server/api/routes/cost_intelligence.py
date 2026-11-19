# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cost Intelligence endpoints for the executive dashboard."""

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger as optic
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, require_role
from models.user import User, UserRole
from schemas.cost import CostDetails, CostOverview, RangeKey
from services.cost import service
from services.cost.queries import CostDataUnavailableError

router = APIRouter(prefix="/api/v1/exec/cost", tags=["exec-dashboard"])

_UNAVAILABLE = "Usage data is temporarily unavailable. Try again shortly."


@router.get("/overview", response_model=CostOverview)
async def cost_overview(
    range_: RangeKey = Query("30d", alias="range"),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    optic.trace("range={}", range_)
    try:
        return await service.build_overview(range_)
    except CostDataUnavailableError:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)


@router.get("/details", response_model=CostDetails)
async def cost_details(
    range_: RangeKey = Query("30d", alias="range"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    optic.trace("range={}", range_)
    try:
        return await service.build_details(db, range_)
    except CostDataUnavailableError:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
