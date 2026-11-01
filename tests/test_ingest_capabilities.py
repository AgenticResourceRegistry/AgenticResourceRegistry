# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Session ingest: the capabilities_used field and its ClickHouse insert."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from api.routes.ingest import MAX_CAPABILITIES_PER_PUSH, CapabilityUsed, SessionIngestRequest
from services.clickhouse import insert as ch_insert

URN = "urn:air:observal.example.com:skill:0f3c4d5e-6a7b-4c8d-9e0f-1a2b3c4d5e6f"


def _use(**overrides):
    base = {
        "identifier": URN,
        "kind": "skill",
        "component_id": "0f3c4d5e-6a7b-4c8d-9e0f-1a2b3c4d5e6f",
        "native_ref": "acme/security-review@1.2.0",
        "version": "1.2.0",
        "digest": "sha256:abc",
        "mode": "context",
        "source": "discover-cli",
        "used_at": "2026-09-20T12:00:00Z",
        "confidence": "window",
    }
    base.update(overrides)
    return base


def test_ingest_request_accepts_capabilities_used():
    req = SessionIngestRequest(session_id="s-1", lines=["{}"], capabilities_used=[_use()])
    assert req.capabilities_used[0].identifier == URN
    assert SessionIngestRequest(session_id="s-1", lines=["{}"]).capabilities_used is None


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "spaceship"},
        {"mode": "teleport"},
        {"confidence": "maybe"},
        {"identifier": None, "component_id": None, "native_ref": None},
    ],
)
def test_capability_used_rejects_bad_values(bad):
    with pytest.raises(ValidationError):
        CapabilityUsed(**_use(**bad))


def test_capabilities_used_is_bounded():
    with pytest.raises(ValidationError):
        SessionIngestRequest(
            session_id="s-1", lines=["{}"], capabilities_used=[_use()] * (MAX_CAPABILITIES_PER_PUSH + 1)
        )


@pytest.mark.asyncio
async def test_insert_session_capabilities_writes_one_row_per_use(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_query(sql, params=None, *, data=None):
        calls.append((sql, data))
        return SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(ch_insert._client, "_query", fake_query)
    await ch_insert.insert_session_capabilities(
        session_id="s-1",
        project_id="default",
        user_id="u-1",
        harness="kiro",
        uses=[
            _use(),
            _use(identifier=None, kind="mcp", component_id="m-1", native_ref="acme/github@1.4.2", mode="next-session"),
        ],
    )
    assert len(calls) == 1
    sql, data = calls[0]
    assert sql.startswith("INSERT INTO session_capabilities")
    rows = [json.loads(line) for line in data.split("\n")]
    assert [r["kind"] for r in rows] == ["skill", "mcp"]
    assert rows[0]["identifier"] == URN and rows[0]["used_at"] == "2026-09-20 12:00:00.000"
    assert rows[1]["identifier"] == "" and rows[1]["mode"] == "next-session"
    assert rows[0]["session_id"] == "s-1" and rows[0]["harness"] == "kiro"


@pytest.mark.asyncio
async def test_insert_session_capabilities_swallows_clickhouse_errors(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("clickhouse down")

    monkeypatch.setattr(ch_insert._client, "_query", boom)
    await ch_insert.insert_session_capabilities(
        session_id="s", project_id="p", user_id="u", harness="pi", uses=[_use()]
    )


@pytest.mark.asyncio
async def test_insert_session_capabilities_noop_on_empty(monkeypatch):
    called = False

    async def fake_query(*_a, **_k):
        nonlocal called
        called = True

    monkeypatch.setattr(ch_insert._client, "_query", fake_query)
    await ch_insert.insert_session_capabilities(session_id="s", project_id="p", user_id="u", harness="pi", uses=[])
    assert called is False
