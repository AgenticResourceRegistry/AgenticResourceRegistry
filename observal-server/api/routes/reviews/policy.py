# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Organization and teamspace policy, with immediate open-review re-evaluation."""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import services.dynamic_settings as ds
from api.deps import get_current_user, get_db
from api.routes.reviews.common import notify_update
from models.enterprise_config import EnterpriseConfig
from models.review import Review, ReviewState
from models.team import Team, TeamMembership, TeamRole
from models.user import User, UserRole
from schemas.review import PolicyUpdate
from services.audit import audit_detail
from services.review import notifications
from services.review.decisions import _event, gate, sync_state
from services.review.policy import parse_policy, policy_for

router = APIRouter(tags=["reviews"])


async def _team_owner(db, team_id, user, *, action: str):
    # Shared by reads and writes, so the 403 names what the caller actually tried.
    team = await db.get(Team, team_id)
    if team is None:
        raise HTTPException(404, "Teamspace not found")
    if user.role not in (UserRole.admin, UserRole.super_admin):
        membership = await db.scalar(
            select(TeamMembership).where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == user.id,
                TeamMembership.role == TeamRole.owner,
            )
        )
        if membership is None:
            raise HTTPException(403, f"Only team owners may {action}")
    return team


async def _write(db, key, data, actor_id, *, team_id=None):
    value = data.model_dump(exclude_none=True)
    try:
        parse_policy(value, source="teamspace" if team_id else "organization")
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    stmt = select(Review).where(
        Review.state.in_((ReviewState.open, ReviewState.changes_requested, ReviewState.approved))
    )
    if team_id:
        stmt = stmt.where(Review.team_id == team_id)
    reviews = (await db.scalars(stmt)).all()
    before = {review.id: (await gate(db, review)).ready for review in reviews}
    setting = await db.scalar(select(EnterpriseConfig).where(EnterpriseConfig.key == key))
    if setting is None:
        setting = EnterpriseConfig(key=key, value=json.dumps(value))
        db.add(setting)
    else:
        setting.value = json.dumps(value)
    for review in reviews:
        _event(db, review, "policy_changed", actor_id)
        await notifications.deliver_event(db, review, "policy_changed", actor_id)
        effective = await policy_for(
            review,
            db=db,
            org_raw=value if team_id is None else None,
            team_raw=value if team_id is not None else None,
        )
        await sync_state(db, review, actor_id=actor_id, policy=effective)
        await notifications.deliver_gate_change(db, review, before[review.id], actor_id, policy=effective)
    await db.commit()
    await ds.invalidate(key)
    for review in reviews:
        await notify_update(review, "gate")
    return value


@router.get("/api/v1/admin/review-policy")
async def get_org_policy(user: User = Depends(get_current_user)):
    if user.role != UserRole.super_admin:
        raise HTTPException(403, "Super admin required")
    return json.loads(await ds.get("review.policy", default="{}") or "{}")


@router.put("/api/v1/admin/review-policy")
async def put_org_policy(
    data: PolicyUpdate, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    if user.role != UserRole.super_admin:
        raise HTTPException(403, "Super admin required")
    result = await _write(db, "review.policy", data, user.id)
    audit_detail(request, action="review.policy_changed", resource_type="review_policy", resource_id="organization")
    return result


@router.get("/api/v1/teams/{team_id}/review-policy")
async def get_team_policy(
    team_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    await _team_owner(db, team_id, user, action="view review policy")
    value = await ds.get(f"review.policy.team.{team_id}", default="")
    return json.loads(value) if value else None


@router.put("/api/v1/teams/{team_id}/review-policy")
async def put_team_policy(
    team_id: uuid.UUID,
    data: PolicyUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _team_owner(db, team_id, user, action="edit review policy")
    result = await _write(db, f"review.policy.team.{team_id}", data, user.id, team_id=team_id)
    audit_detail(request, action="review.policy_changed", resource_type="team", resource_id=team_id)
    return result
