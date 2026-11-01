# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from api.deps import get_db, get_registry_user
from api.routes.dashboard import require_leaderboard_enabled
from main import app
from services import dynamic_settings

SETTING = "deployment.leaderboard_enabled"
LEADERBOARD_PATHS = ["/api/v1/overview/leaderboard", "/api/v1/overview/component-leaderboard"]


class _EmptyResult:
    def all(self):
        return []

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return None


class _EmptyDb:
    async def execute(self, stmt):
        return _EmptyResult()


def _settings(stored: dict[str, bool]):
    async def _get_bool(key: str, default: bool = False) -> bool:
        return stored.get(key, default)

    return patch("services.dynamic_settings.get_bool", new=_get_bool)


async def _get(path: str, stored: dict[str, bool]):
    async def _mock_get_db():
        yield _EmptyDb()

    app.dependency_overrides[get_db] = _mock_get_db
    app.dependency_overrides[get_registry_user] = lambda: None
    try:
        with _settings(stored):
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as http:
                return await http.get(path)
    finally:
        app.dependency_overrides.clear()


def test_leaderboard_setting_is_default_off_and_described():
    assert dynamic_settings.DEFAULTS[SETTING] == "false"
    deployment = next(section for section in dynamic_settings.settings_schema() if section["id"] == "deployment")
    setting = next(item for item in deployment["settings"] if item["key"] == SETTING)
    assert setting["default"] == "false"
    assert "deprecated" in setting["subtitle"]


@pytest.mark.asyncio
async def test_public_config_reports_leaderboard_setting():
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value = MagicMock(all=lambda: [])
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)

    async def _mock_get_db():
        yield db

    app.dependency_overrides[get_db] = _mock_get_db
    try:
        for stored, expected in (({}, False), ({SETTING: True}, True)):
            with _settings(stored):
                async with AsyncClient(
                    transport=ASGITransport(app=app, raise_app_exceptions=False),
                    base_url="http://test",
                ) as http:
                    resp = await http.get("/api/v1/config/public")
            assert resp.status_code == 200
            assert resp.json()["leaderboard_enabled"] is expected
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_require_leaderboard_enabled_blocks_when_off():
    with _settings({}), pytest.raises(HTTPException) as exc_info:
        await require_leaderboard_enabled()
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_require_leaderboard_enabled_allows_when_on():
    with _settings({SETTING: True}):
        assert await require_leaderboard_enabled() is None


def test_both_leaderboard_routes_are_gated():
    for path in LEADERBOARD_PATHS:
        route = next(r for r in app.routes if getattr(r, "path", None) == path)
        assert any(dep.call is require_leaderboard_enabled for dep in route.dependant.dependencies), path


@pytest.mark.asyncio
@pytest.mark.parametrize("path", LEADERBOARD_PATHS)
async def test_leaderboard_api_returns_404_while_off(path):
    resp = await _get(path, {})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Leaderboard is disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", LEADERBOARD_PATHS)
async def test_leaderboard_api_serves_rankings_while_on(path):
    resp = await _get(f"{path}?window=all", {SETTING: True})
    assert resp.status_code == 200
    assert resp.json() == []
