# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""SAML 2.0 SSO endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
import uuid
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from loguru import logger as optic
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from sqlalchemy import select

from api.deps import get_db

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

import services.dynamic_settings as ds
from models.saml_config import SamlConfig
from models.user import User, UserRole
from schemas.sso_health import all_pass, make_check
from services import sso_diagnostics
from services.jwt_service import create_access_token, create_refresh_token
from services.redis import get_redis
from services.saml import (
    build_saml_settings,
    check_cert_expiry,
    check_idp_cert_against_metadata,
    check_idp_slo_url_reachable,
    check_idp_sso_url_reachable,
    check_nameid_format,
    check_sp_cert_key_match,
    check_sp_host_consistency,
    decrypt_private_key,
    encrypt_private_key,
    extract_name_id_and_attrs,
    generate_sp_key_pair,
    get_display_name,
    get_idp_metadata_xml,
)
from services.security_events import (
    EventType,
    SecurityEvent,
    Severity,
    emit_security_event,
)
from services.username_generator import generate_unique_username


def _get_frontend_url() -> str:
    return ds.get_sync("deployment.frontend_url", "http://localhost:3000")


def _safe_redirect_path(value: str | None) -> str:
    """Sanitize a redirect target to prevent open redirects.

    Accepts only relative paths (single leading slash, no protocol-relative URLs).
    """
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


router = APIRouter(prefix="/api/v1/sso/saml", tags=["sso"])

_dynamic_saml_config_cache: object | None = None
_dynamic_saml_config_signature: tuple[str, ...] | None = None
_dynamic_saml_config_lock = asyncio.Lock()

_DYNAMIC_SAML_KEYS = (
    "saml.idp_entity_id",
    "saml.idp_sso_url",
    "saml.idp_slo_url",
    "saml.idp_x509_cert",
    "saml.sp_entity_id",
    "saml.sp_acs_url",
    "saml.jit_provisioning",
    "saml.default_role",
    "saml.sp_key_encryption_password",
    "saml.sp_private_key",
    "saml.sp_x509_cert",
)


def _dynamic_saml_signature() -> tuple[str, ...]:
    return tuple(ds.get_sync(key) for key in _DYNAMIC_SAML_KEYS)


def _apply_external_saml_material(config):
    external_sp_key = ds.get_sync("saml.sp_private_key") if ds.has_external_saml_material() else None
    external_sp_cert = ds.get_sync("saml.sp_x509_cert") if external_sp_key else None
    external_idp_cert = ds.get_sync("saml.idp_x509_cert") if ds.is_externally_managed("saml.idp_x509_cert") else None
    if not external_sp_key and not external_idp_cert:
        return config
    return type(
        "ResolvedSamlConfig",
        (),
        {
            "idp_entity_id": config.idp_entity_id,
            "idp_sso_url": config.idp_sso_url,
            "idp_slo_url": config.idp_slo_url,
            "idp_x509_cert": external_idp_cert or config.idp_x509_cert,
            "sp_entity_id": config.sp_entity_id,
            "sp_acs_url": config.sp_acs_url,
            "sp_private_key_enc": config.sp_private_key_enc,
            "sp_x509_cert": external_sp_cert or config.sp_x509_cert,
            "external_sp_private_key": external_sp_key,
            "jit_provisioning": config.jit_provisioning,
            "default_role": config.default_role,
        },
    )()


async def _get_saml_config(db: AsyncSession) -> SamlConfig | None:
    """Return the active SAML config or build one from dynamic settings.

    The dynamic-settings path generates an SP key pair on first use; serialize via
    a lock so two concurrent cold-start requests can't each generate a key
    and have one overwrite the other (silent assertion-signature breakage).
    """
    global _dynamic_saml_config_cache, _dynamic_saml_config_signature

    result = await db.execute(select(SamlConfig).where(SamlConfig.active.is_(True)).limit(1))
    config = result.scalar_one_or_none()
    if config:
        return _apply_external_saml_material(config)
    if not (ds.get_sync("saml.idp_entity_id") and ds.get_sync("saml.idp_sso_url")):
        return None
    signature = _dynamic_saml_signature()
    if _dynamic_saml_config_cache is not None and _dynamic_saml_config_signature == signature:
        return _dynamic_saml_config_cache
    async with _dynamic_saml_config_lock:
        if _dynamic_saml_config_cache is not None and _dynamic_saml_config_signature == signature:
            return _dynamic_saml_config_cache
        sp_entity_id = ds.get_sync("saml.sp_entity_id") or f"{_get_frontend_url()}/api/v1/sso/saml/metadata"
        sp_acs_url = ds.get_sync("saml.sp_acs_url") or f"{_get_frontend_url()}/api/v1/sso/saml/acs"
        external_sp_key = ds.get_sync("saml.sp_private_key") if ds.has_external_saml_material() else None
        if external_sp_key:
            private_key_pem = external_sp_key
            cert_pem = ds.get_sync("saml.sp_x509_cert")
            sp_key_enc = ""
        else:
            enc_password = ds.get_sync("saml.sp_key_encryption_password")
            if not enc_password:
                optic.warning(
                    "saml.sp_key_encryption_password is not set. "
                    "SP private key will be stored unencrypted. "
                    "Set this value in production."
                )
            private_key_pem, cert_pem = generate_sp_key_pair(common_name=sp_entity_id)
            sp_key_enc = encrypt_private_key(private_key_pem, enc_password)
        dynamic_config = type(
            "DynamicSamlConfig",
            (),
            {
                "idp_entity_id": ds.get_sync("saml.idp_entity_id"),
                "idp_sso_url": ds.get_sync("saml.idp_sso_url"),
                "idp_slo_url": ds.get_sync("saml.idp_slo_url"),
                "idp_x509_cert": ds.get_sync("saml.idp_x509_cert"),
                "sp_entity_id": sp_entity_id,
                "sp_acs_url": sp_acs_url,
                "sp_private_key_enc": sp_key_enc,
                "sp_x509_cert": cert_pem,
                "external_sp_private_key": external_sp_key,
                "jit_provisioning": ds.get_sync_bool("saml.jit_provisioning", True),
                "default_role": ds.get_sync("saml.default_role", "user"),
            },
        )()
        _dynamic_saml_config_cache = dynamic_config
        _dynamic_saml_config_signature = signature
        return dynamic_config


def _decrypt_sp_key(config) -> str:
    if external_key := getattr(config, "external_sp_private_key", None):
        return external_key
    password = ds.get_sync("saml.sp_key_encryption_password")
    return decrypt_private_key(config.sp_private_key_enc, password)


def _prepare_saml_request(request: Request) -> dict:
    parsed = urlparse(ds.get_sync("deployment.frontend_url", "http://localhost:3000"))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return {
        "https": "on" if parsed.scheme == "https" else "off",
        "http_host": f"{parsed.hostname}:{port}" if port not in (80, 443) else parsed.hostname,
        "server_port": str(port),
        "script_name": request.url.path,
        "get_data": dict(request.query_params),
        "post_data": {},
    }


async def _prepare_saml_request_with_body(request: Request) -> dict:
    req_data = _prepare_saml_request(request)
    form = await request.form()
    req_data["post_data"] = dict(form)
    return req_data


def _build_auth(config, sp_private_key: str, request_data: dict) -> OneLogin_Saml2_Auth:
    sp_slo_url = ""
    if getattr(config, "idp_slo_url", ""):
        sp_slo_url = f"{_get_frontend_url()}/api/v1/sso/saml/sls"
    saml_settings = build_saml_settings(
        idp_entity_id=config.idp_entity_id,
        idp_sso_url=config.idp_sso_url,
        idp_x509_cert=config.idp_x509_cert,
        sp_entity_id=config.sp_entity_id,
        sp_acs_url=config.sp_acs_url,
        sp_private_key=sp_private_key,
        sp_x509_cert=config.sp_x509_cert,
        idp_slo_url=config.idp_slo_url or "",
        sp_slo_url=sp_slo_url,
    )
    return OneLogin_Saml2_Auth(request_data, old_settings=saml_settings)


async def _issue_tokens(user: User) -> tuple[str, str, int]:
    access_token, expires_in = create_access_token(user.id, user.role)
    refresh_token, jti = create_refresh_token(user.id, user.role)
    refresh_ttl = ds.get_sync_int("jwt.refresh_token_expire_days", 30) * 86400
    redis = get_redis()
    await redis.setex(f"refresh_jti:{jti}", refresh_ttl, str(user.id))
    await redis.delete(f"revoked_user:{user.id}")
    return access_token, refresh_token, expires_in


_SAML_HEALTH_TIMEOUT = 10.0


async def _run_saml_check_suite(
    config,
    sp_key: str,
    frontend_url: str,
    client: httpx.AsyncClient,
) -> list[dict]:
    """Run all SAML checks against a config. One HTTP client; one metadata fetch."""
    checks: list[dict] = []
    parsed = urlparse(frontend_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    request_data = {
        "https": "on" if parsed.scheme == "https" else "off",
        "http_host": f"{parsed.hostname}:{port}" if port not in (80, 443) else parsed.hostname,
        "server_port": str(port),
        "script_name": "/api/v1/sso/saml/login",
        "get_data": {},
        "post_data": {},
    }
    try:
        auth = _build_auth(config, sp_key, request_data)
        auth.login(return_to="/")
        checks.append(make_check("onelogin_build", "OneLogin SAML settings load", "pass"))
        checks.append(make_check("authn_request", "AuthnRequest builds", "pass"))
    except Exception as e:
        msg_lower = str(e).lower()
        optic.exception("saml._run_saml_check_suite build/login failed")
        if "idp_cert" in msg_lower:
            msg = "IdP certificate missing or malformed."
        elif "sp_cert" in msg_lower or ("sp" in msg_lower and "key" in msg_lower):
            msg = "SP key or certificate invalid."
        elif "idp_sso_url" in msg_lower:
            msg = "IdP SSO URL missing or invalid."
        else:
            msg = "SAML configuration error."
        checks.append(
            make_check(
                "onelogin_build",
                "OneLogin SAML settings load",
                "fail",
                msg,
                "Open the admin SAML page for full diagnostics.",
            )
        )
        return checks

    metadata_xml, sso_check, slo_check = await asyncio.gather(
        get_idp_metadata_xml(client),
        check_idp_sso_url_reachable(client, config.idp_sso_url),
        check_idp_slo_url_reachable(client, getattr(config, "idp_slo_url", None)),
    )
    metadata_url_set = bool(ds.get_sync("saml.idp_metadata_url", ""))
    if metadata_url_set and metadata_xml is None:
        checks.append(
            make_check(
                "idp_metadata_reachable",
                "IdP metadata URL reachable",
                "fail",
                "IdP metadata URL is configured but unreachable or oversized.",
                "Verify saml.idp_metadata_url is correct and reachable from this server.",
            )
        )

    for opt in (
        check_idp_cert_against_metadata(config.idp_x509_cert, metadata_xml),
        check_cert_expiry(config.idp_x509_cert, "IdP"),
        check_cert_expiry(config.sp_x509_cert, "SP"),
        check_sp_host_consistency(config.sp_acs_url, frontend_url),
        check_sp_cert_key_match(config.sp_x509_cert, sp_key),
        sso_check,
        slo_check,
        check_nameid_format(metadata_xml, "emailAddress"),
    ):
        if opt is not None:
            checks.append(opt)
    return checks


async def saml_health_probe(db: AsyncSession) -> dict | None:
    """Public SAML health probe — exercises the saml_login code path.

    Registered into the core hook so the unauthenticated /config/sso-health
    endpoint can report SAML status. Returns ``None``
    when SAML is not configured. Server-side validation cannot replay a signed
    assertion, so a green result still depends on a real user login round-trip.
    """
    config = await _get_saml_config(db)
    if not config:
        return None

    optic.info("saml_health_probe start")
    start = time.monotonic()
    checks: list[dict] = []

    try:
        sp_key = _decrypt_sp_key(config)
        checks.append(make_check("sp_key_decrypt", "SP private key decrypts", "pass"))
    except Exception:
        optic.exception("saml_health_probe SP key decrypt failed")
        checks.append(
            make_check(
                "sp_key_decrypt",
                "SP private key decrypts",
                "fail",
                "SP private key could not be decrypted.",
                "Check saml.sp_key_encryption_password.",
            )
        )
        return {"ok": False, "checks": checks, "latency_ms": round((time.monotonic() - start) * 1000)}

    frontend_url = _get_frontend_url()
    async with httpx.AsyncClient(timeout=_SAML_HEALTH_TIMEOUT, follow_redirects=False) as client:
        suite = await _run_saml_check_suite(config, sp_key, frontend_url, client)
    checks.extend(suite)

    ok = all_pass(checks)
    latency_ms = round((time.monotonic() - start) * 1000)
    optic.info("saml_health_probe done ok={} checks={} latency_ms={}", ok, len(checks), latency_ms)
    return {"ok": ok, "checks": checks, "latency_ms": latency_ms}


@router.get("/login")
async def saml_login(
    request: Request,
    next: str | None = None,
    e2e: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """SP-initiated SSO: redirect user to the IdP login page.

    ``e2e`` is an opaque diagnostics session id (no slashes, ≤64 chars). When
    present, RelayState is set to ``__e2e:<id>`` so the ACS handler runs in
    end-to-end test mode, assertion is validated but no tokens are issued.
    """
    config = await _get_saml_config(db)
    if not config:
        raise HTTPException(status_code=404, detail="SAML SSO is not configured")

    if e2e:
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", e2e) or len(e2e) > 64:
            raise HTTPException(status_code=400, detail="Invalid e2e session id")
        relay_state = f"{_E2E_RELAY_PREFIX}{e2e}"
    else:
        relay_state = _safe_redirect_path(next)

    sp_key = _decrypt_sp_key(config)
    request_data = _prepare_saml_request(request)
    auth = _build_auth(config, sp_key, request_data)
    redirect_url = auth.login(return_to=relay_state)
    return RedirectResponse(url=redirect_url, status_code=302)


# SAML ACS — RelayState sentinels.
#
# A normal login carries RelayState=/some/path (post-login redirect target).
# The E2E test flow uses RelayState=__e2e:<session_id> so the same ACS endpoint
# can run in test mode without registering a separate IdP-side ACS URL (most
# IdPs only whitelist one). On e2e mode the handler records per-step
# diagnostics under ``session_id`` and never issues tokens or cookies.
_E2E_RELAY_PREFIX = sso_diagnostics.E2E_SENTINEL_PREFIX


def _parse_relay_state(raw: str | None) -> tuple[str | None, str]:
    """Return (e2e_session_id, post_login_path) extracted from RelayState."""
    if not raw:
        return None, "/"
    if raw.startswith(_E2E_RELAY_PREFIX):
        sid = raw[len(_E2E_RELAY_PREFIX) :].split(":", 1)[0]
        if sid and re.fullmatch(r"[A-Za-z0-9_\-]+", sid) and len(sid) <= 64:
            return sid, "/"
        return None, "/"
    return None, _safe_redirect_path(raw)


def _saml_error_redirect(corr_id: str) -> RedirectResponse:
    """Redirect to the login page with a sanitized correlation id.

    ``corr_id`` is either server-generated (``new_session_id`` → URL-safe
    base64) or extracted from RelayState after the regex guard in
    ``_parse_relay_state``. Re-validate at this boundary as defence-in-depth.
    Both the path and host are fully server-controlled (``frontend_base`` is
    deployment config, never request input); only the query value derives
    from internal state and is encoded via ``urlencode``.
    """
    if not sso_diagnostics.is_safe_session_id(corr_id):
        corr_id = "invalid"
    base = _get_frontend_url().rstrip("/")
    qs = urlencode({"sso_error": corr_id})
    return RedirectResponse(url=f"{base}/login?{qs}", status_code=302)


async def _saml_finalize_diag(
    diag: list[dict],
    summary: str,
    actor_email: str | None,
    e2e_session_id: str | None,
) -> str:
    """Persist diagnostics. For real-login uses a fresh corr_id; for e2e the
    caller-supplied session_id (already known to the admin page poller).
    Returns the id the caller should use for the redirect URL."""
    sid = e2e_session_id or sso_diagnostics.new_session_id()
    try:
        await sso_diagnostics.finalize(sid, checks=diag, summary=summary, actor_email=actor_email)
    except Exception as e:
        optic.warning("saml acs: failed to persist diagnostics: {}", e)
    return sid


async def _e2e_done_response(session_id: str, ok: bool) -> Response:
    """Observal-themed result page rendered after a SAML e2e test.

    Pulls the finalized diagnostics out of Redis so the page can show the same
    per-step list the admin page polls for. The admin page is still the source
    of truth, this is just immediate confirmation in the tab the operator is
    looking at.
    """
    admin_url = f"{_get_frontend_url().rstrip('/')}/sso"
    session = await sso_diagnostics.get_session(session_id) or {}
    body = sso_diagnostics.render_result_page(
        session_id=session_id,
        provider="saml",
        ok=ok,
        checks=session.get("checks", []),
        actor_email=session.get("actor_email"),
        summary=session.get("summary"),
        admin_url=admin_url,
    )
    return Response(content=body, media_type="text/html", status_code=200)


@router.post("/acs")
async def saml_acs(request: Request, db: AsyncSession = Depends(get_db)):
    """Assertion Consumer Service: receives the SAML Response from the IdP.

    Captures per-step diagnostics regardless of outcome. On failure the user
    is redirected to ``/login?sso_error=<corr_id>`` so the frontend can render
    a ChecksList instead of a one-line HTTP error.

    If the RelayState is ``__e2e:<session_id>`` the handler runs in
    end-to-end test mode: it never issues tokens, never commits the user, but
    records the same checks under ``session_id`` for admin-side polling.
    """
    source_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
    diag: list[dict] = []

    # Pre-parse RelayState so we know up front whether we're in e2e mode.
    form = await request.form()
    request_data = _prepare_saml_request(request)
    request_data["post_data"] = dict(form)
    raw_relay = request_data["post_data"].get("RelayState")
    e2e_session_id, relay_state = _parse_relay_state(raw_relay)
    is_e2e = e2e_session_id is not None

    config = await _get_saml_config(db)
    if not config:
        diag.append(
            make_check(
                "saml_configured",
                "SAML SSO is configured on server",
                "fail",
                "No SAML config found in DB or dynamic settings.",
                "Configure SAML in the SSO tab.",
            )
        )
        corr = await _saml_finalize_diag(diag, "SAML not configured", None, e2e_session_id)
        if is_e2e:
            return await _e2e_done_response(corr, ok=False)
        return _saml_error_redirect(corr)
    diag.append(make_check("saml_configured", "SAML SSO is configured on server", "pass"))

    # Step 1: decrypt SP signing key.
    try:
        sp_key = _decrypt_sp_key(config)
        diag.append(make_check("sp_key_decrypt", "SP private key decrypts", "pass"))
    except Exception as e:
        diag.append(
            make_check(
                "sp_key_decrypt",
                "SP private key decrypts",
                "fail",
                str(e),
                "Check saml.sp_key_encryption_password or re-import the SP key.",
            )
        )
        corr = await _saml_finalize_diag(diag, "SP key decrypt failed", None, e2e_session_id)
        return (await _e2e_done_response(corr, ok=False)) if is_e2e else _saml_error_redirect(corr)

    # Step 2: process the Response (signature, audience, conditions).
    try:
        auth = _build_auth(config, sp_key, request_data)
        auth.process_response()
        errors = auth.get_errors()
    except Exception as e:
        optic.exception("saml acs: process_response crashed")
        diag.append(
            make_check(
                "process_response",
                "SAML Response parsed",
                "fail",
                str(e),
                "The assertion XML may be malformed. Capture the SAMLResponse parameter and validate offline.",
            )
        )
        corr = await _saml_finalize_diag(diag, "process_response crashed", None, e2e_session_id)
        return (await _e2e_done_response(corr, ok=False)) if is_e2e else _saml_error_redirect(corr)

    if errors:
        error_reason = auth.get_last_error_reason() or ", ".join(errors)
        msg_lower = error_reason.lower()
        hint = "Check the IdP cert in the SAML config matches the cert the IdP is signing with right now."
        if "signature" in msg_lower:
            hint = "Signature mismatch. The IdP may have rotated its cert. Refresh saml.idp_x509_cert from current IdP metadata."
        elif "audience" in msg_lower:
            hint = "Assertion 'audience' does not match SP entityID. Update the SP entityID at the IdP to match saml.sp_entity_id."
        elif "notbefore" in msg_lower or "notonorafter" in msg_lower or "expired" in msg_lower:
            hint = "Clock skew between IdP and SP. Verify NTP is healthy on this host."
        elif "destination" in msg_lower:
            hint = "Destination URL in the assertion does not match this ACS URL. Re-register the ACS URL at the IdP."
        diag.append(
            make_check("process_response", "SAML Response signature & conditions valid", "fail", error_reason, hint)
        )
        await emit_security_event(
            SecurityEvent(
                event_type=EventType.SSO_FAILURE,
                severity=Severity.WARNING,
                outcome="failure",
                source_ip=source_ip,
                user_agent=user_agent,
                detail=f"SAML assertion failed: {error_reason}",
            )
        )
        corr = await _saml_finalize_diag(diag, f"Assertion invalid: {error_reason}", None, e2e_session_id)
        return (await _e2e_done_response(corr, ok=False)) if is_e2e else _saml_error_redirect(corr)
    diag.append(make_check("process_response", "SAML Response signature & conditions valid", "pass"))

    if not auth.is_authenticated():
        diag.append(
            make_check(
                "is_authenticated",
                "Assertion marks subject as authenticated",
                "fail",
                "The IdP returned a SAML Response but it did not authenticate the user.",
                "Check the IdP's authentication policy and confirm the test user has rights to this SP.",
            )
        )
        corr = await _saml_finalize_diag(diag, "Subject not authenticated", None, e2e_session_id)
        return (await _e2e_done_response(corr, ok=False)) if is_e2e else _saml_error_redirect(corr)
    diag.append(make_check("is_authenticated", "Assertion marks subject as authenticated", "pass"))

    # Step 3: replay protection.
    response_id = auth.get_last_message_id() if hasattr(auth, "get_last_message_id") else None
    if not response_id:
        response_xml = auth.get_last_response_xml() if hasattr(auth, "get_last_response_xml") else None
        if response_xml:
            raw = response_xml.encode() if isinstance(response_xml, str) else response_xml
            response_id = hashlib.sha256(raw).hexdigest()

    if not response_id:
        diag.append(
            make_check(
                "replay_protection",
                "Assertion has a unique identifier",
                "fail",
                "Could not extract a Response ID for replay tracking.",
                "Confirm the IdP issues a Response/@ID attribute.",
            )
        )
        corr = await _saml_finalize_diag(diag, "no response id", None, e2e_session_id)
        return (await _e2e_done_response(corr, ok=False)) if is_e2e else _saml_error_redirect(corr)

    try:
        redis = get_redis()
        replay_key = f"saml_assertion:{response_id}"
        existing = await redis.get(replay_key)
        if existing:
            diag.append(
                make_check(
                    "replay_protection",
                    "Assertion not replayed",
                    "fail",
                    "This assertion was already processed.",
                    "Tab refresh / back button may have replayed the POST. Start a new login.",
                )
            )
            await emit_security_event(
                SecurityEvent(
                    event_type=EventType.SSO_FAILURE,
                    severity=Severity.WARNING,
                    outcome="failure",
                    source_ip=source_ip,
                    user_agent=user_agent,
                    detail="SAML assertion replay detected",
                )
            )
            corr = await _saml_finalize_diag(diag, "replay detected", None, e2e_session_id)
            return (await _e2e_done_response(corr, ok=False)) if is_e2e else _saml_error_redirect(corr)
        await redis.setex(replay_key, 300, "1")
        diag.append(make_check("replay_protection", "Assertion not replayed", "pass"))
    except Exception:
        optic.error("SAML replay protection: Redis unavailable, rejecting assertion")
        diag.append(
            make_check(
                "replay_protection",
                "Assertion not replayed",
                "fail",
                "Redis unavailable; cannot verify uniqueness.",
                "Check Redis health from the API container.",
            )
        )
        corr = await _saml_finalize_diag(diag, "redis down", None, e2e_session_id)
        return (await _e2e_done_response(corr, ok=False)) if is_e2e else _saml_error_redirect(corr)

    # Step 4: extract NameID + attributes.
    try:
        email, attributes = extract_name_id_and_attrs(auth)
    except Exception as e:
        optic.exception("saml acs: attribute extract failed")
        diag.append(make_check("nameid_extract", "NameID & attributes extracted", "fail", str(e)))
        corr = await _saml_finalize_diag(diag, "extract failed", None, e2e_session_id)
        return (await _e2e_done_response(corr, ok=False)) if is_e2e else _saml_error_redirect(corr)

    if not email:
        attr_names = ", ".join(sorted(attributes.keys())) if attributes else "(none)"
        diag.append(
            make_check(
                "nameid_extract",
                "Assertion contains an email",
                "fail",
                f"NameID is empty and no email attribute found. Attributes returned: {attr_names}",
                "Configure the IdP to send NameID format emailAddress, or release a 'mail'/'email' attribute.",
            )
        )
        corr = await _saml_finalize_diag(diag, "no email in assertion", None, e2e_session_id)
        return (await _e2e_done_response(corr, ok=False)) if is_e2e else _saml_error_redirect(corr)
    diag.append(make_check("nameid_extract", f"Assertion contains email ({email})", "pass"))

    name = get_display_name(attributes, fallback="SSO User")
    subject_id = auth.get_nameid() or email
    if name == "SSO User":
        diag.append(
            make_check(
                "name_attribute",
                "Display-name attribute released",
                "skip",
                "No 'name' or 'displayName' attribute returned.",
                "Map a name/displayName attribute at the IdP for nicer UI.",
            )
        )
    else:
        diag.append(make_check("name_attribute", "Display-name attribute released", "pass"))

    # ── End-to-end test mode: stop here, do not issue tokens or persist user. ──
    if is_e2e:
        try:
            # Optionally look up to see if the user would exist (read-only).
            result = await db.execute(select(User).where(User.email == email))
            existing_user = result.scalar_one_or_none()
            if existing_user is None:
                diag.append(
                    make_check(
                        "user_lookup",
                        "User exists in DB",
                        "skip",
                        "User does not yet exist; production login would JIT-create.",
                    )
                )
            else:
                diag.append(make_check("user_lookup", "User exists in DB", "pass"))
        except Exception as e:
            diag.append(make_check("user_lookup", "User exists in DB", "fail", str(e)))
        diag.append(make_check("e2e_complete", "End-to-end test finished without issuing a session", "pass"))
        corr = await _saml_finalize_diag(diag, "e2e completed", email, e2e_session_id)
        return await _e2e_done_response(corr, ok=all_pass(diag))

    # Step 5: user lookup / JIT (real-login mode).
    try:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
    except Exception as e:
        optic.exception("saml acs: user lookup failed")
        diag.append(make_check("user_lookup", "Look up user by email", "fail", str(e), "Check database connectivity."))
        corr = await _saml_finalize_diag(diag, "DB lookup failed", email, None)
        return _saml_error_redirect(corr)

    if not user:
        jit_enabled = getattr(config, "jit_provisioning", True)
        if not jit_enabled:
            diag.append(
                make_check(
                    "jit_provisioning",
                    "JIT provisioning permitted",
                    "fail",
                    "JIT provisioning disabled; user does not exist in DB.",
                    "Either provision the user manually or enable JIT in SAML config.",
                )
            )
            await emit_security_event(
                SecurityEvent(
                    event_type=EventType.SSO_FAILURE,
                    severity=Severity.WARNING,
                    outcome="failure",
                    actor_email=email,
                    source_ip=source_ip,
                    user_agent=user_agent,
                    detail="JIT provisioning disabled; user does not exist",
                )
            )
            corr = await _saml_finalize_diag(diag, "JIT disabled", email, None)
            return _saml_error_redirect(corr)

        try:
            default_role_str = getattr(config, "default_role", "user")
            try:
                role = UserRole(default_role_str)
            except ValueError:
                role = UserRole.user
            user = User(
                email=email,
                username=await generate_unique_username(email, db),
                name=name,
                role=role,
                auth_provider="saml",
                sso_subject_id=subject_id,
            )
            db.add(user)
            await db.flush()
            diag.append(make_check("jit_provisioning", "JIT-create new user", "pass"))
        except Exception as e:
            optic.exception("saml acs: JIT create failed")
            diag.append(
                make_check(
                    "jit_provisioning", "JIT-create new user", "fail", str(e), "Check the users table constraints."
                )
            )
            corr = await _saml_finalize_diag(diag, "JIT failed", email, None)
            return _saml_error_redirect(corr)
    else:
        if user.auth_provider == "deactivated":
            diag.append(
                make_check(
                    "account_active",
                    "Account is active",
                    "fail",
                    "User exists but is marked deactivated locally.",
                    "Re-activate the user in the admin Users page.",
                )
            )
            await emit_security_event(
                SecurityEvent(
                    event_type=EventType.SSO_FAILURE,
                    severity=Severity.WARNING,
                    outcome="failure",
                    actor_email=email,
                    source_ip=source_ip,
                    user_agent=user_agent,
                    detail="Deactivated user attempted SAML login",
                )
            )
            corr = await _saml_finalize_diag(diag, "deactivated", email, None)
            return _saml_error_redirect(corr)

        if user.auth_provider == "local" and not user.sso_subject_id:
            user.auth_provider = "saml"
            user.sso_subject_id = subject_id
        if user.name == "SSO User" and name != "SSO User":
            user.name = name
        diag.append(make_check("user_lookup", "Look up existing user by email", "pass"))

    # Step 6: issue tokens + commit.
    try:
        access_token, refresh_token, expires_in = await _issue_tokens(user)
        await db.commit()
        diag.append(make_check("issue_tokens", "Issue JWT access + refresh tokens", "pass"))
    except Exception as e:
        optic.exception("saml acs: token issuance failed")
        diag.append(make_check("issue_tokens", "Issue JWT access + refresh tokens", "fail", str(e)))
        corr = await _saml_finalize_diag(diag, "token issuance failed", email, None)
        return _saml_error_redirect(corr)

    code = secrets.token_urlsafe(32)
    redis = get_redis()
    await redis.setex(
        f"oauth_code:{code}",
        120,
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
            detail="SAML SSO login",
        )
    )

    token_id = str(uuid.uuid4())
    await redis.setex(
        f"saml_login:{token_id}",
        120,
        json.dumps(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_in": expires_in,
                "user_id": str(user.id),
                "role": user.role.value,
                "name": user.name,
                "email": user.email,
                "username": user.username or "",
            }
        ),
    )

    frontend_url = _get_frontend_url()
    redirect_url = f"{frontend_url}/login?saml_token={token_id}"
    if relay_state != "/":
        redirect_url += f"&next={relay_state}"
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/exchange")
async def saml_exchange(request: Request, token_id: str):
    """Exchange a one-time SAML login token for credentials.

    Uses POST to keep the token out of server logs and referrer headers.
    The token is single-use (redis GETDEL) with a 120s TTL.
    """
    redis = get_redis()

    # Basic rate-limit: 5 failed attempts per IP per minute
    source_ip = request.client.host if request.client else "unknown"
    rate_key = f"saml_exchange_rate:{source_ip}"
    attempts = await redis.incr(rate_key)
    if attempts == 1:
        await redis.expire(rate_key, 60)
    if attempts > 5:
        raise HTTPException(status_code=429, detail="Too many attempts")

    data = await redis.getdel(f"saml_login:{token_id}")
    if not data:
        raise HTTPException(status_code=400, detail="Invalid or expired SAML token")

    # Reset rate-limit on success
    await redis.delete(rate_key)

    payload = json.loads(data)
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_in": payload.get("expires_in", 3600),
        "user": {
            "id": payload["user_id"],
            "role": payload["role"],
            "name": payload["name"],
            "email": payload["email"],
            "username": payload.get("username", ""),
        },
    }


@router.get("/logout")
async def saml_logout(request: Request, db: AsyncSession = Depends(get_db)):
    """SP-initiated Single Logout: redirect user to IdP SLO endpoint."""
    config = await _get_saml_config(db)
    if not config or not getattr(config, "idp_slo_url", None):
        # No SLO configured, just redirect to login
        return RedirectResponse(url=f"{_get_frontend_url()}/login", status_code=302)

    sp_key = _decrypt_sp_key(config)
    request_data = _prepare_saml_request(request)
    auth = _build_auth(config, sp_key, request_data)
    redirect_url = auth.logout(return_to=f"{_get_frontend_url()}/login")
    return RedirectResponse(url=redirect_url, status_code=302)


@router.get("/sls")
async def saml_sls(request: Request, db: AsyncSession = Depends(get_db)):
    """SLO callback: handle LogoutResponse from IdP after logout completes."""
    config = await _get_saml_config(db)
    if not config:
        return RedirectResponse(url=f"{_get_frontend_url()}/login", status_code=302)

    sp_key = _decrypt_sp_key(config)
    request_data = _prepare_saml_request(request)
    auth = _build_auth(config, sp_key, request_data)

    url = auth.process_slo(delete_session_cb=lambda: None)
    errors = auth.get_errors()
    if errors:
        optic.warning("SAML SLO failed: %s", errors)

    login_url = f"{_get_frontend_url()}/login"
    redirect_target = url if url and url.startswith(_get_frontend_url()) else login_url
    return RedirectResponse(url=redirect_target, status_code=302)


@router.get("/metadata")
async def saml_metadata(request: Request, db: AsyncSession = Depends(get_db)):
    """Return SP metadata XML for IdP configuration."""
    config = await _get_saml_config(db)
    if not config:
        raise HTTPException(status_code=404, detail="SAML SSO is not configured")

    sp_key = _decrypt_sp_key(config)
    request_data = _prepare_saml_request(request)
    auth = _build_auth(config, sp_key, request_data)
    metadata = auth.get_settings().get_sp_metadata()
    errors = auth.get_settings().validate_metadata(metadata)
    if errors:
        raise HTTPException(
            status_code=500,
            detail=f"SP metadata validation error: {', '.join(errors)}",
        )

    return Response(content=metadata, media_type="application/xml")
