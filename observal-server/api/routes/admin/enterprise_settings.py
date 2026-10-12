# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Admin settings, diagnostics, and resource tuning routes."""

import json
import time
from datetime import UTC, datetime

import litellm
from fastapi import Depends, HTTPException
from loguru import logger as optic
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

import services.dynamic_settings as ds
from api.deps import get_db, require_role
from config import settings
from models.agent import Agent
from models.enterprise_config import RESTART_PENDING_KEY, EnterpriseConfig
from models.insight_meta_cache import InsightMetaCache
from models.insight_report import InsightReport
from models.insight_session_facets import InsightSessionFacets
from models.insight_session_meta import InsightSessionMeta
from models.user import User, UserRole
from observal_shared.migration.constants import DEFAULT_PROJECT_ID
from schemas.admin import EnterpriseConfigResponse, EnterpriseConfigUpdate, SettingRevokedResponse
from services.config_validator import validate_runtime_config_async
from services.insights import _normalize_model_id
from services.otel.destinations import SETTING_KEY as OTLP_DESTINATIONS_KEY
from services.secrets_redactor import REDACTED
from services.security_events import EventType, SecurityEvent, Severity, emit_security_event

from ._router import router
from .helpers import _validate_branding_app_name, _validate_branding_logo


async def _prepare_otlp_destinations(value: str) -> str:
    """Validate OTLP destinations and return the value to store, with ``added_at`` stamped.

    The 422 detail names the destination and field, never a header value.
    """
    import asyncio

    from services.otel.destinations import DestinationError, load_destinations, prepare_setting_value

    previous = await load_destinations()
    try:
        # The SSRF check resolves each host, so keep DNS off the event loop.
        stored, _destinations = await asyncio.to_thread(
            prepare_setting_value, value, previous=previous, now=datetime.now(UTC)
        )
    except DestinationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return stored


async def _lift_otlp_pauses(stored: str) -> None:
    """A save may carry the fix for a paused destination (a new URL or credential), so retry it now."""
    from services.otel.destinations import parse_destinations
    from services.otel.forwarder import clear_pauses

    try:
        await clear_pauses([destination.id for destination in parse_destinations(stored)])
    except Exception as exc:
        optic.warning("could not lift otlp destination pauses: {}", exc)


def _require_usage_ping_super_admin(key: str, current_user: User) -> None:
    if key.startswith("usage_ping.") and current_user.role != UserRole.super_admin:
        raise HTTPException(status_code=403, detail="Usage reporting can only be changed by a super administrator")


async def _mark_restart_pending(db: AsyncSession, key: str) -> None:
    result = await db.execute(select(EnterpriseConfig).where(EnterpriseConfig.key == RESTART_PENDING_KEY))
    marker = result.scalar_one_or_none()
    keys: set[str] = set()
    if marker:
        try:
            keys.update(json.loads(marker.value).get("keys", []))
        except (json.JSONDecodeError, AttributeError):
            pass
    keys.add(key)
    value = json.dumps({"changed_at": datetime.now(UTC).isoformat(), "keys": sorted(keys)})
    if marker:
        marker.value = value
    else:
        db.add(EnterpriseConfig(key=RESTART_PENDING_KEY, value=value))


# ── Diagnostics ─────────────────────────────────────────


@router.get("/diagnostics")
async def diagnostics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Authenticated system health - full status for ops dashboards."""
    optic.debug("diagnostics called")
    from services.crypto import get_key_manager

    diag: dict[str, object] = {
        "status": "ok",
        "checks": {},
    }

    # Database
    try:
        await db.execute(text("SELECT 1"))
        user_count = await db.scalar(select(func.count()).select_from(User))
        demo_count = await db.scalar(select(func.count()).select_from(User).where(User.is_demo.is_(True)))
        diag["checks"]["database"] = {
            "status": "ok",
            "users": user_count or 0,
            "demo_accounts": demo_count or 0,
        }
    except Exception:
        optic.exception("diagnostics database check failed")
        diag["checks"]["database"] = {"status": "error", "detail": "Database health check failed."}
        diag["status"] = "unhealthy"

    # JWT keys
    try:
        get_key_manager()
        diag["checks"]["jwt_keys"] = {
            "status": "ok",
            "algorithm": settings.JWT_SIGNING_ALGORITHM,
        }
    except RuntimeError:
        diag["checks"]["jwt_keys"] = {
            "status": "missing",
            "algorithm": settings.JWT_SIGNING_ALGORITHM,
        }

    issues = await validate_runtime_config_async(settings)
    diag["checks"]["runtime_config"] = {
        "status": "ok" if not issues else "misconfigured",
        "sso_only": await ds.get_bool("deployment.sso_only"),
        "sso_configured": bool(await ds.get("oauth.client_id")),
        "issues": issues,
    }
    if issues:
        diag["status"] = "degraded"
    return diag


@router.get("/system-warnings", response_model=list[dict])
async def system_warnings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Return actionable security warnings for the admin settings page."""
    optic.debug("system_warnings called")
    warnings: list[dict] = []

    weak_keys = {"change-me-to-a-random-string", "changeme", "secret", "dev", ""}
    if settings.SECRET_KEY in weak_keys or len(settings.SECRET_KEY) < 32:
        warnings.append(
            {
                "level": "critical",
                "code": "weak_secret_key",
                "message": "SECRET_KEY is insecure. Set a random string of at least 32 characters.",
            }
        )

    demo_count = await db.scalar(select(func.count()).select_from(User).where(User.is_demo.is_(True)))
    if demo_count:
        warnings.append(
            {
                "level": "warning",
                "code": "demo_accounts_active",
                "message": f"{demo_count} demo account(s) are still active. Remove them or change their passwords before going to production.",
            }
        )

    return warnings


@router.get("/settings/schema")
async def settings_schema(
    _current_user: User = Depends(require_role(UserRole.admin)),
):
    optic.debug("admin settings schema")
    return ds.settings_schema()


@router.get("/settings", response_model=list[EnterpriseConfigResponse])
async def list_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    optic.debug("admin settings list")
    result = await db.execute(select(EnterpriseConfig).order_by(EnterpriseConfig.key))
    configs = []
    seen = set()
    for c in result.scalars().all():
        if c.key == RESTART_PENDING_KEY:
            continue
        seen.add(c.key)
        sensitive = c.key in ds.SENSITIVE_KEYS
        externally_managed = ds.is_externally_managed(c.key)
        has_value = externally_managed or bool(c.value)
        effective_value = ds.get_sync(c.key) if externally_managed else c.value
        # Never return plaintext or ciphertext for sensitive keys.
        display_value = (REDACTED if has_value else "") if sensitive else effective_value
        configs.append(
            EnterpriseConfigResponse(
                key=c.key,
                value=display_value,
                is_sensitive=sensitive,
                is_set=has_value,
                is_externally_managed=externally_managed,
            )
        )
    for key in sorted(ds.external_setting_keys() - seen):
        if key not in ds.DEFAULTS:
            continue
        sensitive = key in ds.SENSITIVE_KEYS
        configs.append(
            EnterpriseConfigResponse(
                key=key,
                value=REDACTED if sensitive else ds.get_sync(key),
                is_sensitive=sensitive,
                is_set=True,
                is_externally_managed=True,
            )
        )
    return configs


@router.get("/settings/{key}", response_model=EnterpriseConfigResponse)
async def get_setting(
    key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    optic.debug("admin setting get")
    if key in ds.FILE_ONLY_KEYS:
        raise HTTPException(status_code=404, detail="Setting not found")
    externally_managed = ds.is_externally_managed(key)
    result = await db.execute(select(EnterpriseConfig).where(EnterpriseConfig.key == key))
    cfg = result.scalar_one_or_none()
    if not cfg and not externally_managed:
        raise HTTPException(status_code=404, detail="Setting not found")
    sensitive = key in ds.SENSITIVE_KEYS
    has_value = externally_managed or bool(cfg and cfg.value)
    value = ds.get_sync(key) if externally_managed else cfg.value
    display_value = (REDACTED if has_value else "") if sensitive else value
    return EnterpriseConfigResponse(
        key=key,
        value=display_value,
        is_sensitive=sensitive,
        is_set=has_value,
        is_externally_managed=externally_managed,
    )


@router.put("/settings/{key}", response_model=EnterpriseConfigResponse)
async def upsert_setting(
    key: str,
    req: EnterpriseConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    optic.trace("key={}", key)
    _require_usage_ping_super_admin(key, current_user)
    if key in ds.FILE_ONLY_KEYS:
        raise HTTPException(status_code=409, detail="Setting can only be managed through dedicated files")
    if ds.is_externally_managed(key):
        raise HTTPException(status_code=409, detail="Setting is externally managed by a secret file")
    # Wrapping whitespace from copy-paste silently breaks downstream consumers
    # (discovery URLs, client secrets), so values are normalized on write.
    # Interior content (multi-line PEM certs) is preserved.
    value = req.value.strip() if isinstance(req.value, str) else req.value

    if key in ("branding.logo", "branding.wordmark"):
        _validate_branding_logo(value)
    elif key == "branding.app_name":
        _validate_branding_app_name(value)
    elif key == "usage_ping.company_name" and len(value) > 160:
        raise HTTPException(status_code=422, detail="Company name cannot exceed 160 characters")
    elif key == "usage_ping.frequency":
        value = value.lower()
        if value not in {"every_6_hours", "daily", "weekly"}:
            raise HTTPException(
                status_code=422, detail="Usage reporting frequency must be every_6_hours, daily, or weekly"
            )
    elif key == "usage_ping.enabled":
        value = value.lower()
        if value not in {"true", "false"}:
            raise HTTPException(status_code=422, detail="Usage reporting must be true or false")
        if value == "true":
            company_name = (await ds.get("usage_ping.company_name")).strip()
            public_url = (await ds.get("deployment.public_url")).strip()
            if not company_name or not public_url:
                raise HTTPException(
                    status_code=422,
                    detail="Company name and deployment public URL are required before enabling usage reporting",
                )
    elif key == OTLP_DESTINATIONS_KEY:
        value = await _prepare_otlp_destinations(value)

    sensitive = key in ds.SENSITIVE_KEYS
    store_value = ds.encrypt_value(value) if sensitive else value

    result = await db.execute(select(EnterpriseConfig).where(EnterpriseConfig.key == key))
    cfg = result.scalar_one_or_none()
    changed = cfg is None or cfg.value != store_value
    if cfg:
        cfg.value = store_value
    else:
        cfg = EnterpriseConfig(key=key, value=store_value)
        db.add(cfg)
    if changed and key in ds.RESTART_REQUIRED_KEYS:
        await _mark_restart_pending(db, key)
    await db.commit()
    await db.refresh(cfg)
    await ds.invalidate(key)
    await ds.refresh_sync_cache()

    if key == OTLP_DESTINATIONS_KEY:
        await _lift_otlp_pauses(value)

    # Auto-clean deprecated AWS/legacy settings when new API key is configured
    if key == "insights.api_key" and value:
        deprecated_keys = [
            "insights.aws_region",
            "insights.aws_access_key_id",
            "insights.aws_secret_access_key",
            "insights.aws_session_token",
            "insights.model_url",
            "insights.model_api_key",
        ]
        await db.execute(delete(EnterpriseConfig).where(EnterpriseConfig.key.in_(deprecated_keys)))
        await db.commit()
        for dk in deprecated_keys:
            await ds.invalidate(dk)
        await ds.refresh_sync_cache()

    await emit_security_event(
        SecurityEvent(
            event_type=EventType.SETTING_CHANGED,
            severity=Severity.WARNING,
            outcome="success",
            actor_id=str(current_user.id),
            actor_email=current_user.email,
            actor_role=current_user.role.value,
            target_id=key,
            target_type="setting",
        )
    )
    # Sensitive values are only visible in this single response (the moment of entry).
    # All subsequent GET requests will return the redacted constant.
    return EnterpriseConfigResponse(
        key=cfg.key,
        value=REDACTED if sensitive else cfg.value,
        is_sensitive=sensitive,
        is_set=True,
        is_externally_managed=False,
    )


@router.delete("/settings/{key}")
async def delete_setting(
    key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    optic.trace("key={}", key)
    _require_usage_ping_super_admin(key, current_user)
    if ds.is_externally_managed(key):
        raise HTTPException(status_code=409, detail="Setting is externally managed by a secret file")
    result = await db.execute(select(EnterpriseConfig).where(EnterpriseConfig.key == key))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Setting not found")
    await db.delete(cfg)
    if key in ds.RESTART_REQUIRED_KEYS:
        await _mark_restart_pending(db, key)
    await db.commit()
    await ds.invalidate(key)
    await ds.refresh_sync_cache()
    return {"deleted": key}


@router.post("/settings/{key}/revoke", response_model=SettingRevokedResponse)
async def revoke_setting(
    key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Revoke a sensitive setting (API key, secret). Permanently deletes the value.

    Unlike DELETE which removes any setting, this endpoint is restricted to
    sensitive keys only and emits a dedicated security audit event.
    """
    optic.trace("key={}", key)
    if key not in ds.SENSITIVE_KEYS:
        raise HTTPException(status_code=400, detail="Only sensitive keys can be revoked")
    if ds.is_externally_managed(key):
        raise HTTPException(status_code=409, detail="Setting is externally managed by a secret file")
    result = await db.execute(select(EnterpriseConfig).where(EnterpriseConfig.key == key))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Setting not found or already revoked")
    await db.delete(cfg)
    if key in ds.RESTART_REQUIRED_KEYS:
        await _mark_restart_pending(db, key)
    await db.commit()
    await ds.invalidate(key)
    await ds.refresh_sync_cache()
    await emit_security_event(
        SecurityEvent(
            event_type=EventType.SETTING_CHANGED,
            severity=Severity.CRITICAL,
            outcome="success",
            actor_id=str(current_user.id),
            actor_email=current_user.email,
            actor_role=current_user.role.value,
            target_id=key,
            target_type="sensitive_setting",
            detail=f"Sensitive setting revoked: {key}",
        )
    )
    return {"revoked": key, "message": "Secret has been permanently deleted"}


@router.post("/resources/apply")
async def apply_resources(
    current_user: User = Depends(require_role(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
):
    """Re-apply resource tuning settings to ClickHouse without restart."""
    optic.trace("user_id={}", current_user.id)
    from services.clickhouse import RESOURCE_SETTINGS_MAP, apply_resource_settings

    result = await db.execute(select(EnterpriseConfig).where(EnterpriseConfig.key.like("resource.%")))
    current = {cfg.key: cfg.value for cfg in result.scalars().all()}

    await apply_resource_settings(overrides=current)

    await emit_security_event(
        SecurityEvent(
            event_type=EventType.SETTING_CHANGED,
            severity=Severity.WARNING,
            outcome="success",
            actor_id=str(current_user.id),
            actor_email=current_user.email,
            actor_role=current_user.role.value,
            target_id="resource_settings",
            target_type="setting",
            detail=f"Applied resource settings: {list(current.keys())}",
        )
    )

    applied_keys = [k for k in current if k in RESOURCE_SETTINGS_MAP]
    return {
        "applied": {k: current[k] for k in applied_keys},
        "message": "ClickHouse resource settings applied",
    }


# ── Danger Zone Purge ───────────────────────────────


class _PurgeTracesInsightsResponse(BaseModel):
    project_id: str
    clickhouse_tables: list[str]
    deleted_reports: int | None = None
    deleted_facets: int | None = None
    deleted_session_meta: int | None = None
    deleted_meta_cache: int | None = None


@router.post("/settings/danger/purge-traces-insights", response_model=_PurgeTracesInsightsResponse)
async def purge_traces_and_insights(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Danger-zone purge: delete all deployment telemetry and insight reports."""
    optic.warning("danger purge traces+insights requested by user={}", current_user.id)
    project_id = DEFAULT_PROJECT_ID

    from services.clickhouse.client import _query as ch_query

    clickhouse_tables = ["session_events", "session_stats_agg"]
    for table in clickhouse_tables:
        try:
            await ch_query(
                f"ALTER TABLE {table} DELETE WHERE project_id = {{project_id:String}}",
                {"param_project_id": project_id},
            )
        except Exception as e:
            optic.warning("danger purge failed for ClickHouse table {}: {}", table, e)

    agent_ids_stmt = select(Agent.id)
    report_result = await db.execute(delete(InsightReport).where(InsightReport.agent_id.in_(agent_ids_stmt)))
    facets_result = await db.execute(
        delete(InsightSessionFacets).where(InsightSessionFacets.agent_id.in_(agent_ids_stmt))
    )
    meta_result = await db.execute(delete(InsightSessionMeta).where(InsightSessionMeta.agent_id.in_(agent_ids_stmt)))
    cache_result = await db.execute(delete(InsightMetaCache).where(InsightMetaCache.agent_id.in_(agent_ids_stmt)))
    await db.commit()

    await emit_security_event(
        SecurityEvent(
            event_type=EventType.SETTING_CHANGED,
            severity=Severity.CRITICAL,
            outcome="success",
            actor_id=str(current_user.id),
            actor_email=current_user.email,
            actor_role=current_user.role.value,
            target_id="danger.purge_traces_insights",
            target_type="danger_zone",
            detail="Purged deployment telemetry traces/session data and insight reports",
        )
    )

    return _PurgeTracesInsightsResponse(
        project_id=project_id,
        clickhouse_tables=clickhouse_tables,
        deleted_reports=report_result.rowcount,
        deleted_facets=facets_result.rowcount,
        deleted_session_meta=meta_result.rowcount,
        deleted_meta_cache=cache_result.rowcount,
    )


# ── Insights Test Connection ───────────────────────────────


class _TestConnectionRequest(BaseModel):
    model: str | None = None


class _TestConnectionResponse(BaseModel):
    success: bool
    model: str | None = None
    latency_ms: int | None = None
    error: str | None = None
    hint: str | None = None


def _get_connection_error_hint(error_str: str, model: str) -> str:
    model_lower = model.lower()
    if "model identifier is invalid" in error_str or "model_not_found" in error_str:
        if "bedrock" in model_lower:
            return (
                "Model ID is not available in your region. "
                "Ensure the Base URL region matches where the model is enabled. "
                "Cross-region models use prefixes like us./eu./apac. (e.g., bedrock/us.anthropic.claude-sonnet-4-6-v1)."
            )
        return "Model ID not recognized. Verify the format: provider/model-name"
    if "auth" in error_str or "401" in error_str or "invalid api key" in error_str or "forbidden" in error_str:
        if "anthropic" in model_lower:
            return "Invalid API key. Get one at console.anthropic.com"
        if "bedrock" in model_lower:
            return "Bearer token may be expired. Regenerate in AWS Console."
        if "openai" in model_lower:
            return "Invalid API key. Get one at platform.openai.com/api-keys"
        if "gemini" in model_lower:
            return "Invalid API key. Get one at aistudio.google.com/apikey"
        return "Authentication failed. Verify your API key."
    if "timeout" in error_str or "timed out" in error_str or "connect" in error_str:
        return "Could not reach endpoint. Check your Base URL and network connectivity."
    if "not found" in error_str or "does not exist" in error_str or "unknown provider" in error_str:
        return "Model ID not recognized. Verify the format: provider/model-name"
    if "rate" in error_str or "429" in error_str:
        return "Rate limited by provider. The key is valid, try again in a moment."
    if "access" in error_str and "bedrock" in model_lower:
        return "Model access not enabled. Enable the model in your AWS Bedrock console for this region."
    return "Connection test failed. Check your settings and try again."


@router.post("/insights/test-connection", response_model=_TestConnectionResponse)
async def test_insights_connection(
    req: _TestConnectionRequest,
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Test LLM connectivity with a minimal prompt."""
    api_key = await ds.get("insights.api_key")
    api_base = await ds.get("insights.api_base")
    api_version = await ds.get("insights.api_version")
    aws_region = await ds.get("insights.aws_region")
    model = req.model or await ds.get("insights.model_sections")

    if not model:
        return _TestConnectionResponse(
            success=False,
            error="No model configured",
            hint="Set the Sections Model first, or provide a model in the request.",
        )

    model = _normalize_model_id(model)

    kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": "Say hello in exactly one word."}],
        # Reasoning models (Gemini 3, o-series, Claude thinking) spend their
        # budget thinking before emitting text. A budget of 10 leaves nothing
        # for the answer, so the call "succeeds" with zero choices and the
        # test reports a bogus IndexError. Give it room to actually reply.
        "max_tokens": 2048,
        "timeout": 15,
        "drop_params": True,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base
    if api_version and model.startswith("azure/"):
        kwargs["api_version"] = api_version
    if aws_region and "bedrock" in model:
        kwargs["aws_region_name"] = aws_region

    start = time.time()
    try:
        await litellm.acompletion(**kwargs)
        latency_ms = int((time.time() - start) * 1000)
        return _TestConnectionResponse(success=True, model=model, latency_ms=latency_ms)
    except Exception as e:
        optic.warning("insights connection test failed, model={}, error={}", model, str(e))
        error_str = str(e).lower()
        hint = _get_connection_error_hint(error_str, model)
        return _TestConnectionResponse(success=False, model=model, error=str(e)[:200], hint=hint)
