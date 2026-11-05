# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Administrator controls for the optional aggregate usage ping."""

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, require_role, require_super_admin
from models.user import User, UserRole
from schemas.usage_ping import UsagePingAdminResponse, UsagePingStatus
from services.usage_ping import build_usage_ping, send_usage_ping, usage_ping_status

from ._router import router


@router.get("/usage-ping/status", response_model=UsagePingStatus)
async def get_usage_ping_status(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_role(UserRole.admin)),
):
    return await usage_ping_status(db)


@router.get("/usage-ping/preview", response_model=UsagePingAdminResponse)
async def preview_usage_ping(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_role(UserRole.admin)),
):
    status = await usage_ping_status(db)
    return UsagePingAdminResponse(status=status, payload=await build_usage_ping(db))


@router.post("/usage-ping/send", response_model=UsagePingAdminResponse)
async def trigger_usage_ping(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_super_admin),
):
    status = await usage_ping_status(db)
    if not status.enabled:
        raise HTTPException(status_code=409, detail="Usage reporting is disabled")
    result = await send_usage_ping(db)
    if result != "sent":
        updated = await usage_ping_status(db)
        raise HTTPException(status_code=502, detail=updated.last_error or "Usage report delivery failed")
    return UsagePingAdminResponse(status=await usage_ping_status(db), payload=await build_usage_ping(db))
