# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Kiro metrics and transcripts measure native records, not manufactured messages."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.insights.session_meta_extractor import extract_session_meta
from services.insights.transcript import _format_rows


def v3(kind, second, **fields):
    return {"id": f"r{second}", "timestamp": f"2026-10-29T10:00:{second:02d}Z", "payload": {"type": kind, **fields}}


V3 = [
    v3("user", 0, content="fix a.py"),
    v3("assistant", 1, operationType="Reasoning", content="...", reasoningSignature="sealed-private"),
    v3(
        "tool_call",
        2,
        toolName="fs_write",
        toolCallId="t1",
        args={
            "command": "str_replace",
            "path": "a.py",
            "old_str": "x\ny",
            "new_str": "z",
            "__tool_use_purpose": "private",
        },
    ),
    v3("tool_result", 3, toolCallId="t1", success=False, content="No such file"),
    v3("assistant", 4, operationType="Say", content="The file is missing."),
]
V2 = [
    {"kind": "Prompt", "data": {"meta": {"timestamp": 1793268000}, "content": [{"kind": "text", "data": "fix a.py"}]}},
    {
        "kind": "AssistantMessage",
        "data": {
            "content": [
                {"kind": "text", "data": "Checking."},
                {
                    "kind": "toolUse",
                    "data": {
                        "name": "edit",
                        "toolUseId": "t1",
                        "input": {"path": "a.py", "oldStr": "x\ny", "newStr": "z"},
                    },
                },
            ]
        },
    },
    {
        "kind": "ToolResults",
        "data": {
            "content": [
                {
                    "kind": "toolResult",
                    "data": {
                        "toolUseId": "t1",
                        "status": "error",
                        "content": [{"kind": "text", "data": "No such file"}],
                    },
                }
            ]
        },
    },
]


@pytest.mark.parametrize("records,assistant_count", [(V3, 2), (V2, 1)])
def test_both_native_shapes_measure_tools_errors_edits_and_messages(records, assistant_count):
    meta = extract_session_meta("s", [json.dumps(r) for r in records], "kiro")
    assert meta["user_message_count"] == 1
    assert meta["assistant_message_count"] == assistant_count
    assert meta["first_prompt"] == "fix a.py"
    assert sum(meta["tool_counts"].values()) == 1
    assert meta["tool_errors"] == 1
    assert meta["tool_error_categories"] == {"file_not_found": 1}
    assert (meta["lines_removed"], meta["lines_added"], meta["files_modified"]) == (2, 1, 1)
    assert meta["languages"] == {"Python": 1}
    assert meta["duration_seconds"] == (4 if records is V3 else 0)
    assert meta["input_tokens"] == meta["output_tokens"] == 0


@pytest.mark.parametrize("records,reply", [(V3, "The file is missing."), (V2, "Checking.")])
def test_transcripts_retain_assistant_text_each_tool_input_and_errors(records, reply):
    from services.session_parsers.ingest_classify import get_classifier

    classify, preview, tool_info = get_classifier("kiro")
    rows = [
        {
            "raw_line": json.dumps(r),
            "event_type": classify(r),
            "tool_name": tool_info(r)[0],
            "content_preview": preview(r, classify(r)),
        }
        for r in records
    ]
    text = _format_rows(rows, harness="kiro")
    assert "[User]: fix a.py" in text
    assert f"[Assistant]: {reply}" in text
    assert "file: a.py" in text
    assert "ERROR: No such file" in text
    assert text.count("[Tool:") == 1
    assert "private" not in text and "sealed" not in text
    assert "[Assistant]: ..." not in text


def test_repeated_tool_ids_do_not_double_count_and_orphans_still_measure():
    raw = [V3[2], V3[2], V3[3]]
    meta = extract_session_meta("s", [json.dumps(r) for r in raw], "kiro")
    assert sum(meta["tool_counts"].values()) == 1
    assert meta["assistant_message_count"] == 0
    assert meta["tool_errors"] == 1


def test_invalid_values_and_timestamps_do_not_break_measurement():
    raw = [
        "null",
        '{"kind":"Prompt","data":{"meta":{"timestamp":1e300},"content":[]}}',
        json.dumps(v3("tool_call", 1, toolName=None, args=[], toolCallId=[])),
        json.dumps(v3("tool_result", 2, content=None, success=False)),
        json.dumps(v3("assistant", 3, content=[])),
    ]
    meta = extract_session_meta("s", raw, "kiro")
    assert meta["user_message_count"] == 0
    assert meta["tool_errors"] == 1


@pytest.mark.asyncio
async def test_only_kiro_old_facets_are_ineligible(monkeypatch):
    from models.insight_session_facets import InsightSessionFacets
    from services.insights import facets
    from services.insights.scope import SessionKey

    monkeypatch.setattr(facets, "get_facets_model", lambda: InsightSessionFacets)
    keys = [SessionKey("p", "u", h, "s") for h in ["kiro", "claude-code"]]
    rows = [
        SimpleNamespace(
            project_id="p", user_id="u", harness=h, session_id="s", facet_version=version, facets={"version": version}
        )
        for h, version in [("kiro", 1), ("kiro", 2), ("claude-code", 1)]
    ]
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows)))
    )
    result = await facets.load_cached_facets_batch(keys, db)
    assert result[keys[0]] == {"version": 2}
    assert result[keys[1]] == {"version": 1}


def test_native_v3_fixture_matches_record_counts_without_tool_message_inflation():
    from pathlib import Path

    records = [
        json.loads(line)
        for line in (Path(__file__).parent / "fixtures/kiro/v3-messages.jsonl").read_text().splitlines()
    ]
    meta = extract_session_meta("native", [json.dumps(r) for r in records], "kiro")
    assert meta["user_message_count"] == sum(r["payload"]["type"] == "user" for r in records)
    assert meta["assistant_message_count"] == sum(r["payload"]["type"] == "assistant" for r in records)
    assert sum(meta["tool_counts"].values()) == len(
        {r["payload"]["toolCallId"] for r in records if r["payload"]["type"] == "tool_call"}
    )
    transcript = _format_rows([{"raw_line": json.dumps(r)} for r in records], harness="kiro")
    assert "[Assistant]: Checked a.py." in transcript
    assert "[Tool:" in transcript


@pytest.mark.asyncio
async def test_old_facet_row_is_updated_not_duplicated_when_version_changes(monkeypatch):
    from models.insight_session_facets import InsightSessionFacets
    from services.insights import facets
    from services.insights.scope import SessionKey

    monkeypatch.setattr(facets, "get_facets_model", lambda: InsightSessionFacets)
    existing = SimpleNamespace(facet_version=1, facets={"old": True})
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: existing)),
        add=Mock(),
        flush=AsyncMock(),
    )
    await facets.store_facets(SessionKey("p", "u", "kiro", "s"), "", {"new": True}, db)
    assert existing.facet_version == 2 and existing.facets == {"new": True}
    db.add.assert_not_called()
    assert "facet_version" not in str(db.execute.await_args.args[0].whereclause)
