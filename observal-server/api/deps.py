# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import asyncio
import uuid as _uuid
from collections.abc import AsyncGenerator

import jwt
from fastapi import Depends, Header, HTTPException
from loguru import logger as optic
from redis.exceptions import RedisError
from sqlalchemy import String, cast, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

import services.dynamic_settings as ds
from database import async_session
from models.user import User, UserRole
from services.jwt_service import decode_access_token
from services.redis import get_redis
from services.registry_namespace import _namespace_slug_parts, identity_for_user
from services.security_events import (
    EventType,
    SecurityEvent,
    Severity,
    emit_security_event,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


async def _authenticate_via_jwt(token: str, db: AsyncSession) -> User | None:
    """Try to authenticate using a JWT access token. Returns User or None.

    Resolves the deployment trace privacy policy from the settings cache so
    downstream code never needs a separate DB call for it.
    """
    try:
        payload = decode_access_token(token)
    except jwt.InvalidTokenError as e:
        optic.trace("JWT decode failed: {}", e)
        return None

    jti = payload.get("jti")
    user_id_str = payload.get("sub")
    try:
        redis = get_redis()
        # SEC-002: fail closed on revocation checks - Redis errors must not
        # re-enable revoked tokens or user-level revocations.
        # Gather both checks concurrently to reduce latency at scale
        # (2 sequential GETs → 1 concurrent round-trip).
        checks = []
        if jti:
            checks.append(redis.get(f"revoked_jti:{jti}"))
        if user_id_str:
            checks.append(redis.get(f"revoked_user:{user_id_str}"))
        results = await asyncio.gather(*checks) if checks else []
        idx = 0
        if jti:
            if results[idx]:
                return None
            idx += 1
        if user_id_str and results[idx]:
            return None  # SEC-001: account-wide revocation (e.g. logout all)
    except RedisError as e:
        # Redis is down: block all token-based auth to prevent revoked tokens
        # from regaining access. Requests will receive HTTP 503.
        optic.error("Redis unavailable during auth - blocking request to prevent revoked token use: {}", e)
        raise RedisError("Auth service temporarily unavailable - please retry")

    user_id = payload.get("sub")

    if not user_id:
        return None

    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        return None

    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        return None
    user._trace_privacy = ds.get_sync_bool("security.trace_privacy")
    user._groups = payload.get("groups", [])
    return user


# Paths that must remain accessible even when must_change_password is set
_PASSWORD_CHANGE_EXEMPT_PATHS = frozenset(
    {
        "/api/v1/auth/profile/password",
        "/api/v1/auth/whoami",
        "/api/v1/auth/token/refresh",
        "/api/v1/auth/token/revoke",
    }
)


async def _enforce_password_change(request: Request, user: User) -> None:
    """Block authenticated requests until a required password change is complete."""
    if request.url.path in _PASSWORD_CHANGE_EXEMPT_PATHS:
        return
    try:
        redis = get_redis()
        if await redis.get(f"must_change_password:{user.id}"):
            raise HTTPException(status_code=403, detail="Password change required")
    except RedisError:
        raise HTTPException(status_code=503, detail="Auth service temporarily unavailable")


async def get_current_user(
    request: Request,
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        optic.trace("request has no Bearer token")
        raise HTTPException(status_code=401, detail="Missing credentials")
    token = authorization.removeprefix("Bearer ").strip()
    user = await _authenticate_via_jwt(token, db)
    if not user:
        optic.debug("authentication failed - token invalid or expired")
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Block deactivated users (SCIM sets auth_provider to "deactivated")
    if user.auth_provider == "deactivated":
        optic.warning("deactivated user {} attempted access", user.id)
        raise HTTPException(status_code=403, detail="Account deactivated")

    # Expose user to audit middleware
    request.state.audit_user = user

    # Fail closed when a required password change cannot be verified.
    await _enforce_password_change(request, user)

    request.state.current_user = user
    return user


async def optional_current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Return the authenticated user when a valid token is present, else None.

    Raises HTTP 401 if a Bearer token is present but invalid/expired -
    this distinguishes 'no credentials' from 'bad credentials'.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    user = await _authenticate_via_jwt(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if user.auth_provider == "deactivated":
        raise HTTPException(status_code=401, detail="Account deactivated")
    return user


async def get_registry_user(
    request: Request,
    current_user: User | None = Depends(optional_current_user),
) -> User | None:
    """Authorize registry reads, allowing guests only when public access is enabled."""
    if current_user is None:
        if not await ds.get_bool("deployment.public_registry_enabled"):
            raise HTTPException(status_code=401, detail="Authentication required")
        return None

    request.state.audit_user = current_user
    await _enforce_password_change(request, current_user)
    request.state.current_user = current_user
    return current_user


# Role hierarchy: lower number = higher privilege
ROLE_HIERARCHY: dict[UserRole, int] = {
    UserRole.super_admin: 0,
    UserRole.admin: 1,
    UserRole.reviewer: 2,
    UserRole.user: 3,
}


def require_role(min_role: UserRole):
    """FastAPI dependency that requires the user to have at least the given role level.

    Usage: current_user: User = Depends(require_role(UserRole.admin))
    """

    async def _check(current_user: User = Depends(get_current_user)) -> User:
        user_level = ROLE_HIERARCHY.get(current_user.role, 999)
        required_level = ROLE_HIERARCHY[min_role]
        if user_level > required_level:
            await emit_security_event(
                SecurityEvent(
                    event_type=EventType.PERMISSION_DENIED,
                    severity=Severity.WARNING,
                    outcome="failure",
                    actor_id=str(current_user.id),
                    actor_email=current_user.email,
                    actor_role=current_user.role.value,
                    detail=f"Required role: {min_role.value}, has: {current_user.role.value}",
                )
            )
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return current_user

    return _check


def get_effective_agent_permission(agent: "Agent", user: User | None) -> str:  # noqa: F821
    """Evaluate effective permission for an agent: 'owner', 'edit', 'view', or 'none'."""
    if not user:
        return "view"

    if agent.created_by == user.id:
        return "owner"

    # Co-authors have full owner-level access
    if str(user.id) in [str(uid) for uid in (agent.co_authors or [])]:
        return "owner"

    user_role_level = ROLE_HIERARCHY.get(user.role, 999)
    if user_role_level <= ROLE_HIERARCHY[UserRole.admin]:
        return "owner"  # admins can edit anything

    return "view"


def get_effective_component_permission(listing, user: User | None) -> str:
    """Evaluate effective permission for a component listing: 'owner', 'view', or 'none'.

    Works with McpListing, HookListing, SandboxListing, PromptListing.
    Co-authors have full owner-level access (can edit, publish, manage co-authors).
    """
    if not user:
        return "view"

    if listing.submitted_by == user.id:
        return "owner"

    # Co-authors have full owner-level access
    if str(user.id) in [str(uid) for uid in (listing.co_authors or [])]:
        return "owner"

    user_role_level = ROLE_HIERARCHY.get(user.role, 999)
    if user_role_level <= ROLE_HIERARCHY[UserRole.admin]:
        return "owner"  # admins can edit anything

    return "view"


def may_view_unapproved(permission: str, user: User | None) -> bool:
    """Non-approved listings are visible only to owners, co-authors, admins, and reviewers.

    ``permission`` is the return of ``get_effective_component_permission`` or
    ``get_effective_agent_permission``. Both already answer "owner" for the
    submitter, for co-authors, and for admins and super_admins, so those three
    arrive through ``permission``.

    Reviewers are deliberately absent from those two functions: "owner" there
    also means "may edit", and a reviewer must be able to open a pending item
    without gaining edit rights over it. The reviewer arm below is therefore the
    only thing that lets the review queue read what it has to review. Removing it
    as redundant would close the queue.
    """
    return permission == "owner" or (user is not None and user.role == UserRole.reviewer)


def can_see_private_listings(user: User | None) -> bool:
    """Whether a global role by itself may read team-private listings.

    Only admins and super_admins qualify. Global reviewers are deliberately
    excluded: their mandate is the PUBLIC catalog, and team-private content is
    reviewed inside its own teamspace by a team owner or team reviewer, so a
    global reviewer has no reason to read another team's private listings.

    This answers a PRIVACY question and is not the same gate as
    ``may_view_unapproved``, which answers a STATUS question. A reviewer must
    still open a pending public listing to review it, so that helper keeps its
    reviewer arm while this one does not grow one.
    """
    return user is not None and ROLE_HIERARCHY.get(user.role, 999) <= ROLE_HIERARCHY[UserRole.admin]


# Convenience shorthand for super_admin-only endpoints
require_super_admin = require_role(UserRole.super_admin)


def registry_identity(user: User, name: str) -> tuple[str, str]:
    try:
        return identity_for_user(user, name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def commit_or_name_conflict(db: AsyncSession, item_type: str) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        detail = str(exc.orig).lower()
        if ("namespace" in detail or "slug" in detail or "name" in detail) and (
            "unique" in detail or "duplicate" in detail
        ):
            raise HTTPException(status_code=409, detail=f"A {item_type} with this namespace and slug already exists")
        raise


async def require_password_auth() -> None:
    """FastAPI dependency that blocks the endpoint when deployment.sso_only is enabled."""
    import services.dynamic_settings as ds

    sso_only = await ds.get_bool("deployment.sso_only")
    if sso_only:
        raise HTTPException(status_code=403, detail="Password authentication is disabled (SSO-only mode)")


# Legacy bare names can collide across namespaces. Fetch a small window instead
# of two rows so the ambiguity report still sees alternatives after the listings
# the caller may not see are dropped.
MAX_BARE_NAME_MATCHES = 6


async def resolve_listing(
    model, identifier: str, db: AsyncSession, *, require_status=None, current_user=None, load_options=()
):
    """Resolve a listing by UUID, canonical name, or unambiguous legacy bare name.

    ``current_user`` decides what the caller may see, and a listing they may not
    see resolves to None exactly as a nonexistent one does. This is the single
    gate every read AND write path inherits: archive, unarchive, draft edit and
    the editing lock all resolve through here, and they authorize with
    ``get_effective_component_permission``, which answers "owner" for the original
    submitter forever. Without the check below, a user removed from a teamspace
    could still mutate a listing they can no longer read.

    ``current_user=None`` means an anonymous caller, so omitting it fails closed
    rather than open. Every call site under ``observal-server/api`` passes it
    explicitly, and ``TestEveryCallSitePassesTheCaller`` in
    ``tests/test_sec009_org_scoping.py`` fails the build when a new one does not.

    The legacy bare-name collision report is scoped the same way: the 409 names
    only listings the caller may see, and a name colliding solely with hidden
    listings resolves to the single visible one rather than disclosing the others.
    """

    value = str(identifier).strip()
    try:
        uid = identifier if isinstance(identifier, _uuid.UUID) else _uuid.UUID(value)
    except ValueError:
        uid = None

    if uid is not None:
        stmt = select(model).where(model.id == uid)
        ambiguous_label = None
    elif parts := _namespace_slug_parts(value):
        namespace, slug = parts
        stmt = select(model).where(model.namespace == namespace, model.slug == slug)
        ambiguous_label = None
    else:
        stmt = select(model).where(or_(model.slug == value.lower(), model.name == value))
        ambiguous_label = value

    if require_status is not None:
        version_model = model.__mapper__.relationships["latest_version"].entity.class_
        stmt = stmt.join(version_model, model.latest_version_id == version_model.id).where(
            version_model.status == require_status
        )
    result = await db.execute(
        stmt.options(*load_options).limit(MAX_BARE_NAME_MATCHES if ambiguous_label is not None else 2)
    )
    scalars = result.scalars()
    matches = scalars.all()
    if not isinstance(matches, (list, tuple)):
        first = scalars.first()
        matches = [first] if first is not None else []
    visible = [item for item in matches if await check_listing_visibility_async(item, current_user, db)]
    if ambiguous_label is not None and len(visible) > 1:
        choices = ", ".join(item.qualified_name for item in visible)
        raise HTTPException(
            status_code=409,
            detail=f"'{ambiguous_label}' is ambiguous; use one of: {choices}",
        )
    return visible[0] if visible else None


MIN_ID_PREFIX_LENGTH = 4
MAX_AMBIGUOUS_MATCHES_SHOWN = 5


async def resolve_prefix_id(
    model,
    identifier: str,
    db: AsyncSession,
    *,
    extra_conditions=None,
    load_options=None,
    display_field: str = "name",
):
    """Find a record by UUID or unique prefix."""
    norm_id = identifier.strip().lower()

    try:
        uid = _uuid.UUID(norm_id)
        stmt = select(model).where(model.id == uid)
        if load_options:
            stmt = stmt.options(*load_options)
        if extra_conditions:
            stmt = stmt.where(*extra_conditions)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
        return record
    except ValueError:
        pass

    if len(norm_id) < MIN_ID_PREFIX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Prefix '{norm_id}' is too short (minimum 4 characters required)",
        )

    stmt = select(model).where(cast(model.id, String).like(f"{norm_id}%"))
    if load_options:
        stmt = stmt.options(*load_options)
    if extra_conditions:
        stmt = stmt.where(*extra_conditions)
    result = await db.execute(stmt)
    records = result.scalars().all()

    if not records:
        raise HTTPException(
            status_code=404,
            detail=f"No {model.__name__} found matching prefix '{norm_id}'",
        )
    if len(records) == 1:
        return records[0]

    matches = []
    for r in records[:MAX_AMBIGUOUS_MATCHES_SHOWN]:
        label = getattr(r, display_field, None) or "unnamed"
        matches.append(f"{label} ({str(r.id)[:13]}...)")
    detail = f"Ambiguous prefix '{norm_id}' matches {len(records)} records: {', '.join(matches)}"
    if len(records) > MAX_AMBIGUOUS_MATCHES_SHOWN:
        detail += " and more..."
    raise HTTPException(status_code=400, detail=detail)


def apply_publish_scope(stmt, model, target_team_id):
    """Limit components to what an agent published for one target can contain."""
    from sqlalchemy import or_

    if not hasattr(model, "is_private") or not hasattr(model, "team_id"):
        return stmt
    public = model.is_private == False  # noqa: E712
    if target_team_id is None:
        return stmt.where(public)
    return stmt.where(or_(public, (model.is_private == True) & (model.team_id == target_team_id)))  # noqa: E712


def apply_registry_scope(stmt, model, current_user, *, team_id=None, composable_for_team_id=None, public_only=False):
    """Apply caller visibility plus one optional teamspace filter.

    Two different questions used to share ``team_id``, which made every teamspace
    view show the whole public registry:

    ``team_id`` means OWNERSHIP: show what this teamspace published, and nothing
    else. That is what a teamspace page, the ``?team_id=`` list filter, and the CLI
    ``--team TEAM_HANDLE`` flag all want. A listing owned by another namespace has
    no business appearing under a team's own tab just because it is public.

    ``composable_for_team_id`` means PUBLISH SCOPE: public items plus this
    teamspace's private items, which is the set an agent published for that target
    may contain. Only the agent builder's component picker wants this.

    Caller visibility is applied first either way, so a non-member passing a
    teamspace id still sees only that team's public rows.
    """
    stmt = apply_visibility_filter(stmt, model, current_user)
    if public_only:
        return stmt.where(model.is_private == False)  # noqa: E712
    if composable_for_team_id is not None:
        return apply_publish_scope(stmt, model, composable_for_team_id)
    if team_id is not None and hasattr(model, "team_id"):
        return stmt.where(model.team_id == team_id)
    return stmt


def apply_visibility_filter(stmt, model, current_user):
    """Restrict team-private listings without using deployment scope state.

    Public listings are visible to everyone. Team-private listings require a
    membership row for the requesting user, while legacy private user listings
    remain visible only to their creator.

    Admins and super_admins skip the filter entirely. Global reviewers do not:
    see ``can_see_private_listings``.
    """
    from sqlalchemy import or_

    from models.team import TeamMembership

    if not hasattr(model, "is_private"):
        return stmt

    if can_see_private_listings(current_user):
        return stmt

    public = model.is_private == False  # noqa: E712
    if current_user is None:
        return stmt.where(public)

    creator_column = getattr(model, "submitted_by", None) or getattr(model, "created_by", None)
    if hasattr(model, "team_id"):
        own = (model.is_private == True) & model.team_id.is_(None)  # noqa: E712
    else:
        own = model.is_private == True  # noqa: E712
    own = own & (creator_column == current_user.id) if creator_column is not None else False

    if hasattr(model, "team_id"):
        member = (
            select(TeamMembership.id)
            .where(TeamMembership.team_id == model.team_id, TeamMembership.user_id == current_user.id)
            .correlate(model)
            .exists()
        )
        team_private = (model.is_private == True) & model.team_id.is_not(None) & member  # noqa: E712
        return stmt.where(or_(public, own, team_private))

    # Models without team_id are outside the registry visibility contract.
    return stmt.where(or_(public, own))


async def check_listing_visibility_async(listing, current_user, db: AsyncSession) -> bool:
    """Check visibility with a membership query for detail and install routes.

    This is the row-level twin of ``apply_visibility_filter`` and must stay
    identical to it: a team-private listing is reachable through team membership
    only. Being the original submitter is not a standing grant, otherwise someone
    removed from a teamspace would keep reading by id an item that has already
    vanished from every list.
    """
    if not getattr(listing, "is_private", False):
        return True
    if current_user is None:
        return False

    from models.team import TeamMembership

    if can_see_private_listings(current_user):
        return True
    team_id = getattr(listing, "team_id", None)
    if team_id is None:
        # Private with no teamspace is a personal listing: only its creator sees it.
        creator_id = getattr(listing, "submitted_by", None) or getattr(listing, "created_by", None)
        return creator_id == current_user.id
    return (
        await db.scalar(
            select(TeamMembership.id)
            .where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == current_user.id,
            )
            .with_for_update(read=True)
        )
        is not None
    )


async def resolve_visible_listing(
    model, identifier: str, db: AsyncSession, current_user, *, require_status=None, load_options=()
):
    """Resolve a listing and hide it when the caller cannot see it."""
    listing = await resolve_listing(
        model, identifier, db, require_status=require_status, current_user=current_user, load_options=load_options
    )
    if listing is None or not await check_listing_visibility_async(listing, current_user, db):
        return None
    return listing
