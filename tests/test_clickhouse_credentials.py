# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClickHouse credentials must never ride in the URL: raise_for_status() prints the URL and callers log it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def post(self, url, content=None, params=None, headers=None, **_kwargs):
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        return SimpleNamespace(status_code=200, text="")


@pytest.fixture
def recorder(monkeypatch):
    from services.clickhouse import client

    recorded = _Recorder()
    monkeypatch.setattr(client, "_get_client", lambda: recorded)
    monkeypatch.setattr(client, "CLICKHOUSE_USER", "proof")
    monkeypatch.setattr(client, "CLICKHOUSE_PASSWORD", "s3cret-value")
    return recorded


def _assert_credentials_only_in_headers(call: dict) -> None:
    assert "password" not in call["params"] and "user" not in call["params"]
    assert "s3cret-value" not in call["url"]
    assert call["headers"] == {"X-ClickHouse-User": "proof", "X-ClickHouse-Key": "s3cret-value"}


@pytest.mark.asyncio
async def test_queries_send_credentials_as_headers(recorder):
    from services.clickhouse.client import _query

    await _query("SELECT 1")
    _assert_credentials_only_in_headers(recorder.calls[0])


@pytest.mark.asyncio
async def test_migration_runner_sends_credentials_as_headers(recorder):
    from services.clickhouse import migrations

    await migrations._query_server("SELECT 1")
    _assert_credentials_only_in_headers(recorder.calls[0])
