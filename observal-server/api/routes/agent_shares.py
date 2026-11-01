# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Authenticated, expiring share manifests for pinned Agent versions."""

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, require_role
from api.ratelimit import limiter
from api.routes.agent.helpers import _load_agent
from models.agent import AgentStatus, AgentVersion
from models.agent_share import AgentShareItem, AgentShareManifest
from models.user import User, UserRole
from schemas.agent_share import (
    AgentShareCreateRequest,
    AgentShareCreateResponse,
    AgentShareResponse,
    AgentShareRevokeResponse,
    SharedAgentSummary,
)
from services import dynamic_settings as ds

router = APIRouter(prefix="/api/v1/agent-shares", tags=["agent-shares"])
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _validate_token(token: str) -> str:
    if not _TOKEN_RE.fullmatch(token):
        raise HTTPException(status_code=404, detail="Share not found")
    return token


async def _load_manifest(token: str, db: AsyncSession, *, allow_inactive: bool = False) -> AgentShareManifest:
    token = _validate_token(token)
    manifest = (
        await db.execute(select(AgentShareManifest).where(AgentShareManifest.token_hash == _token_hash(token)))
    ).scalar_one_or_none()
    if manifest is None:
        raise HTTPException(status_code=404, detail="Share not found")
    now = datetime.now(UTC)
    if not allow_inactive and (manifest.revoked_at is not None or manifest.expires_at <= now):
        raise HTTPException(status_code=410, detail="Share has expired or been revoked")
    return manifest


@router.post("", response_model=AgentShareCreateResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/hour")
async def create_agent_share(
    req: AgentShareCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
) -> AgentShareCreateResponse:
    """Create an opaque manifest after validating every pinned Agent version."""
    resolved: list[tuple[object, AgentVersion]] = []
    for requested in req.items:
        agent = await _load_agent(
            db,
            str(requested.agent_id),
            prefer_user_id=current_user.id,
            current_user=current_user,
        )
        if agent is None:
            raise HTTPException(status_code=404, detail="One or more Agent versions are unavailable")
        version = (
            await db.execute(
                select(AgentVersion).where(
                    AgentVersion.agent_id == agent.id,
                    AgentVersion.version == requested.version,
                    AgentVersion.status == AgentStatus.approved,
                )
            )
        ).scalar_one_or_none()
        if version is None:
            raise HTTPException(status_code=404, detail="One or more Agent versions are unavailable")
        resolved.append((agent, version))

    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    manifest = AgentShareManifest(
        token_hash=_token_hash(token),
        created_by=current_user.id,
        title=req.title,
        created_at=now,
        expires_at=now + timedelta(days=req.expires_in_days),
    )
    db.add(manifest)
    await db.flush()
    for position, (agent, version) in enumerate(resolved):
        db.add(
            AgentShareItem(
                manifest_id=manifest.id,
                agent_id=agent.id,
                agent_version_id=version.id,
                position=position,
            )
        )
    await db.commit()

    frontend = str(ds.get_sync("deployment.frontend_url", "http://localhost:3000")).rstrip("/")
    return AgentShareCreateResponse(
        token=token,
        url=f"{frontend}/shares/agents/{token}",
        created_at=manifest.created_at,
        expires_at=manifest.expires_at,
        item_count=len(resolved),
    )


@router.get("/{token}", response_model=AgentShareResponse)
@limiter.limit("120/minute")
async def get_agent_share(
    token: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
) -> AgentShareResponse:
    """Return only items the authenticated recipient can currently access."""
    response.headers["Cache-Control"] = "no-store"
    manifest = await _load_manifest(token, db)
    creator = await db.get(User, manifest.created_by)
    visible: list[SharedAgentSummary] = []
    unavailable = 0

    for item in manifest.items:
        agent = await _load_agent(db, str(item.agent_id), current_user=current_user)
        if agent is None:
            unavailable += 1
            continue
        version = (
            await db.execute(
                select(AgentVersion).where(
                    AgentVersion.id == item.agent_version_id,
                    AgentVersion.agent_id == agent.id,
                    AgentVersion.status == AgentStatus.approved,
                )
            )
        ).scalar_one_or_none()
        if version is None:
            unavailable += 1
            continue
        visible.append(
            SharedAgentSummary(
                agent_id=agent.id,
                version=version.version,
                name=agent.name,
                namespace=agent.namespace,
                slug=agent.slug,
                qualified_name=agent.qualified_name,
                description=version.description,
                supported_harnesses=version.inferred_supported_harnesses or version.supported_harnesses or [],
                required_capabilities=version.required_capabilities or [],
                position=item.position,
            )
        )

    return AgentShareResponse(
        token=token,
        title=manifest.title,
        created_at=manifest.created_at,
        expires_at=manifest.expires_at,
        created_by_username=creator.username if creator else "unknown",
        items=visible,
        unavailable_count=unavailable,
    )


@router.delete("/{token}", response_model=AgentShareRevokeResponse)
@limiter.limit("20/hour")
async def revoke_agent_share(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
) -> AgentShareRevokeResponse:
    """Revoke a share without exposing or deleting its item history."""
    manifest = await _load_manifest(token, db, allow_inactive=True)
    if manifest.created_by != current_user.id and current_user.role not in {UserRole.admin, UserRole.super_admin}:
        raise HTTPException(status_code=403, detail="Only the creator or an administrator can revoke this share")
    if manifest.revoked_at is not None or manifest.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=410, detail="Share has expired or been revoked")
    manifest.revoked_at = datetime.now(UTC)
    await db.commit()
    return AgentShareRevokeResponse(revoked=True)
