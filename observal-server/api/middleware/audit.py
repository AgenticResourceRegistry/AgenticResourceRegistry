# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Yash Gadgil <yashgadgil08@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Audit middleware: captures every request lifecycle via loguru."""

from __future__ import annotations

import re
import time
import uuid

from loguru import logger as optic
from starlette.requests import Request

from services.audit.classification import classify_route


class AuditMiddleware:
    """Emit a loguru audit record for every non-skipped HTTP request.

    Pure ASGI: the record is emitted when the response starts, after the route
    has set its ``request.state.audit_*`` attributes.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        request = Request(scope)
        path = request.url.path

        sensitivity = classify_route(request.method, path)
        if sensitivity == "skip":
            await self.app(scope, receive, send)
            return

        event_id = str(uuid.uuid4())
        request_id = getattr(request.state, "request_id", "")
        # Real IP is resolved by TrustedProxyMiddleware into request.scope["client"]
        ip = request.client.host if request.client else "127.0.0.1"

        async def send_with_audit(message):
            if message["type"] == "http.response.start":
                _emit_audit(request, path, sensitivity, event_id, request_id, ip, message["status"], start)
            await send(message)

        await self.app(scope, receive, send_with_audit)


def _emit_audit(
    request: Request,
    path: str,
    sensitivity: str,
    event_id: str,
    request_id: str,
    ip: str,
    status: int,
    start: float,
) -> None:
    duration_ms = (time.perf_counter() - start) * 1000

    user = getattr(request.state, "audit_user", None)

    if status < 300:
        outcome = "success"
    elif status in (401, 403):
        outcome = "denied"
    elif status == 404:
        outcome = "not_found"
    elif status >= 500:
        outcome = "error"
    else:
        outcome = "client_error"

    # Share tokens are bearer credentials; never persist them in audit records.
    audit_path = re.sub(r"(/agent-shares/)[^/]+", r"\1{token}", path)
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
        actor_role=(user.role.value if user and hasattr(user, "role") and hasattr(user.role, "value") else "anonymous"),
        status_code=status,
        outcome=outcome,
        duration_ms=round(duration_ms, 2),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        detail=detail,
    ).info("audit")
