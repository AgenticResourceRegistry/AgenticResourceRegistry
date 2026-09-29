# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""SCIM 2.0 provisioning endpoints."""

from __future__ import annotations

import hmac
import logging
import uuid as _uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from models.scim_token import ScimToken
from models.user import User, UserRole, live_users
from services.events import UserCreated, UserDeleted, bus
from services.scim_service import (
    SCIM_PATCH_SCHEMA,
    SCIM_USER_SCHEMA,
    format_scim_error,
    format_scim_list,
    format_scim_user,
    hash_scim_token,
    parse_scim_filter,
    parse_scim_user,
    validate_scim_pagination,
)
from services.security_events import (
    EventType,
    SecurityEvent,
    Severity,
    emit_security_event,
)
from services.user_deletion import LastAdminError, delete_user_account, revoke_deleted_user_tokens
from services.username_generator import generate_unique_username

logger = logging.getLogger("observal.scim")


router = APIRouter(prefix="/api/v1/scim", tags=["scim"])

SCIM_CONTENT_TYPE = "application/scim+json"


async def _get_scoped_user(user_id: str, db: AsyncSession) -> User | None:
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        return None
    q = select(User).where(User.id == uid, live_users())
    result = await db.execute(q)
    return result.scalar_one_or_none()


async def _verify_scim_token(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> ScimToken:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid SCIM bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    token_hash = hash_scim_token(token)

    result = await db.execute(
        select(ScimToken).where(
            ScimToken.active.is_(True),
        )
    )
    scim_token = None
    for candidate in result.scalars().all():
        if hmac.compare_digest(candidate.token_hash, token_hash):
            scim_token = candidate
            break
    if not scim_token:
        await emit_security_event(
            SecurityEvent(
                event_type=EventType.API_KEY_REJECTED,
                severity=Severity.WARNING,
                outcome="failure",
                detail="Invalid SCIM bearer token",
            )
        )
        raise HTTPException(status_code=401, detail="Invalid SCIM bearer token")
    return scim_token


@router.get("/Users")
async def list_users(
    request: Request,
    startIndex: int = 1,  # noqa: N803 - SCIM spec parameter name
    count: int = 100,
    filter: str | None = None,
    _scim_token: ScimToken = Depends(_verify_scim_token),
    db: AsyncSession = Depends(get_db),
):
    base_url = str(request.base_url).rstrip("/") + "/api/v1/scim"
    startIndex, count = validate_scim_pagination(startIndex, count)  # noqa: N806

    if filter:
        parsed_filter = parse_scim_filter(filter)
        if not parsed_filter:
            return JSONResponse(
                status_code=400,
                content=format_scim_error(400, f"Invalid filter expression: {filter}"),
                media_type=SCIM_CONTENT_TYPE,
            )

        if parsed_filter.attr == "username":
            value = parsed_filter.value.strip().lower()
            q = select(User).where(live_users())
            if parsed_filter.op == "eq":
                q = q.where(User.email == value)
            elif parsed_filter.op == "sw":
                q = q.where(User.email.startswith(value))
            elif parsed_filter.op == "co":
                q = q.where(User.email.contains(value))
            elif parsed_filter.op == "ne":
                q = q.where(User.email != value)
            else:
                return JSONResponse(
                    status_code=400,
                    content=format_scim_error(400, f"Unsupported filter operator: {parsed_filter.op}"),
                    media_type=SCIM_CONTENT_TYPE,
                )
            total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
            result = await db.execute(q.order_by(User.created_at).offset(startIndex - 1).limit(count))
            users = list(result.scalars().all())
            resources = [format_scim_user(u, base_url) for u in users]
            return JSONResponse(
                content=format_scim_list(resources, total, startIndex),
                media_type=SCIM_CONTENT_TYPE,
            )

        return JSONResponse(
            status_code=400,
            content=format_scim_error(400, f"Unsupported filter attribute: {parsed_filter.attr}"),
            media_type=SCIM_CONTENT_TYPE,
        )

    base_q = select(User).where(live_users())

    total_q = select(func.count()).select_from(base_q.subquery())
    total = (await db.execute(total_q)).scalar() or 0

    users_q = base_q.order_by(User.created_at).offset(startIndex - 1).limit(count)
    result = await db.execute(users_q)
    users = list(result.scalars().all())

    resources = [format_scim_user(u, base_url) for u in users]
    return JSONResponse(
        content=format_scim_list(resources, total, startIndex),
        media_type=SCIM_CONTENT_TYPE,
    )


@router.post("/Users", status_code=201)
async def create_user(
    request: Request,
    _scim_token: ScimToken = Depends(_verify_scim_token),
    db: AsyncSession = Depends(get_db),
):
    body = await request.json()
    parsed = parse_scim_user(body)
    email = parsed["email"]
    if not email:
        return JSONResponse(
            status_code=400,
            content=format_scim_error(400, "userName or email is required"),
            media_type=SCIM_CONTENT_TYPE,
        )

    result = await db.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    if existing:
        return JSONResponse(
            status_code=409,
            content=format_scim_error(409, f"User with email {email} already exists"),
            media_type=SCIM_CONTENT_TYPE,
        )

    user = User(
        email=email,
        username=await generate_unique_username(email, db),
        name=parsed["name"],
        role=UserRole.user,
        auth_provider="scim",
    )
    db.add(user)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return JSONResponse(
            status_code=409,
            content=format_scim_error(409, f"User with email {email} already exists"),
            media_type=SCIM_CONTENT_TYPE,
        )

    await db.commit()
    await bus.emit(UserCreated(user_id=str(user.id), email=user.email, role=user.role.value))

    base_url = str(request.base_url).rstrip("/") + "/api/v1/scim"
    return JSONResponse(
        status_code=201,
        content=format_scim_user(user, base_url),
        media_type=SCIM_CONTENT_TYPE,
    )


@router.get("/Users/{user_id}")
async def get_user(
    user_id: str,
    request: Request,
    _scim_token: ScimToken = Depends(_verify_scim_token),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_scoped_user(user_id, db)
    if not user:
        return JSONResponse(
            status_code=404,
            content=format_scim_error(404, "User not found"),
            media_type=SCIM_CONTENT_TYPE,
        )

    base_url = str(request.base_url).rstrip("/") + "/api/v1/scim"
    return JSONResponse(
        content=format_scim_user(user, base_url),
        media_type=SCIM_CONTENT_TYPE,
    )


@router.put("/Users/{user_id}")
async def update_user(
    user_id: str,
    request: Request,
    _scim_token: ScimToken = Depends(_verify_scim_token),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_scoped_user(user_id, db)
    if not user:
        return JSONResponse(
            status_code=404,
            content=format_scim_error(404, "User not found"),
            media_type=SCIM_CONTENT_TYPE,
        )

    body = await request.json()
    parsed = parse_scim_user(body)

    if parsed["email"] and parsed["email"] != user.email:
        user.email = parsed["email"]
    if parsed["name"]:
        user.name = parsed["name"]

    if not parsed["active"] and user.auth_provider != "deactivated":
        user.password_hash = None
        user.auth_provider = "deactivated"
    elif parsed["active"] and user.auth_provider == "deactivated":
        user.auth_provider = "scim"

    await db.commit()

    base_url = str(request.base_url).rstrip("/") + "/api/v1/scim"
    return JSONResponse(
        content=format_scim_user(user, base_url),
        media_type=SCIM_CONTENT_TYPE,
    )


@router.delete("/Users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    _scim_token: ScimToken = Depends(_verify_scim_token),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_scoped_user(user_id, db)
    if not user:
        return JSONResponse(
            status_code=404,
            content=format_scim_error(404, "User not found"),
            media_type=SCIM_CONTENT_TYPE,
        )

    email = user.email
    user_id_str = str(user.id)
    # Same account deletion as the admin route: the row becomes a scrubbed
    # shell so authored records and telemetry keep resolving.
    try:
        await delete_user_account(db, user)
    except LastAdminError as error:
        await db.rollback()
        return JSONResponse(
            status_code=400,
            content=format_scim_error(400, str(error)),
            media_type=SCIM_CONTENT_TYPE,
        )
    await db.commit()
    await revoke_deleted_user_tokens(user.id)

    await bus.emit(UserDeleted(user_id=user_id_str, email=email))


# ---------------------------------------------------------------------------
# PATCH /Users/{user_id} - RFC 7644 Section 3.5.2
# ---------------------------------------------------------------------------

_VALID_PATCH_OPS = {"replace", "add", "remove"}


def _apply_patch_op(user: User, op: str, path: str | None, value: object) -> str | None:
    """Apply a single SCIM PATCH operation to a User.

    Returns an error string if the operation is invalid, else ``None``.
    """
    op = op.lower()
    if op not in _VALID_PATCH_OPS:
        return f"Unsupported op: {op}"

    if op == "remove":
        return "Cannot remove required attributes"

    # Normalise path for bracket-notation email paths
    normalised = (path or "").strip()
    if normalised.startswith("emails[") or normalised.startswith("emails."):
        normalised = "emails"

    if normalised in ("displayName", "name"):
        user.name = str(value) if value else user.name
    elif normalised in ("name.givenName",):
        parts = (user.name or "").split(" ", 1)
        family = parts[1] if len(parts) > 1 else ""
        user.name = f"{value} {family}".strip()
    elif normalised in ("name.familyName",):
        parts = (user.name or "").split(" ", 1)
        given = parts[0] if parts else ""
        user.name = f"{given} {value}".strip()
    elif normalised in ("userName", "emails"):
        new_email = str(value).strip().lower() if value else ""
        if new_email:
            user.email = new_email
    elif normalised == "active":
        if not isinstance(value, bool):
            return "active must be a boolean"
        is_active = value
        if not is_active and user.auth_provider != "deactivated":
            user.password_hash = None
            user.auth_provider = "deactivated"
        elif is_active and user.auth_provider == "deactivated":
            user.auth_provider = "scim"
    else:
        return f"Unknown path: {path}"

    return None


@router.patch("/Users/{user_id}")
async def patch_user(
    user_id: str,
    request: Request,
    _scim_token: ScimToken = Depends(_verify_scim_token),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_scoped_user(user_id, db)
    if not user:
        return JSONResponse(
            status_code=404,
            content=format_scim_error(404, "User not found"),
            media_type=SCIM_CONTENT_TYPE,
        )

    body = await request.json()

    schemas = body.get("schemas", [])
    if SCIM_PATCH_SCHEMA not in schemas:
        return JSONResponse(
            status_code=400,
            content=format_scim_error(400, f"Request must include schema {SCIM_PATCH_SCHEMA}"),
            media_type=SCIM_CONTENT_TYPE,
        )

    operations = body.get("Operations", [])
    if not operations:
        return JSONResponse(
            status_code=400,
            content=format_scim_error(400, "No operations provided"),
            media_type=SCIM_CONTENT_TYPE,
        )

    for operation in operations:
        op = operation.get("op", "")
        path = operation.get("path")
        value = operation.get("value")
        err = _apply_patch_op(user, op, path, value)
        if err:
            return JSONResponse(
                status_code=400,
                content=format_scim_error(400, err),
                media_type=SCIM_CONTENT_TYPE,
            )

    await db.commit()

    base_url = str(request.base_url).rstrip("/") + "/api/v1/scim"
    return JSONResponse(
        content=format_scim_user(user, base_url),
        media_type=SCIM_CONTENT_TYPE,
    )


# ---------------------------------------------------------------------------
# Discovery endpoints - RFC 7644 Section 4 (no authentication required)
# ---------------------------------------------------------------------------


@router.get("/ServiceProviderConfig")
async def service_provider_config():
    return JSONResponse(
        content={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "documentationUri": "https://observal.dev/docs/scim",
            "patch": {"supported": True},
            "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": 100},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [
                {
                    "type": "oauthbearertoken",
                    "name": "OAuth Bearer Token",
                    "description": "Authentication via bearer token",
                    "specUri": "https://www.rfc-editor.org/rfc/rfc6750",
                }
            ],
        },
        media_type=SCIM_CONTENT_TYPE,
    )


@router.get("/Schemas")
async def schemas():
    return JSONResponse(
        content={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
            "totalResults": 1,
            "Resources": [
                {
                    "id": SCIM_USER_SCHEMA,
                    "name": "User",
                    "description": "SCIM core User schema",
                    "attributes": [
                        {
                            "name": "userName",
                            "type": "string",
                            "multiValued": False,
                            "required": True,
                            "uniqueness": "server",
                        },
                        {
                            "name": "displayName",
                            "type": "string",
                            "multiValued": False,
                            "required": False,
                        },
                        {
                            "name": "name",
                            "type": "complex",
                            "multiValued": False,
                            "required": False,
                            "subAttributes": [
                                {
                                    "name": "givenName",
                                    "type": "string",
                                    "multiValued": False,
                                    "required": False,
                                },
                                {
                                    "name": "familyName",
                                    "type": "string",
                                    "multiValued": False,
                                    "required": False,
                                },
                                {
                                    "name": "formatted",
                                    "type": "string",
                                    "multiValued": False,
                                    "required": False,
                                },
                            ],
                        },
                        {
                            "name": "emails",
                            "type": "complex",
                            "multiValued": True,
                            "required": True,
                            "subAttributes": [
                                {
                                    "name": "value",
                                    "type": "string",
                                    "multiValued": False,
                                    "required": True,
                                },
                                {
                                    "name": "type",
                                    "type": "string",
                                    "multiValued": False,
                                    "required": False,
                                },
                                {
                                    "name": "primary",
                                    "type": "boolean",
                                    "multiValued": False,
                                    "required": False,
                                },
                            ],
                        },
                        {
                            "name": "active",
                            "type": "boolean",
                            "multiValued": False,
                            "required": False,
                        },
                    ],
                    "meta": {
                        "resourceType": "Schema",
                        "location": "/api/v1/scim/Schemas/" + SCIM_USER_SCHEMA,
                    },
                }
            ],
        },
        media_type=SCIM_CONTENT_TYPE,
    )


@router.get("/ResourceTypes")
async def resource_types():
    return JSONResponse(
        content={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "totalResults": 1,
            "Resources": [
                {
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                    "id": "User",
                    "name": "User",
                    "endpoint": "/Users",
                    "schema": SCIM_USER_SCHEMA,
                }
            ],
        },
        media_type=SCIM_CONTENT_TYPE,
    )
