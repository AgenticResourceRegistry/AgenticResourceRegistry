# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Run generated hook commands in real Windows shells, not simulated POSIX shells.

The Linux launcher suite exercises Git Bash syntax. This suite must run on a
Windows runner to detect cmd.exe quoting, PowerShell invocation and imports
from an untrusted project or inherited PYTHONPATH.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from observal_cli import cmd_pull
from observal_cli.harness_specs import (
    antigravity_hooks_spec,
    claude_code_hooks_spec,
    codex_hooks_spec,
    copilot_cli_hooks_spec,
    kiro_hooks_spec,
)
from observal_cli.shared import launcher

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires native cmd.exe and PowerShell")
_PROBE = "observal_cli.probe"
_AGENT = "agent-%PATH%-$env:TEMP-'literal'"


def test_windows_login_menu_uses_native_console(monkeypatch):
    import msvcrt

    from observal_cli import prompts

    keys = iter(["x", "2"])
    monkeypatch.setattr(prompts.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(msvcrt, "getwch", lambda: next(keys))
    assert prompts.quick_choice("Login method", ["1", "2"]) == "2"


def test_claude_mcp_registration_starts_a_windows_cmd_shim(tmp_path, monkeypatch):
    import shutil

    from observal_cli.harness.claude_code import ClaudeCodeAdapter

    shim = tmp_path / "npm tools with spaces" / "claude.cmd"
    shim.parent.mkdir(parents=True)
    shim.write_text("@echo off\r\necho CLAUDE_SETUP_OK\r\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: str(shim) if name == "claude.cmd" else None)
    prepared = ClaudeCodeAdapter().prepare_mcp_setup_command(["claude", "mcp", "add", "srv"], "project")
    assert prepared == [str(shim), "mcp", "add", "--scope", "project", "srv"]
    result = subprocess.run(prepared, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "CLAUDE_SETUP_OK" in result.stdout


def _probe_package(root: Path, marker: str) -> None:
    package = root / "observal_cli"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "probe.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        f"print(json.dumps({{'marker': {marker!r}, 'file': str(Path(__file__).resolve()), "
        "'agent': os.environ.get('OBSERVAL_AGENT_ID')}))\n",
        encoding="utf-8",
    )


def _venv(tmp_path: Path, *, installed: bool) -> tuple[str, Path]:
    home = tmp_path / "Python dir $' literal" / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(home)], check=True)
    python = str(home / "Scripts" / "python.exe")
    site = subprocess.check_output(
        [python, "-I", "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"], text=True
    ).strip()
    source = tmp_path / "Observal source $' with spaces"
    if installed:
        _probe_package(Path(site), "TRUSTED")
    else:
        _probe_package(source, "TRUSTED")
    return python, (Path(site) if installed else source)


def _hostile_project(tmp_path: Path) -> Path:
    project = tmp_path / "hostile project"
    _probe_package(project, "HIJACKED")
    return project


def _run(command: str | list[str], project: Path, *, shell: bool = False) -> dict:
    env = {**os.environ, "PYTHONPATH": str(project), "OBSERVAL_AGENT_ID": "INHERITED"}
    result = subprocess.run(command, cwd=project, env=env, shell=shell, text=True, capture_output=True, check=False)
    assert result.returncode == 0, (command, result.stdout, result.stderr)
    data = json.loads(result.stdout)
    assert data["marker"] == "TRUSTED", (command, data)
    assert data["agent"] == _AGENT, (command, data)
    return data


def _replace_module(command: str) -> str:
    updated = re.sub(r"observal_cli\.hooks\.[A-Za-z_]+", _PROBE, command)
    assert updated != command, command
    return updated


def _generated_cmd_hooks() -> dict[str, str]:
    return {
        "claude-code": claude_code_hooks_spec.get_desired_hooks()["Stop"][0]["hooks"][0]["command"],
        "codex": codex_hooks_spec.build_codex_hooks()["hooks"]["Stop"][0]["hooks"][0]["command"],
        "kiro": kiro_hooks_spec.build_kiro_push_command(_AGENT),
        "antigravity": antigravity_hooks_spec.build_antigravity_hooks()["observal-telemetry"]["Stop"][0]["command"],
        "copilot-cli bash": copilot_cli_hooks_spec.build_copilot_cli_hooks(_AGENT)["hooks"]["sessionStart"][0]["bash"],
        "cursor (doctor)": launcher.module_command("observal_cli.hooks.session_push", {"OBSERVAL_AGENT_ID": _AGENT}),
        "agent pull rewrite": cmd_pull.rewrite_launcher_command(
            "python3 -m observal_cli.hooks.session_push --harness claude-code"
        ),
    }


@pytest.mark.parametrize("installed", [True, False], ids=["installed", "source-checkout"])
def test_generated_hooks_execute_in_cmd_and_copilot_in_powershell(tmp_path, monkeypatch, installed):
    """Use real Windows subprocesses, a spaced interpreter, and a hostile cwd/PYTHONPATH."""
    python, trusted_root = _venv(tmp_path, installed=installed)
    project = _hostile_project(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "executable", python)
        patch.setattr(launcher, "importable_in_isolation", lambda: installed)
        patch.setattr(launcher, "package_root", lambda: str(trusted_root))
        for name, command in _generated_cmd_hooks().items():
            # Only the agent-scoped commands embed the agent ID in the hook.
            if name not in ("kiro", "copilot-cli bash", "cursor (doctor)"):
                command = _replace_module(command)
                env_value = "INHERITED"
            else:
                command = _replace_module(command)
                env_value = _AGENT
            result = subprocess.run(
                command,
                cwd=project,
                env={**os.environ, "PYTHONPATH": str(project), "OBSERVAL_AGENT_ID": "INHERITED"},
                shell=True,
                text=True,
                capture_output=True,
                check=False,
            )
            assert result.returncode == 0, (name, command, result.stdout, result.stderr)
            data = json.loads(result.stdout)
            assert data["marker"] == "TRUSTED" and data["agent"] == env_value, (name, data)
            assert Path(data["file"]).is_relative_to(trusted_root), (name, data)

        profile = copilot_cli_hooks_spec.build_copilot_cli_hooks(_AGENT)
        powershell = _replace_module(profile["hooks"]["sessionStart"][0]["powershell"])
        data = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", powershell], project)
        assert Path(data["file"]).is_relative_to(trusted_root)

        # Agent pull's argv form must not use a shell or trust the project either.
        argv = cmd_pull.rewrite_observal_interpreter(["claude", "mcp", "add", "x", "--", "python3", "-m", _PROBE])
        child = argv[argv.index("--") + 1 :]
        assert child[0] == python and child[1] == "-I"
        result = subprocess.run(
            child,
            cwd=project,
            env={**os.environ, "PYTHONPATH": str(project), "OBSERVAL_AGENT_ID": _AGENT},
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, (child, result.stderr)
        assert json.loads(result.stdout)["marker"] == "TRUSTED"
