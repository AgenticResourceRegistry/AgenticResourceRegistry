# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import asyncio
import base64
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from loguru import logger as optic
from sqlalchemy import select

import services.dynamic_settings as ds_mod
from api.deps import get_db
from api.ratelimit import limiter
from models.enterprise_config import EnterpriseConfig
from observal_shared.harness_registry import HARNESS_REGISTRY, get_harness_runtime_facts
from schemas.sso_health import all_pass
from services.oidc_health import run_oidc_checks
from services.saml_health import run_saml_health_probe
from version import get_server_version

router = APIRouter(prefix="/api/v1/config", tags=["config"])

_SSO_HEALTH_WALL_TIMEOUT = 15.0  # outer budget: every probe + saml metadata + DB combined


@router.get("/version")
async def get_version():
    """Server version and compatibility info. No auth required.

    The server_version is the canonical target: CLI and frontend must match it.
    """
    optic.debug("config.get_version called")
    import services.dynamic_settings as ds

    max_cli = await ds.get("misc.max_cli_version")
    api_version = await ds.get("misc.api_version")
    frontend_version = await ds.get("misc.frontend_version")

    server_ver = get_server_version()
    return {
        "server_version": server_ver,
        "max_cli_version": max_cli or None,
        "api_version": api_version or None,
        "frontend_version": frontend_version or server_ver,
        # Deprecated: kept for backward compat with CLIs < 1.0.0. Will be removed in 1.2.0.
        "recommended_cli_version": server_ver,
    }


async def derive_endpoints(request: Request | None = None) -> dict[str, str]:
    """Derive all endpoint URLs from settings, falling back to request context."""
    optic.debug("derive_endpoints called")
    import services.dynamic_settings as ds

    public_url_setting = await ds.get("deployment.public_url")
    public_url = public_url_setting.rstrip("/") if public_url_setting else ""
    if not public_url and request:
        public_url = str(request.base_url).rstrip("/")
    if not public_url:
        public_url = "http://localhost:8000"

    parsed = urlparse(public_url)
    hostname = parsed.hostname or "localhost"
    scheme = parsed.scheme or ("http" if hostname in ("localhost", "127.0.0.1") else "https")

    frontend_setting = await ds.get("deployment.frontend_url")
    web = frontend_setting.rstrip("/") if frontend_setting else f"{scheme}://{hostname}:3000"

    return {
        "api": public_url,
        "web": web,
    }


@router.get("/endpoints")
async def get_endpoints(request: Request):
    """Endpoint discovery: returns all service URLs. No auth required."""
    optic.debug("config.derive_endpoints called")
    return await derive_endpoints(request)


@router.get("/favicon")
async def get_favicon(db=Depends(get_db)):
    """Return the branding logo as a binary file, or redirect to default icon."""
    try:
        result = await db.execute(select(EnterpriseConfig).where(EnterpriseConfig.key == "branding.logo"))
        cfg = result.scalar_one_or_none()
        if cfg and cfg.value and cfg.value.startswith("data:"):
            # Parse data URI: data:image/png;base64,....
            header, encoded = cfg.value.split(",", 1)
            mime_type = header.split(";")[0].replace("data:", "")
            decoded = base64.b64decode(encoded)
            return Response(content=decoded, media_type=mime_type, headers={"Cache-Control": "public, max-age=60"})
        elif cfg and cfg.value and cfg.value.startswith("http"):
            return RedirectResponse(url=cfg.value)
    except Exception:
        pass
    return RedirectResponse(url="/icon.png")


@router.get("/public")
async def get_public_config(response: Response, db=Depends(get_db)):
    """Public configuration for frontend. No auth required."""
    optic.debug("config.get_public_config called")
    import services.dynamic_settings as ds

    # SAML: check DB-backed dynamic settings, then fall back to SamlConfig model
    saml_idp_entity = await ds.get("saml.idp_entity_id")
    saml_idp_sso = await ds.get("saml.idp_sso_url")
    saml_enabled = bool(saml_idp_entity and saml_idp_sso)

    if not saml_enabled:
        try:
            from models.saml_config import SamlConfig

            result = await db.execute(select(SamlConfig).where(SamlConfig.active.is_(True)).limit(1))
            saml_enabled = result.scalar_one_or_none() is not None
        except Exception:
            pass

    branding_logo = None
    branding_app_name = None
    branding_wordmark = None
    try:
        result = await db.execute(
            select(EnterpriseConfig).where(
                EnterpriseConfig.key.in_(["branding.logo", "branding.app_name", "branding.wordmark"])
            )
        )
        for cfg in result.scalars().all():
            if cfg.key == "branding.logo" and cfg.value:
                branding_logo = cfg.value
            elif cfg.key == "branding.app_name" and cfg.value:
                branding_app_name = cfg.value
            elif cfg.key == "branding.wordmark" and cfg.value:
                branding_wordmark = cfg.value
    except Exception:
        pass

    enabled_features = ["all"]
    exec_dashboard_available = True

    sso_only = await ds.get_bool("deployment.sso_only")
    public_registry_enabled = await ds.get_bool("deployment.public_registry_enabled")
    self_registration_enabled = await ds.get_bool("auth.self_registration_enabled")
    skill_folder_delivery_enabled = await ds.get_bool("registry.skill_folder_delivery_enabled", False)
    response.headers["Cache-Control"] = "no-store"

    from api.routes.auth import is_github_oauth_configured, is_google_oauth_configured, is_oidc_configured

    return {
        # Legacy compatibility: existing clients/deployments may still read these.
        "licensed": True,
        "licensed_features": enabled_features,
        "sso_enabled": is_oidc_configured(),
        "google_sso_enabled": is_google_oauth_configured(),
        "github_sso_enabled": is_github_oauth_configured(),
        "sso_only": sso_only,
        "public_registry_enabled": public_registry_enabled,
        "self_registration_enabled": self_registration_enabled,
        "skill_folder_delivery_enabled": skill_folder_delivery_enabled,
        "saml_enabled": saml_enabled,
        "exec_dashboard_available": exec_dashboard_available,
        "enabled_features": enabled_features,
        "branding_logo": branding_logo,
        "branding_app_name": branding_app_name,
        "branding_wordmark": branding_wordmark,
    }


async def _oidc_health_probe() -> dict | None:
    client_id = ds_mod.get_sync("oauth.client_id")
    client_secret = ds_mod.get_sync("oauth.client_secret")
    metadata_url = ds_mod.get_sync("oauth.server_metadata_url")
    if not (client_id and metadata_url):
        return None
    start = time.monotonic()
    redirect_uri = (
        ds_mod.get_sync("deployment.frontend_url", "http://localhost:3000").rstrip("/") + "/api/v1/auth/oauth/callback"
    )
    try:
        checks, _metadata = await run_oidc_checks(
            metadata_url,
            client_id,
            client_secret,
            redirect_uri,
        )
    except Exception:
        optic.exception("sso_health.oidc_probe unexpected failure")
        return {"ok": False, "checks": [], "error": "OIDC health probe failed"}
    latency_ms = round((time.monotonic() - start) * 1000)
    return {"ok": all_pass(checks), "checks": checks, "latency_ms": latency_ms}


@router.get("/sso-health")
@limiter.limit(ds_mod.get_sync("security.rate_limit_sso_health", "10/minute"))
async def sso_health(request: Request, db=Depends(get_db)):
    """Public (unauthenticated) SSO health check for the login page.

    Runs OIDC and SAML probes concurrently with an outer wall-clock budget so
    a slow IdP cannot tie up the worker indefinitely. 100% validation is not
    possible: assertion replay, per-user policy, and IdP-only state are not
    visible server-side, so a green result still depends on a real user login
    round-trip.
    """
    optic.debug("sso_health start")
    timed_out = False
    try:
        oidc_result, saml_result = await asyncio.wait_for(
            asyncio.gather(
                _oidc_health_probe(),
                run_saml_health_probe(db),
                return_exceptions=False,
            ),
            timeout=_SSO_HEALTH_WALL_TIMEOUT,
        )
    except TimeoutError:
        optic.warning("sso_health wall-clock timeout exceeded ({}s)", _SSO_HEALTH_WALL_TIMEOUT)
        oidc_result = {"ok": False, "checks": [], "error": "OIDC probe exceeded wall-clock budget"}
        saml_result = {"ok": False, "checks": [], "error": "SAML probe exceeded wall-clock budget"}
        timed_out = True

    optic.info(
        "sso_health done oidc_ok={} saml_ok={} timed_out={}",
        (oidc_result or {}).get("ok"),
        (saml_result or {}).get("ok"),
        timed_out,
    )
    return JSONResponse(
        content={"oidc": oidc_result, "saml": saml_result},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/harnesses")
async def get_harnesses():
    """Return the canonical harness list from HARNESS_REGISTRY, filtered by allowlist."""
    from services.dynamic_settings import get

    optic.debug("config.get_harnesses called")

    allowlist_raw = await get("misc.harness_allowlist")
    requested_allowlist = [s.strip() for s in allowlist_raw.split(",") if s.strip()] if allowlist_raw else []
    valid_allowlist = [name for name in requested_allowlist if name in HARNESS_REGISTRY]
    allowlist = set(valid_allowlist) if valid_allowlist else None

    default_harness_raw = await get("misc.default_harness")

    harnesses = []
    for name, spec in HARNESS_REGISTRY.items():
        if allowlist and name not in allowlist:
            continue
        harnesses.append(
            {
                "name": name,
                "display_name": spec["display_name"],
                "capabilities": sorted(spec["capabilities"]),
                "supported_models": spec.get("supported_models", []),
                "runtime": get_harness_runtime_facts(name),
            }
        )
    from fastapi.responses import JSONResponse

    available_names = {harness["name"] for harness in harnesses}
    default_harness = default_harness_raw if default_harness_raw in available_names else None

    return JSONResponse(
        content={"harnesses": harnesses, "default_harness": default_harness},
        headers={"Cache-Control": "no-store"},
    )
