# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _meta(session_id: str, duration: int, tool_count: int) -> dict:
    return {
        "session_id": session_id,
        "total_messages": 3,
        "duration_seconds": duration,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_cost": 0.0,
        "credits": 0.0,
        "lines_added": 0,
        "lines_removed": 0,
        "files_modified": 0,
        "git_commits": 0,
        "git_pushes": 0,
        "tool_errors": 0,
        "user_interruptions": 0,
        "uses_subagent": False,
        "uses_mcp": False,
        "tool_counts": {"bash": tool_count} if tool_count else {},
        "languages": {},
        "model_usage": {},
        "tool_error_categories": {},
        "project_path": "/repo/app",
        "user_response_times": [],
        "message_hours": [],
        "start_time": "2026-01-01T00:00:00Z",
        "harness": "pi",
        "user_message_count": 2,
    }


@pytest.mark.asyncio
async def test_generate_report_uses_cached_facets_outside_top_sessions(monkeypatch):
    import services.dynamic_settings as ds
    from services.insights import generator

    cached_facet = {
        "underlying_goal": "fix a bug",
        "goal_categories": ["fix_bug"],
        "outcome": "mostly_achieved",
        "user_satisfaction": "satisfied",
        "agent_helpfulness": "very_helpful",
        "session_type": "single_task",
        "complexity": "low",
        "friction_points": [],
        "primary_success_factors": ["correct_code_edits"],
        "tools_effective": ["bash"],
        "tools_problematic": [],
        "repeated_instructions": [],
        "brief_summary": "cached session counted",
    }
    top_metas = [_meta(f"top-{i}", duration=1000 - i, tool_count=2) for i in range(50)]
    metas = [*top_metas, _meta("cached-low", duration=1, tool_count=0)]

    load_cached = AsyncMock(return_value={"cached-low": cached_facet})
    monkeypatch.setattr(generator, "extract_all_session_metas", AsyncMock(return_value=metas))
    monkeypatch.setattr(generator, "load_cached_facets_batch", load_cached)
    monkeypatch.setattr(generator, "build_session_transcript", AsyncMock(return_value=""))
    monkeypatch.setattr(generator, "generate_sections", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(ds, "get", AsyncMock(return_value=5))

    report = await generator.generate_report_content(
        agent_name="agent",
        agent_id="agent-id",
        period_start="2026-01-01",
        period_end="2026-01-02",
        db=SimpleNamespace(),
    )

    assert "cached-low" in load_cached.await_args.args[0]
    assert report["facets_summary"]["sessions_with_facets"] == 1
    assert report["facets_summary"]["goal_categories"] == [("fix_bug", 1)]


@pytest.mark.asyncio
async def test_build_session_transcript_summarizes_long_sessions(monkeypatch):
    import services.dynamic_settings as ds
    from services.insights import transcript

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            rows = []
            for i in range(70):
                rows.append(
                    {
                        "line_offset": i,
                        "event_type": "user_prompt",
                        "tool_name": "",
                        "raw_line": '{"message":{"content":"' + ("x" * 600) + '"}}',
                    }
                )
            return {"data": rows}

    async def query(_sql: str, _params: dict) -> Response:
        return Response()

    summaries: list[str] = []

    async def call_model(prompt: str, **_kwargs) -> dict:
        summaries.append(prompt)
        return {"summary": f"summary {len(summaries)}"}

    monkeypatch.setattr(transcript, "get_query", lambda: query)
    monkeypatch.setattr(transcript, "get_call_model", lambda: call_model)
    monkeypatch.setattr(ds, "get", AsyncMock(return_value=None))

    result = await transcript.build_session_transcript("session-123456")

    assert summaries
    assert "[Long session summarized]" in result
    assert "summary 1" in result
    assert "[...truncated...]" not in result


@pytest.mark.asyncio
async def test_extract_facets_batch_never_uses_db_concurrently(monkeypatch):
    import asyncio

    from services.insights import generator

    class SingleUseSession:
        """Raises like an AsyncSession when two tasks use it at once."""

        def __init__(self) -> None:
            self.busy = False
            self.stored: list[str] = []

        async def store(self, session_id: str) -> None:
            if self.busy:
                raise RuntimeError("concurrent operations are not permitted")
            self.busy = True
            await asyncio.sleep(0)
            self.stored.append(session_id)
            self.busy = False

    async def extract(session_id: str, _transcript: str, _meta: dict) -> dict:
        await asyncio.sleep(0)
        return {"brief_summary": session_id}

    async def store(session_id: str, _agent_id: str, _facets: dict, db) -> None:
        await db.store(session_id)

    monkeypatch.setattr(generator, "extract_facets", extract)
    monkeypatch.setattr(generator, "store_facets", store)

    db = SingleUseSession()
    transcripts = {f"s{i}": "transcript" for i in range(14)}
    facets = await generator._extract_facets_batch(
        transcripts=transcripts,
        session_metas={},
        agent_id="",
        db=db,
        max_concurrent=5,
    )

    assert set(facets) == set(transcripts)
    assert sorted(db.stored) == sorted(transcripts)
