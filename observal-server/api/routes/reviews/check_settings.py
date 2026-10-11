# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Which automated review checks run: organization (admins) and teamspace (team owners) settings."""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import services.dynamic_settings as ds
from api.deps import get_current_user, get_db
from api.routes.reviews.policy import _team_owner
from models.enterprise_config import EnterpriseConfig
from models.user import User, UserRole
from services.audit import audit_detail
from services.review import check_config

router = APIRouter(tags=["reviews"])


class CheckSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    checks: dict[str, dict[str, bool]] = {}


def _view(scope: str, stored: dict) -> dict:
    effective = check_config.resolve(stored)
    return {
        "scope": scope,
        "checks": [
            {
                "id": spec.id,
                "name": spec.name,
                "description": spec.description,
                "subjects": list(spec.subjects),
                "network": spec.network,
                "locked": spec.locked,
                "enabled": True if spec.locked else effective[spec.id]["enabled"],
                "required": True if spec.locked else effective[spec.id]["required"],
            }
            for spec in check_config.SPECS
        ],
    }


async def _save(db: AsyncSession, key: str, data: CheckSettingsUpdate) -> dict:
    try:
        stored = check_config.parse_config(data.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    value = json.dumps({"checks": stored})
    setting = await db.scalar(select(EnterpriseConfig).where(EnterpriseConfig.key == key))
    if setting is None:
        db.add(EnterpriseConfig(key=key, value=value))
    else:
        setting.value = value
    await db.commit()
    await ds.invalidate(key)
    return stored


def _require_admin(user: User) -> None:
    if user.role not in (UserRole.admin, UserRole.super_admin):
        raise HTTPException(403, "Admin required")


@router.get("/api/v1/admin/review-checks")
async def get_org_checks(user: User = Depends(get_current_user)):
    _require_admin(user)
    return _view("organization", await check_config.load_stored())


@router.put("/api/v1/admin/review-checks")
async def put_org_checks(
    data: CheckSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_admin(user)
    stored = await _save(db, check_config.ORG_KEY, data)
    audit_detail(request, action="review.checks_changed", resource_type="review_checks", resource_id="organization")
    return _view("organization", stored)


@router.get("/api/v1/teams/{team_id}/review-checks")
async def get_team_checks(
    team_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    await _team_owner(db, team_id, user)
    return _view("teamspace", await check_config.load_stored(team_id, is_private=True))


@router.put("/api/v1/teams/{team_id}/review-checks")
async def put_team_checks(
    team_id: uuid.UUID,
    data: CheckSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _team_owner(db, team_id, user)
    stored = await _save(db, check_config.team_key(team_id), data)
    audit_detail(request, action="review.checks_changed", resource_type="team", resource_id=team_id)
    return _view("teamspace", stored)
