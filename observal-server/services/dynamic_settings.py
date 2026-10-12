# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Dynamic settings service: DB-backed runtime configuration with Redis cache.

All non-boot-time settings are stored in the `enterprise_config` table and
accessed through this module. No env-var fallback; if a setting isn't in the
DB, the hardcoded default is used. Legacy SSO env vars are imported once at
startup only when the matching DB setting is absent.

Usage:
    from services.dynamic_settings import get, get_int, get_bool

    model_name = get("insights.model_sections")        # returns "" if not set
    batch_days = get_int("insights.batch_period_days")  # returns 14 if not set
    sso_only = get_bool("deployment.sso_only")       # returns False if not set
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from loguru import logger as optic

from observal_shared.secrets import resolve_secret

# ─── Encryption for sensitive values ───────────────────────────────────────
# Uses Fernet symmetric encryption keyed from SECRET_KEY.
# Values are stored as "enc:" + base64(ciphertext) in the DB.
# On key rotation: set OLD_SECRET_KEY=<previous key> in env, restart.
# The system re-encrypts all sensitive values with the new key at startup,
# then you can remove OLD_SECRET_KEY.

_ENC_PREFIX = "enc:"


def _derive_fernet_key(secret: str):
    """Derive a Fernet key from a secret string.

    Uses SHA-256 as a KDF: no salt or iteration count since this is keyed from a
    server-unique SECRET_KEY for at-rest encryption only (not password hashing).
    Identical SECRET_KEY always yields the same Fernet key, which is intentional.
    """
    key_bytes = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key_bytes)


def _get_fernet():
    """Derive a Fernet key from SECRET_KEY."""
    from cryptography.fernet import Fernet

    from config import settings

    return Fernet(_derive_fernet_key(settings.SECRET_KEY))


def _old_secret_key() -> str | None:
    from config import settings

    return resolve_secret("OLD_SECRET_KEY") or settings.OLD_SECRET_KEY


def _get_old_fernet():
    """Derive a Fernet key from OLD_SECRET_KEY (for key rotation)."""
    from cryptography.fernet import Fernet

    old_key = _old_secret_key()
    if not old_key:
        return None
    return Fernet(_derive_fernet_key(old_key))


def encrypt_value(value: str) -> str:
    """Encrypt a value for storage. Returns 'enc:' prefixed ciphertext."""
    if not value:
        return value
    f = _get_fernet()
    encrypted = f.encrypt(value.encode())
    return _ENC_PREFIX + encrypted.decode()


def decrypt_value(stored: str) -> str:
    """Decrypt a stored value. Tries current key first, then OLD_SECRET_KEY."""
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored
    ciphertext = stored[len(_ENC_PREFIX) :].encode()
    # Try current key
    try:
        f = _get_fernet()
        return f.decrypt(ciphertext).decode()
    except Exception:
        pass
    # Try old key (rotation scenario)
    try:
        old_f = _get_old_fernet()
        if old_f:
            return old_f.decrypt(ciphertext).decode()
    except Exception:
        pass
    optic.error("dynamic settings decrypt failed with current and old keys")
    return ""


async def reencrypt_on_key_rotation() -> int:
    """Re-encrypt all sensitive values with the current SECRET_KEY.

    Call at startup. If OLD_SECRET_KEY is set and any values can only be
    decrypted with the old key, they are re-encrypted with the new key.
    Once complete, remove OLD_SECRET_KEY from your env.

    Returns the number of values re-encrypted.
    """
    if not _old_secret_key():
        return 0

    try:
        from sqlalchemy import select

        from database import async_session
        from models.enterprise_config import EnterpriseConfig
        from models.team_invite import TeamInvite

        def rotated_value(stored: str | None) -> str | None:
            if not stored or not stored.startswith(_ENC_PREFIX):
                return None
            ciphertext = stored[len(_ENC_PREFIX) :].encode()
            try:
                _get_fernet().decrypt(ciphertext)
                return None
            except Exception:
                plaintext = decrypt_value(stored)
                return encrypt_value(plaintext) if plaintext else None

        count = 0
        async with async_session() as session:
            result = await session.execute(
                select(EnterpriseConfig).where(EnterpriseConfig.key.in_(list(SENSITIVE_KEYS)))
            )
            for cfg in result.scalars().all():
                rotated = rotated_value(cfg.value)
                if rotated:
                    cfg.value = rotated
                    count += 1
            invites = await session.scalars(select(TeamInvite).where(TeamInvite.token_encrypted.is_not(None)))
            for invite in invites:
                rotated = rotated_value(invite.token_encrypted)
                if rotated:
                    invite.token_encrypted = rotated
                    count += 1
            if count > 0:
                await session.commit()
                optic.info("encrypted values re-encrypted count={}", count)
        return count
    except Exception:
        optic.exception("dynamic settings re-encryption failed")
        return 0


SSO_ENV_IMPORTS: dict[str, str] = {
    "INSIGHTS_API_KEY": "insights.api_key",
    "OAUTH_CLIENT_ID": "oauth.client_id",
    "OAUTH_CLIENT_SECRET": "oauth.client_secret",
    "OAUTH_SERVER_METADATA_URL": "oauth.server_metadata_url",
    "GOOGLE_OAUTH_CLIENT_ID": "google.client_id",
    "GOOGLE_OAUTH_CLIENT_SECRET": "google.client_secret",
    "GOOGLE_OAUTH_ALLOWED_DOMAINS": "google.allowed_domains",
    "GITHUB_OAUTH_CLIENT_ID": "github.client_id",
    "GITHUB_OAUTH_CLIENT_SECRET": "github.client_secret",
    "GITHUB_OAUTH_ALLOWED_ORGS": "github.allowed_orgs",
    "SSO_ONLY": "deployment.sso_only",
    "SAML_IDP_ENTITY_ID": "saml.idp_entity_id",
    "SAML_IDP_SSO_URL": "saml.idp_sso_url",
    "SAML_IDP_SLO_URL": "saml.idp_slo_url",
    "SAML_IDP_X509_CERT": "saml.idp_x509_cert",
    "SAML_IDP_METADATA_URL": "saml.idp_metadata_url",
    "SAML_SP_ENTITY_ID": "saml.sp_entity_id",
    "SAML_SP_ACS_URL": "saml.sp_acs_url",
    "SAML_JIT_PROVISIONING": "saml.jit_provisioning",
    "SAML_DEFAULT_ROLE": "saml.default_role",
    "SAML_SP_KEY_ENCRYPTION_PASSWORD": "saml.sp_key_encryption_password",
}

SAML_MATERIAL_IMPORTS: dict[str, str] = {
    "SAML_SP_PRIVATE_KEY": "saml.sp_private_key",
    "SAML_SP_X509_CERT": "saml.sp_x509_cert",
}
FILE_ONLY_KEYS = frozenset(SAML_MATERIAL_IMPORTS.values())

_external_settings: dict[str, str] = {}


def _environment_values() -> dict[str, str]:
    import os

    from dotenv import dotenv_values

    env_file = {key: value for key, value in dotenv_values(".env").items() if value is not None}
    return {**env_file, **os.environ}


def load_external_settings() -> None:
    """Load file-backed dynamic settings into memory without DB or Redis writes."""
    values = _environment_values()
    loaded: dict[str, str] = {}
    for env_key, setting_key in SSO_ENV_IMPORTS.items():
        if f"{env_key}_FILE" not in values:
            continue
        value = resolve_secret(env_key, values)
        if value is not None:
            loaded[setting_key] = value
    for env_key, setting_key in SAML_MATERIAL_IMPORTS.items():
        if f"{env_key}_FILE" not in values:
            continue
        value = resolve_secret(env_key, values)
        if value is not None:
            loaded[setting_key] = value

    sp_values = {"saml.sp_private_key", "saml.sp_x509_cert"} & loaded.keys()
    if sp_values and len(sp_values) != 2:
        raise ValueError(
            "File-backed SAML SP material overrides database material; "
            "SAML_SP_PRIVATE_KEY_FILE and SAML_SP_X509_CERT_FILE must be configured together"
        )

    _external_settings.clear()
    _external_settings.update(loaded)


def is_externally_managed(key: str) -> bool:
    return key in _external_settings


def external_setting_keys() -> set[str]:
    return set(_external_settings)


def has_external_saml_material() -> bool:
    return "saml.sp_private_key" in _external_settings


async def import_sso_env_once() -> int:
    """Import legacy SSO env vars into dynamic settings when DB has no value.

    A row whose value is empty counts as "no value": the env var overwrites it.
    Only a non-empty DB value (set via the admin UI/API) wins over the env var.
    """
    from sqlalchemy import select

    from database import async_session
    from models.enterprise_config import EnterpriseConfig

    async with async_session() as session:
        result = await session.execute(
            select(EnterpriseConfig).where(EnterpriseConfig.key.in_(tuple(SSO_ENV_IMPORTS.values())))
        )
        rows = {cfg.key: cfg for cfg in result.scalars().all()}

        imported = 0
        values = _environment_values()
        for env_key, setting_key in SSO_ENV_IMPORTS.items():
            if is_externally_managed(setting_key):
                continue
            env_value = (values.get(env_key) or "").strip()
            if not env_value:
                continue
            existing = rows.get(setting_key)
            if existing is not None and existing.value:
                continue
            store_value = encrypt_value(env_value) if setting_key in SENSITIVE_KEYS else env_value
            if existing is not None:
                existing.value = store_value
            else:
                session.add(EnterpriseConfig(key=setting_key, value=store_value))
            imported += 1
        if imported:
            await session.commit()
            await invalidate_all()
            await refresh_sync_cache()
            optic.info("dynamic_settings_sso_env_imported count={}", imported)
        return imported


# Redis key namespace for settings cache
_CACHE_PREFIX = "settings:"
_CACHE_TTL = 30  # seconds, short TTL for consistency, Redis is fast

# ─── Default values (hardcoded, no env fallback) ─────────────────────────────
# These match the old env-var defaults from config.py. When a key is not in the
# DB, these are returned. Once configured via the settings page, DB values win.

DEFAULTS: dict[str, str] = {
    # Insights: LLM provider credentials (via LiteLLM)
    "insights.api_key": "",
    "insights.api_base": "",
    "insights.api_version": "",
    # Insights: per-stage models (LiteLLM format: provider/model-name)
    "insights.model_sections": "",
    "insights.model_synthesis": "",
    "insights.model_facets": "",
    # Insights: batch processing
    "insights.batch_enabled": "true",
    "insights.batch_period_days": "14",
    "insights.min_sessions": "5",
    "insights.facet_max_calls": "100",
    "insights.facet_concurrency": "25",
    # Insights: reuse-existing-component suggestions. Disabling falls back to
    # suggestions that only ever propose building something new.
    "insights.registry_match_enabled": "true",
    "insights.registry_match_per_type": "6",
    "insights.registry_match_max_items": "24",
    # Auth
    "auth.self_registration_enabled": "false",
    # OIDC SSO. Changes require an API restart because the Authlib client is built at startup.
    "oauth.client_id": "",
    "oauth.client_secret": "",
    "oauth.server_metadata_url": "",
    "google.client_id": "",
    "google.client_secret": "",
    "google.allowed_domains": "",
    "github.client_id": "",
    "github.client_secret": "",
    "github.allowed_orgs": "",
    # Deployment
    "deployment.sso_only": "false",
    "deployment.public_registry_enabled": "false",
    "deployment.frontend_url": "http://localhost:3000",
    "deployment.public_url": "",
    "deployment.cors_origins": "http://localhost:3000",
    # Optional aggregate usage reporting. Company identity and public URL are
    # required before the sender will transmit anything.
    "usage_ping.enabled": "true",
    "usage_ping.company_name": "",
    "usage_ping.frequency": "every_6_hours",
    # Danger-zone actions (rendered as buttons; value is informational only)
    "danger.purge_traces_insights": "",
    # Security
    "security.allow_internal_git_urls": "false",
    "security.allow_draft_install": "false",
    "security.rate_limit_auth": "10/minute",
    "security.rate_limit_auth_strict": "5/minute",
    "security.trace_privacy": "false",
    # Registry policy
    "registry.registered_agents_only": "false",
    # Must be enabled only after all old API/worker processes are drained and
    # complete-folder installers have been deployed. A new worker cannot make
    # an old worker enforce skill review and delivery markers.
    "registry.skill_folder_delivery_enabled": "false",
    # Registry forking: kill switch and per-user hourly creation limit.
    "registry.fork.enabled": "true",
    "registry.fork.max_per_user_per_hour": "30",
    # Application retention policy, separate from the ClickHouse TTL below
    "retention.enabled": "false",
    "retention.trace_days": "",
    "retention.score_days": "",
    "retention.max_trace_count": "",
    # NOTE: Defaults to RFC 1918 private ranges so the Docker compose stack
    # works out of the box (nginx LB connects from a Docker-bridge IP).
    # Tradeoff: any process on the same private network can inject XFF headers
    # that the middleware will trust. For hardened deployments where the API is
    # directly exposed, narrow this to only the actual proxy IP(s).
    "security.trusted_proxy_ips": "172.16.0.0/12,10.0.0.0/8,192.168.0.0/16,127.0.0.1",
    # SAML
    "saml.idp_entity_id": "",
    "saml.idp_sso_url": "",
    "saml.idp_slo_url": "",
    "saml.idp_x509_cert": "",
    "saml.idp_metadata_url": "",
    "saml.sp_entity_id": "",
    "saml.sp_acs_url": "",
    "saml.jit_provisioning": "true",
    "saml.default_role": "user",
    "saml.sp_key_encryption_password": "",
    # JWT (runtime-tunable expiry settings)
    "jwt.access_token_expire_minutes": "60",
    "jwt.refresh_token_expire_days": "30",
    "jwt.hooks_token_expire_minutes": "43200",
    # Resources
    "resource.db_pool_size": "10",
    "resource.db_max_overflow": "20",
    "resource.redis_max_connections": "50",
    "resource.redis_socket_timeout": "2.0",
    "resource.clickhouse_max_connections": "20",
    "resource.clickhouse_max_keepalive": "10",
    "resource.clickhouse_timeout": "10.0",
    # Data
    "data.retention_days": "90",
    # Resolved inbox items are purged after this many days; open items never are.
    "inbox.retention_days": "90",
    "data.cache_ttl_default": "30",
    "data.cache_ttl_dashboard": "60",
    # Observability
    "observability.log_level": "INFO",  # TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL
    "observability.log_format": "json",  # 'json' or 'console' (colorized). Requires restart.
    "observability.enable_openapi": "false",
    "observability.enable_metrics": "false",
    # OTLP forwarding: a JSON list of destinations that receive every stored
    # session record (see services/otel/destinations.py for the shape).
    "telemetry.otlp_forward_destinations": "[]",
    # Discovery (ARD). The publisher domain is the first segment of every
    # permanent identifier; it is pinned automatically the first time a real
    # deployment.public_url is seen and changing it renames every identifier.
    "discovery.publisher_domain": "",
    # Delegation (ADR 0002). When on, every pulled Agent gets the observal-agents
    # MCP server so it can find and hand tasks to other approved agents.
    "discovery.delegation_enabled": "true",
    # Comma-separated hosts whose Agent Cards may be fetched even though they
    # resolve to private addresses (internal A2A agents). Plain http is allowed
    # for these hosts only.
    "discovery.a2a_private_hosts": "",
    # Misc
    "misc.harness_allowlist": "",
    "misc.default_harness": "",
    "misc.git_mirror_base_path": "",
}

# Sensitive keys: values are masked in API responses unless explicitly revealed
SENSITIVE_KEYS: set[str] = {
    "insights.api_key",
    "oauth.client_secret",
    "google.client_secret",
    "github.client_secret",
    "saml.idp_x509_cert",
    "saml.sp_private_key",
    "saml.sp_key_encryption_password",
    # Destination headers carry backend credentials.
    "telemetry.otlp_forward_destinations",
}

SETTING_FEATURES: dict[str, str] = {}

SETTING_SUBTITLES: dict[str, str] = {
    "registry.skill_folder_delivery_enabled": (
        "Enable complete-folder skill review and installation only after upgrading and draining all API/worker processes, "
        "migrating a restored database through the current head, and confirming supported CLI clients. "
        "Keep off during mixed-version rollout; see docs/skill-folder-rollout.md."
    ),
    "deployment.public_registry_enabled": (
        "Allow signed-out visitors to browse and install approved public registry content. "
        "Publishing, private data, telemetry, and administration still require authentication."
    ),
    "telemetry.otlp_forward_destinations": (
        "JSON list of OTLP/HTTP destinations that receive every stored session record as OTLP logs. "
        "Content is sent only to destinations with include_content set."
    ),
}

RESTART_REQUIRED_KEYS: set[str] = {
    "oauth.client_id",
    "oauth.client_secret",
    "oauth.server_metadata_url",
    "google.client_id",
    "google.client_secret",
    "google.allowed_domains",
    "github.client_id",
    "github.client_secret",
    "github.allowed_orgs",
    "security.rate_limit_auth",
    "security.rate_limit_auth_strict",
    "data.cache_ttl_default",
    "data.cache_ttl_dashboard",
    "observability.log_format",
    "observability.enable_openapi",
    "observability.enable_metrics",
    "misc.git_mirror_base_path",
}


# Section definitions for the settings schema endpoint
def _setting_label(key: str) -> str:
    label = key.rsplit(".", 1)[-1].replace("_", " ").title()
    return (
        label.replace("Api", "API")
        .replace("Url", "URL")
        .replace("Sso", "SSO")
        .replace("Oauth", "OAuth")
        .replace("Jwt", "JWT")
        .replace("Idp", "IdP")
        .replace("Jit", "JIT")
        .replace("Slo", "SLO")
        .replace("Acs", "ACS")
        .replace("X509", "X.509")
        .replace("Sp ", "SP ")
        .replace("Db ", "DB ")
        .replace("Ttl", "TTL")
        .replace(" Id", " ID")
    )


def settings_schema() -> list[dict[str, Any]]:
    """Return admin settings metadata for the web UI."""
    sections = []
    for section in SECTIONS:
        items = []
        for key in section["keys"]:
            items.append(
                {
                    "key": key,
                    "label": _setting_label(key),
                    "subtitle": SETTING_SUBTITLES.get(key, ""),
                    "default": DEFAULTS.get(key, ""),
                    "requires_feature": SETTING_FEATURES.get(key) or section.get("requires_feature"),
                    "restart_required": key in RESTART_REQUIRED_KEYS,
                    "is_externally_managed": is_externally_managed(key),
                }
            )
        sections.append({**section, "settings": items})
    return sections


SECTIONS: list[dict[str, Any]] = [
    {
        "id": "auth",
        "title": "Authentication",
        "description": "Authentication policy for public entry points.",
        "icon": "key",
        "danger": True,
        "keys": [k for k in DEFAULTS if k.startswith("auth.")],
    },
    {
        "id": "insights",
        "title": "Agent Insights",
        "description": "Configure LLM provider for the insights engine. Supports any LiteLLM-compatible provider (Anthropic, OpenAI, Bedrock, Gemini, Azure, Ollama, etc).",
        "icon": "sparkles",
        "keys": [k for k in DEFAULTS if k.startswith("insights.")],
    },
    {
        "id": "danger",
        "title": "Danger Zone",
        "description": "Destructive maintenance actions. Use only when you intentionally want to purge stored data.",
        "icon": "alert-triangle",
        "danger": True,
        "keys": [k for k in DEFAULTS if k.startswith("danger.")],
    },
    {
        "id": "deployment",
        "title": "Deployment",
        "description": "Core deployment configuration. Changes may affect authentication and access. Proceed with caution.",
        "icon": "server",
        "danger": True,
        "keys": [k for k in DEFAULTS if k.startswith("deployment.") and k != "deployment.sso_only"],
    },
    {
        "id": "usage_ping",
        "title": "Usage Reporting",
        "description": "Share aggregate adoption data with Observal on a super-admin-selected schedule. No prompts, traces, source code, credentials, or user identities are included.",
        "icon": "activity",
        "keys": [k for k in DEFAULTS if k.startswith("usage_ping.")],
    },
    {
        "id": "discovery",
        "title": "Discovery",
        "description": "Agentic Resource Discovery identifiers and agent delegation. The publisher domain is pinned from the public URL on first use; changing it renames every published identifier.",
        "icon": "search",
        "keys": [k for k in DEFAULTS if k.startswith("discovery.")],
    },
    {
        "id": "security",
        "title": "Security",
        "description": "Security policies and rate limiting. Misconfiguration can expose the instance to attacks.",
        "icon": "shield",
        "danger": True,
        "keys": [k for k in DEFAULTS if k.startswith("security.")],
    },
    {
        "id": "sso",
        "title": "SSO",
        "description": "OIDC, SAML, and SSO-only authentication settings.",
        "icon": "key",
        "danger": True,
        "keys": [
            "deployment.sso_only",
            "oauth.client_id",
            "oauth.client_secret",
            "oauth.server_metadata_url",
            "google.client_id",
            "google.client_secret",
            "google.allowed_domains",
            "github.client_id",
            "github.client_secret",
            "github.allowed_orgs",
            *[k for k in DEFAULTS if k.startswith("saml.")],
        ],
    },
    {
        "id": "jwt",
        "title": "JWT Token Expiry",
        "description": "Token lifetime settings. Shorter values improve security but increase re-authentication frequency.",
        "icon": "clock",
        "keys": [k for k in DEFAULTS if k.startswith("jwt.")],
    },
    {
        "id": "resource",
        "title": "Resource Tuning",
        "description": "Connection pool sizes and query limits. Changes take effect on next connection. May require restart for pool sizes.",
        "icon": "database",
        "keys": [k for k in DEFAULTS if k.startswith("resource.")],
    },
    {
        "id": "data",
        "title": "Data & Retention",
        "description": "Deployment-wide retention policies and cache TTLs.",
        "icon": "hard-drive",
        "keys": [k for k in DEFAULTS if k.startswith("data.") or k.startswith("retention.") or k.startswith("inbox.")],
    },
    {
        "id": "registry",
        "title": "Registry",
        "description": "Deployment-wide registry policy.",
        "icon": "package",
        "keys": [k for k in DEFAULTS if k.startswith("registry.")],
    },
    {
        "id": "observability",
        "title": "Observability",
        "description": "Logging, metrics and OTLP forwarding configuration.",
        "icon": "activity",
        "keys": [k for k in DEFAULTS if k.startswith("observability.") or k.startswith("telemetry.")],
    },
    {
        "id": "misc",
        "title": "Miscellaneous",
        "description": "Other system settings.",
        "icon": "settings",
        "keys": [k for k in DEFAULTS if k.startswith("misc.")],
    },
]


# ─── Cache + DB read layer ───────────────────────────────────────────────────


async def get(key: str, default: str | None = None) -> str:
    """Get a setting value. Checks Redis cache first, then DB, then hardcoded default.

    Args:
        key: Dotted setting key (e.g., "insights.model_sections")
        default: Override default (if None, uses DEFAULTS dict)

    Returns:
        The setting value as a string.
    """
    optic.trace("reading setting: {}", key)
    if key in _external_settings:
        return _external_settings[key]

    # 1. Try Redis cache
    try:
        from services.redis import get_redis

        r = get_redis()
        cached = await r.get(f"{_CACHE_PREFIX}{key}")
        if cached is not None:
            return cached
    except Exception:
        # Redis down, fall through to DB
        pass

    # 2. Read from DB
    value = await _read_from_db(key)

    if value is not None:
        # Cache the DB value
        try:
            from services.redis import get_redis

            r = get_redis()
            await r.set(f"{_CACHE_PREFIX}{key}", value, ex=_CACHE_TTL)
        except Exception:
            pass
        return value

    # 3. Return hardcoded default
    if default is not None:
        return default
    return DEFAULTS.get(key, "")


async def get_int(key: str, default: int | None = None) -> int:
    """Get a setting as an integer."""
    raw = await get(key)
    if not raw:
        if default is not None:
            return default
        fallback = DEFAULTS.get(key, "0")
        try:
            return int(fallback)
        except (ValueError, TypeError):
            return 0
    try:
        return int(raw)
    except (ValueError, TypeError):
        optic.warning("invalid integer dynamic setting key={}", key)
        if default is not None:
            return default
        fallback = DEFAULTS.get(key, "0")
        try:
            return int(fallback)
        except (ValueError, TypeError):
            return 0


async def get_float(key: str, default: float | None = None) -> float:
    """Get a setting as a float."""
    raw = await get(key)
    if not raw:
        if default is not None:
            return default
        fallback = DEFAULTS.get(key, "0.0")
        try:
            return float(fallback)
        except (ValueError, TypeError):
            return 0.0
    try:
        return float(raw)
    except (ValueError, TypeError):
        optic.warning("invalid float dynamic setting key={}", key)
        if default is not None:
            return default
        fallback = DEFAULTS.get(key, "0.0")
        try:
            return float(fallback)
        except (ValueError, TypeError):
            return 0.0


async def get_bool(key: str, default: bool | None = None) -> bool:
    """Get a setting as a boolean."""
    raw = await get(key)
    if not raw:
        if default is not None:
            return default
        fallback = DEFAULTS.get(key, "false")
        return fallback.lower() in ("true", "1", "yes")
    return raw.lower() in ("true", "1", "yes")


async def get_list(key: str, separator: str = ",") -> list[str]:
    """Get a setting as a list of strings (split by separator)."""
    raw = await get(key)
    if not raw:
        return []
    return [item.strip() for item in raw.split(separator) if item.strip()]


async def invalidate(key: str) -> None:
    """Invalidate a cached setting (call after writes)."""
    try:
        from services.redis import get_redis

        r = get_redis()
        await r.delete(f"{_CACHE_PREFIX}{key}")
    except Exception:
        pass


async def invalidate_all() -> None:
    """Invalidate all cached settings."""
    try:
        from services.redis import get_redis

        r = get_redis()
        # Use SCAN to find and delete all settings keys
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match=f"{_CACHE_PREFIX}*", count=100)
            if keys:
                await r.delete(*keys)
            if cursor == 0:
                break
    except Exception:
        pass


async def get_all() -> dict[str, str]:
    """Get all settings from DB, merged with defaults for missing keys.

    Returns a dict of all known settings with their current values.
    """
    db_values = await _read_all_from_db()
    result = dict(DEFAULTS)
    result.update(db_values)
    result.update(_external_settings)
    return result


async def get_section(section_id: str) -> dict[str, str]:
    """Get all settings for a specific section."""
    prefix = f"{section_id}."
    all_settings = await get_all()
    return {k: v for k, v in all_settings.items() if k.startswith(prefix)}


# ─── Internal DB access ──────────────────────────────────────────────────────


async def _read_from_db(key: str) -> str | None:
    """Read a single setting from the database. Decrypts if sensitive."""
    try:
        from sqlalchemy import select

        from database import async_session
        from models.enterprise_config import EnterpriseConfig

        async with async_session() as session:
            result = await session.execute(select(EnterpriseConfig.value).where(EnterpriseConfig.key == key))
            row = result.scalar_one_or_none()
            if row is None:
                return None
            # Decrypt if it's an encrypted value
            if row.startswith(_ENC_PREFIX):
                return decrypt_value(row)
            return row
    except Exception:
        optic.warning("dynamic settings database read failed for key={}", key)
        return None


async def _read_all_from_db() -> dict[str, str]:
    """Read all settings from the database. Decrypts sensitive values."""
    try:
        from sqlalchemy import select

        from database import async_session
        from models.enterprise_config import EnterpriseConfig

        async with async_session() as session:
            result = await session.execute(select(EnterpriseConfig.key, EnterpriseConfig.value))
            settings_dict = {}
            for row in result.all():
                value = row.value
                if value and value.startswith(_ENC_PREFIX):
                    value = decrypt_value(value)
                settings_dict[row.key] = value
            return settings_dict
    except Exception:
        optic.warning("dynamic settings database read-all failed")
        return {}


def mask_value(key: str, value: str) -> str:
    """Mask sensitive values for API display."""
    if key not in SENSITIVE_KEYS:
        return value
    if not value or len(value) <= 4:
        return "••••••••"
    return "••••••" + value[-4:]


# ─── Sync cache for module-level / sync-function access ──────────────────────
# Populated once at startup via `load_sync_cache()`, refreshed on setting writes.

_sync_cache: dict[str, str] = {}
_sync_cache_loaded: bool = False


def get_sync(key: str, default: str | None = None) -> str:
    """Synchronous setting access from the in-memory cache.

    Falls back to DEFAULTS if not in cache. Call `load_sync_cache()` at startup.
    """
    if key in _sync_cache:
        return _sync_cache[key]
    if default is not None:
        return default
    return DEFAULTS.get(key, "")


def get_sync_int(key: str, default: int | None = None) -> int:
    """Synchronous int setting access."""
    raw = get_sync(key)
    if not raw:
        if default is not None:
            return default
        fallback = DEFAULTS.get(key, "0")
        try:
            return int(fallback)
        except (ValueError, TypeError):
            return 0
    try:
        return int(raw)
    except (ValueError, TypeError):
        if default is not None:
            return default
        return 0


def get_sync_bool(key: str, default: bool | None = None) -> bool:
    """Synchronous bool setting access."""
    raw = get_sync(key)
    if not raw:
        if default is not None:
            return default
        fallback = DEFAULTS.get(key, "false")
        return fallback.lower() in ("true", "1", "yes")
    return raw.lower() in ("true", "1", "yes")


async def load_sync_cache() -> None:
    """Load all settings into the sync cache. Call once at startup."""
    optic.debug("loading dynamic settings sync cache")
    global _sync_cache, _sync_cache_loaded
    try:
        db_values = await _read_all_from_db()
        _sync_cache = dict(DEFAULTS)
        _sync_cache.update(db_values)
        _sync_cache.update(_external_settings)
        _sync_cache_loaded = True
        optic.info("dynamic_settings_cache_loaded count={}", len(db_values))
    except Exception:
        optic.warning("dynamic_settings_cache_load_failed")
        _sync_cache = dict(DEFAULTS)
        _sync_cache.update(_external_settings)
        _sync_cache_loaded = True


async def refresh_sync_cache() -> None:
    """Refresh the sync cache (call after writes)."""
    await load_sync_cache()
