# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Runs that never got a model response are left out of agent insights."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from services.insights import session_meta_extractor
from services.insights.scope import SessionKey
from tests.test_session_meta_harness_lines import CLAUDE_CODE, PI

# Pi 1.3 records a refused request (here a 401 from a bad key) as an empty assistant message.
PI_REFUSED = json.dumps(
    {
        "type": "message",
        "timestamp": "2026-10-19T10:00:02.000Z",
        "message": {
            "role": "assistant",
            "content": [],
            "model": "gpt-6-sol",
            "stopReason": "error",
            "errorMessage": "Azure OpenAI API error (401)",
            "usage": {"input": 0, "output": 0},
        },
    }
)
# Claude Code writes its own notice when it is not logged in.
CLAUDE_NOT_LOGGED_IN = json.dumps(
    {
        "type": "assistant",
        "timestamp": "2026-10-19T10:00:01.000Z",
        "isApiErrorMessage": True,
        "error": "authentication_failed",
        "message": {
            "id": "m-error",
            "role": "assistant",
            "model": "<synthetic>",
            "content": [{"type": "text", "text": "Not logged in · Please run /login"}],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    }
)


@pytest.mark.asyncio
async def test_sessions_with_prompts_but_no_response_are_skipped(monkeypatch):
    def key(session_id: str, harness: str) -> SessionKey:
        return SessionKey("default", "user-1", harness, session_id)

    transcripts = {
        key("pi-ok", "pi"): PI,
        # Pi and Claude Code runs that failed before reaching the model: the prompt is all there is.
        key("pi-failed", "pi"): PI[:2],
        key("claude-failed", "claude-code"): CLAUDE_CODE[:1],
        # Runs the provider refused leave an error notice instead of a response.
        key("pi-refused", "pi"): [*PI[:2], PI_REFUSED],
        key("claude-not-logged-in", "claude-code"): [CLAUDE_CODE[0], CLAUDE_NOT_LOGGED_IN],
        key("claude-ok", "claude-code"): CLAUDE_CODE,
        # A format the extractor cannot read counts no prompts either, so it is kept.
        key("cursor", "cursor"): [json.dumps({"role": "user", "text": "hi"})],
    }
    monkeypatch.setattr(session_meta_extractor, "fetch_all_session_transcripts", AsyncMock(return_value=transcripts))
    monkeypatch.setattr(session_meta_extractor, "fetch_session_stats", AsyncMock(return_value={}))

    metas = await session_meta_extractor.extract_all_session_metas("agent", "2026-10-01", "2026-10-20")

    assert [meta["session_id"] for meta in metas] == ["pi-ok", "claude-ok", "cursor"]
