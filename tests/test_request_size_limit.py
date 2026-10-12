# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Streaming request-size enforcement tests."""

from __future__ import annotations

import pytest
from fastapi.responses import JSONResponse

from middleware import RequestSizeLimitMiddleware


async def _request_status(
    path: str,
    chunks: list[bytes],
    headers: list[tuple[bytes, bytes]] | None = None,
    *,
    skill_limit: int | None = None,
) -> int:
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    sent: list[dict] = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    async def app(scope, receive_body, send_response):
        while True:
            message = await receive_body()
            if not message.get("more_body", False):
                break
        await JSONResponse({"ok": True})(scope, receive_body, send_response)

    middleware = RequestSizeLimitMiddleware(
        app,
        max_request_size_bytes=5,
        max_migration_request_size_bytes=10,
        max_skill_request_size_bytes=skill_limit,
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers or [],
    }
    await middleware(scope, receive, send)
    return next(message["status"] for message in sent if message["type"] == "http.response.start")


@pytest.mark.asyncio
async def test_chunked_body_cannot_bypass_normal_request_limit():
    assert await _request_status("/api/v1/other", [b"123", b"456"]) == 413


@pytest.mark.asyncio
async def test_migration_limit_applies_to_trailing_slash_and_streamed_bytes():
    assert await _request_status("/api/v1/admin/migrate/validate/", [b"12345", b"67890"]) == 200
    assert await _request_status("/api/v1/admin/migrate/validate/", [b"12345", b"678901"]) == 413


@pytest.mark.asyncio
async def test_skill_authoring_has_independent_declared_and_streamed_limit():
    assert await _request_status("/api/v1/skills/submit", [b"123456"], skill_limit=7) == 200
    assert await _request_status("/api/v1/skills/draft", [b"1234", b"5678"], skill_limit=7) == 413
    assert await _request_status("/api/v1/skills/folder-drafts", [b"123456"], skill_limit=7) == 200
    assert await _request_status("/api/v1/skills/folder-drafts", [b"1234", b"5678"], skill_limit=7) == 413
    assert await _request_status("/api/v1/skills/abc/draft", [b""], [(b"content-length", b"8")], skill_limit=7) == 413
    assert await _request_status("/api/v1/skills/abc/versions", [b"1234", b"5678"], skill_limit=7) == 413
    assert await _request_status("/api/v1/skills/abc/versions/def/files", [b"123456"], skill_limit=7) == 200
    assert await _request_status("/api/v1/skills/abc/versions/def/files", [b"1234", b"5678"], skill_limit=7) == 413
    assert await _request_status("/api/v1/other", [b"123456"], skill_limit=7) == 413


@pytest.mark.asyncio
async def test_declared_oversized_body_is_rejected_before_consumption():
    assert await _request_status("/api/v1/other", [b""], [(b"content-length", b"6")]) == 413
