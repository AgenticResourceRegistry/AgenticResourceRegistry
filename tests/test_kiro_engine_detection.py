# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Native global engine settings, CLI-only detection, and doctor health regressions."""

import json
from pathlib import Path

import pytest

from observal_cli.harness import ensure_loaded
from observal_cli.harness.kiro import (
    kiro_cli_engine,
    kiro_cli_major,
    needs_inline_compatibility_hooks,
    use_inline_hooks,
)
from observal_cli.harness_specs.kiro_hooks_spec import build_kiro_hooks_file


@pytest.fixture(autouse=True)
def isolated_engine_environment(monkeypatch):
    """Ambient overrides must not decide which native settings a test exercises."""
    for key in ("KIRO_HOME", "OBSERVAL_KIRO_ENGINE", "OBSERVAL_KIRO_CLI_VERSION"):
        monkeypatch.delenv(key, raising=False)


def test_saved_v3_does_not_remove_v2_override_compatibility(tmp_path, monkeypatch):
    """Saved V3 chooses standalone health, but CLI 2.x retains its V2 override guard."""
    (tmp_path / ".kiro/settings").mkdir(parents=True)
    (tmp_path / ".kiro/settings/cli.json").write_text('{"chat.agentEngine":"v3"}')
    monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "2.35.0")
    assert kiro_cli_engine(tmp_path) == "v3"
    assert not use_inline_hooks(tmp_path)
    assert needs_inline_compatibility_hooks(tmp_path)


@pytest.mark.parametrize("global_engine,workspace_engine", [("v2", "v3"), ("v3", "v2")])
def test_global_engine_ignores_workspace_and_explicit_override_wins(
    tmp_path, monkeypatch, global_engine, workspace_engine
):
    """Native chat.agentEngine is global-only even when workspace cli.json contains that key."""
    home, cwd = tmp_path / "home", tmp_path / "work"
    for root, engine in [(home, global_engine), (cwd, workspace_engine)]:
        (root / ".kiro/settings").mkdir(parents=True)
        (root / ".kiro/settings/cli.json").write_text(json.dumps({"chat.agentEngine": engine}))
    assert kiro_cli_engine(home, cwd) == global_engine
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", workspace_engine)
    assert kiro_cli_engine(home, cwd) == workspace_engine
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", "invalid")
    assert kiro_cli_engine(home, cwd) is None


@pytest.mark.parametrize("global_engine", ["v2", "v3"])
@pytest.mark.parametrize("version", [None, "2.35.0", "3.0.0"])
def test_inline_compatibility_uses_global_engine_and_keeps_2x_guard(tmp_path, monkeypatch, global_engine, version):
    """Workspace noise and missing PATH entries cannot override the native global choice."""
    home, caller, target = (tmp_path / name for name in ("home", "caller", "target"))
    for workspace, engine in [(home, global_engine), (caller, "v3"), (target, "v2")]:
        settings = workspace / ".kiro/settings/cli.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"chat.agentEngine": engine}))
    monkeypatch.setenv("KIRO_HOME", str(home / ".kiro"))
    if version is not None:
        monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", version)
    monkeypatch.setattr("observal_cli.harness.kiro.shutil.which", lambda _name: None)
    monkeypatch.chdir(caller)
    for cwd in (caller, target):
        assert use_inline_hooks(home, cwd=cwd) is (global_engine == "v2")
        assert needs_inline_compatibility_hooks(home, cwd=cwd) is (global_engine == "v2" or version == "2.35.0")


def test_ide_launcher_is_never_used_as_cli_version(monkeypatch):
    """An IDE-only kiro executable cannot establish the CLI engine or version."""
    monkeypatch.setattr("observal_cli.harness.kiro.shutil.which", lambda name: "/ide/kiro" if name == "kiro" else None)
    assert kiro_cli_major() is None


def test_doctor_accepts_standalone_v3_on_2x(tmp_path, monkeypatch):
    """Doctor agrees with adapter health even without a home agents directory."""
    from observal_cli.cmd_doctor import _check_kiro

    (tmp_path / ".kiro/hooks").mkdir(parents=True)
    (tmp_path / ".kiro/hooks/observal.json").write_text(json.dumps(build_kiro_hooks_file()))
    monkeypatch.setenv("OBSERVAL_KIRO_ENGINE", "v3")
    monkeypatch.setenv("OBSERVAL_KIRO_CLI_VERSION", "2.35.0")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ensure_loaded()
    issues, warnings = [], []
    _check_kiro(issues, warnings)
    assert not issues and not warnings
