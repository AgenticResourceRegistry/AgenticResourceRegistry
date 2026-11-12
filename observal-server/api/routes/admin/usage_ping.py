# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Administrator controls for the optional aggregate usage ping."""

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, require_role, require_super_admin
from models.user import User, UserRole
from schemas.usage_ping import (
    UsagePingAdminResponse,
    UsagePingContactDefer,
    UsagePingContactState,
    UsagePingContactUpdate,
    UsagePingStatus,
)
from services.usage_ping import (
    build_usage_ping,
    contact_state,
    defer_contact,
    save_contact,
    send_usage_ping,
    usage_ping_status,
    withdraw_contact,
)

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
    # The payload carries the shared contact, which only super admins may read.
    _current_user: User = Depends(require_super_admin),
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


@router.get("/usage-ping/contact", response_model=UsagePingContactState)
async def get_usage_ping_contact(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_super_admin),
):
    """Return the shared contact and whether to prompt for one."""
    return await contact_state(db)


@router.put("/usage-ping/contact", response_model=UsagePingContactState)
async def put_usage_ping_contact(
    req: UsagePingContactUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_super_admin),
):
    """Save the contact a super administrator agreed to share."""
    return await save_contact(db, req)


@router.delete("/usage-ping/contact", response_model=UsagePingContactState)
async def delete_usage_ping_contact(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_super_admin),
):
    """Stop sending the contact with future reports."""
    return await withdraw_contact(db)


@router.post("/usage-ping/contact/defer", response_model=UsagePingContactState)
async def defer_usage_ping_contact(
    req: UsagePingContactDefer,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_super_admin),
):
    """Record "Not now" or "Don't ask again" for the contact prompt."""
    return await defer_contact(db, never=req.never)
