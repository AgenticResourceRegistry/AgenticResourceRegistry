# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

from typer.main import get_command
from typer.testing import CliRunner

from observal_cli.harness import SessionSource
from observal_cli.main import app

runner = CliRunner()


def test_public_reconcile_drains_outbox_then_all_detected_adapters(monkeypatch):
    from observal_cli import cmd_reconcile_cli

    calls: list[str] = []

    class Adapter:
        def __init__(self, installed: bool):
            self.installed = installed

        def is_installed(self):
            return self.installed

    monkeypatch.setattr(cmd_reconcile_cli, "load_config", lambda: {"user_id": "user"})
    monkeypatch.setattr(cmd_reconcile_cli, "ensure_loaded", lambda: None)
    monkeypatch.setattr(
        cmd_reconcile_cli,
        "drain_outbox",
        lambda _config, **_kwargs: calls.append("outbox") or True,
    )
    monkeypatch.setattr(
        cmd_reconcile_cli,
        "get_all_adapters",
        lambda: {"claude-code": Adapter(True), "kiro": Adapter(True), "pi": Adapter(False)},
    )

    def reconcile_harness(harness, *_args):
        calls.append(harness)
        return {
            "harness": harness,
            "discovered": 1,
            "pushed": 1,
            "finalized": 0,
            "queued": 0,
            "rejected": 0,
            "would_push": 0,
            "would_finalize": 0,
            "up_to_date": 0,
            "skipped": 0,
            "errors": 0,
            "sessions": [],
        }

    monkeypatch.setattr(cmd_reconcile_cli, "_reconcile_harness", reconcile_harness)

    cmd_reconcile_cli.reconcile(harness="", since_hours=24, dry_run=False)

    assert calls == ["outbox", "claude-code", "kiro"]


def test_public_reconcile_discovers_claude_and_kiro_fixtures(tmp_path: Path, monkeypatch):
    from observal_cli import cmd_reconcile_cli

    claude = tmp_path / ".claude" / "projects" / "-work" / "claude-session.jsonl"
    kiro = tmp_path / ".kiro" / "sessions" / "cli" / "kiro-session.jsonl"
    claude.parent.mkdir(parents=True)
    kiro.parent.mkdir(parents=True)
    claude.write_text("{}\n")
    kiro.write_text("{}\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cmd_reconcile_cli, "load_config", lambda: {"user_id": "user"})
    monkeypatch.setattr(cmd_reconcile_cli, "drain_outbox", lambda _config, **_kwargs: True)
    delivered: list[str] = []
    monkeypatch.setattr(
        cmd_reconcile_cli,
        "drain_session_source",
        lambda source, *_args, **_kwargs: delivered.append(source.harness) or True,
    )

    cmd_reconcile_cli.reconcile(harness="", since_hours=24, dry_run=False)

    assert sorted(delivered) == ["claude-code", "kiro"]


def test_background_recovery_uses_adapter_sources_and_shared_drain(tmp_path: Path, monkeypatch):
    from observal_cli.hooks import session_push

    old_source = tmp_path / "old.jsonl"
    old_source.write_text("{}\n")
    os.utime(old_source, (time.time() - 300, time.time() - 300))
    finished_source = tmp_path / "finished.jsonl"
    finished_source.write_text("{}\n")
    os.utime(finished_source, (time.time() - 300, time.time() - 300))
    sources = [
        SessionSource("claude-code", "unfinished", old_source),
        SessionSource("claude-code", "finished", finished_source),
    ]

    class Adapter:
        def discover_session_sources(self, home=None):
            assert home == tmp_path
            return sources

        def should_capture_session(self, source, home=None):
            return True

        def related_session_sources(self, source, home=None):
            return []

        def aged_recovery_final(self):
            return True

        def session_extra_fields(self, source, event, final, home=None):
            return {"source": source.session_id}

    calls: list[str] = []
    finals: list[bool] = []
    monkeypatch.setattr(session_push, "ensure_loaded", lambda: None)
    monkeypatch.setattr(session_push, "get_adapter", lambda _harness: Adapter())
    monkeypatch.setattr(session_push, "load_config", lambda home=None: {"user_id": "user"})
    monkeypatch.setattr(session_push, "drain_outbox", lambda *_args, **_kwargs: calls.append("outbox") or True)
    monkeypatch.setattr(
        session_push,
        "read_cursor_state",
        lambda key, home=None: (finished_source.stat().st_size, 1, True) if key == "finished" else (0, 0, False),
    )
    monkeypatch.setattr(
        session_push,
        "drain_session_source",
        lambda source, *_args, **kwargs: (calls.append(source.session_id), finals.append(kwargs["final"])) and True,
    )

    session_push._recover_sessions("claude-code", home=tmp_path)

    assert calls == ["outbox", "unfinished"]
    assert finals == [True]


def test_reconcile_is_a_leaf_command_with_json_output():
    command = get_command(app).commands["reconcile"]

    assert type(command).__name__ == "TyperCommand"
    assert any(parameter.name == "output" for parameter in command.params)


def test_reconcile_dry_run_json_has_no_network_or_human_output(tmp_path, monkeypatch):
    from observal_cli import cmd_reconcile_cli

    transcript = tmp_path / "session.jsonl"
    transcript.write_text("{}\n")
    source = SessionSource("kiro", "session-1", transcript)

    class Adapter:
        def is_installed(self):
            return True

        def discover_session_sources(self, since_hours):
            assert since_hours == 24
            return [source]

        def related_session_sources(self, source):
            return []

    monkeypatch.setattr(cmd_reconcile_cli, "load_config", lambda: {"user_id": "user", "server_url": "http://server"})
    monkeypatch.setattr(cmd_reconcile_cli, "ensure_loaded", lambda: None)
    monkeypatch.setattr(cmd_reconcile_cli, "get_all_adapters", lambda: {"kiro": Adapter()})
    monkeypatch.setattr(cmd_reconcile_cli, "get_adapter", lambda _name: Adapter())
    monkeypatch.setattr(cmd_reconcile_cli, "read_cursor_state", lambda _key: (0, 0, False))
    drain = MagicMock(side_effect=AssertionError("dry run must not drain"))
    monkeypatch.setattr(cmd_reconcile_cli, "drain_outbox", drain)

    result = runner.invoke(
        app,
        ["reconcile", "--harness", "kiro", "--since", "24", "--dry-run", "--output", "json"],
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["outbox_drained"] is None
    assert payload["summary"]["would_push"] == 1
    assert payload["targets"][0]["sessions"] == [{"session_id": "session-1", "status": "would_push", "bytes_new": 3}]
    drain.assert_not_called()


def test_reconcile_json_validation_happens_before_outbox_side_effects(monkeypatch):
    from observal_cli import cmd_reconcile_cli

    monkeypatch.setattr(cmd_reconcile_cli, "load_config", lambda: {"user_id": "user"})
    monkeypatch.setattr(cmd_reconcile_cli, "ensure_loaded", lambda: None)
    monkeypatch.setattr(cmd_reconcile_cli, "get_all_adapters", lambda: {"kiro": object()})
    drain = MagicMock(side_effect=AssertionError("must validate first"))
    monkeypatch.setattr(cmd_reconcile_cli, "drain_outbox", drain)

    result = runner.invoke(app, ["reconcile", "--harness", "unknown", "--output", "json"])

    assert result.exit_code == 7
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"
    drain.assert_not_called()


def test_reconcile_json_requires_session_delivery_identity(monkeypatch):
    from observal_cli import cmd_reconcile_cli

    monkeypatch.setattr(cmd_reconcile_cli, "load_config", lambda: {"server_url": "http://server"})

    result = runner.invoke(app, ["reconcile", "--output", "json"])

    assert result.exit_code == 3
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "authentication"


def test_reconcile_finalizes_fully_uploaded_unfinished_session(tmp_path, monkeypatch):
    from observal_cli import cmd_reconcile_cli

    transcript = tmp_path / "session.jsonl"
    transcript.write_text("{}\n")
    source = SessionSource("kiro", "session-1", transcript)

    class Adapter:
        def discover_session_sources(self, since_hours):
            return [source]

        def related_session_sources(self, source):
            return []

        def session_extra_records(self, source, event, final):
            return ()

    drain = MagicMock(return_value=True)
    monkeypatch.setattr(cmd_reconcile_cli, "get_adapter", lambda _name: Adapter())
    monkeypatch.setattr(cmd_reconcile_cli, "read_cursor_state", lambda _key: (3, 1, False))
    monkeypatch.setattr(cmd_reconcile_cli, "recover_cursor_from_server", lambda _source, _cfg: (3, 1))
    monkeypatch.setattr(cmd_reconcile_cli, "drain_session_source", drain)

    result = cmd_reconcile_cli._reconcile_harness("kiro", {"user_id": "user"}, 24, False)

    assert result["finalized"] == 1
    assert result["sessions"] == [{"session_id": "session-1", "status": "finalized", "bytes_new": 0}]
    drain.assert_called_once()
    assert drain.call_args.kwargs["final"] is True


def test_reconcile_reports_permanent_rejection(tmp_path, monkeypatch):
    from observal_cli import cmd_reconcile_cli

    transcript = tmp_path / "session.jsonl"
    transcript.write_text("{}\n")
    source = SessionSource("kiro", "session-1", transcript)

    class Adapter:
        def discover_session_sources(self, since_hours):
            return [source]

        def related_session_sources(self, source):
            return []

        def session_extra_records(self, source, event, final):
            return ()

    def reject(_source, _cfg, **kwargs):
        kwargs["rejections"].append(("kiro", "session-1", 422))
        return True

    rejections = []
    monkeypatch.setattr(cmd_reconcile_cli, "get_adapter", lambda _name: Adapter())
    monkeypatch.setattr(cmd_reconcile_cli, "read_cursor_state", lambda _key: (0, 0, False))
    monkeypatch.setattr(cmd_reconcile_cli, "recover_cursor_from_server", lambda _source, _cfg: (0, 0))
    monkeypatch.setattr(cmd_reconcile_cli, "drain_session_source", reject)

    result = cmd_reconcile_cli._reconcile_harness("kiro", {"user_id": "user"}, 24, False, rejections)

    assert result["pushed"] == 0
    assert result["rejected"] == 1
    assert result["sessions"][0]["status"] == "rejected"
    assert rejections == [("kiro", "session-1", 422)]
