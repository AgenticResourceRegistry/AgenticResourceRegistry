# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Content-Type validation and JSON depth protection middleware.

Rejects POST/PUT/PATCH requests that do not carry an acceptable Content-Type
header and guards against deeply-nested JSON payloads (JSON bomb mitigation).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse
from loguru import logger as optic
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from starlette.requests import Request


# Methods that carry a request body and therefore require Content-Type validation.
_BODY_METHODS = {"POST", "PUT", "PATCH"}

# Paths that are exempt from strict Content-Type enforcement.
# GraphQL may receive multipart for file variables.
_SKIP_PATHS: set[str] = {
    "/health",
}

_SKIP_PREFIXES: tuple[str, ...] = (
    "/api/v1/graphql",
    "/api/v1/sso/saml",
    "/api/v1/ingest/",
    "/api/v1/telemetry/",
)

# Accepted content types for normal endpoints.
_ALLOWED_TYPES = {
    "application/json",
    "multipart/form-data",
}

# Maximum allowed nesting depth for JSON payloads.
MAX_JSON_DEPTH = 20


def _check_depth(obj: object, current: int = 0) -> bool:
    """Return True if *obj* exceeds MAX_JSON_DEPTH levels of nesting.

    Only dicts and lists count as nesting levels.  Scalar values do not
    increment the depth counter.
    """
    if isinstance(obj, dict):
        if current + 1 > MAX_JSON_DEPTH:
            return True
        return any(_check_depth(v, current + 1) for v in obj.values())
    if isinstance(obj, list):
        if current + 1 > MAX_JSON_DEPTH:
            return True
        return any(_check_depth(item, current + 1) for item in obj)
    return False


def _has_nul(obj: object) -> bool:
    """PostgreSQL text cannot store NUL; JSON can carry it as \\u0000 in any string or key."""
    if isinstance(obj, str):
        return "\x00" in obj
    if isinstance(obj, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in obj.items())
    if isinstance(obj, list):
        return any(_has_nul(item) for item in obj)
    return False


def _content_type_base(header: str | None) -> str:
    """Extract the media type portion (before any parameters like charset)."""
    if not header:
        return ""
    return header.split(";")[0].strip().lower()


class ContentTypeMiddleware(BaseHTTPMiddleware):
    """Validate Content-Type and JSON payload depth."""

    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()

        # Only validate body-bearing methods.
        if method not in _BODY_METHODS:
            return await call_next(request)

        path = request.url.path.rstrip("/")

        # Skip exempt paths.
        if path in _SKIP_PATHS or any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        # If there is no body to validate, skip Content-Type enforcement.
        content_length = request.headers.get("content-length")
        ct_header = request.headers.get("content-type")
        if content_length is not None and int(content_length) == 0:
            return await call_next(request)
        if content_length is None and not ct_header:
            return await call_next(request)
        ct = _content_type_base(request.headers.get("content-type"))

        # Determine allowed set based on path.
        allowed = _ALLOWED_TYPES

        if ct not in allowed:
            optic.warning(
                "Rejected {} {} with Content-Type: {}",
                method,
                path,
                request.headers.get("content-type"),
            )
            return JSONResponse(
                status_code=415,
                content={"detail": f"Unsupported media type: {ct or '(none)'}. Expected application/json."},
            )

        # JSON depth check - only for application/json bodies.
        if ct == "application/json":
            try:
                body = await request.body()
                if body:
                    parsed = json.loads(body)
                    if _check_depth(parsed):
                        optic.warning("Rejected {} {}: JSON nesting exceeds {} levels", method, path, MAX_JSON_DEPTH)
                        return JSONResponse(
                            status_code=400,
                            content={"detail": f"JSON payload exceeds maximum nesting depth of {MAX_JSON_DEPTH}"},
                        )
                    if _has_nul(parsed):
                        return JSONResponse(
                            status_code=422,
                            content={"detail": "Text fields must not contain NUL characters"},
                        )
            except json.JSONDecodeError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Malformed JSON in request body"},
                )

        return await call_next(request)
