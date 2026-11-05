# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import pytest

from middleware import CacheControlMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", [b"HIT", b"MISS"])
async def test_cached_get_replaces_cache_control_with_private(marker, monkeypatch):
    from middleware import ds

    monkeypatch.setattr(ds, "get_sync_int", lambda key, default: 30)
    sent = []

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"cache-control", b"max-age=30"), (b"x-fastapi-cache", marker)],
            }
        )

    async def send(message):
        sent.append(message)

    await CacheControlMiddleware(app)({"type": "http", "method": "GET"}, None, send)
    headers = sent[0]["headers"]
    assert [value for key, value in headers if key.lower() == b"cache-control"] == [b"private, max-age=30"]
    assert (b"x-fastapi-cache", marker) in headers


@pytest.mark.asyncio
async def test_uncached_response_keeps_its_cache_policy():
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"cache-control", b"no-store")]})

    async def send(message):
        sent.append(message)

    await CacheControlMiddleware(app)({"type": "http", "method": "GET"}, None, send)
    assert sent[0]["headers"] == [(b"cache-control", b"no-store")]
