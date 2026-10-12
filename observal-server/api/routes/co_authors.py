# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Co-author management endpoints for agents and component listings."""

from __future__ import annotations

import uuid as _uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import (
    check_listing_visibility_async,
    commit_or_name_conflict,
    get_current_user,
    get_db,
    get_effective_agent_permission,
    get_effective_component_permission,
    resolve_listing,
)
from models.agent import Agent, AgentVersion
from models.hook import HookListing, HookVersion
from models.mcp import McpListing, McpVersion
from models.prompt import PromptListing, PromptVersion
from models.sandbox import SandboxListing, SandboxVersion
from models.skill import SkillListing, SkillVersion
from models.team import TeamRole
from models.user import User
from services import dynamic_settings as _ds
from services.inbox import sources as inbox
from services.ownership import transfer_entity_owner
from services.registry_namespace import identity_exists
from services.skill_identity import rebind_skill_identity, snapshot_skill_identity
from services.teamspace import (
    is_admin,
    review_publication_to_public,
    review_skill_identity_transfer,
    skill_transition_needs_rollout_gate,
    team_membership,
)

router = APIRouter(prefix="/api/v1", tags=["co-authors"])

# Map entity type to model class
ENTITY_MODELS: dict[str, type] = {
    "agents": Agent,
    "mcps": McpListing,
    "hooks": HookListing,
    "sandboxes": SandboxListing,
    "prompts": PromptListing,
    "skills": SkillListing,
}

# Singular subject names, spelled out rather than derived. Trimming the trailing
# "s" turns "sandboxes" into "sandboxe", which would be stored on inbox items and
# break their links.
ENTITY_SINGULAR: dict[str, str] = {
    "agents": "agent",
    "mcps": "mcp",
    "hooks": "hook",
    "sandboxes": "sandbox",
    "prompts": "prompt",
    "skills": "skill",
}

# Map entity type to version model class
VERSION_MODELS: dict[str, type] = {
    "agents": AgentVersion,
    "mcps": McpVersion,
    "hooks": HookVersion,
    "sandboxes": SandboxVersion,
    "prompts": PromptVersion,
    "skills": SkillVersion,
}


class AddCoAuthorRequest(BaseModel):
    email: str | None = None
    username: str | None = None
    user_id: str | None = None


class TransferOwnershipRequest(BaseModel):
    username: str | None = None
    email: str | None = None
    user_id: str | None = None


class CoAuthorResponse(BaseModel):
    id: str
    email: str
    username: str | None = None
    is_active: bool = True


class TransferOwnershipResponse(BaseModel):
    id: str
    owner: str
    owner_id: str
    previous_owner: str
    previous_owner_id: str
    namespace: str
    slug: str
    qualified_name: str


async def _get_entity_and_check_permission(
    entity_type: str,
    entity_id: _uuid.UUID,
    current_user: User,
    db: AsyncSession,
):
    """Load entity and verify the user has owner-level permission."""
    model = ENTITY_MODELS.get(entity_type)
    if not model:
        raise HTTPException(status_code=400, detail=f"Invalid entity type: {entity_type}")

    # resolve_listing, not db.get: it applies the caller's visibility, so a listing
    # they cannot read is not one they can edit the co-authors of either.
    entity = await resolve_listing(model, str(entity_id), db, current_user=current_user)
    if not entity:
        raise HTTPException(status_code=404, detail=f"{entity_type[:-1].title()} not found")

    # Check permission
    if entity_type == "agents":
        perm = get_effective_agent_permission(entity, current_user)
    else:
        perm = get_effective_component_permission(entity, current_user)

    if perm != "owner":
        raise HTTPException(status_code=403, detail="You don't have permission to manage co-authors")

    return entity


async def _get_entity_for_transfer(entity_type: str, entity_id: str, current_user: User, db: AsyncSession):
    model = ENTITY_MODELS.get(entity_type)
    if not model:
        raise HTTPException(status_code=400, detail=f"Invalid entity type: {entity_type}")

    entity = await resolve_listing(model, entity_id, db, current_user=current_user)

    if not entity:
        raise HTTPException(status_code=404, detail=f"{entity_type[:-1].title()} not found")

    # Authority depends on where the listing lives. A team-owned listing belongs to
    # its teamspace, so a team owner or admin may move it even though they did not
    # submit it. Checking the submitter first, as this used to, meant a team owner
    # was refused before the teamspace rule was ever consulted, which left a
    # teamspace impossible to empty and therefore impossible to delete.
    owner_id = entity.created_by if entity_type == "agents" else entity.submitted_by
    if getattr(entity, "team_id", None) is not None:
        if is_admin(current_user):
            return entity
        membership = await team_membership(db, entity.team_id, current_user.id)
        if membership and membership.role == TeamRole.owner:
            return entity
        raise HTTPException(
            status_code=403,
            detail="Only a teamspace owner or an admin can transfer a listing out of a teamspace",
        )
    if owner_id != current_user.id and not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Only the current owner can transfer ownership")
    return entity


async def _resolve_target_user(
    db: AsyncSession,
    *,
    user_id: str | None = None,
    email: str | None = None,
    username: str | None = None,
) -> User:
    if user_id:
        try:
            parsed_id = _uuid.UUID(user_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid user ID") from exc
        result = await db.execute(select(User).where(User.id == parsed_id))
    elif email:
        result = await db.execute(select(User).where(User.email == email.strip().lower()))
    elif username:
        result = await db.execute(select(User).where(User.username == username.strip().lstrip("@")))
    else:
        raise HTTPException(status_code=422, detail="Provide a user")

    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    return target_user


@router.post("/{entity_type}/{entity_id}/transfer-ownership", response_model=TransferOwnershipResponse)
async def transfer_ownership(
    entity_type: str,
    entity_id: str,
    req: TransferOwnershipRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    entity = await _get_entity_for_transfer(entity_type, entity_id, current_user, db)

    # Transfer rewrites the namespace to the target user's handle, so a teamspace
    # listing has to leave its teamspace on the way out. Refusing outright used to
    # be a dead end: deleting a teamspace requires emptying it, emptying it
    # requires transferring, and transferring refused every team-owned listing.
    #
    # Leaving a teamspace also means losing team visibility, because team-private
    # requires a teamspace. That makes the listing public, which is a publication,
    # so it returns to the review queue on the way just like any other
    # private-to-public transition.
    # _get_entity_for_transfer has already authorized the move; it needs the
    # teamspace rule itself in order to decide.
    team_id = getattr(entity, "team_id", None)
    target_user = await _resolve_target_user(db, user_id=req.user_id, email=req.email, username=req.username)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if target_user.auth_provider == "deactivated":
        raise HTTPException(status_code=422, detail="Cannot transfer ownership to a deactivated user")
    if target_user.id == current_user.id and team_id is None:
        raise HTTPException(status_code=422, detail="You already own this item")

    was_private = bool(getattr(entity, "is_private", False))
    identity_snapshot = None
    if entity_type == "skills":
        # Every transfer changes reviewable identity, even when already public.
        # Keep tracked rows blocked by default; on a compatible fleet, lock and
        # rebind every version after computing the final namespace/owner. Legacy
        # publication uses the same listing-then-version review lock order.
        await db.execute(select(SkillListing.id).where(SkillListing.id == entity.id).with_for_update())
        await db.refresh(entity, attribute_names=["team_id", "is_private", "submitted_by"])
        if entity.team_id != team_id or entity.is_private != was_private:
            raise HTTPException(status_code=409, detail="Skill ownership changed; refresh before transferring")
        if team_id is not None and not is_admin(current_user):
            membership = await team_membership(db, team_id, current_user.id)
            if not membership or membership.role != TeamRole.owner:
                raise HTTPException(
                    status_code=403, detail="Team owner membership changed; refresh before transferring"
                )
        if team_id is None and entity.submitted_by != current_user.id and not is_admin(current_user):
            raise HTTPException(status_code=403, detail="Skill ownership changed; refresh before transferring")
        if await skill_transition_needs_rollout_gate(db, entity.id):
            if not await _ds.get_bool("registry.skill_folder_delivery_enabled", False):
                raise HTTPException(
                    status_code=409, detail="Tracked skill transfer requires version identity migration"
                )
            identity_snapshot = await snapshot_skill_identity(db, entity)
            if not was_private and any(row.pre_public_status is not None for row in identity_snapshot.versions):
                # These approvals belong to the former team. Moving the public
                # item to a new owner before global review resolves them would
                # later restore that team's approval in an unrelated teamspace.
                raise HTTPException(
                    status_code=409, detail="Resolve the prior team's public re-review before transferring"
                )
    returned_to_review = False
    if team_id is not None:
        entity.team_id = None
        entity.is_private = False
        if entity_type != "skills":
            returned_to_review = await review_publication_to_public(entity, current_user, db, was_private=was_private)

    model = ENTITY_MODELS[entity_type]
    if await identity_exists(db, model, target_user.username, entity.slug, exclude_id=entity.id):
        raise HTTPException(
            status_code=409,
            detail=f"{target_user.username}/{entity.slug} already exists",
        )

    previous_owner, previous_owner_id = transfer_entity_owner(entity, entity_type, current_user, target_user)
    if entity_type == "skills" and team_id is not None:
        returned_to_review = await review_publication_to_public(entity, current_user, db, was_private=was_private)
    if identity_snapshot is not None:
        if not was_private:
            await review_skill_identity_transfer(entity, current_user, db)
        rebind_skill_identity(entity, identity_snapshot)
    if returned_to_review and entity_type != "skills":
        # Skill transition notices are per version and sent by the helper.
        # Delivered here rather than beside review_publication_to_public: the
        # name-collision check above can still 409 out of this request, and
        # resolving recipients for a transfer that never happens is wasted work.
        await inbox.on_review_requested(
            db,
            entity,
            subject_type=ENTITY_SINGULAR.get(entity_type, entity_type),
            actor_id=current_user.id,
            version=getattr(getattr(entity, "latest_version", None), "version", None),
        )
    await commit_or_name_conflict(db, entity_type[:-1])
    await db.refresh(entity)
    return TransferOwnershipResponse(
        id=str(entity.id),
        owner=entity.owner,
        owner_id=str(target_user.id),
        previous_owner=previous_owner,
        previous_owner_id=str(previous_owner_id),
        namespace=entity.namespace,
        slug=entity.slug,
        qualified_name=entity.qualified_name,
    )


@router.get("/{entity_type}/{entity_id}/co-authors", response_model=list[CoAuthorResponse])
async def list_co_authors(
    entity_type: str,
    entity_id: _uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List co-authors for an agent or component listing."""
    model = ENTITY_MODELS.get(entity_type)
    if not model:
        raise HTTPException(status_code=400, detail=f"Invalid entity type: {entity_type}")

    entity = await db.get(model, entity_id)
    if not entity or not await check_listing_visibility_async(entity, current_user, db):
        raise HTTPException(status_code=404, detail=f"{entity_type[:-1].title()} not found")

    co_author_ids = entity.co_authors or []
    if not co_author_ids:
        return []

    # Resolve user info
    uuids = [_uuid.UUID(str(uid)) for uid in co_author_ids]
    result = await db.execute(select(User).where(User.id.in_(uuids)))
    users = result.scalars().all()

    return [
        CoAuthorResponse(
            id=str(u.id),
            email=u.email,
            username=u.username,
            is_active=u.auth_provider != "deactivated",
        )
        for u in users
    ]


@router.post("/{entity_type}/{entity_id}/co-authors", response_model=CoAuthorResponse)
async def add_co_author(
    entity_type: str,
    entity_id: _uuid.UUID,
    req: AddCoAuthorRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a co-author by selected user, email, or username."""
    entity = await _get_entity_and_check_permission(entity_type, entity_id, current_user, db)
    target_user = await _resolve_target_user(db, user_id=req.user_id, email=req.email, username=req.username)

    # Can't add yourself
    owner_id = entity.created_by if entity_type == "agents" else entity.submitted_by
    if target_user.id == owner_id:
        raise HTTPException(status_code=422, detail="Owner is already implicit - no need to add as co-author")

    # Check if already a co-author
    co_authors = [str(uid) for uid in (entity.co_authors or [])]
    if str(target_user.id) in co_authors:
        raise HTTPException(status_code=409, detail="User is already a co-author")

    # Append and save
    new_list = [*co_authors, str(target_user.id)]
    entity.co_authors = new_list
    await db.commit()

    return CoAuthorResponse(
        id=str(target_user.id),
        email=target_user.email,
        username=target_user.username,
        is_active=target_user.auth_provider != "deactivated",
    )


@router.delete("/{entity_type}/{entity_id}/co-authors/{user_id}")
async def remove_co_author(
    entity_type: str,
    entity_id: _uuid.UUID,
    user_id: _uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a co-author."""
    entity = await _get_entity_and_check_permission(entity_type, entity_id, current_user, db)

    co_authors = [str(uid) for uid in (entity.co_authors or [])]
    if str(user_id) not in co_authors:
        raise HTTPException(status_code=404, detail="User is not a co-author")

    co_authors.remove(str(user_id))
    entity.co_authors = co_authors
    await db.commit()

    return {"detail": "Co-author removed"}


@router.get("/{entity_type}/{entity_id}/editors", response_model=list[CoAuthorResponse])
async def list_editors(
    entity_type: str,
    entity_id: _uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List users who have released versions of this entity."""
    version_model = VERSION_MODELS.get(entity_type)
    if not version_model:
        raise HTTPException(status_code=400, detail=f"Invalid entity type: {entity_type}")

    # Determine the FK column name
    fk_col = "agent_id" if entity_type == "agents" else "listing_id"

    stmt = select(version_model.released_by).where(getattr(version_model, fk_col) == entity_id).distinct()
    result = await db.execute(stmt)
    editor_ids = [row[0] for row in result.all()]

    if not editor_ids:
        return []

    users_result = await db.execute(select(User).where(User.id.in_(editor_ids)))
    users = users_result.scalars().all()

    return [
        CoAuthorResponse(
            id=str(u.id),
            email=u.email,
            username=u.username,
            is_active=u.auth_provider != "deactivated",
        )
        for u in users
    ]
