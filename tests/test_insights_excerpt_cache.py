# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Bounded excerpts and cache provenance must not silently erase evidence."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.insights.scope import SessionKey
from services.insights.session_meta_extractor import extract_session_meta
from services.insights.transcript import MAX_ASSISTANT_CHARS, MAX_PROMPT_CHARS, _format_rows


def test_cli_delegation_task_survives_long_instructions():
    text = (
        "You are acting as the agent `trial`. Follow its instructions.\n\n"
        "<agent-instructions>\n"
        + "role instructions " * 400
        + "\n</agent-instructions>\n\n<task>\nWrite a candidate test. Do not build Docker.\n</task>"
    )
    result = _format_rows([{"event_type": "user_prompt", "raw_line": {"content": text}}])
    assert "Write a candidate test. Do not build Docker." in result
    assert "role instructions" not in result
    assert len(result) <= MAX_PROMPT_CHARS + len("[User]: ")


def test_long_final_answer_retains_tail_and_discloses_loss():
    text = "Candidate test: test_x.py\n" + "evidence " * 1200 + "\nVerification: pytest test_x.py\nUnverified: not run"
    result = _format_rows([{"event_type": "assistant_text", "raw_line": {"content": text}}])
    assert "Candidate test" in result and "Verification:" in result and "Unverified:" in result
    assert "middle omitted" in result
    assert len(result) <= MAX_ASSISTANT_CHARS + len("[Assistant]: ")


def test_source_hash_changes_when_session_grows():
    rows = [json.dumps({"type": "message", "message": {"role": "user", "content": "task"}})]
    first = extract_session_meta("s", rows, "pi")["source_hash"]
    assert first == extract_session_meta("s", rows, "pi")["source_hash"]
    rows.append(json.dumps({"type": "message", "message": {"role": "assistant", "content": []}}))
    assert first != extract_session_meta("s", rows, "pi")["source_hash"]


@pytest.mark.asyncio
async def test_source_mismatch_invalidates_current_version_cache(monkeypatch):
    from models.insight_session_facets import InsightSessionFacets
    from services.insights import facets

    key = SessionKey("p", "u", "pi", "s")
    row = SimpleNamespace(
        project_id="p",
        user_id="u",
        harness="pi",
        session_id="s",
        facet_version=3,
        facets={"brief_summary": "old", "_source_hash": "old-hash"},
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row])))
    )
    monkeypatch.setattr(facets, "get_facets_model", lambda: InsightSessionFacets)
    assert not await facets.load_cached_facets_batch([key], db, source_hashes={key: "new-hash"})
    assert key in await facets.load_cached_facets_batch([key], db, source_hashes={key: "old-hash"})


def test_pi_and_claude_native_tool_errors_are_preserved():
    rows = [
        {
            "event_type": "tool_result",
            "raw_line": {
                "type": "message",
                "message": {
                    "role": "toolResult",
                    "isError": True,
                    "content": [{"type": "text", "text": "Pi tool failed"}],
                },
            },
        },
        {
            "event_type": "tool_result",
            "raw_line": {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "is_error": True, "content": "Claude tool failed"}],
                },
            },
        },
    ]
    result = _format_rows(rows)
    assert "ERROR: Pi tool failed" in result and "ERROR: Claude tool failed" in result
