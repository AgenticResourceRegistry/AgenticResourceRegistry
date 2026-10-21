# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Request ID middleware.

Assigns a unique UUID to every request and returns it via the ``X-Request-ID``
response header.  If the client supplies a valid UUID in the ``X-Request-ID``
request header it is reused; otherwise a new one is generated.  Invalid values
are silently replaced to prevent header injection.

The request ID is also bound to the structlog context so that every log line
emitted during the request carries it automatically.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.requests import Request

from services.request_context import set_request_context


class RequestIDMiddleware:
    """Attach an ``X-Request-ID`` header to every response and bind it to the structlog context."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        incoming = request.headers.get("x-request-id")
        request_id: str

        if incoming:
            try:
                request_id = str(uuid.UUID(incoming))
            except (ValueError, AttributeError):
                request_id = str(uuid.uuid4())
        else:
            request_id = str(uuid.uuid4())

        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        set_request_context(request)

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_with_request_id)
