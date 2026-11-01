# SPDX-FileCopyrightText: 2026 Chandhini Veerabuthiran <Chandhini03@users.noreply.github.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Admin endpoint to toggle the recommended flag on agents and component listings."""

from __future__ import annotations

import uuid  # noqa: TC003 - Pydantic needs uuid.UUID at runtime for validation
from typing import Literal

from fastapi import Depends, HTTPException, Request
from loguru import logger as optic
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002 - FastAPI needs the type at runtime for Depends()
from sqlalchemy.orm.attributes import flag_modified

from api.deps import get_db, require_role
from models.agent import Agent
from models.hook import HookListing
from models.mcp import McpListing
from models.prompt import PromptListing
from models.sandbox import SandboxListing
from models.skill import SkillListing
from models.user import User, UserRole
from services.audit.helpers import audit_detail

from ._router import router

EntityType = Literal["agent", "mcp", "skill", "hook", "prompt", "sandbox"]

_MODEL_MAP: dict[str, type] = {
    "agent": Agent,
    "mcp": McpListing,
    "skill": SkillListing,
    "hook": HookListing,
    "prompt": PromptListing,
    "sandbox": SandboxListing,
}


class SetRecommendedRequest(BaseModel):
    entity_type: EntityType
    entity_id: uuid.UUID
    recommended: bool


class SetRecommendedResponse(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    is_recommended: bool


@router.patch("/recommended", response_model=SetRecommendedResponse)
async def set_recommended(
    req: SetRecommendedRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Toggle the recommended flag on an agent or component listing. Admin only."""
    optic.info("set_recommended: type={}, id={}, recommended={}", req.entity_type, req.entity_id, req.recommended)

    model = _MODEL_MAP.get(req.entity_type)
    if model is None:
        raise HTTPException(status_code=422, detail="Invalid entity_type")

    entity = (await db.execute(select(model).where(model.id == req.entity_id))).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail=f"{req.entity_type} not found")

    entity.is_recommended = req.recommended
    # Curation is not an edit: writing updated_at back explicitly stops its
    # onupdate default, so "recently updated" ordering and updatedAt stay put.
    flag_modified(entity, "updated_at")
    audit_detail(
        request,
        action="registry.recommended.update",
        resource_type=req.entity_type,
        resource_id=str(entity.id),
        resource_name=entity.qualified_name,
        detail=f"recommended={req.recommended}",
    )
    await db.commit()
    await db.refresh(entity)

    return SetRecommendedResponse(
        entity_type=req.entity_type,
        entity_id=req.entity_id,
        is_recommended=entity.is_recommended,
    )
