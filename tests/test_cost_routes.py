# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import get_current_user, get_db
from api.routes import cost_intelligence as routes
from models.user import UserRole
from services.cost.queries import CostDataUnavailableError


def make_app(role: UserRole | None) -> FastAPI:
    app = FastAPI()
    app.include_router(routes.router)

    async def no_db():
        yield None

    app.dependency_overrides[get_db] = no_db
    if role is not None:
        user = SimpleNamespace(id=uuid.uuid4(), email="person@example.test", role=role)

        async def current_user():
            return user

        app.dependency_overrides[get_current_user] = current_user
    return app


async def get(app: FastAPI, path: str, **params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, params=params)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/exec/cost/overview", "/api/v1/exec/cost/details"])
async def test_cost_routes_require_an_admin(path, monkeypatch):
    monkeypatch.setattr("api.deps.emit_security_event", AsyncMock())

    assert (await get(make_app(None), path)).status_code == 401
    assert (await get(make_app(UserRole.reviewer), path)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/exec/cost/overview", "/api/v1/exec/cost/details"])
async def test_cost_routes_reject_unknown_ranges(path):
    response = await get(make_app(UserRole.admin), path, range="1y")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cost_routes_return_503_when_usage_data_is_unavailable(monkeypatch):
    monkeypatch.setattr(routes.service, "build_overview", AsyncMock(side_effect=CostDataUnavailableError))
    monkeypatch.setattr(routes.service, "build_details", AsyncMock(side_effect=CostDataUnavailableError))
    app = make_app(UserRole.admin)

    for path in ("/api/v1/exec/cost/overview", "/api/v1/exec/cost/details"):
        response = await get(app, path)
        assert response.status_code == 503
        assert response.json() == {"detail": "Usage data is temporarily unavailable. Try again shortly."}


@pytest.mark.asyncio
async def test_cost_overview_passes_the_range_and_serializes_the_response(monkeypatch):
    from tests.test_cost_service import NOW, install

    install(monkeypatch)
    real_build = routes.service.build_overview

    async def build_at_fixed_time(range_key):
        return await real_build(range_key, now=NOW)

    build = AsyncMock(side_effect=build_at_fixed_time)
    monkeypatch.setattr(routes.service, "build_overview", build)

    response = await get(make_app(UserRole.admin), "/api/v1/exec/cost/overview", range="7d")

    assert response.status_code == 200
    build.assert_awaited_once_with("7d")
    body = response.json()
    assert body["range"]["key"] == "7d"
    assert set(body) == {"range", "currency", "kpis", "coverage", "series", "model_share", "harnesses"}
