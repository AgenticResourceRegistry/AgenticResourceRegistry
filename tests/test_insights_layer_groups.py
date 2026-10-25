# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Layer groups for the version impact section of insight reports."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import pytest


class _Response:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"data": self._rows}


@pytest.mark.asyncio
async def test_layer_groups_query_never_aliases_the_version_expression_as_its_column():
    from services.insights import version_impact

    query = AsyncMock(return_value=_Response([]))
    with patch.object(version_impact, "get_query", return_value=query):
        await version_impact.detect_layer_groups("agent-id", "2026-10-01", "2026-10-20", agent_version="1.0.0")

    sql = query.await_args.args[0]
    # ClickHouse resolves agent_version inside the expression to an alias of the same
    # name, and the GROUP BY then fails (Code 215), so no report had version impact.
    assert not re.search(r"\)\s+AS\s+agent_version\b", sql)
    assert re.search(r"GROUP BY\s+version_key,\s+layer_hash", sql)


@pytest.mark.asyncio
async def test_layer_groups_rows_report_their_version():
    from services.insights import version_impact

    row = {"version_key": "1.2.0", "layer_hash": "abc", "sessions": "4", "users": "2", "success_proxy": "0.75"}
    query = AsyncMock(return_value=_Response([row]))
    with patch.object(version_impact, "get_query", return_value=query):
        groups = await version_impact.detect_layer_groups("agent-id", "2026-10-01", "2026-10-20")

    assert [(g["agent_version"], g["layer_hash"], g["sessions"], g["success_proxy"]) for g in groups] == [
        ("1.2.0", "abc", 4, 0.75)
    ]
