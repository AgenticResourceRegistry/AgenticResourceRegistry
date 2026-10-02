# SPDX-FileCopyrightText: 2026 Dheirav Prakash <dheirav2005@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Hook commands keep the CLI's interpreter as one argument (#1777).

The CLI often lives under a user directory, and on Windows that directory
commonly has a space in it. An unquoted interpreter or PYTHONPATH is split by
the shell and the hook never starts, so these tests build commands from paths
with spaces and check that a shell reads them back intact.
"""

from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from observal_cli.cmd_doctor import _patch_antigravity, _patch_codex, _patch_cursor
from observal_cli.harness_specs import (
    antigravity_hooks_spec,
    claude_code_hooks_spec,
    codex_hooks_spec,
    kiro_hooks_spec,
)
from observal_cli.shared import utils
from observal_cli.shared.utils import hook_python_cmd, quote_shell_arg

if TYPE_CHECKING:
    from pathlib import Path

POSIX_PYTHON = "/home/First Last/.local/share/uv/tools/observal-cli/bin/python"
WINDOWS_PYTHON = r"C:\Users\First Last\AppData\Roaming\uv\tools\observal-cli\Scripts\python.exe"


@pytest.fixture
def posix_python(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "executable", POSIX_PYTHON)
    return POSIX_PYTHON


@pytest.fixture
def not_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)


@pytest.fixture
def no_wsl(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args, **_kwargs):
        raise FileNotFoundError("wslpath")

    monkeypatch.setattr(subprocess, "run", fail)


# -- quote_shell_arg ---------------------------------------------------------


def test_posix_path_with_space_stays_one_argument(posix_python: str):
    assert shlex.split(quote_shell_arg(posix_python)) == [posix_python]


def test_windows_path_with_space_is_double_quoted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert quote_shell_arg(WINDOWS_PYTHON) == f'"{WINDOWS_PYTHON}"'


@pytest.mark.parametrize("path", [r"C:\R&D\venv\Scripts\python.exe", r"C:\a^b\python.exe", r"C:\x(y)\python.exe"])
def test_windows_path_with_cmd_metacharacter_is_double_quoted(monkeypatch: pytest.MonkeyPatch, path: str):
    # No space, so list2cmdline would leave these bare and cmd.exe would act on the operator.
    monkeypatch.setattr(sys, "platform", "win32")
    assert quote_shell_arg(path) == f'"{path}"'


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_path_without_special_characters_is_unchanged(monkeypatch: pytest.MonkeyPatch, platform: str):
    # Existing hooks for ordinary paths must not be rewritten.
    monkeypatch.setattr(sys, "platform", platform)
    assert quote_shell_arg("/usr/bin/python3") == "/usr/bin/python3"


# -- hook_python_cmd ---------------------------------------------------------


def test_importable_cli_gives_quoted_interpreter_only(posix_python: str):
    assert shlex.split(hook_python_cmd()) == [posix_python]


def test_posix_pythonpath_with_space_stays_one_assignment(
    posix_python: str, not_importable: None, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(utils, "_PKG_ROOT", "/opt/First Last/site")
    assert shlex.split(hook_python_cmd()) == ["PYTHONPATH=/opt/First Last/site", posix_python]


def test_windows_pythonpath_and_interpreter_are_quoted(not_importable: None, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", WINDOWS_PYTHON)
    monkeypatch.setattr(utils, "_PKG_ROOT", r"C:\Program Files\observal")
    assert hook_python_cmd() == rf'set "PYTHONPATH=C:\Program Files\observal" && "{WINDOWS_PYTHON}"'


# -- every doctor patch writer -----------------------------------------------


def _claude_code_cmd() -> str:
    return claude_code_hooks_spec.get_desired_hooks()["Stop"][0]["hooks"][0]["command"]


def _codex_cmd() -> str:
    return codex_hooks_spec.build_codex_hooks()["hooks"]["Stop"][0]["hooks"][0]["command"]


def _kiro_cmd() -> str:
    return kiro_hooks_spec.build_kiro_push_command()


def _antigravity_cmd() -> str:
    return antigravity_hooks_spec.build_antigravity_hooks()["observal-telemetry"]["Stop"][0]["command"]


@pytest.mark.parametrize(
    ("build", "module"),
    [
        (_claude_code_cmd, "observal_cli.hooks.session_push"),
        (_codex_cmd, "observal_cli.hooks.session_push"),
        (_kiro_cmd, "observal_cli.hooks.session_push"),
        (_antigravity_cmd, "observal_cli.hooks.antigravity_session_push"),
    ],
)
def test_spec_command_keeps_interpreter_whole(posix_python: str, no_wsl: None, build, module: str):
    assert shlex.split(build())[:3] == [posix_python, "-m", module]


def test_cursor_command_keeps_interpreter_whole(posix_python: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()

    assert _patch_cursor(dry_run=False) is True

    hooks = json.loads((tmp_path / ".cursor/hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert shlex.split(hooks["stop"][0]["command"])[:3] == [posix_python, "-m", "observal_cli.hooks.session_push"]


# -- doctor patch repairs hooks written before quoting -----------------------


def test_cursor_patch_replaces_an_unquoted_entry(posix_python: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    stale = {"command": f"{posix_python} -m observal_cli.hooks.session_push --harness cursor", "type": "command"}
    hooks_path = tmp_path / ".cursor/hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text(json.dumps({"version": 1, "hooks": {"beforeSubmitPrompt": [stale], "stop": [stale]}}))

    assert _patch_cursor(dry_run=False) is True

    entries = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]["stop"]
    assert len(entries) == 1
    assert shlex.split(entries[0]["command"])[0] == posix_python
    assert _patch_cursor(dry_run=False) is False


def test_codex_patch_replaces_an_unquoted_entry(posix_python: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("codex_hooks = true\n", encoding="utf-8")
    stale = [
        {
            "matcher": "",
            "hooks": [
                {"type": "command", "command": f"{posix_python} -m observal_cli.hooks.session_push --harness codex"}
            ],
        }
    ]
    (codex_dir / "hooks.json").write_text(json.dumps({"hooks": {"UserPromptSubmit": stale, "Stop": stale}}))

    assert _patch_codex(dry_run=False) is True

    groups = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))["hooks"]["Stop"]
    assert len(groups) == 1
    assert shlex.split(groups[0]["hooks"][0]["command"])[0] == posix_python
    assert _patch_codex(dry_run=False) is False


def test_cursor_patch_removes_a_stale_copy_beside_the_current_entry(
    posix_python: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # One current entry must not hide a stale one that would still fire.
    monkeypatch.setenv("HOME", str(tmp_path))
    current = {"command": f"{shlex.quote(posix_python)} -m observal_cli.hooks.session_push --harness cursor"}
    stale = {"command": "/old/python -m observal_cli.hooks.session_push --harness cursor"}
    current["type"] = stale["type"] = "command"
    hooks_path = tmp_path / ".cursor/hooks.json"
    hooks_path.parent.mkdir()
    events = {"beforeSubmitPrompt": [current, stale], "stop": [current, stale]}
    hooks_path.write_text(json.dumps({"version": 1, "hooks": events}))

    assert _patch_cursor(dry_run=False) is True

    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]
    assert hooks["stop"] == [current]
    assert hooks["beforeSubmitPrompt"] == [current]


def test_codex_patch_removes_a_stale_copy_beside_the_current_group(
    posix_python: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("codex_hooks = true\n", encoding="utf-8")
    current = codex_hooks_spec.build_codex_hooks()["hooks"]["Stop"]
    stale = [
        {
            "matcher": "",
            "hooks": [{"type": "command", "command": "/old/python -m observal_cli.hooks.session_push --harness codex"}],
        }
    ]
    events = {"UserPromptSubmit": current + stale, "Stop": current + stale}
    (codex_dir / "hooks.json").write_text(json.dumps({"hooks": events}))

    assert _patch_codex(dry_run=False) is True

    hooks = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert hooks["Stop"] == current
    assert hooks["UserPromptSubmit"] == current


_OTHER_PUSH = "python -m my_company.session_push --harness internal"


def _cursor_hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: dict) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    hooks_path = tmp_path / ".cursor/hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text(json.dumps({"version": 1, "hooks": events}))
    return hooks_path


def test_cursor_patch_keeps_an_unrelated_session_push_hook_beside_the_current_entry(
    posix_python: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # "session_push" is not ours by itself: another tool's hook can contain it, and
    # its presence must neither trigger a repair nor be deleted by one.
    current = {"command": f"{shlex.quote(posix_python)} -m observal_cli.hooks.session_push --harness cursor"}
    other = {"command": _OTHER_PUSH}
    current["type"] = other["type"] = "command"
    events = {"beforeSubmitPrompt": [current, other], "stop": [current, other]}
    hooks_path = _cursor_hooks(tmp_path, monkeypatch, events)

    assert _patch_cursor(dry_run=False) is False

    assert json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"] == events


def test_cursor_patch_keeps_an_unrelated_hook_when_replacing_a_stale_entry(
    posix_python: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    stale = {"command": "/old/python -m observal_cli.hooks.session_push --harness cursor", "type": "command"}
    other = {"command": _OTHER_PUSH, "type": "command"}
    hooks_path = _cursor_hooks(tmp_path, monkeypatch, {"beforeSubmitPrompt": [stale, other], "stop": [stale, other]})

    assert _patch_cursor(dry_run=False) is True

    current = f"{shlex.quote(posix_python)} -m observal_cli.hooks.session_push --harness cursor"
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]
    for event in ("beforeSubmitPrompt", "stop"):
        assert [h["command"] for h in hooks[event]] == [_OTHER_PUSH, current]


def _codex_hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: dict) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("codex_hooks = true\n", encoding="utf-8")
    (codex_dir / "hooks.json").write_text(json.dumps({"hooks": events}))
    return codex_dir / "hooks.json"


def test_codex_patch_keeps_an_unrelated_hook_in_the_same_group_as_the_current_one(
    posix_python: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    other = {"type": "command", "command": _OTHER_PUSH}
    mixed = json.loads(json.dumps(codex_hooks_spec.build_codex_hooks()["hooks"]["Stop"]))
    mixed[0]["hooks"].append(other)
    events = {"UserPromptSubmit": mixed, "Stop": mixed}
    hooks_path = _codex_hooks(tmp_path, monkeypatch, events)

    assert _patch_codex(dry_run=False) is False

    assert json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"] == events


def test_codex_patch_keeps_the_unrelated_hook_of_a_group_with_a_stale_entry(
    posix_python: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    other = {"type": "command", "command": _OTHER_PUSH}
    stale = {"type": "command", "command": "/old/python -m observal_cli.hooks.session_push --harness codex"}
    mixed = [{"matcher": "", "hooks": [stale, other]}]
    hooks_path = _codex_hooks(tmp_path, monkeypatch, {"UserPromptSubmit": mixed, "Stop": mixed})

    assert _patch_codex(dry_run=False) is True

    current = codex_hooks_spec.build_codex_hooks()["hooks"]["Stop"]
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]
    assert hooks["Stop"] == [{"matcher": "", "hooks": [other]}, *current]
    assert hooks["UserPromptSubmit"] == [{"matcher": "", "hooks": [other]}, *current]


def test_antigravity_patch_replaces_an_unquoted_entry(
    posix_python: str, no_wsl: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_dir = tmp_path / ".gemini/antigravity-cli"
    config_dir.mkdir(parents=True)
    monkeypatch.setattr("observal_cli.shared.utils.resolve_antigravity_config_dir", lambda: config_dir)
    stale_cmd = f"{posix_python} -m observal_cli.hooks.antigravity_session_push"
    stale = {"Stop": [{"type": "command", "command": stale_cmd, "timeout": 30}]}
    (config_dir / "hooks.json").write_text(json.dumps({"observal-telemetry": stale}))

    assert _patch_antigravity(dry_run=False) is True

    entry = json.loads((config_dir / "hooks.json").read_text(encoding="utf-8"))["observal-telemetry"]
    assert shlex.split(entry["Stop"][0]["command"])[0] == posix_python
    assert _patch_antigravity(dry_run=False) is False
