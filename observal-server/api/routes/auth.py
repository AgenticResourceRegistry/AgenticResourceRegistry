# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import asyncio
import base64
import json
import re
import secrets
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

import httpx
import jwt as pyjwt
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from jwt import PyJWKClient
from loguru import logger as optic
from redis.exceptions import RedisError
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import services.dynamic_settings as ds
from api.deps import get_current_user, get_db, require_password_auth
from api.ratelimit import limiter
from models.user import User, UserRole
from models.user_group import UserGroup
from schemas.auth import (
    ChangePasswordRequest,
    CodeExchangeRequest,
    InitRequest,
    InitResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    RevokeRequest,
    TokenRequest,
    TokenResponse,
    UsernameUpdateRequest,
    UserResponse,
)
from schemas.sso_health import make_check
from services import sso_diagnostics
from services.jwt_service import create_access_token, create_refresh_token, decode_access_token, decode_refresh_token
from services.redis import get_redis
from services.registry_namespace import is_valid_namespace, rename_namespace, user_has_listings
from services.security_events import (
    EventType,
    SecurityEvent,
    Severity,
    _extract_request_info,
    emit_security_event,
)
from services.teamspace import reserve_handle as reserve_team_handle
from services.username_generator import generate_unique_username

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_COMMON_WEAK_PASSWORDS = frozenset(
    {
        # Passwords that fail the complexity rules anyway (kept for clarity)
        "password",
        "password1",
        "password123",
        "12345678",
        "123456789",
        "qwerty123",
        "iloveyou",
        "letmein123",
        "welcome1",
        "monkey123",
        # Passwords that pass all four complexity rules but are still common
        "P@ssw0rd!123",
        "Admin@1234!!",
        "Welcome@123!",
        "Password@1!2",
        "Qwerty@123!!",
        "Letmein@123!",
        "Passw0rd@123",
        "P@$$w0rd1234",
    }
)


def _validate_password_strength(password: str) -> None:
    optic.debug("_validate_password_strength called")
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
    if not re.search(r"[A-Z]", password):
        raise HTTPException(status_code=422, detail="Password must contain at least one uppercase letter")
    if not re.search(r"[0-9]", password):
        raise HTTPException(status_code=422, detail="Password must contain at least one digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise HTTPException(status_code=422, detail="Password must contain at least one special character")
    if password.lower() in _COMMON_WEAK_PASSWORDS:
        raise HTTPException(status_code=422, detail="Password is too common")


oauth = OAuth()


GOOGLE_OAUTH_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

# GitHub is plain OAuth 2.0, not OIDC: no discovery document, no ID token.
# Profile and verified emails are fetched from the REST API after the exchange.
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE_URL = "https://api.github.com/"

_GITHUB_API_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
# GitHub org logins: alphanumeric and hyphens, no leading/trailing hyphen.
_GITHUB_ORG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")


def configure_oauth_client() -> None:
    """Build OAuth clients from dynamic settings at API startup."""
    global oauth
    oauth = OAuth()
    client_id = ds.get_sync("oauth.client_id")
    client_secret = ds.get_sync("oauth.client_secret")
    metadata_url = ds.get_sync("oauth.server_metadata_url")
    if client_id and client_secret and metadata_url:
        oauth.register(
            name="oidc",
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url=metadata_url,
            client_kwargs={
                "scope": "openid email profile groups",
            },
        )

    google_client_id = ds.get_sync("google.client_id")
    google_client_secret = ds.get_sync("google.client_secret")
    if google_client_id and google_client_secret:
        oauth.register(
            name="google",
            client_id=google_client_id,
            client_secret=google_client_secret,
            server_metadata_url=GOOGLE_OAUTH_DISCOVERY_URL,
            client_kwargs={
                "scope": "openid email profile",
            },
        )

    github_client_id = ds.get_sync("github.client_id")
    github_client_secret = ds.get_sync("github.client_secret")
    if github_client_id and github_client_secret:
        # read:org is only needed (and only requested) when logins are
        # restricted to org members, so the consent screen stays minimal.
        github_scope = "read:user user:email"
        if _github_allowed_orgs():
            github_scope += " read:org"
        oauth.register(
            name="github",
            client_id=github_client_id,
            client_secret=github_client_secret,
            authorize_url=GITHUB_AUTHORIZE_URL,
            access_token_url=GITHUB_ACCESS_TOKEN_URL,
            api_base_url=GITHUB_API_BASE_URL,
            client_kwargs={
                "scope": github_scope,
            },
        )


def is_oidc_configured() -> bool:
    return bool(getattr(oauth, "oidc", None))


def is_google_oauth_configured() -> bool:
    return bool(getattr(oauth, "google", None))


def is_github_oauth_configured() -> bool:
    return bool(getattr(oauth, "github", None))


def _parse_allowed_domains(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def _google_allowed_domains() -> set[str]:
    return _parse_allowed_domains(ds.get_sync("google.allowed_domains"))


def _github_allowed_orgs() -> set[str]:
    """Parse github.allowed_orgs into validated org slugs.

    Slugs that don't match GitHub's org-name grammar are dropped so a
    misconfigured value can never turn into API path segments.
    """
    raw = ds.get_sync("github.allowed_orgs")
    if not raw:
        return set()
    orgs = {o.strip().lstrip("@").lower() for o in raw.split(",") if o.strip()}
    return {o for o in orgs if _GITHUB_ORG_RE.fullmatch(o)}


def _is_safe_next(next_path: str | None) -> bool:
    # Reject control characters (tab, newline, CR, ...) as well: a browser
    # strips them before resolving the URL, so "/\n/evil.com" collapses to a
    # protocol-relative "//evil.com" open redirect that the naive checks miss.
    return bool(
        next_path
        and next_path.startswith("/")
        and not next_path.startswith("//")
        and "\\" not in next_path
        and not any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in next_path)
    )


async def _start_oauth_flow(
    request: Request,
    client,
    *,
    callback_path: str,
    next_path: str | None,
) -> RedirectResponse:
    if _is_safe_next(next_path):
        request.session["oauth_next"] = next_path
    redirect_uri = ds.get_sync("deployment.frontend_url", "http://localhost:3000").rstrip("/") + callback_path
    return await client.authorize_redirect(request, redirect_uri)


async def _issue_tokens(user: User, groups: list[str] | None = None) -> tuple[str, str, int]:
    """Issue JWT access + refresh tokens for a user, storing refresh JTI in Redis.

    Returns (access_token, refresh_token, expires_in).
    Fails open: tokens are still returned if Redis is temporarily unreachable.
    """
    optic.trace("user_id={}, groups={}", user.id, groups)
    access_token, expires_in = create_access_token(user.id, user.role, groups=groups)
    refresh_token, jti = create_refresh_token(user.id, user.role, groups=groups)

    try:
        redis = get_redis()
        refresh_ttl = ds.get_sync_int("jwt.refresh_token_expire_days", 30) * 86400
        await redis.setex(f"refresh_jti:{jti}", refresh_ttl, str(user.id))
        # Clear any logout revocation so hooks resume after re-login
        await redis.delete(f"revoked_user:{user.id}")
    except RedisError as e:
        optic.warning("Redis unavailable when storing refresh JTI: {}", e)
        raise HTTPException(status_code=503, detail="Auth service temporarily unavailable")

    return access_token, refresh_token, expires_in


@router.post("/init", response_model=InitResponse, dependencies=[Depends(require_password_auth)])
async def init_admin(req: InitRequest, db: AsyncSession = Depends(get_db)):
    optic.trace("email={}", req.email)
    count = await db.scalar(select(func.count()).select_from(User))
    if count and count > 0:
        raise HTTPException(status_code=400, detail="System already initialized")

    try:
        username = await generate_unique_username(req.email, db, explicit=req.username)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    user = User(
        email=req.email,
        username=username,
        name=req.name,
        role=UserRole.admin,
    )
    if req.password:
        _validate_password_strength(req.password)
        user.set_password(req.password)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="System already initialized or email/username already exists")
    await db.refresh(user)

    access_token, refresh_token, expires_in = await _issue_tokens(user)
    return InitResponse(
        user=UserResponse.model_validate(user),
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/bootstrap", response_model=InitResponse)
@limiter.limit("1/minute")
async def bootstrap(request: Request, db: AsyncSession = Depends(get_db)):
    """Auto-create admin account on a fresh server. No input needed."""
    optic.debug("bootstrap called")
    client_host = request.client.host if request.client else None
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Bootstrap is only available from localhost")

    count = await db.scalar(select(func.count()).select_from(User))
    if count and count > 0:
        raise HTTPException(status_code=400, detail="System already initialized")

    user = User(
        email="admin@localhost",
        username=await generate_unique_username("admin@localhost", db),
        name="admin",
        role=UserRole.admin,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="System already initialized")
    await db.refresh(user)

    access_token, refresh_token, expires_in = await _issue_tokens(user)
    return InitResponse(
        user=UserResponse.model_validate(user),
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/register", response_model=InitResponse, dependencies=[Depends(require_password_auth)])
@limiter.limit(ds.get_sync("security.rate_limit_auth_strict", "5/minute"))
async def register(request: Request, req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Create a standard user only when self-registration is enabled."""
    optic.debug("auth register attempt")
    if not await ds.get_bool("auth.self_registration_enabled"):
        raise HTTPException(status_code=403, detail="Registration is disabled")

    _validate_password_strength(req.password)
    source_ip, user_agent = _extract_request_info(request)
    try:
        username = await generate_unique_username(req.email, db, explicit=req.username)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    user = User(
        email=req.email,
        username=username,
        name=req.name,
        role=UserRole.user,
    )
    user.set_password(req.password)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Email or username already exists")
    await db.refresh(user)

    await emit_security_event(
        SecurityEvent(
            event_type=EventType.REGISTRATION,
            severity=Severity.INFO,
            outcome="success",
            actor_id=str(user.id),
            actor_email=user.email,
            actor_role=user.role.value,
            source_ip=source_ip,
            user_agent=user_agent,
        )
    )
    access_token, refresh_token, expires_in = await _issue_tokens(user)
    return InitResponse(
        user=UserResponse.model_validate(user),
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/login", response_model=InitResponse, dependencies=[Depends(require_password_auth)])
@limiter.limit("5/minute")
async def login(request: Request, req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login with email/username + password. Returns user info and JWT tokens."""
    optic.debug("auth login attempt")
    source_ip, user_agent = _extract_request_info(request)
    identifier = req.email
    if "@" in identifier:
        stmt = select(User).where(User.email == identifier)
    else:
        stmt = select(User).where(or_(User.username == identifier, User.email == identifier))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user or not user.verify_password(req.password):
        await emit_security_event(
            SecurityEvent(
                event_type=EventType.LOGIN_FAILURE,
                severity=Severity.WARNING,
                outcome="failure",
                actor_email=identifier,
                source_ip=source_ip,
                user_agent=user_agent,
                detail="Invalid credentials",
            )
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token, refresh_token, expires_in = await _issue_tokens(user)
    await emit_security_event(
        SecurityEvent(
            event_type=EventType.LOGIN_SUCCESS,
            severity=Severity.INFO,
            outcome="success",
            actor_id=str(user.id),
            actor_email=user.email,
            actor_role=user.role.value,
            source_ip=source_ip,
            user_agent=user_agent,
        )
    )

    must_change = False
    try:
        redis = get_redis()
        must_change = bool(await redis.get(f"must_change_password:{user.id}"))
    except RedisError:
        pass

    return {
        **InitResponse(
            user=UserResponse.model_validate(user),
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        ).model_dump(),
        "must_change_password": must_change,
    }


async def _provision_sso_user(
    db: AsyncSession,
    *,
    email: str,
    name: str,
    groups: list[str] | None,
    provider: str,
    subject_id: str | None,
) -> User:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            email=email,
            username=await generate_unique_username(email, db),
            name=name,
            role=UserRole.user,
            auth_provider=provider,
            sso_subject_id=subject_id,
        )
        db.add(user)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()
            if user is None:
                raise HTTPException(status_code=500, detail="Failed to create or find user")
    elif user.auth_provider == "local" and not user.sso_subject_id:
        user.auth_provider = provider
        user.sso_subject_id = subject_id

    if groups:
        try:
            await db.execute(sa_delete(UserGroup).where(UserGroup.user_id == user.id))
            db.add_all([UserGroup(user_id=user.id, group_name=g) for g in groups])
        except Exception as e:
            optic.warning("Failed to persist SSO groups: {}", e)

    return user


async def _complete_sso_login(
    request: Request,
    db: AsyncSession,
    user: User,
    groups: list[str] | None,
) -> RedirectResponse:
    access_token, refresh_token, expires_in = await _issue_tokens(user, groups=groups)
    await db.commit()

    code = secrets.token_urlsafe(32)
    try:
        redis = get_redis()
        await redis.setex(
            f"oauth_code:{code}",
            30,
            json.dumps(
                {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_in": expires_in,
                    "user_id": str(user.id),
                    "role": user.role.value,
                }
            ),
        )
    except RedisError as e:
        optic.warning("Redis unavailable during OAuth callback: {}", e)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    source_ip, user_agent = _extract_request_info(request)
    await emit_security_event(
        SecurityEvent(
            event_type=EventType.SSO_SUCCESS,
            severity=Severity.INFO,
            outcome="success",
            actor_id=str(user.id),
            actor_email=user.email,
            actor_role=user.role.value,
            source_ip=source_ip,
            user_agent=user_agent,
        )
    )

    oauth_next = request.session.pop("oauth_next", None)
    frontend_base = ds.get_sync("deployment.frontend_url", "http://localhost:3000")
    frontend_redirect = f"{frontend_base}/login?code={code}"
    if _is_safe_next(oauth_next):
        frontend_redirect += f"&next={quote(oauth_next, safe='')}"
    return RedirectResponse(url=frontend_redirect)


@router.get("/oauth/login")
async def oauth_login(request: Request, next: str | None = None):
    """Initiates the OAuth SSO flow"""
    optic.debug("oauth_login called")
    if not oauth.oidc:
        raise HTTPException(status_code=500, detail="OAuth is not configured on the server")
    return await _start_oauth_flow(
        request,
        oauth.oidc,
        callback_path="/api/v1/auth/oauth/callback",
        next_path=next,
    )


# ── OAuth / OIDC callback ───────────────────────────────────────────────
#
# Each step appends a check to ``diag``. On the happy path, ``diag`` is dropped
# in memory and the user is redirected to /login?code=X. On any failure, the
# checks are persisted in Redis (TTL 10 min) and the user is redirected to
# /login?sso_error=<corr_id>; the frontend fetches the diagnostics via the
# public endpoint below. This means real logins also surface per-step failure
# detail in the UI -- not just generic "OAuth authorization failed".


async def _persist_oidc_failure(diag: list[dict], summary: str, actor_email: str | None) -> str:
    """Persist the failure diagnostics and return a correlation id for the URL."""
    corr_id = sso_diagnostics.new_session_id()
    try:
        await sso_diagnostics.finalize(corr_id, checks=diag, summary=summary, actor_email=actor_email)
    except Exception as e:
        optic.warning("oidc callback: failed to persist diagnostics: {}", e)
    return corr_id


def _oidc_error_redirect(frontend_base: str, corr_id: str) -> RedirectResponse:
    """Redirect to the login page carrying a sanitized correlation id.

    ``frontend_base`` is from server deployment config, never request input.
    ``corr_id`` is server-generated (``new_session_id``) but we re-validate
    at this boundary anyway. Query values are encoded by ``urlencode``.
    """
    if not sso_diagnostics.is_safe_session_id(corr_id):
        corr_id = "invalid"
    base = frontend_base.rstrip("/")
    qs = urlencode({"sso_error": corr_id})
    return RedirectResponse(url=f"{base}/login?{qs}")


@router.get("/oauth/callback")
async def oauth_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle the OAuth SSO callback with per-step diagnostics on failure.

    Dual-mode: a normal IdP redirect goes through authlib's state machine and
    issues a real session. An end-to-end test request is identifiable by a
    ``state=__e2e:<session_id>`` parameter -- in that case we bypass authlib
    (its state is keyed in a separate session cookie that the admin doesn't
    have here) and do a raw token exchange, recording each step but never
    issuing tokens. Routing both modes through the *same* registered redirect
    URI means admins don't have to whitelist a second URL at their IdP.
    """
    optic.debug("oauth_callback called")
    frontend_base = ds.get_sync("deployment.frontend_url", "http://localhost:3000")

    # Detect e2e mode before any other work so we can short-circuit into the
    # raw-OAuth branch without authlib running first and failing on state.
    state_param = request.query_params.get("state", "")
    if state_param.startswith(sso_diagnostics.E2E_SENTINEL_PREFIX):
        return await _handle_oidc_e2e_callback(request, db, frontend_base, state_param)

    if not oauth.oidc:
        # Server isn't configured for OIDC at all -- not a per-step failure;
        # surface as a plain error redirect with a synthetic diagnostics entry.
        corr_id = await _persist_oidc_failure(
            [
                make_check(
                    "oidc_configured",
                    "OIDC client configured on server",
                    "fail",
                    "OIDC client ID, client secret, or discovery URL not set.",
                    "Configure OIDC in the SSO tab and restart the API.",
                )
            ],
            summary="OIDC not configured",
            actor_email=None,
        )
        return _oidc_error_redirect(frontend_base, corr_id)

    source_ip, user_agent = _extract_request_info(request)
    diag: list[dict] = [make_check("callback_received", "Callback hit from IdP", "pass")]

    # Step 1: exchange the authorization code for tokens.
    try:
        token = await oauth.oidc.authorize_access_token(request)
        diag.append(make_check("token_exchange", "Authorization code exchanged for tokens", "pass"))
    except Exception as e:
        msg = str(e) or e.__class__.__name__
        hint = "Verify the redirect URI registered at the IdP matches deployment.frontend_url + /api/v1/auth/oauth/callback exactly."
        if "state" in msg.lower():
            hint = "The 'state' parameter didn't round-trip. Check that cookies/session survive the IdP redirect (SameSite, domain, browser extensions)."
        elif "invalid_client" in msg.lower():
            hint = "IdP rejected the client credentials. Verify oauth.client_id and oauth.client_secret."
        elif "invalid_grant" in msg.lower():
            hint = "Authorization code already used, expired, or for a different client. Try logging in again."
        diag.append(make_check("token_exchange", "Authorization code exchanged for tokens", "fail", msg, hint))
        await emit_security_event(
            SecurityEvent(
                event_type=EventType.SSO_FAILURE,
                severity=Severity.WARNING,
                outcome="failure",
                source_ip=source_ip,
                user_agent=user_agent,
                detail=f"OAuth authorization failed: {e}",
            )
        )
        optic.warning("OIDC authorize_access_token failed: {}", e)
        corr_id = await _persist_oidc_failure(diag, summary=f"Token exchange failed: {msg}", actor_email=None)
        return _oidc_error_redirect(frontend_base, corr_id)

    # Step 2: extract userinfo (merged ID-token + /userinfo claims by authlib).
    userinfo = token.get("userinfo") if isinstance(token, dict) else None
    if not isinstance(userinfo, dict):
        diag.append(
            make_check(
                "userinfo_present",
                "ID token contains userinfo claims",
                "fail",
                "IdP returned no 'userinfo' alongside the token.",
                "Confirm the IdP issues an ID token with the 'openid' scope. If using a userinfo endpoint, ensure it returns JSON.",
            )
        )
        corr_id = await _persist_oidc_failure(diag, summary="No userinfo in IdP token", actor_email=None)
        return _oidc_error_redirect(frontend_base, corr_id)
    diag.append(make_check("userinfo_present", "ID token contains userinfo claims", "pass"))

    # Step 3: required email claim.
    email = userinfo.get("email")
    if not email or not isinstance(email, str):
        present_claims = ", ".join(sorted(k for k in userinfo if not k.startswith("_"))) or "(none)"
        diag.append(
            make_check(
                "email_claim",
                "Email claim present in ID token",
                "fail",
                f"IdP returned no 'email' claim. Claims received: {present_claims}",
                "Request the 'email' scope at the IdP, or map an alternative attribute (e.g. preferred_username) to the email claim.",
            )
        )
        corr_id = await _persist_oidc_failure(diag, summary="email claim missing", actor_email=None)
        return _oidc_error_redirect(frontend_base, corr_id)
    email = email.strip().lower()
    diag.append(make_check("email_claim", "Email claim present in ID token", "pass"))

    # Step 4: name + groups (non-fatal absences are 'skip').
    name_raw = userinfo.get("name") or userinfo.get("preferred_username")
    name = name_raw if isinstance(name_raw, str) and name_raw else "SSO User"
    if name_raw:
        diag.append(make_check("name_claim", "Display-name claim present", "pass"))
    else:
        diag.append(
            make_check(
                "name_claim",
                "Display-name claim present",
                "skip",
                "No 'name' or 'preferred_username' claim; using fallback 'SSO User'.",
                "Add 'profile' scope at the IdP if you want a real display name.",
            )
        )

    subject_raw = userinfo.get("sub")
    subject_id = subject_raw if isinstance(subject_raw, str) else None

    groups_raw = userinfo.get("groups", [])
    groups: list[str] = []
    if isinstance(groups_raw, list) and all(isinstance(g, str) for g in groups_raw):
        groups = groups_raw
        if groups:
            diag.append(make_check("groups_claim", f"Groups claim present ({len(groups)})", "pass"))
        else:
            diag.append(
                make_check("groups_claim", "Groups claim present", "skip", "IdP returned an empty 'groups' array.")
            )
    elif groups_raw:
        diag.append(
            make_check(
                "groups_claim",
                "Groups claim present",
                "skip",
                "IdP returned 'groups' in an unexpected format (expected list of strings).",
                "Map your IdP roles claim to 'groups' as an array of strings.",
            )
        )
    else:
        diag.append(
            make_check(
                "groups_claim",
                "Groups claim present",
                "skip",
                "No 'groups' claim returned. RBAC will fall back to default role.",
            )
        )

    # Step 5: user lookup / JIT.
    try:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
    except Exception as e:
        optic.exception("oauth_callback: user lookup failed")
        diag.append(
            make_check(
                "user_lookup",
                "Look up existing user by email",
                "fail",
                str(e),
                "Check database connectivity from the API container.",
            )
        )
        corr_id = await _persist_oidc_failure(diag, summary="DB lookup failed", actor_email=email)
        return _oidc_error_redirect(frontend_base, corr_id)

    if user is None:
        try:
            user = User(
                email=email,
                username=await generate_unique_username(email, db),
                name=name,
                role=UserRole.user,
                auth_provider="oidc",
                sso_subject_id=subject_id,
            )
            db.add(user)
            await db.flush()
            diag.append(make_check("jit_provisioning", "JIT-create new user", "pass"))
        except IntegrityError:
            await db.rollback()
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()
            if not user:
                diag.append(
                    make_check(
                        "jit_provisioning",
                        "JIT-create new user",
                        "fail",
                        "Race condition: user could not be created or found after retry.",
                        "Inspect the users table for constraint violations.",
                    )
                )
                corr_id = await _persist_oidc_failure(diag, summary="JIT user-create race", actor_email=email)
                return _oidc_error_redirect(frontend_base, corr_id)
            diag.append(
                make_check(
                    "jit_provisioning",
                    "JIT-create new user",
                    "pass",
                    "Created concurrently by another request; using existing record.",
                )
            )
        except Exception as e:
            optic.exception("oauth_callback: JIT user create failed")
            diag.append(
                make_check(
                    "jit_provisioning",
                    "JIT-create new user",
                    "fail",
                    str(e),
                    "Check the users table schema (email uniqueness, sso_subject_id column).",
                )
            )
            corr_id = await _persist_oidc_failure(diag, summary="JIT user create failed", actor_email=email)
            return _oidc_error_redirect(frontend_base, corr_id)
    else:
        diag.append(make_check("user_lookup", "Look up existing user by email", "pass"))
        if user.auth_provider == "local" and not user.sso_subject_id:
            user.auth_provider = "oidc"
            user.sso_subject_id = subject_id

    # Step 6: persist SSO groups (best-effort, never blocks login).
    if groups:
        try:
            await db.execute(sa_delete(UserGroup).where(UserGroup.user_id == user.id))
            db.add_all([UserGroup(user_id=user.id, group_name=g) for g in groups])
        except Exception as e:
            optic.warning("Failed to persist SSO groups: {}", e)

    # Step 7: issue tokens + commit.
    try:
        access_token, refresh_token, expires_in = await _issue_tokens(user, groups=groups)
        await db.commit()
        diag.append(make_check("issue_tokens", "Issue JWT access + refresh tokens", "pass"))
    except HTTPException:
        raise  # _issue_tokens already raised the right thing
    except Exception as e:
        optic.exception("oauth_callback: token issue failed")
        diag.append(make_check("issue_tokens", "Issue JWT access + refresh tokens", "fail", str(e)))
        corr_id = await _persist_oidc_failure(diag, summary="Token issuance failed", actor_email=email)
        return _oidc_error_redirect(frontend_base, corr_id)

    # Step 8: hand off via short-lived code in Redis.
    code = secrets.token_urlsafe(32)
    try:
        redis = get_redis()
        await redis.setex(
            f"oauth_code:{code}",
            30,
            json.dumps(
                {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_in": expires_in,
                    "user_id": str(user.id),
                    "role": user.role.value,
                }
            ),
        )
    except RedisError as e:
        optic.warning("Redis unavailable during OAuth callback: {}", e)
        diag.append(
            make_check(
                "session_handoff",
                "Stash one-shot code for frontend exchange",
                "fail",
                "Redis unavailable.",
                "Check Redis health from the API container.",
            )
        )
        corr_id = await _persist_oidc_failure(diag, summary="Redis unavailable", actor_email=email)
        return _oidc_error_redirect(frontend_base, corr_id)

    await emit_security_event(
        SecurityEvent(
            event_type=EventType.SSO_SUCCESS,
            severity=Severity.INFO,
            outcome="success",
            actor_id=str(user.id),
            actor_email=email,
            actor_role=user.role.value,
            source_ip=source_ip,
            user_agent=user_agent,
        )
    )
    oauth_next = request.session.pop("oauth_next", None)
    frontend_redirect = f"{frontend_base}/login?code={code}"
    if oauth_next and oauth_next.startswith("/") and not oauth_next.startswith("//") and "\\" not in oauth_next:
        frontend_redirect += f"&next={quote(oauth_next, safe='')}"
    return RedirectResponse(url=frontend_redirect)


@router.get("/oauth/google/login")
async def google_oauth_login(request: Request, next: str | None = None):
    """Initiates the Google OAuth flow."""
    optic.debug("google_oauth_login called")
    google_client = getattr(oauth, "google", None)
    if not google_client:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured on the server")
    return await _start_oauth_flow(
        request,
        google_client,
        callback_path="/api/v1/auth/oauth/google/callback",
        next_path=next,
    )


@router.get("/oauth/google/callback")
async def google_oauth_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Handles the Google OAuth callback."""
    optic.debug("google_oauth_callback called")
    google_client = getattr(oauth, "google", None)
    if not google_client:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured on the server")

    source_ip, user_agent = _extract_request_info(request)
    try:
        token = await google_client.authorize_access_token(request)
    except Exception as e:
        await emit_security_event(
            SecurityEvent(
                event_type=EventType.SSO_FAILURE,
                severity=Severity.WARNING,
                outcome="failure",
                source_ip=source_ip,
                user_agent=user_agent,
                detail=f"Google OAuth authorization failed: {e}",
            )
        )
        optic.warning("Google authorize_access_token failed: {}", e)
        raise HTTPException(status_code=400, detail="Google OAuth authorization failed")

    userinfo = token.get("userinfo") if isinstance(token, dict) else None
    if not isinstance(userinfo, dict):
        raise HTTPException(status_code=400, detail="Missing userinfo in token")

    email = userinfo.get("email")
    if not email or not isinstance(email, str):
        raise HTTPException(status_code=400, detail="Email claim is missing from ID token")
    if not userinfo.get("email_verified", False):
        raise HTTPException(status_code=400, detail="Google account email is not verified")

    email = email.strip().lower()
    allowed_domains = _google_allowed_domains()
    if allowed_domains:
        domain = email.rsplit("@", 1)[-1]
        if domain not in allowed_domains:
            await emit_security_event(
                SecurityEvent(
                    event_type=EventType.SSO_FAILURE,
                    severity=Severity.WARNING,
                    outcome="failure",
                    actor_email=email,
                    source_ip=source_ip,
                    user_agent=user_agent,
                    detail=f"Email domain '{domain}' not in Google OAuth allowlist",
                )
            )
            raise HTTPException(status_code=403, detail="Your email domain is not allowed to sign in")

    name_raw = userinfo.get("name") or userinfo.get("given_name")
    name = name_raw if isinstance(name_raw, str) and name_raw else "Google User"
    subject = userinfo.get("sub")
    subject_id = subject if isinstance(subject, str) else None

    user = await _provision_sso_user(db, email=email, name=name, groups=None, provider="google", subject_id=subject_id)
    return await _complete_sso_login(request, db, user, None)


@router.get("/oauth/github/login")
async def github_oauth_login(request: Request, next: str | None = None):
    """Initiates the GitHub OAuth flow."""
    optic.debug("github_oauth_login called")
    github_client = getattr(oauth, "github", None)
    if not github_client:
        raise HTTPException(status_code=500, detail="GitHub OAuth is not configured on the server")
    return await _start_oauth_flow(
        request,
        github_client,
        callback_path="/api/v1/auth/oauth/github/callback",
        next_path=next,
    )


async def _github_pick_verified_email(github_client, token: dict) -> str:
    """Return the user's primary verified email, or any verified one.

    The profile 'email' field is never trusted: it can be absent (hidden) and
    GitHub does not require it to be verified. Only /user/emails entries with
    verified=true count; no verified address means no login.
    """
    resp = await github_client.get("user/emails", token=token, headers=_GITHUB_API_HEADERS)
    if resp.status_code != 200:
        optic.warning("GitHub /user/emails returned HTTP {}", resp.status_code)
        raise HTTPException(status_code=400, detail="Could not fetch GitHub email addresses")
    entries = resp.json()
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="Could not fetch GitHub email addresses")

    verified = [
        e for e in entries if isinstance(e, dict) and e.get("verified") is True and isinstance(e.get("email"), str)
    ]
    primary = next((e["email"] for e in verified if e.get("primary") is True), None)
    email = primary or (verified[0]["email"] if verified else None)
    if not email:
        raise HTTPException(status_code=400, detail="GitHub account has no verified email address")
    return email.strip().lower()


async def _github_check_org_membership(github_client, token: dict, allowed_orgs: set[str]) -> str | None:
    """Return the first allowed org the user is an *active* member of, else None.

    Pending invitations (state != active) don't count. API errors are treated
    as non-membership so login fails closed when GitHub is unreachable.
    """
    for org in sorted(allowed_orgs):
        try:
            resp = await github_client.get(f"user/memberships/orgs/{org}", token=token, headers=_GITHUB_API_HEADERS)
        except Exception as e:
            optic.warning("GitHub org membership check errored for '{}': {}", org, e)
            continue
        if resp.status_code != 200:
            continue
        membership = resp.json()
        if isinstance(membership, dict) and membership.get("state") == "active":
            return org
    return None


@router.get("/oauth/github/callback")
async def github_oauth_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Handles the GitHub OAuth callback."""
    optic.debug("github_oauth_callback called")
    github_client = getattr(oauth, "github", None)
    if not github_client:
        raise HTTPException(status_code=500, detail="GitHub OAuth is not configured on the server")

    source_ip, user_agent = _extract_request_info(request)
    try:
        token = await github_client.authorize_access_token(request)
    except Exception as e:
        await emit_security_event(
            SecurityEvent(
                event_type=EventType.SSO_FAILURE,
                severity=Severity.WARNING,
                outcome="failure",
                source_ip=source_ip,
                user_agent=user_agent,
                detail=f"GitHub OAuth authorization failed: {e}",
            )
        )
        optic.warning("GitHub authorize_access_token failed: {}", e)
        raise HTTPException(status_code=400, detail="GitHub OAuth authorization failed")

    try:
        profile_resp = await github_client.get("user", token=token, headers=_GITHUB_API_HEADERS)
    except Exception as e:
        optic.warning("GitHub /user fetch failed: {}", e)
        raise HTTPException(status_code=502, detail="Could not fetch GitHub profile")
    if profile_resp.status_code != 200:
        optic.warning("GitHub /user returned HTTP {}", profile_resp.status_code)
        raise HTTPException(status_code=400, detail="Could not fetch GitHub profile")
    profile = profile_resp.json()
    # The numeric id is the stable subject; the login handle can be renamed
    # and re-registered by someone else, so it is never used as an identifier.
    if not isinstance(profile, dict) or profile.get("id") is None:
        raise HTTPException(status_code=400, detail="GitHub profile is missing a user id")
    subject_id = str(profile["id"])

    try:
        email = await _github_pick_verified_email(github_client, token)
    except HTTPException as exc:
        if exc.status_code == 400 and "verified" in exc.detail:
            await emit_security_event(
                SecurityEvent(
                    event_type=EventType.SSO_FAILURE,
                    severity=Severity.WARNING,
                    outcome="failure",
                    source_ip=source_ip,
                    user_agent=user_agent,
                    detail="GitHub account has no verified email address",
                )
            )
        raise

    allowed_orgs = _github_allowed_orgs()
    if allowed_orgs:
        member_org = await _github_check_org_membership(github_client, token, allowed_orgs)
        if member_org is None:
            await emit_security_event(
                SecurityEvent(
                    event_type=EventType.SSO_FAILURE,
                    severity=Severity.WARNING,
                    outcome="failure",
                    actor_email=email,
                    source_ip=source_ip,
                    user_agent=user_agent,
                    detail="GitHub user is not an active member of an allowed organization",
                )
            )
            raise HTTPException(
                status_code=403, detail="Your GitHub account is not a member of an allowed organization"
            )

    name_raw = profile.get("name") or profile.get("login")
    name = name_raw if isinstance(name_raw, str) and name_raw else "GitHub User"

    user = await _provision_sso_user(db, email=email, name=name, groups=None, provider="github", subject_id=subject_id)
    return await _complete_sso_login(request, db, user, None)


# ── OIDC end-to-end test callback ───────────────────────────────────────
#
# Called by the IdP after the admin clicks "End to End Test" in the SSO admin
# page and authenticates with a real (test) user. We exchange the code,
# validate the ID token (signature + iss/aud/exp/nonce), and record per-step
# checks under the diagnostics session id passed via state. Crucially we
# never issue JWTs or set cookies -- the admin SSO page polls for the result.


_E2E_TOKEN_TIMEOUT = 15.0


def _decode_id_token_with_jwks(id_token: str, jwks_uri: str, client_id: str, issuer: str | None) -> dict:
    """Verify signature + iss/aud/exp/nonce. Blocking; call via to_thread."""
    jwks_client = PyJWKClient(jwks_uri, timeout=10)
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)
    return pyjwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA", "PS256"],
        audience=client_id,
        issuer=issuer,
        options={
            "verify_signature": True,
            "verify_aud": True,
            "verify_exp": True,
            "verify_iat": True,
            "verify_iss": bool(issuer),
        },
    )


async def _handle_oidc_e2e_callback(
    request: Request,
    db: AsyncSession,
    frontend_base: str,
    state_param: str,
) -> Response:
    """Process an end-to-end test callback. Called from ``oauth_callback`` when
    ``state`` starts with the e2e sentinel. We bypass authlib entirely and do
    a raw token exchange so the admin doesn't need to whitelist a second
    redirect URI at the IdP.
    """
    params = request.query_params
    err = params.get("error")
    code = params.get("code")
    # state is "__e2e:<session_id>". Extract just the session id.
    session_id_from_state = state_param[len(sso_diagnostics.E2E_SENTINEL_PREFIX) :]

    admin_sso_url = f"{frontend_base.rstrip('/')}/sso"

    if (
        not session_id_from_state
        or not re.fullmatch(r"[A-Za-z0-9_\-]+", session_id_from_state)
        or len(session_id_from_state) > 64
    ):
        return Response(
            content=sso_diagnostics.render_error_page(
                "Malformed test session",
                "The state parameter returned by the IdP was not in the expected format. "
                "Start a fresh end-to-end test from the admin SSO page.",
                admin_sso_url,
            ),
            media_type="text/html",
            status_code=400,
        )
    session = await sso_diagnostics.get_session(session_id_from_state)
    if session is None or session.get("provider") != "oidc" or session.get("mode") != "e2e":
        return Response(
            content=sso_diagnostics.render_error_page(
                "Test session expired",
                "This test session is no longer available (10-minute TTL elapsed, "
                "or the session was never created). Start a fresh end-to-end test.",
                admin_sso_url,
            ),
            media_type="text/html",
            status_code=404,
        )
    session_id = session["session_id"]

    diag: list[dict] = list(session.get("checks", []))
    actor_email: str | None = None

    async def _done(ok: bool, summary: str) -> Response:
        await sso_diagnostics.finalize(session_id, checks=diag, summary=summary, actor_email=actor_email)
        return Response(
            content=sso_diagnostics.render_result_page(
                session_id=session_id,
                provider="oidc",
                ok=ok,
                checks=diag,
                actor_email=actor_email,
                summary=summary,
                admin_url=admin_sso_url,
            ),
            media_type="text/html",
            status_code=200,
        )

    # Step A: IdP returned an explicit error.
    if err:
        err_desc = params.get("error_description", "")
        diag.append(
            make_check(
                "idp_authorization",
                "IdP authorized the user",
                "fail",
                f"IdP returned error={err}; {err_desc}".strip("; "),
                "Verify the client_id, redirect_uri, and that the test user has access to this client.",
            )
        )
        return await _done(ok=False, summary=f"IdP error: {err}")

    if not code:
        diag.append(
            make_check(
                "idp_authorization",
                "IdP returned an authorization code",
                "fail",
                "Callback hit without a 'code' parameter.",
                "Verify the OIDC client is configured for response_type=code.",
            )
        )
        return await _done(ok=False, summary="No code in callback")
    diag.append(make_check("idp_authorization", "IdP returned an authorization code", "pass"))

    # State binding is implicit: we located the session by the state value,
    # so a tampered state would have failed the lookup above.
    diag.append(make_check("state_match", "State parameter echoed unchanged", "pass"))

    client_id = ds.get_sync("oauth.client_id")
    client_secret = ds.get_sync("oauth.client_secret")
    if not client_id or not client_secret:
        diag.append(make_check("oidc_configured", "OIDC client credentials configured", "fail"))
        return await _done(ok=False, summary="OIDC creds missing")

    token_endpoint = session.get("token_endpoint")
    if not token_endpoint:
        diag.append(
            make_check(
                "token_endpoint_known",
                "Token endpoint resolved from discovery",
                "fail",
                "Discovery doc had no token_endpoint when we started.",
            )
        )
        return await _done(ok=False, summary="No token endpoint")
    diag.append(make_check("token_endpoint_known", "Token endpoint resolved from discovery", "pass"))

    # Step B: exchange code for tokens (raw, so we control diagnostics).
    # We reuse the *production* callback URL so the IdP only ever sees one
    # redirect URI -- the same one whitelisted for normal logins.
    redirect_uri = frontend_base.rstrip("/") + "/api/v1/auth/oauth/callback"
    try:
        async with httpx.AsyncClient(timeout=_E2E_TOKEN_TIMEOUT, follow_redirects=False) as client:
            resp = await client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                auth=(client_id, client_secret),
                headers={"Accept": "application/json"},
            )
    except httpx.TimeoutException:
        diag.append(
            make_check(
                "token_exchange",
                "Token endpoint responds",
                "fail",
                "Timed out talking to the IdP token endpoint.",
                "Check egress to the IdP from the API container.",
            )
        )
        return await _done(ok=False, summary="Token endpoint timeout")
    except Exception as e:
        diag.append(make_check("token_exchange", "Token endpoint responds", "fail", str(e)))
        return await _done(ok=False, summary="Token endpoint network error")

    if resp.status_code != 200:
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text[:400]}
        err_name = body.get("error") if isinstance(body, dict) else None
        err_desc = body.get("error_description") if isinstance(body, dict) else None
        hint = "IdP rejected the token exchange."
        if err_name == "invalid_client":
            hint = "oauth.client_secret is wrong, or this IdP requires a different auth method."
        elif err_name == "invalid_grant":
            hint = "Authorization code expired or already used. Try the test again."
        elif err_name == "redirect_uri_mismatch":
            hint = f"IdP didn't accept redirect_uri={redirect_uri}. Whitelist this URL at the IdP."
        diag.append(
            make_check(
                "token_exchange",
                "Token endpoint accepted the code",
                "fail",
                f"HTTP {resp.status_code}: {err_name or 'error'}{f' -- {err_desc}' if err_desc else ''}",
                hint,
            )
        )
        return await _done(ok=False, summary=f"Token exchange HTTP {resp.status_code}")
    diag.append(make_check("token_exchange", "Token endpoint accepted the code", "pass"))

    try:
        token_data = resp.json()
    except ValueError:
        diag.append(
            make_check(
                "token_response_json",
                "Token response is JSON",
                "fail",
                "IdP returned a 200 but the body isn't JSON.",
                "Check the Content-Type the IdP sets on /token.",
            )
        )
        return await _done(ok=False, summary="Non-JSON token response")

    if "refresh_token" in token_data:
        diag.append(make_check("refresh_token_issued", "Refresh token issued", "pass"))
    else:
        diag.append(
            make_check(
                "refresh_token_issued",
                "Refresh token issued",
                "skip",
                "IdP didn't issue a refresh_token. Sessions will expire when the access token does.",
                "Request the 'offline_access' scope or enable refresh tokens on the OIDC client.",
            )
        )

    id_token = token_data.get("id_token")
    if not id_token or not isinstance(id_token, str):
        diag.append(
            make_check(
                "id_token_present",
                "ID token returned",
                "fail",
                "Token response contains no id_token.",
                "Confirm the 'openid' scope was granted by the IdP.",
            )
        )
        return await _done(ok=False, summary="No id_token")
    diag.append(make_check("id_token_present", "ID token returned", "pass"))

    # Step C: verify signature + iss/aud/exp/nonce.
    jwks_uri = session.get("jwks_uri")
    if not jwks_uri:
        diag.append(
            make_check(
                "id_token_signature",
                "ID token signature verified",
                "skip",
                "Discovery doc had no jwks_uri; cannot verify signature.",
                "Configure jwks_uri on the IdP discovery doc.",
            )
        )
        claims = None
    else:
        try:
            claims = await asyncio.to_thread(
                _decode_id_token_with_jwks,
                id_token,
                jwks_uri,
                client_id,
                session.get("issuer"),
            )
            diag.append(make_check("id_token_signature", "ID token signature & audience verified", "pass"))
        except pyjwt.InvalidAudienceError:
            diag.append(
                make_check(
                    "id_token_signature",
                    "ID token audience matches client_id",
                    "fail",
                    f"id_token 'aud' is not {client_id}.",
                    "Configure the OIDC client at the IdP so the issued ID token's aud is exactly our client_id.",
                )
            )
            return await _done(ok=False, summary="Audience mismatch")
        except pyjwt.ExpiredSignatureError:
            diag.append(
                make_check(
                    "id_token_signature",
                    "ID token not expired",
                    "fail",
                    "id_token 'exp' is in the past.",
                    "Clock skew between IdP and SP -- check NTP.",
                )
            )
            return await _done(ok=False, summary="ID token expired")
        except pyjwt.InvalidIssuerError:
            diag.append(
                make_check(
                    "id_token_signature",
                    "ID token issuer matches",
                    "fail",
                    f"id_token 'iss' does not match {session.get('issuer')}.",
                )
            )
            return await _done(ok=False, summary="Issuer mismatch")
        except pyjwt.InvalidTokenError as e:
            diag.append(
                make_check(
                    "id_token_signature",
                    "ID token signature verified",
                    "fail",
                    str(e),
                    "Check JWKS reachability and key rotation policy on the IdP.",
                )
            )
            return await _done(ok=False, summary="ID token invalid")
        except Exception as e:
            optic.exception("oidc e2e: id_token validation crashed")
            diag.append(make_check("id_token_signature", "ID token signature verified", "fail", str(e)))
            return await _done(ok=False, summary="ID token validation error")

    if claims is None:
        # We couldn't verify signature but can still inspect the payload for diagnostics.
        try:
            claims = pyjwt.decode(id_token, options={"verify_signature": False})
        except pyjwt.InvalidTokenError:
            return await _done(ok=False, summary="Unverifiable id_token")

    # Nonce echo (defends against token replay across sessions).
    expected_nonce = session.get("nonce")
    if expected_nonce and claims.get("nonce") != expected_nonce:
        diag.append(
            make_check(
                "nonce_match",
                "Nonce echoed in ID token matches",
                "fail",
                "Nonce in id_token does not match the value we sent on /start.",
                "Verify the IdP supports and forwards the OIDC 'nonce' parameter.",
            )
        )
        return await _done(ok=False, summary="Nonce mismatch")
    diag.append(make_check("nonce_match", "Nonce echoed in ID token matches", "pass"))

    # Step D: claim extraction.
    email = claims.get("email")
    if isinstance(email, str) and email:
        actor_email = email.strip().lower()
        diag.append(make_check("email_claim", "Email claim present", "pass"))
    else:
        present = ", ".join(sorted(k for k in claims if not k.startswith("_"))) or "(none)"
        diag.append(
            make_check(
                "email_claim",
                "Email claim present",
                "fail",
                f"id_token has no 'email' claim. Claims received: {present}",
                "Request the 'email' scope at the IdP, or map an alternative attribute.",
            )
        )
        return await _done(ok=False, summary="No email claim")

    name_raw = claims.get("name") or claims.get("preferred_username")
    if isinstance(name_raw, str) and name_raw:
        diag.append(make_check("name_claim", "Display-name claim present", "pass"))
    else:
        diag.append(
            make_check(
                "name_claim",
                "Display-name claim present",
                "skip",
                "No name/preferred_username. UI would fall back to 'SSO User'.",
            )
        )

    groups = claims.get("groups", [])
    if isinstance(groups, list) and groups:
        diag.append(make_check("groups_claim", f"Groups claim present ({len(groups)})", "pass"))
    elif groups:
        diag.append(
            make_check("groups_claim", "Groups claim present", "skip", "IdP returned 'groups' in an unexpected format.")
        )
    else:
        diag.append(
            make_check(
                "groups_claim",
                "Groups claim present",
                "skip",
                "No 'groups' claim. RBAC would fall back to default role.",
            )
        )

    # Step E: would the production flow find/create the user?
    # Read-only -- the e2e flow never writes to the users table.
    try:
        result = await db.execute(select(User).where(User.email == actor_email))
        existing = result.scalar_one_or_none()
        if existing:
            diag.append(make_check("user_lookup", "User already exists in DB", "pass"))
        else:
            diag.append(
                make_check(
                    "user_lookup",
                    "User would be JIT-created",
                    "skip",
                    "User does not yet exist; production login would JIT-create.",
                )
            )
    except Exception as e:
        diag.append(make_check("user_lookup", "Look up user by email", "skip", f"Could not check DB ({e})."))

    diag.append(make_check("e2e_complete", "End-to-end test finished without issuing a session", "pass"))
    return await _done(ok=True, summary="OIDC end-to-end test completed")


@router.get("/sso/diagnostics/{corr_id}")
async def get_sso_diagnostics(corr_id: str):
    """Public lookup of a short-lived SSO failure diagnostics record.

    Used by the /login page to render a ChecksList when the user lands there
    with ``?sso_error=<corr_id>``. Returns 404 once the record expires (10
    minutes). The contents are intentionally sanitized -- check labels and
    hints only, no tokens or raw assertions.
    """
    if not corr_id or len(corr_id) > 64 or not re.fullmatch(r"[A-Za-z0-9_\-]+", corr_id):
        raise HTTPException(status_code=400, detail="Invalid diagnostics id")
    session = await sso_diagnostics.get_session(corr_id)
    if not session:
        raise HTTPException(status_code=404, detail="Diagnostics not found or expired")
    return sso_diagnostics.public_view(session)


@router.post("/exchange", response_model=InitResponse)
async def exchange_code(req: CodeExchangeRequest, db: AsyncSession = Depends(get_db)):
    """Exchange a one-time OAuth auth code for JWT credentials.

    The code is stored in Redis with a 30-second TTL and is deleted after
    a single successful use, preventing replay attacks.
    """
    optic.debug("exchange_code called")
    try:
        redis = get_redis()
        redis_key = f"oauth_code:{req.code}"
        data = await redis.getdel(redis_key)
    except RedisError as e:
        optic.warning("Redis unavailable during code exchange: {}", e)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not data:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    user_id = payload.get("user_id")

    if not access_token or not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    return InitResponse(
        user=UserResponse.model_validate(user),
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.get("/whoami", response_model=UserResponse)
async def whoami(current_user: User = Depends(get_current_user)):
    optic.debug("auth whoami")
    return UserResponse.model_validate(current_user)


@router.post("/logout")
async def logout(
    request: Request,
    req: LogoutRequest,
    current_user: User = Depends(get_current_user),
):
    """Revoke the current access token and optionally a refresh token.

    Blacklists the access token JTI in Redis so it can no longer be used,
    and marks the user_id as revoked so hook scripts stop sending telemetry.
    """
    # Extract the raw token from the Authorization header so we can get jti/exp
    optic.debug("auth logout")
    auth_header = request.headers.get("authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""

    try:
        payload = decode_access_token(token)
        jti = payload.get("jti")
        exp = payload.get("exp")
    except Exception:
        jti = None
        exp = None

    try:
        redis = get_redis()
        if jti and exp:
            now_ts = int(datetime.now(UTC).timestamp())
            ttl = max(exp - now_ts, 1)
            await redis.setex(f"revoked_jti:{jti}", ttl, "1")

        # Mark the user as revoked for 30 days (max hooks token lifetime)
        hooks_ttl = 30 * 86400
        await redis.setex(f"revoked_user:{current_user.id}", hooks_ttl, "1")
    except RedisError as e:
        optic.warning("Redis unavailable during logout: {}", e)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    # Optionally revoke the refresh token
    if req.refresh_token:
        try:
            refresh_payload = decode_refresh_token(req.refresh_token)
            refresh_jti = refresh_payload.get("jti")
            if refresh_jti:
                try:
                    await redis.delete(f"refresh_jti:{refresh_jti}")
                except RedisError:
                    pass
        except Exception:
            pass  # Best-effort

    source_ip, user_agent = _extract_request_info(request)
    await emit_security_event(
        SecurityEvent(
            event_type=EventType.LOGOUT,
            severity=Severity.INFO,
            outcome="success",
            actor_id=str(current_user.id),
            actor_email=current_user.email,
            actor_role=current_user.role.value,
            source_ip=source_ip,
            user_agent=user_agent,
        )
    )
    return {"detail": "Logged out"}


# ── JWT Token Endpoints ────────────────────────────────────


@router.post("/token", response_model=TokenResponse, dependencies=[Depends(require_password_auth)])
@limiter.limit(ds.get_sync("security.rate_limit_auth", "10/minute"))
async def issue_token(request: Request, req: TokenRequest, db: AsyncSession = Depends(get_db)):
    """Exchange email/username + password for JWT access + refresh tokens."""
    optic.trace("email={}", req.email)
    source_ip, user_agent = _extract_request_info(request)
    identifier = req.email
    if "@" in identifier:
        stmt = select(User).where(User.email == identifier)
    else:
        stmt = select(User).where(or_(User.username == identifier, User.email == identifier))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user or not user.verify_password(req.password):
        await emit_security_event(
            SecurityEvent(
                event_type=EventType.LOGIN_FAILURE,
                severity=Severity.WARNING,
                outcome="failure",
                actor_email=identifier,
                source_ip=source_ip,
                user_agent=user_agent,
                detail="Invalid credentials (token endpoint)",
            )
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await emit_security_event(
        SecurityEvent(
            event_type=EventType.LOGIN_SUCCESS,
            severity=Severity.INFO,
            outcome="success",
            actor_id=str(user.id),
            actor_email=user.email,
            actor_role=user.role.value,
            source_ip=source_ip,
            user_agent=user_agent,
        )
    )
    access_token, refresh_token, expires_in = await _issue_tokens(user)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/token/refresh", response_model=TokenResponse)
@limiter.limit(ds.get_sync("security.rate_limit_auth", "10/minute"))
async def refresh_token(request: Request, req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Exchange a valid refresh token for a new access token (and rotated refresh token)."""
    optic.debug("auth token refresh")
    try:
        payload = decode_refresh_token(req.refresh_token)
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid refresh token: {exc}")

    jti = payload.get("jti")
    user_id = payload.get("sub")
    if not jti or not user_id:
        raise HTTPException(status_code=401, detail="Invalid refresh token claims")

    # Check that the JTI has not been revoked
    try:
        redis = get_redis()
        stored = await redis.get(f"refresh_jti:{jti}")
    except RedisError as e:
        optic.warning("Redis unavailable during token refresh: {}", e)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if stored is None:
        raise HTTPException(status_code=401, detail="Refresh token has been revoked or expired")

    # Revoke the old refresh token (one-time use / rotation)
    try:
        await redis.delete(f"refresh_jti:{jti}")
    except RedisError:
        pass

    # Look up the user to ensure they still exist
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")

    # Issue new token pair
    groups = payload.get("groups", [])
    access_token, expires_in = create_access_token(user.id, user.role, groups=groups)
    new_refresh_token, new_jti = create_refresh_token(user.id, user.role, groups=groups)

    try:
        refresh_ttl = ds.get_sync_int("jwt.refresh_token_expire_days", 30) * 86400
        await redis.setex(f"refresh_jti:{new_jti}", refresh_ttl, str(user.id))
    except RedisError as e:
        optic.warning("Redis unavailable when storing new refresh JTI: {}", e)
        raise HTTPException(status_code=503, detail="Auth service temporarily unavailable")
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=expires_in,
    )


@router.post("/token/revoke")
@limiter.limit(ds.get_sync("security.rate_limit_auth", "10/minute"))
async def revoke_token(request: Request, req: RevokeRequest):
    """Revoke a refresh token so it can no longer be used."""
    optic.debug("revoke_token called")
    try:
        payload = decode_refresh_token(req.refresh_token)
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid refresh token: {exc}")

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=401, detail="Invalid refresh token claims")

    try:
        redis = get_redis()
        await redis.delete(f"refresh_jti:{jti}")
    except RedisError as e:
        optic.warning("Redis unavailable during token revocation: {}", e)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    return {"detail": "Token revoked"}


@router.put("/profile/password", dependencies=[Depends(require_password_auth)])
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    req: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Change the current user's password. Clears forced-change flag if set."""
    optic.debug("user initiated password change")
    if not current_user.verify_password(req.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    _validate_password_strength(req.new_password)
    current_user.set_password(req.new_password)
    await db.commit()

    try:
        redis = get_redis()
        await redis.delete(f"must_change_password:{current_user.id}")
    except RedisError:
        pass
    return {"message": "Password changed"}


@router.put("/profile/username", response_model=UserResponse)
async def set_username(
    req: UsernameUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set or update the current user's username."""
    optic.trace("req={}", req)
    if req.username == current_user.username:
        return current_user
    existing = await db.execute(select(User).where(User.username == req.username, User.id != current_user.id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")

    # A username that isn't a valid namespace (dots, uppercase, … kept verbatim by
    # migration 016) can never publish, so the publish lock would trap the user
    # for good. Let them out, and carry any pre-validation listings across so the
    # old namespace is not left orphaned.
    stuck_namespace = None if is_valid_namespace(current_user.username) else current_user.username
    if stuck_namespace is None and await user_has_listings(db, current_user.id):
        raise HTTPException(status_code=409, detail="Username cannot change after publishing a registry item")
    # Reserve across users and teams so a username never collides with a teamspace.
    try:
        await reserve_team_handle(db, req.username, exclude_user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    current_user.username = req.username
    if stuck_namespace is not None:
        await rename_namespace(db, stuck_namespace, req.username)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Username already taken")
    await db.refresh(current_user)
    return UserResponse.model_validate(current_user)


@router.post("/hooks-token")
async def create_hooks_token(current_user: User = Depends(get_current_user)):
    """Return a long-lived access token for OTEL telemetry hooks.

    Hooks need a static token in the environment that can't do refresh
    mid-session, so this endpoint issues a 30-day access token by default.
    """
    optic.trace("user_id={}", current_user.id)
    token, expires_in = create_access_token(
        current_user.id,
        current_user.role,
        expires_in_minutes=ds.get_sync_int("jwt.hooks_token_expire_minutes", 43200),
        groups=getattr(current_user, "_groups", []),
    )
    await emit_security_event(
        SecurityEvent(
            event_type=EventType.API_KEY_CREATED,
            severity=Severity.INFO,
            outcome="success",
            actor_id=str(current_user.id),
            actor_email=current_user.email,
            actor_role=current_user.role.value,
            detail="Hooks token created (30-day)",
        )
    )
    return {"access_token": token, "expires_in": expires_in}


# ── Avatar Upload ─────────────────────────────────────────────────

_MAX_AVATAR_BYTES = 2 * 1024 * 1024
# Data URL encoding adds ~33% overhead; 2MB binary → ~2.7MB encoded.
# Frontend also caps at 2MB before upload so these stay in sync.
_MAX_AVATAR_DATA_URL_LEN = int(2 * 1024 * 1024 * 1.4)
_AVATAR_ALLOWED_MIMES = {"image/png", "image/jpeg", "image/webp"}
_AVATAR_MAGIC_BYTES: dict[str, list[bytes]] = {
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/webp": [b"RIFF"],
}


def _validate_avatar_data_url(value: str) -> None:
    optic.trace("value={}", value)
    if len(value) > _MAX_AVATAR_DATA_URL_LEN:
        raise HTTPException(status_code=422, detail="Image data too large")

    match = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", value, re.DOTALL)
    if not match:
        raise HTTPException(status_code=422, detail="Avatar must be a base64 data URL")

    mime_type = match.group(1)
    b64_data = match.group(2)

    if mime_type not in _AVATAR_ALLOWED_MIMES:
        raise HTTPException(status_code=422, detail="Only PNG, JPEG, and WebP images are allowed")

    try:
        raw = base64.b64decode(b64_data)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid base64 data")

    if len(raw) > _MAX_AVATAR_BYTES:
        raise HTTPException(status_code=422, detail="Image too large (max 2MB)")

    signatures = _AVATAR_MAGIC_BYTES.get(mime_type, [])
    if not any(raw.startswith(sig) for sig in signatures):
        raise HTTPException(status_code=422, detail="File content does not match declared type")
    if mime_type == "image/webp" and raw[8:12] != b"WEBP":
        raise HTTPException(status_code=422, detail="File content does not match declared type")


@router.put("/profile/avatar", response_model=UserResponse)
@limiter.limit("1/minute")
async def upload_avatar(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    optic.debug("upload_avatar called")
    body = await request.json()
    avatar_url = body.get("avatar_url")
    if not avatar_url or not isinstance(avatar_url, str):
        raise HTTPException(status_code=422, detail="avatar_url is required")

    _validate_avatar_data_url(avatar_url)

    current_user.avatar_url = avatar_url
    await db.commit()
    await db.refresh(current_user)
    return UserResponse.model_validate(current_user)


@router.delete("/profile/avatar", response_model=UserResponse)
async def delete_avatar(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    optic.debug("delete_avatar called")
    current_user.avatar_url = None
    await db.commit()
    await db.refresh(current_user)
    return UserResponse.model_validate(current_user)
