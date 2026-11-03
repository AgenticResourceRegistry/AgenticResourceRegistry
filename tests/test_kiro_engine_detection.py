# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Configured Kiro engines, CLI-only detection, and doctor health regressions."""

import json
from pathlib import Path

from observal_cli.harness import ensure_loaded
from observal_cli.harness.kiro import (
    kiro_cli_engine,
    kiro_cli_major,
    needs_inline_compatibility_hooks,
    use_inline_hooks,
)
from observal_cli.harness_specs.kiro_hooks_spec import build_kiro_hooks_file


def test_saved_v3_does_not_remove_v2_override_compatibility(tmp_path, monkeypatch):
    """Saved V3 chooses standalone health, but CLI 2.x still needs inline V2 compatibility."""
    (tmp_path / ".kiro/settings").mkdir(parents=True)
    (tmp_path / ".kiro/settings/cli.json").write_text('{"chat.agentEngine":"v3"}')
    monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "2.35.0")
    assert kiro_cli_engine(tmp_path) == "v3"
    assert not use_inline_hooks(tmp_path)
    assert needs_inline_compatibility_hooks(tmp_path)


def test_workspace_and_explicit_engine_precedence(tmp_path, monkeypatch):
    """Workspace and explicit testing/launch overrides take precedence over global settings."""
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    for root, engine in [(home, "v2"), (cwd, "v3")]:
        (root / ".kiro/settings").mkdir(parents=True)
        (root / ".kiro/settings/cli.json").write_text(json.dumps({"chat.agentEngine": engine}))
    assert kiro_cli_engine(home, cwd) == "v3"
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", "v2")
    assert kiro_cli_engine(home, cwd) == "v2"
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", "invalid")
    assert kiro_cli_engine(home, cwd) is None


def test_ide_launcher_is_never_used_as_cli_version(monkeypatch):
    """An IDE-only kiro executable cannot establish the CLI engine or version."""
    monkeypatch.delenv("OBSERVAL_KIRO_CLI_VERSION", raising=False)
    monkeypatch.setattr("observal_cli.harness.kiro.shutil.which", lambda name: "/ide/kiro" if name == "kiro" else None)
    assert kiro_cli_major() is None


def test_doctor_accepts_standalone_v3_on_2x(tmp_path, monkeypatch):
    """Doctor agrees with adapter health even when no home agents directory exists."""
    from observal_cli.cmd_doctor import _check_kiro

    (tmp_path / ".kiro/hooks").mkdir(parents=True)
    (tmp_path / ".kiro/hooks/observal.json").write_text(json.dumps(build_kiro_hooks_file()))
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", "v3")
    monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "2.35.0")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ensure_loaded()
    issues = []
    warnings = []
    _check_kiro(issues, warnings)
    assert not issues and not warnings
