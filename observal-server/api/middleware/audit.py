# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Yash Gadgil <yashgadgil08@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Audit middleware: captures every request lifecycle via loguru."""

from __future__ import annotations

import re
import time
import uuid
from typing import TYPE_CHECKING

from loguru import logger as optic
from starlette.middleware.base import BaseHTTPMiddleware

from services.audit.classification import classify_route

if TYPE_CHECKING:
    from starlette.requests import Request

HTTP_REDIRECT_THRESHOLD = 300
HTTP_NOT_FOUND = 404
HTTP_SERVER_ERROR_THRESHOLD = 500


class AuditMiddleware(BaseHTTPMiddleware):
    """Emit a loguru audit record for every non-skipped HTTP request."""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        event_id = str(uuid.uuid4())
        request_id = getattr(request.state, "request_id", "")

        sensitivity = classify_route(request.method, request.url.path)
        if sensitivity == "skip":
            return await call_next(request)

        # Real IP is resolved by TrustedProxyMiddleware into request.scope["client"]
        ip = request.client.host if request.client else "127.0.0.1"

        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        user = getattr(request.state, "audit_user", None)

        status = response.status_code
        if status < HTTP_REDIRECT_THRESHOLD:
            outcome = "success"
        elif status in (401, 403):
            outcome = "denied"
        elif status == HTTP_NOT_FOUND:
            outcome = "not_found"
        elif status >= HTTP_SERVER_ERROR_THRESHOLD:
            outcome = "error"
        else:
            outcome = "client_error"

        # Share tokens are bearer credentials; never persist them in audit records.
        audit_path = re.sub(r"(/agent-shares/)[^/]+", r"\1{token}", request.url.path)
        action = getattr(request.state, "audit_action", f"{request.method.lower()}.{audit_path}")
        resource_type = getattr(request.state, "audit_resource_type", "")
        resource_id = getattr(request.state, "audit_resource_id", "")
        resource_name = getattr(request.state, "audit_resource_name", "")
        detail = getattr(request.state, "audit_detail", "")

        optic.bind(
            audit=True,
            event_id=event_id,
            request_id=request_id,
            http_method=request.method,
            http_path=audit_path,
            ip_address=ip,
            user_agent=request.headers.get("user-agent", "")[:256],
            sensitivity=sensitivity,
            source="server",
            actor_id=str(user.id) if user else "anonymous",
            actor_email=user.email if user else "",
            actor_role=(
                user.role.value if user and hasattr(user, "role") and hasattr(user.role, "value") else "anonymous"
            ),
            status_code=status,
            outcome=outcome,
            duration_ms=round(duration_ms, 2),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            detail=detail,
        ).info("audit")

        return response
