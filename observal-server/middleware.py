# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger as optic
from redis.exceptions import RedisError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

import services.dynamic_settings as ds
from api.middleware.audit import AuditMiddleware
from api.middleware.content_type import ContentTypeMiddleware
from api.middleware.request_id import RequestIDMiddleware
from api.middleware.sso_config_guard import SsoConfigGuardMiddleware
from api.middleware.trusted_proxy import TrustedProxyMiddleware
from api.ratelimit import limiter
from config import settings
from services.audit import AUDIT_ENABLED

DEFAULT_CORS_ALLOWED_ORIGINS = "http://localhost:3000"
DEFAULT_MAX_REQUEST_SIZE_MB = "10"
DEFAULT_MAX_SKILL_REQUEST_SIZE_MB = "10"
DEFAULT_MAX_MIGRATION_REQUEST_SIZE_MB = "6144"
SKILL_AUTHORING_PATHS = frozenset({"/api/v1/skills/submit", "/api/v1/skills/draft", "/api/v1/skills/folder-drafts"})
MIGRATION_UPLOAD_PATHS = frozenset(
    {
        "/api/v1/admin/migrate/import",
        "/api/v1/admin/migrate/validate",
    }
)
CLI_VERSION_EXEMPT_PATHS = {
    "/api/v1/config/version",
    "/api/v1/config/public",
    "/api/v1/config/endpoints",
    "/api/v1/graphql",
    "/api/v1/mcp",
    "/api/v1/skills",
    "/api/v1/agents",
}
CLI_VERSION_EXEMPT_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/ingest/",
    "/api/v1/telemetry/",
    "/api/v1/support/",
    "/api/v1/audit/cli-event",
    "/api/v1/sessions/crypto/",
)
CLI_USER_AGENT_PREFIXES = (
    "observal-cli/",
    "python-httpx/",
    "python-requests/",
    "python-urllib3/",
)


class RequestBodyTooLargeError(Exception):
    """Raised while streaming an ASGI request body beyond its route limit."""


class RequestSizeLimitMiddleware:
    """Reject declared or streamed request bodies that exceed route limits."""

    def __init__(
        self,
        app,
        max_request_size_bytes: int,
        max_migration_request_size_bytes: int,
        max_skill_request_size_bytes: int | None = None,
    ):
        self.app = app
        self.max_request_size_bytes = max_request_size_bytes
        self.max_migration_request_size_bytes = max_migration_request_size_bytes
        self.max_skill_request_size_bytes = max_skill_request_size_bytes or max_request_size_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        request_path = (scope.get("path") or "/").rstrip("/") or "/"
        request_limit = (
            self.max_migration_request_size_bytes
            if request_path in MIGRATION_UPLOAD_PATHS
            else self.max_skill_request_size_bytes
            if request_path in SKILL_AUTHORING_PATHS
            or (request_path.startswith("/api/v1/skills/") and request_path.endswith("/draft"))
            or (request_path.startswith("/api/v1/skills/") and request_path.endswith("/versions"))
            or (
                request_path.startswith("/api/v1/skills/")
                and "/versions/" in request_path
                and request_path.endswith("/files")
            )
            else self.max_request_size_bytes
        )
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                response = JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
                await response(scope, receive, send)
                return
            if declared_length > request_limit:
                response = JSONResponse(status_code=413, content={"detail": "Request body too large"})
                await response(scope, receive, send)
                return

        bytes_received = 0

        async def receive_with_limit():
            nonlocal bytes_received
            message = await receive()
            if message["type"] == "http.request":
                bytes_received += len(message.get("body", b""))
                if bytes_received > request_limit:
                    raise RequestBodyTooLargeError
            return message

        try:
            await self.app(scope, receive_with_limit, send)
        except RequestBodyTooLargeError:
            response = JSONResponse(status_code=413, content={"detail": "Request body too large"})
            await response(scope, receive, send)


class SecurityHeadersMiddleware:
    """Attach common security headers to every HTTP response."""

    def __init__(self, app, security_headers: list[tuple[bytes, bytes]]):
        self.app = app
        self.security_headers = security_headers

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self.security_headers)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class CacheControlMiddleware:
    """Set Cache-Control headers on responses served from cache."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_cache_control(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                cache_header = headers.get(b"x-fastapi-cache", b"").decode()
                if cache_header == "HIT" or (scope["method"] == "GET" and cache_header == "MISS"):
                    extra = [
                        (b"cache-control", f"public, max-age={ds.get_sync_int('data.cache_ttl_default', 30)}".encode())
                    ]
                    msg_headers = list(message.get("headers", []))
                    msg_headers.extend(extra)
                    message = {**message, "headers": msg_headers}
            await send(message)

        await self.app(scope, receive, send_with_cache_control)


def get_cors_allowed_origins() -> list[str]:
    cors_env = os.environ.get("CORS_ALLOWED_ORIGINS", DEFAULT_CORS_ALLOWED_ORIGINS)
    return [origin.strip() for origin in cors_env.split(",") if origin.strip()]


def get_max_request_size_bytes() -> int:
    return int(os.environ.get("MAX_REQUEST_SIZE_MB", DEFAULT_MAX_REQUEST_SIZE_MB)) * 1024 * 1024


def get_max_migration_request_size_bytes() -> int:
    return int(os.environ.get("MAX_MIGRATION_REQUEST_SIZE_MB", DEFAULT_MAX_MIGRATION_REQUEST_SIZE_MB)) * 1024 * 1024


def build_security_headers(cors_allowed_origins: list[str]) -> list[tuple[bytes, bytes]]:
    is_localhost = any(
        origin.startswith("http://localhost") or origin.startswith("http://127.0.0.1")
        for origin in cors_allowed_origins
    )
    security_headers = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"x-xss-protection", b"1; mode=block"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        (
            b"content-security-policy",
            (
                b"default-src 'self'; "
                b"script-src 'self'; "
                b"style-src 'self' 'unsafe-inline'; "
                b"img-src 'self' data: https:; "
                b"font-src 'self'; "
                b"connect-src 'self' https:; "
                b"frame-ancestors 'none'; "
                b"base-uri 'self'; "
                b"form-action 'self'"
            ),
        ),
        (b"x-permitted-cross-domain-policies", b"none"),
    ]
    if not is_localhost:
        security_headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
    return security_headers


async def redis_error_handler(request: Request, exc: RedisError):
    optic.error("redis_error method={} path={} error={}", request.method, request.url.path, str(exc))
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"})


def configure_rate_limit_handlers(app: FastAPI) -> None:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    async def set_rate_limit_defaults(request: Request, call_next):
        request.state.view_rate_limit = None
        return await call_next(request)

    app.middleware("http")(set_rate_limit_defaults)


def _should_require_cli_version_header(request: Request) -> bool:
    path = request.url.path
    if not path.startswith("/api/v1/"):
        return False
    if path in CLI_VERSION_EXEMPT_PATHS:
        return False
    if path.startswith(CLI_VERSION_EXEMPT_PREFIXES):
        return False
    auth_header = request.headers.get("authorization", "").lower()
    if not auth_header.startswith("bearer "):
        return False
    user_agent = request.headers.get("user-agent", "").lower()
    return user_agent.startswith(CLI_USER_AGENT_PREFIXES)


def _cli_version_response(server_ver: str, cli_ver_str: str | None) -> JSONResponse:
    from packaging.version import InvalidVersion, Version

    # Determine direction: if CLI is ahead, suggest downgrade; otherwise upgrade
    direction = "upgrade"
    if cli_ver_str:
        try:
            if Version(cli_ver_str) > Version(server_ver):
                direction = "downgrade"
        except InvalidVersion:
            pass

    install_command = f"observal self {direction} --version {server_ver}"
    shown_cli_version = cli_ver_str or "unknown"
    return JSONResponse(
        status_code=426,
        content={
            "detail": (
                f"Observal CLI {shown_cli_version} cannot connect to server {server_ver}. Run: {install_command}"
            ),
            "server_version": server_ver,
            "cli_version": cli_ver_str,
            "install_command": install_command,
        },
        headers={
            "X-Observal-Server": server_ver,
            "X-Observal-Required-CLI": server_ver,
        },
    )


def configure_version_middleware(app: FastAPI) -> None:
    async def version_middleware(request: Request, call_next):
        from packaging.version import InvalidVersion, Version

        from version import get_server_version

        server_ver = get_server_version()
        cli_ver_str = request.headers.get("x-observal-cli-version")
        effective = server_ver

        if server_ver != "dev":
            if not cli_ver_str and _should_require_cli_version_header(request):
                return _cli_version_response(server_ver, None)
            if cli_ver_str:
                try:
                    client_ver = Version(cli_ver_str)
                    server_version = Version(server_ver)
                    if client_ver != server_version:
                        return _cli_version_response(server_ver, cli_ver_str)
                    effective = str(server_version)
                except InvalidVersion:
                    return _cli_version_response(server_ver, cli_ver_str)

        request.state.effective_version = effective
        response = await call_next(request)
        response.headers["X-Observal-Server"] = server_ver
        response.headers["X-Observal-Effective"] = effective
        return response

    app.middleware("http")(version_middleware)


def configure_middleware(app: FastAPI) -> None:
    configure_rate_limit_handlers(app)
    configure_version_middleware(app)
    app.add_exception_handler(RedisError, redis_error_handler)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        max_age=3600,
    )

    cors_allowed_origins = get_cors_allowed_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    )

    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_request_size_bytes=get_max_request_size_bytes(),
        max_skill_request_size_bytes=int(os.environ.get("MAX_SKILL_REQUEST_SIZE_MB", DEFAULT_MAX_SKILL_REQUEST_SIZE_MB))
        * 1024
        * 1024,
        max_migration_request_size_bytes=get_max_migration_request_size_bytes(),
    )
    app.add_middleware(SecurityHeadersMiddleware, security_headers=build_security_headers(cors_allowed_origins))
    app.add_middleware(ContentTypeMiddleware)
    app.add_middleware(RequestIDMiddleware)
    if AUDIT_ENABLED:
        app.add_middleware(AuditMiddleware)
    app.add_middleware(SsoConfigGuardMiddleware, settings=settings)
    app.add_middleware(TrustedProxyMiddleware)
    app.add_middleware(CacheControlMiddleware)
