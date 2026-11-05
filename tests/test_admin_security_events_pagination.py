# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression coverage for exact security event pagination counts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.routes.admin import policy
from services.user_search import UserFilterValues


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("events", "page_rows", "estimated_total", "exact_total"),
    [
        ([{"event_type": "login"}] * 50, 50, 60, 120),
        ([], 0, None, 120),
        ([{"event_type": "login"}], 1, None, 1),
        ([], 0, None, 0),
        ([], 0, None, "120"),
    ],
)
async def test_security_events_total_uses_exact_count(monkeypatch, events, page_rows, estimated_total, exact_total):
    import services.clickhouse as clickhouse

    page_response = MagicMock()
    page_response.json.return_value = {"data": events, "rows": page_rows}
    if estimated_total is not None:
        page_response.json.return_value["rows_before_limit_at_least"] = estimated_total
    count_response = MagicMock()
    count_response.json.return_value = {"data": [{"total": exact_total}]}
    query = AsyncMock(side_effect=[page_response, count_response])
    monkeypatch.setattr(clickhouse, "_query", query)

    result = await policy.get_security_events(
        event_type="login",
        severity="warning",
        actor_email=None,
        limit=50,
        offset=50,
        db=MagicMock(),
        current_user=SimpleNamespace(),
    )

    assert result == {"events": events, "total": int(exact_total)}
    assert isinstance(result["total"], int)
    page_response.raise_for_status.assert_called_once_with()
    count_response.raise_for_status.assert_called_once_with()
    assert query.await_count == 2
    page_sql, page_params = query.await_args_list[0].args
    count_sql, count_params = query.await_args_list[1].args
    assert "LIMIT 50 OFFSET 50 FORMAT JSON" in page_sql
    assert count_sql == (
        "SELECT count() AS total FROM security_events WHERE "
        "1 = 1 AND event_type = {et:String} AND severity = {sev:String} FORMAT JSON"
    )
    assert page_params == count_params == {"param_et": "login", "param_sev": "warning"}


@pytest.mark.asyncio
async def test_security_events_actor_filter_is_applied_to_count(monkeypatch):
    import services.clickhouse as clickhouse

    monkeypatch.setattr(
        policy, "resolve_user_filter_values", AsyncMock(return_value=UserFilterValues(ids=[], emails=[]))
    )
    page_response = MagicMock()
    page_response.json.return_value = {"data": [], "rows": 0}
    count_response = MagicMock()
    count_response.json.return_value = {"data": [{"total": 0}]}
    query = AsyncMock(side_effect=[page_response, count_response])
    monkeypatch.setattr(clickhouse, "_query", query)

    await policy.get_security_events(
        event_type=None,
        severity=None,
        actor_email="nobody@example.test",
        limit=20,
        offset=100,
        db=MagicMock(),
        current_user=SimpleNamespace(),
    )

    for call in query.await_args_list:
        sql, params = call.args
        assert "actor_email = {ae:String}" in sql
        assert params == {"param_ae": "nobody@example.test"}
