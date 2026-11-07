# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Runtime reconciliation uses the same explicit Kiro home as the CLI."""

import json
from pathlib import Path

from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.harness.kiro import kiro_cli_engine
from observal_cli.harness_specs.kiro_hooks_spec import build_kiro_hooks_file
from observal_cli.sessions.kiro import find_kiro_jsonl, read_kiro_credits


def test_custom_home_discovery_credits_and_standalone_coverage(tmp_path, monkeypatch):
    """A relocated native transcript resolves without confusing Observal's own auth home."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    custom = tmp_path / "custom-kiro"
    monkeypatch.setenv("KIRO_HOME", str(custom))
    path = custom / "sessions/workspace/session/messages.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"payload": {"type": "usage_summary", "promptTurnSummaries": [{"unit": "credit", "usage": 2}]}})
        + "\n"
    )
    path.with_name("session.json").write_text(json.dumps({"workspacePaths": [str(tmp_path)]}))
    hooks = custom / "hooks"
    hooks.mkdir()
    (hooks / "observal.json").write_text(json.dumps(build_kiro_hooks_file()))
    assert find_kiro_jsonl("session") == path
    assert read_kiro_credits("session") == 2
    ensure_loaded()
    adapter = get_adapter("kiro")
    source = adapter.resolve_session_source({"session_id": "session"})
    assert source.path == path
    assert adapter.discover_session_sources()[0].path == path
    assert not adapter.accepts_session_hook(source, {"hook_event_name": "Stop"}, "inline")


def test_custom_home_settings_with_workspace_precedence(tmp_path, monkeypatch):
    """Saved engine detection shares the custom root and still prioritizes workspace settings."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    custom = tmp_path / "custom-kiro"
    monkeypatch.setenv("KIRO_HOME", str(custom))
    monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "2.35.0")
    monkeypatch.delenv("OBSERVAL_KIRO_ENGINE", raising=False)
    settings = custom / "settings/cli.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"chat.agentEngine": "v3"}))
    project = tmp_path / "project"
    project.mkdir()
    assert kiro_cli_engine(cwd=project) == "v3"
    local = project / ".kiro/settings/cli.json"
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"chat.agentEngine": "v2"}))
    assert kiro_cli_engine(cwd=project) == "v2"
