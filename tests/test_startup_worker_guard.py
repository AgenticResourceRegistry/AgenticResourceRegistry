# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Startup workers must not run CLI startup migrations or bundled-skill sync."""

from __future__ import annotations

import sys

import pytest
from typer.testing import CliRunner

from observal_cli import main, startup_update_apply
from observal_cli.main import app


@pytest.mark.parametrize(
    ("command", "target"),
    [("_startup-apply", "apply_pi"), ("_startup-apply-claude", "apply_claude")],
)
def test_startup_workers_skip_startup_migrations(monkeypatch: pytest.MonkeyPatch, command: str, target: str) -> None:
    calls: list[str] = []
    monkeypatch.setattr(main, "_migrate_legacy_mcp_configs", lambda: calls.append("mcp"))
    monkeypatch.setattr(main, "_try_lockfile_migration", lambda: calls.append("lockfile"))
    monkeypatch.setattr(startup_update_apply, target, lambda *_a, **_k: calls.append("worker"))
    argv = ["observal", command, "--cwd", "/tmp", "--session-id", "s", "--notice-key", "k"]
    monkeypatch.setattr(sys, "argv", argv)

    result = CliRunner().invoke(app, argv[1:])

    assert result.exit_code == 0, result.output
    assert calls == ["worker"], "a startup worker must not run migrations or sync bundled skills"
