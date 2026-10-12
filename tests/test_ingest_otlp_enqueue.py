# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Ingest queues OTLP forwarding after storing a push, and never fails because of it."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import services.session_ingest as session_ingest
from api.deps import get_current_user
from api.ratelimit import limiter
from api.routes import ingest
from models.user import UserRole
from services.otel.types import SessionKey

USER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture(autouse=True)
def _disable_rate_limits():
    enabled = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = enabled


@pytest.fixture
def stored(monkeypatch):
    """Stub the storage side of ingest; these tests are about what happens after it."""
    monkeypatch.setattr(
        session_ingest,
        "ingest_session_lines",
        AsyncMock(return_value=SimpleNamespace(ingested=0, skipped=0, errors=0)),
    )
    monkeypatch.setattr(session_ingest, "advance_session_checkpoint", AsyncMock(return_value=(1, 20)))
    integrity = SimpleNamespace(ok=False, repair_from_line=1, repair_offset=10, server_hash="x")
    monkeypatch.setattr(session_ingest, "check_session_integrity", AsyncMock(return_value=integrity))
    monkeypatch.setattr("services.clickhouse.insert_session_checkpoint", AsyncMock())


@pytest.fixture
def queued(monkeypatch):
    calls: list[SessionKey] = []

    async def fake_enqueue(key):
        calls.append(key)
        return 1

    monkeypatch.setattr("services.otel.forwarder.enqueue_forwarding", fake_enqueue)
    return calls


async def _post(body: dict) -> int:
    app = FastAPI()
    app.include_router(ingest.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=USER_ID, email="dev@example.test", role=UserRole.user
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/ingest/session", json=body)
    await asyncio.gather(*ingest._background_tasks)
    return response.status_code


async def test_a_push_with_lines_queues_forwarding_for_that_session(stored, queued):
    status = await _post({"session_id": "sess-1", "harness": "kiro", "lines": ["{}", "{}"]})

    assert status == 200
    assert queued == [SessionKey(project_id="default", user_id=str(USER_ID), harness="kiro", session_id="sess-1")]


async def test_an_integrity_repair_queues_forwarding_even_without_lines(stored, queued):
    status = await _post(
        {"session_id": "sess-1", "harness": "claude-code", "lines": [], "final": True, "total_line_count": 3}
    )

    assert status == 200
    assert len(queued) == 1


async def test_an_empty_push_without_repair_queues_nothing(stored, queued):
    status = await _post({"session_id": "sess-1", "harness": "claude-code", "lines": []})

    assert status == 200
    assert queued == []


async def test_ingest_succeeds_when_queueing_fails(stored, monkeypatch):
    async def broken(_key):
        raise ConnectionError("redis down")

    monkeypatch.setattr("services.otel.forwarder.enqueue_forwarding", broken)

    status = await _post({"session_id": "sess-1", "harness": "claude-code", "lines": ["{}"]})

    assert status == 200
