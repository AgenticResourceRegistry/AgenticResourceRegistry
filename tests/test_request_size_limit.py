# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
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
    snapshot_limit: int = 10 * 1024 * 1024,
    normal_limit: int = 5,
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
        max_request_size_bytes=normal_limit,
        max_migration_request_size_bytes=10,
        max_skill_request_size_bytes=skill_limit,
        max_layer_snapshot_request_size_bytes=snapshot_limit,
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


@pytest.mark.asyncio
async def test_snapshot_cap_counts_chunks_even_with_false_content_length_and_does_not_cap_migrations():
    path = "/api/v1/layer-snapshots/"
    assert await _request_status(path, [b"12345", b"67890"], snapshot_limit=10) == 200
    assert await _request_status(path, [b"12345", b"678901"], snapshot_limit=10) == 413
    assert await _request_status(path, [b"12345", b"678901"], [(b"content-length", b"2")], snapshot_limit=10) == 413
    assert await _request_status(path, [b""], [(b"content-length", b"11")], snapshot_limit=10) == 413
    assert await _request_status("/api/v1/admin/migrate/import", [b"12345", b"67890"], snapshot_limit=2) == 200
    assert await _request_status("/api/v1/other", [b"123456"], snapshot_limit=10) == 413


@pytest.mark.asyncio
async def test_legitimate_maximum_file_content_snapshot_fits_the_api_byte_limit():
    import json

    from api.routes.layer_snapshot import LayerSnapshotRequest
    from middleware import MAX_LAYER_SNAPSHOT_REQUEST_BYTES

    files = [
        {"path": f"user:skill-{i}.md", "hash": "sha256-" + "a" * 64, "size": 524288, "content": "x" * 524288}
        for i in range(10)
    ]
    payload = json.dumps({"hash": "0123456789abcdef", "harnesses": {"pi": files}}).encode()
    assert 5 * 1024 * 1024 < len(payload) < MAX_LAYER_SNAPSHOT_REQUEST_BYTES
    LayerSnapshotRequest.model_validate_json(payload)
    assert (
        await _request_status(
            "/api/v1/layer-snapshots",
            [payload[: len(payload) // 2], payload[len(payload) // 2 :]],
            snapshot_limit=MAX_LAYER_SNAPSHOT_REQUEST_BYTES,
        )
        == 200
    )


def test_shipped_proxy_configs_keep_snapshot_and_migration_upload_limits_separate():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "docker"
    for name in ("nginx.conf", "nginx.dev.conf", "nginx.production.conf", "nginx-azure.conf", "nginx-spa.conf"):
        config = (root / name).read_text()
        assert "client_max_body_size 10m;" in config, name
        migration = config.index("location ~ ^/api/v1/admin/migrate/(import|validate)/?$")
        api = config.index("location /api/")
        assert migration < api and "client_max_body_size 6g;" in config[migration:api], name
    # ECS routes /api directly to the API target group, never through web nginx.
    assert not any(
        line.lstrip().startswith("proxy_pass ") for line in (root / "nginx-spa-ecs.conf").read_text().splitlines()
    )
