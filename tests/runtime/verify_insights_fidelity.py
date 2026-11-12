# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Run inside a DISPOSABLE API container; writes unique synthetic sessions/cache rows.

    docker exec -i -e INSIGHTS_DISPOSABLE_VERIFY=1 insights-fidelity-observal-api-1 /app/.venv/bin/python < tests/runtime/verify_insights_fidelity.py

No model calls or historical repairs. Never run this against a production database.
"""

import asyncio
import json
import os
import uuid

from sqlalchemy import func, select

from database import async_session
from models.insight_session_facets import InsightSessionFacets
from services.insights import configure_insights
from services.insights.facets import load_cached_facets_batch, store_facets
from services.insights.scope import SessionKey
from services.insights.session_meta_extractor import extract_session_meta
from services.insights.transcript import build_session_transcript
from services.session_ingest import ingest_session_lines


async def main():
    """Verify shared excerpts/cache; opt into Codex only with canonical #2035 present."""
    if os.environ.get("INSIGHTS_DISPOSABLE_VERIFY") != "1":
        raise RuntimeError("Set INSIGHTS_DISPOSABLE_VERIFY=1 only in a disposable stack")
    configure_insights()
    user = str(uuid.uuid4())
    cases = {
        "kiro": [
            {"kind": "Prompt", "data": {"content": [{"kind": "text", "data": "Find the missing file"}]}},
            {
                "kind": "AssistantMessage",
                "data": {
                    "content": [
                        {"kind": "toolUse", "data": {"name": "read", "toolUseId": "t", "input": {"path": "missing.py"}}}
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
                                "toolUseId": "t",
                                "status": "error",
                                "content": [{"kind": "text", "data": "No such file"}],
                            },
                        }
                    ]
                },
            },
            {
                "kind": "AssistantMessage",
                "data": {"content": [{"kind": "text", "data": "Verification: file missing; Unverified: not run"}]},
            },
        ],
        "codex": [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Find the missing file"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "t",
                    "arguments": '{"cmd":"cat missing.py"}',
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "t",
                    "output": "No such file\nProcess exited with code 1",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Verification: file missing; Unverified: not run"}],
                },
            },
        ],
    }
    cases["kiro-ide"] = [
        {"payload": {"type": "user", "content": "Find the missing file"}},
        {"payload": {"type": "tool_call", "toolName": "fs_read", "toolCallId": "t", "args": {"path": "missing.py"}}},
        {"payload": {"type": "tool_result", "toolCallId": "t", "success": False, "content": "No such file"}},
        {"payload": {"type": "assistant", "content": "Verification: file missing; Unverified: not run"}},
    ]
    cases["pi"] = [
        {"type": "message", "message": {"role": "user", "content": "Find the missing file"}},
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "toolCall", "name": "read", "id": "t", "arguments": {"path": "missing.py"}}],
            },
        },
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolName": "read",
                "toolCallId": "t",
                "isError": True,
                "content": [{"type": "text", "text": "No such file"}],
            },
        },
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Verification: file missing; Unverified: not run"}],
            },
        },
    ]
    if os.environ.get("INSIGHTS_WITH_CODEX") != "1":
        cases.pop("codex")  # This shared PR intentionally contains no Codex converter.
    for case, entries in cases.items():
        harness = "kiro" if case == "kiro-ide" else case
        sid = "insights-fidelity-" + str(uuid.uuid4())
        for i, entry in enumerate(entries):
            entry["timestamp"] = f"2026-11-06T10:00:{i:02d}Z"
        lines = [json.dumps(e) for e in entries]
        ingested = await ingest_session_lines(sid, "default", user, None, None, harness=harness, lines=lines)
        assert ingested.errors == 0, ingested
        key = SessionKey("default", user, harness, sid)
        text = await build_session_transcript(key)
        assert "Find the missing file" in text and "Verification:" in text and "Unverified:" in text, text
        assert "ERROR:" in text and "No such file" in text, text
        meta = extract_session_meta(sid, lines, harness)
        assert meta["user_message_count"] == 1 and meta["tool_errors"] == 1, meta
        async with async_session() as db:
            await store_facets(key, "", {"brief_summary": "first"}, db, source_hash=meta["source_hash"])
            await db.commit()
            assert key in await load_cached_facets_batch([key], db, source_hashes={key: meta["source_hash"]})
            assert not await load_cached_facets_batch([key], db, source_hashes={key: "session-grew"})
            await store_facets(key, "", {"brief_summary": "updated"}, db, source_hash="session-grew")
            await db.commit()
            count = await db.scalar(
                select(func.count())
                .select_from(InsightSessionFacets)
                .where(InsightSessionFacets.user_id == user, InsightSessionFacets.session_id == sid)
            )
            assert count == 1, count
        print(
            json.dumps(
                {
                    "harness": harness,
                    "case": case,
                    "session": sid,
                    "ingested": ingested.ingested,
                    "transcript": "passed",
                    "metadata": "passed",
                    "cache": "passed",
                }
            )
        )


asyncio.run(main())
