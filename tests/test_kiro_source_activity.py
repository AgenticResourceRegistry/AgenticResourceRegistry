# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Activity time is independent of Kiro's prompt-only event timestamps."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.clickhouse import insert


@pytest.mark.asyncio
async def test_summary_tracks_source_arrival_not_credit_or_refresh_time(monkeypatch):
    response = MagicMock()
    response.raise_for_status.return_value = None
    query = AsyncMock(return_value=response)
    monkeypatch.setattr(insert._client, "_query", query)

    await insert.refresh_session_summary("sid", "pid", "uid", "kiro")

    sql, params = query.await_args.args
    assert "last_source_ingest_at" in sql
    assert "maxIf(ingested_at, is_source_record = 1 AND rendered = 1) AS last_source_ingest_at" in sql
    assert "maxIf(timestamp, rendered = 1" in sql  # Event time is not rewritten.
    assert "toUnixTimestamp64Micro(now64(6))" in sql  # Beats the older MV's millisecond version.
    assert params["param_harness"] == "kiro"


@pytest.mark.asyncio
async def test_list_uses_source_activity_only_for_kiro(monkeypatch):
    from api.routes import sessions

    query = AsyncMock(return_value=[])
    monkeypatch.setattr(sessions, "_ch_json", query)
    await sessions._list_sessions_query(platform="kiro", user_ids=None, days=7, is_admin=True, uid="uid")
    sql = query.await_args.args[0]
    activity = "if(harness = 'kiro', greatest(last_event_time, last_source_ingest_at), last_event_time)"
    assert f"({activity} > now() - INTERVAL 5 MINUTE) AS is_active" in sql
    assert f"{activity} > now() - INTERVAL 7 DAY" in sql
    assert f"ORDER BY {activity} DESC" in sql
