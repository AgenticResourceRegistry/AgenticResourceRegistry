# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from observal_cli import telemetry_buffer
from observal_cli.harness.copilot import CopilotAdapter
from observal_cli.harness.copilot_cli import CopilotCliAdapter
from observal_cli.sessions import base

if TYPE_CHECKING:
    from pathlib import Path


def _config() -> dict:
    return {"server_url": "http://server", "access_token": "token", "user_id": "user"}


def _disable_metadata(monkeypatch) -> None:
    monkeypatch.setattr(base, "_resolve_agent", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(base, "_get_cached_layer_hash", lambda *_args, **_kwargs: None)


def test_vscode_hook_events_are_materialized_and_recoverable(tmp_path: Path):
    adapter = CopilotAdapter()
    source = adapter.resolve_session_source(
        {
            "session_id": "session/one",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "hello",
            "timestamp": "2026-01-01T00:00:00Z",
        },
        home=tmp_path,
    )
    assert source is not None and source.path is not None

    final_source = adapter.resolve_session_source(
        {
            "session_id": "session/one",
            "hook_event_name": "Stop",
            "stop_reason": "done",
            "timestamp": "2026-01-01T00:01:00Z",
        },
        home=tmp_path,
    )

    records = [json.loads(line) for line in source.path.read_text().splitlines()]
    assert final_source == source
    assert [record["event"]["type"] for record in records] == ["user.message", "session.end"]
    assert adapter.discover_session_sources(home=tmp_path)[0].session_id == "session/one"


def test_vscode_materialized_source_uses_acknowledged_outbox(tmp_path: Path, monkeypatch):
    _disable_metadata(monkeypatch)
    adapter = CopilotAdapter()
    source = adapter.resolve_session_source(
        {
            "session_id": "session",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "hello",
            "timestamp": "2026-01-01T00:00:00Z",
        },
        home=tmp_path,
    )
    assert source is not None
    db = tmp_path / "outbox.db"

    assert base.drain_session_source(
        source,
        _config(),
        hook_event="UserPromptSubmit",
        spool_only=True,
        home=tmp_path,
        db_path=db,
    )
    assert base.read_cursor("session", home=tmp_path, destination="http://server") == (0, 0)
    item = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    assert item.harness == "copilot"
    assert item.start_line == item.end_line == 0

    def acknowledge(payload, _config):
        return {
            "acknowledged_line": payload["start_offset"] + len(payload["lines"]) - 1,
            "acknowledged_offset": payload["end_byte_offsets"][-1],
        }

    assert base.drain_outbox(_config(), home=tmp_path, db_path=db, post=acknowledge)
    assert base.read_cursor("session", home=tmp_path, destination="http://server") == (source.path.stat().st_size, 1)


def test_copilot_cli_resolves_and_discovers_native_jsonl(tmp_path: Path):
    session_file = tmp_path / ".copilot" / "session-state" / "cli-session" / "events.jsonl"
    session_file.parent.mkdir(parents=True)
    session_file.write_text('{"agentId":"a","ts":"t","event":{"type":"user.message","content":"hi"}}\n')
    adapter = CopilotCliAdapter()

    source = adapter.resolve_session_source({"sessionId": "cli-session", "cwd": "/project"}, home=tmp_path)

    assert source is not None
    assert source.path == session_file
    assert source.harness == "copilot-cli"
    discovered = adapter.discover_session_sources(home=tmp_path)
    assert [(item.session_id, item.path) for item in discovered] == [("cli-session", session_file)]
    assert adapter.defer_session_delivery()
