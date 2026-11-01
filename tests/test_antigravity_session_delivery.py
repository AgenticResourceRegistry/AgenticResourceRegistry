# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from observal_cli import telemetry_buffer
from observal_cli.harness.antigravity import AntigravityAdapter
from observal_cli.hooks import session_push
from observal_cli.sessions import base

if TYPE_CHECKING:
    from pathlib import Path


def _transcript(home: Path, session_id: str = "session") -> Path:
    path = (
        home / ".gemini" / "antigravity-cli" / "brain" / session_id / ".system_generated" / "logs" / "transcript.jsonl"
    )
    path.parent.mkdir(parents=True)
    path.write_text('{"step_index":0,"type":"user","content":"hello"}\n')
    return path


def test_adapter_resolves_and_discovers_native_transcript(tmp_path: Path):
    transcript = _transcript(tmp_path)
    adapter = AntigravityAdapter()

    source = adapter.resolve_session_source(
        {
            "conversationId": "session",
            "transcriptPath": str(transcript),
            "workspacePaths": ["/project"],
            "invocationNum": 1,
        },
        home=tmp_path,
    )

    assert source is not None
    assert source.path == transcript
    assert source.cwd == "/project"
    assert adapter.discover_session_sources(home=tmp_path)[0].session_id == "session"
    assert adapter.defer_session_delivery()
    assert adapter.is_session_final({"terminationReason": "done"})


def test_hook_spools_before_detached_delivery(tmp_path: Path, monkeypatch):
    transcript = _transcript(tmp_path)
    config_dir = tmp_path / ".observal"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"server_url": "http://server", "access_token": "token", "user_id": "user"})
    )
    db = tmp_path / "outbox.db"
    monkeypatch.setattr(telemetry_buffer, "DB_PATH", db)
    monkeypatch.setattr(base, "_resolve_agent", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(base, "_get_cached_layer_hash", lambda *_args, **_kwargs: None)
    workers: list[tuple[tuple[str, ...], str]] = []
    monkeypatch.setattr(
        session_push,
        "_spawn_worker",
        lambda *args, harness: workers.append((args, harness)),
    )

    session_push._run_hook(
        {
            "conversationId": "session",
            "transcriptPath": str(transcript),
            "invocationNum": 1,
        },
        harness="antigravity",
        home=tmp_path,
    )

    assert base.read_cursor("session", home=tmp_path) == (0, 0)
    item = telemetry_buffer.pending(destination="http://server", user_id="user", db_path=db)[0]
    assert item.harness == "antigravity"
    assert item.payload["hook_event"] == "PreInvocation"
    assert item.start_line == item.end_line == 0
    assert workers[0] == (("--drain-outbox",), "antigravity")


def test_public_reconcile_uses_shared_drain(tmp_path: Path, monkeypatch):
    from observal_cli import cmd_reconcile_cli
    from observal_cli.harness import SessionSource

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n")
    source = SessionSource("antigravity", "session", transcript)

    class Adapter:
        def discover_session_sources(self, since_hours):
            assert since_hours == 24
            return [source]

        def related_session_sources(self, source):
            return []

        def session_extra_records(self, source, event, final):
            return ()

    calls = []
    monkeypatch.setattr(cmd_reconcile_cli, "get_adapter", lambda _harness: Adapter())
    monkeypatch.setattr(cmd_reconcile_cli, "read_cursor_state", lambda _key: (0, 0, False))
    monkeypatch.setattr(cmd_reconcile_cli, "recover_cursor_from_server", lambda source, cfg: (0, 0))
    monkeypatch.setattr(
        cmd_reconcile_cli,
        "drain_session_source",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    result = cmd_reconcile_cli._reconcile_harness("antigravity", {}, 24, False)

    assert result["pushed"] == 1
    assert result["sessions"] == [{"session_id": "session", "status": "pushed", "bytes_new": 3}]
    assert calls[0][1]["hook_event"] == "Reconcile"
    assert calls[0][1]["final"] is True
