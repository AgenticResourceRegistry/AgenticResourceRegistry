# SPDX-FileCopyrightText: 2026 Dheirav <dheirav2005@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Bad client input must come back as a 4xx, never reach the database as a 500."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["category=%00", "harness=a%00b", "%00=x"])
async def test_nul_in_query_string_is_rejected_on_get(query):
    from api.middleware.content_type import ContentTypeMiddleware

    app = FastAPI()
    app.add_middleware(ContentTypeMiddleware)

    @app.get("/api/v1/mcps")
    async def mcps():
        return []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad = await client.get("/api/v1/mcps?" + query)
        ok = await client.get("/api/v1/mcps?category=general&search=%2500")
    assert bad.status_code == 422
    assert "NUL" in bad.json()["detail"]
    assert ok.status_code == 200
