# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The CLI installs the connected server's version over itself and re-runs the command."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlparse

import httpx
import pytest
import typer

from observal_cli import auto_sync, client, errors, install_detector, upgrade_executor, version_check
from observal_cli.install_detector import InstallInfo, InstallMethod, OtherInstall

# conftest replaces these for every test so nothing reinstalls the CLI by accident.
# These tests exercise the real functions with the installer mocked.
sync_to_server = auto_sync.sync_to_server
sync_other_installs = auto_sync.sync_other_installs

SERVER = "https://registry.example.test"
COMMIT = "a" * 40
REPO = "git+https://github.com/AgenticResourceRegistry/AgenticResourceRegistry"


class RerunError(Exception):
    """Stands in for the process being replaced by the new CLI."""


@pytest.fixture
def stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A uv-installed CLI on 1.18.0.dev0 facing a server on 1.17.0.dev0, with every side effect recorded."""
    state = MagicMock()
    state.install = InstallInfo(InstallMethod.UV_TOOL, tmp_path / "observal", True, "uv")
    state.notices = []
    monkeypatch.setattr(sys, "argv", ["observal", "agent", "list"])
    monkeypatch.setattr(auto_sync, "_command_process", True)  # as main's callback marks an `observal` command
    monkeypatch.setattr(client, "_requests_completed", 0)  # a fresh command: nothing sent yet
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # piped and empty, as agents run commands
    monkeypatch.delenv(auto_sync.SYNC_ENV, raising=False)
    monkeypatch.setattr(
        auto_sync, "fetch_server_build", lambda _url: auto_sync.ServerBuild(version="1.17.0-dev.0", commit=COMMIT)
    )
    monkeypatch.setattr(version_check, "get_current_version", lambda: "1.18.0.dev0")
    monkeypatch.setattr(install_detector, "detect", lambda: state.install)
    monkeypatch.setattr(install_detector, "installed_version", lambda _path: "1.18.0-dev.0")
    monkeypatch.setattr(upgrade_executor, "execute", state.execute)
    monkeypatch.setattr(version_check, "missing_git_reason", lambda _version: None)
    monkeypatch.setattr(auto_sync, "_start_failure", lambda _install: None)
    monkeypatch.setattr(auto_sync, "sync_other_installs", state.others)
    monkeypatch.setattr(auto_sync, "_rerun", MagicMock(side_effect=RerunError))
    monkeypatch.setattr(auto_sync, "_notice", state.notices.append)
    monkeypatch.setattr(auto_sync, "LAST_SYNC_FILE", tmp_path / "last_sync.json")
    monkeypatch.setattr(version_check, "CACHE_FILE", tmp_path / "version_cache.json")
    (tmp_path / "version_cache.json").write_text("{}")
    return state


def test_mismatch_installs_server_version_then_reruns_the_command(stage, tmp_path: Path) -> None:
    with pytest.raises(RerunError):
        sync_to_server(SERVER)

    args, kwargs = stage.execute.call_args
    assert args[:3] == (stage.install, "1.17.0.dev0", "downgrade")
    assert kwargs == {"interactive": False, "commit": COMMIT}
    auto_sync._rerun.assert_called_once_with(stage.install, "1.17.0.dev0")
    # Under the lock this call already holds, so the other copies are not changed concurrently.
    stage.others.assert_called_once_with("1.17.0.dev0", COMMIT, stage.install, lock_held=True)
    # The stale cache would make the next gate check trust the old server version.
    assert not (tmp_path / "version_cache.json").exists()
    record = json.loads((tmp_path / "last_sync.json").read_text())
    assert (record["previous_version"], record["version"], record["server_commit"]) == (
        "1.18.0.dev0",
        "1.17.0.dev0",
        COMMIT,
    )


def test_the_record_names_the_source_actually_installed_not_only_the_reported_commit(
    stage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live finding: the server reported an unpublished commit, the CLI built main's commit instead, and the
    record named only the reported one, pointing anyone recovering by hand at the wrong source."""
    built = f"{REPO}@{'d' * 40}"

    def install(*_args, **_kwargs):
        monkeypatch.setattr(upgrade_executor, "last_installed_source", built)

    stage.execute.side_effect = install

    with pytest.raises(RerunError):
        sync_to_server(SERVER)

    record = json.loads((tmp_path / "last_sync.json").read_text())
    assert (record["server_commit"], record["installed_from"]) == (COMMIT, built)
    assert "Syncing the CLI to it (development build, from source)" in stage.notices[0]


@pytest.mark.parametrize("previous", ["1.17.0.dev0", "1.16.0"])
def test_second_mismatch_in_the_same_invocation_does_not_loop(
    stage, monkeypatch: pytest.MonkeyPatch, previous: str
) -> None:
    # 1.16.0: the re-run met a different server version (a rolling deploy). One attempt is still the limit.
    monkeypatch.setenv(auto_sync.SYNC_ENV, previous)

    assert sync_to_server(SERVER) == f"This command already synced the CLI to {previous}; it now reports 1.18.0.dev0."
    stage.execute.assert_not_called()
    auto_sync._rerun.assert_not_called()


def test_install_already_done_by_another_process_is_not_repeated(stage, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_detector, "installed_version", lambda _path: "1.17.0-dev.0")

    with pytest.raises(RerunError):
        sync_to_server(SERVER)

    stage.execute.assert_not_called()


def test_installer_failure_is_reported_and_nothing_reruns(stage) -> None:
    def fail(*_args, **_kwargs):
        print("uv could not reach the index")
        raise typer.Exit(1)

    stage.execute.side_effect = fail

    assert sync_to_server(SERVER) == "uv could not reach the index"
    auto_sync._rerun.assert_not_called()
    stage.others.assert_not_called()


def test_an_install_that_cannot_start_is_reported_instead_of_rerun(stage, monkeypatch: pytest.MonkeyPatch) -> None:
    # `--version` passes on an install with a broken dependency, so the version check alone would re-run into a crash.
    monkeypatch.setattr(auto_sync, "_start_failure", lambda _install: "AttributeError: 'TyperOption' has no 'type'")

    assert sync_to_server(SERVER) == (
        "Version 1.17.0.dev0 was installed but does not start: AttributeError: 'TyperOption' has no 'type'"
    )
    auto_sync._rerun.assert_not_called()


def test_start_check_loads_a_command_rather_than_asking_the_version(monkeypatch: pytest.MonkeyPatch) -> None:
    run = MagicMock(return_value=MagicMock(returncode=1, stderr="Traceback...\nAttributeError: boom\n", stdout=""))
    monkeypatch.setattr(auto_sync.subprocess, "run", run)
    install = InstallInfo(InstallMethod.UV_TOOL, Path("/home/u/.local/bin/observal"), True, "uv")

    assert auto_sync._start_failure(install) == "AttributeError: boom"
    assert run.call_args.args[0] == [str(install.path), "auth", "whoami", "--help"]


def test_start_check_runs_in_an_empty_directory_not_the_users(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Regression: the new CLI's first start scans its working directory for legacy agent markers. Run from
    the user's directory (a OneDrive checkout through WSL in the live test), that scan outlasted the timeout."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    seen = {}

    def run(argv, **kwargs):
        seen.update(cwd=Path(kwargs["cwd"]), entries=list(Path(kwargs["cwd"]).parent.iterdir()), env=kwargs["env"])
        return MagicMock(returncode=0)

    monkeypatch.setattr(auto_sync.subprocess, "run", run)

    assert auto_sync._start_failure_at(Path("/home/u/.local/bin/observal")) is None
    # Empty, and its parent holds nothing else, so a scan of both is instant; removed afterwards.
    assert seen["cwd"] != project and seen["entries"] == [seen["cwd"]]
    assert not seen["cwd"].exists()
    assert seen["env"]["OBSERVAL_NO_UPDATE_CHECK"] == "1"


def test_downgrade_caps_dependencies_at_the_running_versions_then_retries_uncapped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from contextlib import nullcontext

    ceiling = tmp_path / "sync-constraints.txt"
    run = MagicMock(side_effect=[MagicMock(returncode=1, stderr="conflict"), MagicMock(returncode=0, stderr="")])
    monkeypatch.setattr(upgrade_executor.subprocess, "run", run)

    upgrade_executor._install_via_uv("1.17.1", "downgrade", lambda _m: nullcontext(), ceiling=ceiling)

    capped, uncapped = (call.args[0] for call in run.call_args_list)
    assert capped == ["uv", "tool", "install", "observal-cli==1.17.1", "--force", "-c", str(ceiling)]
    assert uncapped == ["uv", "tool", "install", "observal-cli==1.17.1", "--force"]


def test_dependency_ceiling_lists_installed_versions_but_not_the_cli_itself() -> None:
    lines = upgrade_executor._dependency_ceiling().read_text().splitlines()

    assert any(line.startswith("typer<=") for line in lines)
    assert not any(line.lower().startswith("observal-cli") for line in lines)


@pytest.mark.parametrize(
    ("install", "expected"),
    [
        (InstallInfo(InstallMethod.SYSTEM_PACKAGE, Path("/usr/bin/observal"), False, "apt"), "managed by apt"),
        (InstallInfo(InstallMethod.SYSTEM_PACKAGE, Path("/opt/homebrew/bin/observal"), False, "brew"), "by brew"),
        (InstallInfo(InstallMethod.BINARY, Path("/opt/observal"), True, "curl"), "no standalone binary"),
    ],
)
def test_installs_no_installer_can_change_are_left_alone(stage, install: InstallInfo, expected: str) -> None:
    stage.install = install

    assert expected in sync_to_server(SERVER)
    stage.execute.assert_not_called()


@pytest.mark.parametrize("command", [["self", "downgrade"], ["server", "upgrade"]])
def test_self_and_server_commands_are_never_synced_under_the_user(
    stage, monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["observal", "--debug", *command])

    assert "are not synced automatically" in sync_to_server(SERVER)
    stage.execute.assert_not_called()


def test_without_rerun_the_caller_decides_what_happens_next(stage) -> None:
    assert sync_to_server(SERVER, rerun=False) is None

    stage.execute.assert_called_once()
    auto_sync._rerun.assert_not_called()


@pytest.mark.parametrize(
    "argv",
    [
        # The delegation runner and the observal-agents server import the CLI without its entry point.
        ["/x/observal_cli/delegation/runner.py", "0f6b0c6e-task"],
        ["/x/observal_cli/delegation/mcp_server.py", "--harness", "claude-code"],
    ],
)
def test_workers_and_mcp_servers_never_sync(stage, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """Re-running one would replace it with `observal_cli.main <its arguments>`: a failed task or a dead MCP
    session, after a minute-long install under the harness."""
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(auto_sync, "_command_process", False)

    reason = sync_to_server(SERVER)

    assert "Only an `observal` command updates the CLI" in reason
    stage.execute.assert_not_called()
    assert stage.notices == []


def test_the_delegate_mcp_command_does_not_sync_while_serving(stage, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["observal", "delegate", "mcp", "--harness", "claude-code"])

    assert "does not update the CLI while it serves" in sync_to_server(SERVER)
    stage.execute.assert_not_called()


def test_the_observal_entry_point_marks_its_process_as_a_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from observal_cli.main import app

    monkeypatch.setattr(auto_sync, "_command_process", False)
    CliRunner().invoke(app, ["config", "path"])

    assert auto_sync._command_process is True


def test_a_command_that_read_its_piped_input_is_synced_but_not_rerun(stage, monkeypatch: pytest.MonkeyPatch) -> None:
    """`echo '{...}' | observal api POST ...`: a re-run would find standard input empty and send no body."""
    from observal_cli import cmd_api

    monkeypatch.setattr(sys, "stdin", io.StringIO('{"name": "team"}'))
    auto_sync.begin_command()
    assert cmd_api._stdin_body() == {"name": "team"}

    assert sync_to_server(SERVER) is None
    stage.execute.assert_called_once()
    auto_sync._rerun.assert_not_called()


def test_empty_piped_input_does_not_prevent_the_rerun(stage) -> None:
    """Agents run commands with an empty pipe on standard input; reading it consumes nothing."""
    from observal_cli import cmd_api

    auto_sync.begin_command()
    assert cmd_api._stdin_body() is cmd_api._NO_BODY

    with pytest.raises(RerunError):
        sync_to_server(SERVER)


def test_the_gate_says_a_synced_command_must_be_run_again(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from observal_cli.errors import CliError

    monkeypatch.setattr(version_check, "get_current_version", lambda: "1.18.0")
    monkeypatch.setattr(version_check, "_read_cache", lambda: {"server_version": "1.17.0", "source": "server"})
    monkeypatch.setattr(version_check, "_should_check", lambda *_args: False)
    monkeypatch.setattr(auto_sync, "sync_to_server", lambda _url: None)
    monkeypatch.setattr(auto_sync, "last_rerun_blocker", "had already read its standard input")

    with pytest.raises(CliError) as error:
        version_check.check_version_compatibility(SERVER)

    assert error.value.exit_code == 10
    assert error.value.message == (
        "The CLI was synced to the server's version 1.17.0, but this command had already read its standard input, "
        "so it was not re-run."
    )
    assert error.value.remediation == "Check what the command already did, then run it again with the same input."


def test_no_trigger_reruns_a_command_that_already_reached_the_server(stage, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second review: only the 426 path guarded this; the gate and login re-ran after a direct write."""
    monkeypatch.setattr(client, "_requests_completed", 1)

    assert sync_to_server(SERVER) is None

    stage.execute.assert_called_once()
    auto_sync._rerun.assert_not_called()
    assert auto_sync.last_rerun_blocker == "had already sent a request to the server"


def test_a_waiter_does_not_rerun_into_an_install_that_cannot_start(stage, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second review: the process that installed it failed the start check; reporting the version is not enough."""
    monkeypatch.setattr(install_detector, "installed_version", lambda _path: "1.17.0-dev.0")
    monkeypatch.setattr(auto_sync, "_start_failure", lambda _install: "AttributeError: boom")

    assert sync_to_server(SERVER) == "Version 1.17.0.dev0 is installed but does not start: AttributeError: boom"
    stage.execute.assert_not_called()
    auto_sync._rerun.assert_not_called()


def test_a_sync_to_a_version_without_auto_sync_says_so(stage, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second review: the updater is part of the package it replaces; an older release cannot sync itself later."""
    monkeypatch.setattr(
        auto_sync, "fetch_server_build", lambda _url: auto_sync.ServerBuild(version="1.17.1", commit=None)
    )
    monkeypatch.setattr(version_check, "missing_git_reason", lambda _version: None)

    with pytest.raises(RerunError):
        sync_to_server(SERVER)

    assert stage.notices[-1] == (
        "Version 1.17.1 predates automatic sync: when the server is upgraded, run "
        "`observal self upgrade --version <server version> --force` once."
    )


def test_a_sync_to_a_version_with_auto_sync_does_not_warn(stage, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auto_sync, "fetch_server_build", lambda _url: auto_sync.ServerBuild(version="1.19.0", commit=None)
    )

    with pytest.raises(RerunError):
        sync_to_server(SERVER)

    assert not any("predates" in notice for notice in stage.notices)


@pytest.mark.parametrize("version", ["1.17.1; touch /tmp/pwned", "$(id)", "1.17.1 && rm -rf ~", "`id`"])
def test_a_malformed_server_version_never_reaches_a_command(version: str) -> None:
    """Second review: the version from the server was interpolated into the suggested command unvalidated."""
    remediation = auto_sync.manual_fix(version, "1.18.0")

    assert version not in remediation
    assert not any(char in remediation for char in ";$`&|")
    assert "not valid" in remediation


def test_a_valid_version_is_named_as_the_server_wrote_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version_check, "missing_git_reason", lambda _version: None)

    assert auto_sync.manual_fix("1.17.0-dev.3", "1.18.0") == (
        "Run observal self downgrade --version 1.17.0-dev.3 --force, then retry."
    )


def test_the_426_message_is_built_here_not_copied_from_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second review: the server's `detail` named an install command for an unmerged commit."""
    from observal_cli.errors import CliError

    unmerged = f"uv tool install --force {REPO}@{'c' * 40}"
    response = httpx.Response(
        426,
        json={
            "detail": f"Observal CLI 1.18.0 cannot connect to server 1.17.0-dev.1. Run: {unmerged}",
            "server_version": "1.17.0-dev.1",
            "install_command": unmerged,
        },
        request=httpx.Request("GET", f"{SERVER}/api/v1/agents"),
    )
    monkeypatch.setattr(client, "_get_cli_version", lambda: "1.18.0")
    monkeypatch.setattr(version_check, "missing_git_reason", lambda _version: None)

    with pytest.raises(CliError) as error:
        client._handle_error(httpx.HTTPStatusError("e", request=response.request, response=response), "/api/v1/agents")

    assert "uv tool install" not in error.value.message and "c" * 40 not in error.value.message
    assert error.value.message.startswith(
        "Observal CLI 1.18.0 does not match server 1.17.0-dev.1; the server accepts only its own version."
    )
    assert "uv tool install" not in error.value.remediation


def test_waits_for_a_version_change_another_process_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    from observal_cli import upgrade_lock

    attempts = iter([upgrade_lock.UpgradeLockError("held"), upgrade_lock.UpgradeLockError("held"), Path("lock")])

    def acquire(_scope: str) -> Path:
        outcome = next(attempts)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(upgrade_lock, "acquire_lock", acquire)
    monkeypatch.setattr(auto_sync.time, "sleep", lambda _seconds: None)

    assert auto_sync._wait_for_lock() == Path("lock")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"server_version": "1.17.0-dev.0", "build_commit": COMMIT}, auto_sync.ServerBuild("1.17.0-dev.0", COMMIT)),
        ({"server_version": "1.17.0"}, auto_sync.ServerBuild("1.17.0", None)),
        # A commit is interpolated into an install command: anything but a full SHA is dropped.
        ({"server_version": "1.17.0", "build_commit": "main; rm -rf ~"}, auto_sync.ServerBuild("1.17.0", None)),
        ({}, None),
    ],
)
def test_server_build_is_read_defensively(monkeypatch: pytest.MonkeyPatch, payload: dict, expected) -> None:
    monkeypatch.setattr(auto_sync.httpx, "get", lambda *_a, **_k: httpx.Response(200, json=payload))

    assert auto_sync.fetch_server_build(SERVER) == expected


def test_unreachable_server_is_unknown_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_sync.httpx, "get", MagicMock(side_effect=httpx.ConnectError("offline")))

    assert auto_sync.fetch_server_build(SERVER) is None


def test_other_copies_are_synced_with_their_own_installer_and_strays_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pip_copy = OtherInstall(
        tmp_path / "pip" / "observal",
        InstallInfo(InstallMethod.PIP, tmp_path / "pip" / "observal", True, "pip"),
        "/opt/py/bin/python",
    )
    current_copy = OtherInstall(tmp_path / "ok" / "observal", InstallInfo(InstallMethod.PIPX, tmp_path, True, "pipx"))
    stray = OtherInstall(Path("/usr/bin/observal"), None)
    versions = {pip_copy.path: "0.4.0", current_copy.path: "1.17.1", stray.path: None}
    ceiling = tmp_path / "sync-constraints.txt"
    warnings: list[str] = []

    def install(argv, **_kwargs):
        versions[pip_copy.path] = "1.17.1"
        return MagicMock(returncode=0)

    run = MagicMock(side_effect=install)
    monkeypatch.setattr(install_detector, "find_other_installs", lambda _current: [pip_copy, current_copy, stray])
    monkeypatch.setattr(install_detector, "installed_version", versions.get)
    monkeypatch.setattr(install_detector, "pip_install_prefix", lambda _info, python: [python, "-m", "pip", "install"])
    monkeypatch.setattr(upgrade_executor, "pip_install_prefix", install_detector.pip_install_prefix)
    monkeypatch.setattr(upgrade_executor, "_dependency_ceiling", lambda: ceiling)
    monkeypatch.setattr(auto_sync.subprocess, "run", run)
    monkeypatch.setattr(auto_sync, "_start_failure_at", lambda _path: None)
    monkeypatch.setattr(auto_sync, "_notice", lambda _message: None)
    monkeypatch.setattr(errors, "emit_warning", lambda _category, message, **_kwargs: warnings.append(message))

    sync_other_installs("1.17.1", None, InstallInfo(InstallMethod.UV_TOOL, tmp_path / "observal", True, "uv"))

    # Capped at the running CLI's dependencies, like the running copy's own downgrade.
    assert [call.args[0] for call in run.call_args_list] == [
        ["/opt/py/bin/python", "-m", "pip", "install", "observal-cli==1.17.1", "--quiet", "-c", str(ceiling)]
    ]
    assert warnings == [
        f"Another Observal CLI at {stray.path} is not working on version 1.17.1: "
        "no installer this CLI can drive owns it."
    ]


def test_another_copy_that_installs_but_does_not_start_is_retried_then_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    copy = OtherInstall(tmp_path / "observal", InstallInfo(InstallMethod.PIPX, tmp_path / "observal", True, "pipx"))
    warnings: list[str] = []
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(install_detector, "find_other_installs", lambda _current: [copy])
    monkeypatch.setattr(install_detector, "installed_version", lambda _path: "1.17.1" if run.called else "1.16.0")
    monkeypatch.setattr(upgrade_executor, "_dependency_ceiling", lambda: None)
    monkeypatch.setattr(auto_sync.subprocess, "run", run)
    monkeypatch.setattr(auto_sync, "_start_failure_at", lambda _path: "AttributeError: boom")
    monkeypatch.setattr(auto_sync, "_notice", lambda _message: None)
    monkeypatch.setattr(errors, "emit_warning", lambda _category, message, **_kwargs: warnings.append(message))

    sync_other_installs("1.17.1", None, InstallInfo(InstallMethod.UV_TOOL, tmp_path / "uv", True, "uv"))

    # A zero exit status is not success: each source (PyPI, then the release tag) was tried, then reported.
    assert [call.args[0][2] for call in run.call_args_list] == ["observal-cli==1.17.1", f"{REPO}@v1.17.1"]
    assert warnings == [
        f"Another Observal CLI at {copy.path} is not working on version 1.17.1: "
        "installing it did not produce a copy that starts."
    ]


def test_a_copy_on_the_right_version_that_cannot_start_is_repaired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The audit's case: a copy reports the server's version but crashes on every command (Typer 0.29)."""
    copy = OtherInstall(tmp_path / "observal", InstallInfo(InstallMethod.PIPX, tmp_path / "observal", True, "pipx"))
    ceiling = tmp_path / "sync-constraints.txt"
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(install_detector, "find_other_installs", lambda _current: [copy])
    monkeypatch.setattr(install_detector, "installed_version", lambda _path: "1.17.1")
    monkeypatch.setattr(upgrade_executor, "_dependency_ceiling", lambda: ceiling)
    monkeypatch.setattr(auto_sync.subprocess, "run", run)
    monkeypatch.setattr(auto_sync, "_start_failure_at", lambda _path: None if run.called else "AttributeError: boom")
    monkeypatch.setattr(auto_sync, "_notice", lambda _message: None)
    monkeypatch.setattr(errors, "emit_warning", MagicMock())

    sync_other_installs("1.17.1", None, InstallInfo(InstallMethod.UV_TOOL, tmp_path / "uv", True, "uv"))

    # Reinstalled with dependencies capped (pip and uv downgrade an installed dependency to meet a constraint).
    argv, kwargs = run.call_args.args[0], run.call_args.kwargs
    assert argv == ["pipx", "install", "observal-cli==1.17.1", "--force"]
    assert kwargs["env"]["PIP_CONSTRAINT"] == str(ceiling)
    errors.emit_warning.assert_not_called()


def test_a_copy_that_could_not_be_synced_is_reported_in_json_mode(capsys: pytest.CaptureFixture[str]) -> None:
    token = errors._json_error_mode.set(True)
    try:
        auto_sync._report_copy(Path("/opt/old/observal"), "1.17.1", "no installer this CLI can drive owns it")
    finally:
        errors._json_error_mode.reset(token)

    captured = capsys.readouterr()
    warning = json.loads(captured.err)["warning"]
    # stdout stays the command's JSON; the warning is one JSON object on stderr.
    assert captured.out == ""
    assert warning["category"] == "cli_copy_not_synced"
    assert "/opt/old/observal" in warning["message"] and "1.17.1" in warning["remediation"]


def test_other_copies_are_left_to_a_process_already_changing_versions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from observal_cli import upgrade_lock

    monkeypatch.setattr(upgrade_lock, "CONFIG_DIR", tmp_path)
    held = upgrade_lock.acquire_lock("cli")
    find = MagicMock(return_value=[])
    monkeypatch.setattr(install_detector, "find_other_installs", find)
    try:
        sync_other_installs("1.17.1", None, InstallInfo(InstallMethod.PIP, Path("/x/observal"), True, "pip"))
    finally:
        upgrade_lock.release_lock(held)

    find.assert_not_called()


def test_a_broken_stray_copy_never_blocks_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_detector, "find_other_installs", MagicMock(side_effect=RuntimeError("PATH is odd")))

    assert sync_other_installs("1.17.1", None, InstallInfo(InstallMethod.PIP, Path("/x/observal"), True, "pip")) is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX replaces the process; Windows runs a child")
def test_rerun_replaces_the_process_and_marks_the_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    execve = MagicMock(side_effect=RerunError)
    monkeypatch.setattr(auto_sync.os, "execve", execve)
    monkeypatch.setattr(sys, "argv", ["observal", "agent", "list", "--output", "json"])
    install = InstallInfo(InstallMethod.UV_TOOL, Path("/home/u/.local/bin/observal"), True, "uv")

    with pytest.raises(RerunError):
        auto_sync._rerun(install, "1.17.1")

    path, argv, environment = execve.call_args.args
    assert path == "/home/u/.local/bin/observal"
    assert argv == ["/home/u/.local/bin/observal", "agent", "list", "--output", "json"]
    assert environment[auto_sync.SYNC_ENV] == "1.17.1"


# ── Locating the source of a development build ──────────────────


def _github(pyprojects: dict[str, str], commits: list[str] | None = None):
    def get(url: str, **_kwargs) -> httpx.Response:
        if urlparse(url).hostname == "raw.githubusercontent.com":
            ref = url.split("/")[-2]
            if ref not in pyprojects:
                return httpx.Response(404)
            return httpx.Response(200, text=f'[project]\nname = "observal-cli"\nversion = "{pyprojects[ref]}"\n')
        feed = "".join(f"<id>tag:github.com,2008:Grit::Commit/{sha}</id>" for sha in commits or [])
        return httpx.Response(200, text=f"<feed>{feed}</feed>")

    return get


MAIN_HEAD = "9" * 40


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Commits reachable from main or a release branch. Everything else is a PR head or a fork commit."""
    commits = {MAIN_HEAD}
    monkeypatch.setattr(version_check, "is_published_commit", lambda sha: sha in commits)
    monkeypatch.setattr(version_check, "_main_head", lambda: MAIN_HEAD)
    return commits


def test_reported_commit_is_used_when_published_and_declaring_the_version(
    monkeypatch: pytest.MonkeyPatch, published: set[str]
) -> None:
    published.add(COMMIT)
    monkeypatch.setattr(version_check.httpx, "get", _github({COMMIT: "1.17.0-dev.0"}))

    assert version_check.dev_source_refs("1.17.0.dev0", COMMIT) == [COMMIT]


def test_an_unmerged_commit_named_by_the_server_is_never_built(
    monkeypatch: pytest.MonkeyPatch, published: set[str]
) -> None:
    """A pull request's head is served under the repository's address and declares any version it likes."""
    pull_request_head = "c" * 40
    monkeypatch.setattr(
        version_check.httpx, "get", _github({pull_request_head: "1.17.0-dev.0", MAIN_HEAD: "1.18.0-dev.0"})
    )

    assert version_check.dev_source_refs("1.17.0-dev.0", pull_request_head) == []
    assert install_detector.install_specs("1.17.0-dev.0", []) == []


def test_main_that_moved_on_is_walked_back_to_a_commit_declaring_the_version(
    monkeypatch: pytest.MonkeyPatch, published: set[str]
) -> None:
    newest, bump, match, older = "d" * 40, "b" * 40, "e" * 40, "f" * 40
    published.update({newest, bump, match, older})
    pyprojects = {
        MAIN_HEAD: "1.18.0-dev.0",
        newest: "1.18.0-dev.0",
        bump: "1.18.0-dev.0",
        match: "1.17.0-dev.0",
        older: "1.17.0-dev.0",
    }
    monkeypatch.setattr(version_check.httpx, "get", _github(pyprojects, [newest, bump, match, older]))

    # No commit from the server (an older server), and `main` already on 1.18: building `main` would not match.
    assert version_check.dev_source_refs("1.17.0-dev.0") == [match]
    assert install_detector.install_specs("1.17.0-dev.0", [match]) == [f"{REPO}@{match}"]


def test_a_version_the_history_does_not_declare_installs_nothing(
    monkeypatch: pytest.MonkeyPatch, published: set[str]
) -> None:
    published.add("d" * 40)
    monkeypatch.setattr(
        version_check.httpx, "get", _github({MAIN_HEAD: "1.18.0-dev.0", "d" * 40: "1.16.0-dev.0"}, ["d" * 40])
    )

    assert version_check.dev_source_refs("1.17.0-dev.0") == []
    # Nothing to try: `main` is not a fallback, because installing it would replace the CLI with 1.18.
    assert install_detector.install_specs("1.17.0-dev.0", []) == []


def test_main_is_pinned_to_its_current_commit_while_it_declares_the_version(
    monkeypatch: pytest.MonkeyPatch, published: set[str]
) -> None:
    monkeypatch.setattr(version_check.httpx, "get", _github({MAIN_HEAD: "1.18.0-dev.0"}))

    # The commit, not the branch name: `main` could move between this check and the build.
    assert version_check.dev_source_refs("1.18.0.dev0") == [MAIN_HEAD]


def test_unreadable_published_history_installs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version_check, "_published_history", lambda: False)
    monkeypatch.setattr(version_check.httpx, "get", _github({COMMIT: "1.17.0-dev.0"}))

    assert version_check.dev_source_refs("1.17.0-dev.0", COMMIT) == []


def test_installer_refuses_before_changing_anything_when_no_commit_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import nullcontext

    run = MagicMock()
    messages: list[str] = []
    monkeypatch.setattr(upgrade_executor.subprocess, "run", run)
    monkeypatch.setattr(upgrade_executor, "rprint", messages.append)
    monkeypatch.setattr(version_check, "dev_source_refs", lambda *_args: [])

    with pytest.raises(typer.Exit):
        upgrade_executor._install_via_uv("1.17.0.dev0", "downgrade", lambda _m: nullcontext())

    run.assert_not_called()
    assert "left unchanged" in messages[0]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_published_means_reachable_from_main_or_a_release_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Real git: a commit on a release branch passes; one on any other branch (a PR head) does not."""

    def git(*args: str, cwd: Path) -> str:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git("init", "--quiet", "--initial-branch=main", cwd=upstream)
    git("config", "user.email", "test@example.test", cwd=upstream)
    git("config", "user.name", "test", cwd=upstream)

    def commit(message: str) -> str:
        git("commit", "--quiet", "--allow-empty", "-m", message, cwd=upstream)
        return git("rev-parse", "HEAD", cwd=upstream)

    on_main = commit("on main")
    git("checkout", "--quiet", "-b", "release/1.17", cwd=upstream)
    on_release = commit("on release")
    git("checkout", "--quiet", "-b", "feature", "main", cwd=upstream)
    unmerged = commit("unmerged")
    git("checkout", "--quiet", "main", cwd=upstream)

    monkeypatch.setattr(version_check, "SOURCE_CACHE", tmp_path / "cache" / "source.git")
    monkeypatch.setattr(version_check, "_source_url", lambda: upstream.as_uri())
    monkeypatch.setattr(version_check, "_published_history_fetched", False)

    assert version_check.is_published_commit(on_main)
    assert version_check.is_published_commit(on_release)
    assert not version_check.is_published_commit(unmerged)
    assert not version_check.is_published_commit("0" * 40)
    assert not version_check.is_published_commit("main")
    assert version_check._main_head() == on_main


def test_releases_have_no_source_refs_and_fall_back_to_their_tag() -> None:
    assert version_check.dev_source_refs("1.17.1", COMMIT) == []
    assert install_detector.install_specs("1.17.1") == ["observal-cli==1.17.1", f"{REPO}@v1.17.1"]


# ── Finding the other copies ─────────────────────────────────────


@pytest.mark.skipif(os.name == "nt", reason="console scripts carry a shebang only on POSIX")
def test_other_installs_are_found_on_path_and_classified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def script(directory: str, python: Path | None) -> Path:
        path = tmp_path / directory / "observal"
        path.parent.mkdir(parents=True)
        path.write_text(f"#!{python}\n" if python else "\x7fELF")
        return path

    def python(name: str) -> Path:
        path = tmp_path / name / "bin" / "python"
        path.parent.mkdir(parents=True)
        path.touch()
        return path

    own_python, other_python = python("own-env"), python("other-env")
    current = script("current", own_python)
    second_launcher = script("launcher", own_python)
    other = script("other", other_python)
    stray = script("stray", None)
    monkeypatch.setenv("PATH", os.pathsep.join(str(p.parent) for p in (current, second_launcher, other, stray)))
    monkeypatch.setattr(sys, "executable", str(own_python))
    monkeypatch.setattr(install_detector, "_uv_owns_executable", lambda _path: False)

    found = install_detector.find_other_installs(InstallInfo(InstallMethod.PIP, current, True, "pip"))

    assert [(item.path, item.info.method if item.info else None, item.interpreter) for item in found] == [
        (other, InstallMethod.PIP, str(other_python)),
        (stray, None, None),
    ]


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("observal 1.17.0rc1\n", "1.17.0rc1"),  # canonical prerelease: no separator before rc
        ("observal 1.17.0-dev.0\n", "1.17.0-dev.0"),
        ("Observal CLI v2.4.1+build.7\n", "2.4.1+build.7"),
        ("Observal development build\n", None),
    ],
)
def test_reported_version_keeps_the_whole_version_token(output: str, expected: str | None) -> None:
    assert install_detector.reported_version(output) == expected


def test_installed_rc_matches_the_same_rc_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        install_detector.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0, stdout="observal 1.17.0rc1"))
    )

    assert version_check._same_version(install_detector.installed_version(Path("/x/observal")), "1.17.0-rc.1")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("observal", ["{path}", "--version"]),
        ("observal-linux-x64", ["{path}", "--version"]),  # a standalone binary takes CLI options, not -m
        ("python3.12", ["{path}", "-I", "-m", "observal_cli.main", "--version"]),
        ("python.exe", ["{path}", "-I", "-m", "observal_cli.main", "--version"]),
    ],
)
def test_only_python_interpreters_are_given_module_options(name: str, expected: list[str], tmp_path: Path) -> None:
    path = tmp_path / name

    assert install_detector.version_argv(path) == [part.format(path=path) for part in expected]


@pytest.mark.skipif(os.name == "nt", reason="symlinked launchers as pipx creates them on POSIX")
def test_a_copy_in_another_home_of_the_same_manager_is_reported_not_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def pipx_env(home: str) -> Path:
        env = tmp_path / home / "venvs" / "observal-cli"
        (env / "bin").mkdir(parents=True)
        (env / "pyvenv.cfg").touch()
        script = env / "bin" / "observal"
        script.touch()
        return script

    running, elsewhere = pipx_env("pipx-a"), pipx_env("pipx-b")
    monkeypatch.setattr(install_detector, "_is_pipx_path", lambda _path: True)
    current = InstallInfo(InstallMethod.PIPX, running, True, "pipx")

    # Its own launcher is skipped; the same manager's copy in another home cannot be driven from here.
    assert install_detector._classify_other(running, running, current, Path(sys.executable)) is None
    other = install_detector._classify_other(elsewhere, elsewhere, current, Path(sys.executable))
    assert other == OtherInstall(path=elsewhere, info=None)


def test_windows_launcher_invoked_without_its_suffix_is_detected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = tmp_path / "observal.exe"
    launcher.touch()
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "observal")])
    monkeypatch.setattr(install_detector, "_cached_info", None)
    monkeypatch.setattr(install_detector, "_uv_owns_executable", lambda path: path == launcher.resolve())

    info = install_detector.detect()

    assert (info.method, info.path) == (InstallMethod.UV_TOOL, launcher.resolve())


@pytest.mark.skipif(os.name == "nt", reason="virtualenv interpreters are symlinks on POSIX")
@pytest.mark.parametrize("base", ["opt/homebrew/Cellar/python@3.14/3.14.0/bin/python3.14", "usr/bin/python3"])
def test_module_mode_in_a_virtualenv_is_not_taken_for_a_system_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base: str
) -> None:
    """`python -m observal_cli.main` from a venv whose python links to a Homebrew or /usr/bin interpreter."""
    interpreter = tmp_path / base
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(interpreter)
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "observal_cli" / "main.py"), "auth", "whoami"])
    monkeypatch.setattr(sys, "executable", str(venv_python))
    monkeypatch.setattr(install_detector, "_cached_info", None)
    monkeypatch.setattr(install_detector, "_uv_owns_executable", lambda _path: False)

    info = install_detector.detect()

    # The venv's own python, so pip installs into the venv rather than the base interpreter.
    assert (info.method, info.path, info.managed_by) == (InstallMethod.PIP, venv_python, "pip")


@pytest.mark.skipif(os.name == "nt", reason="POSIX paths")
def test_an_observal_executable_from_homebrew_is_still_left_to_homebrew(tmp_path: Path) -> None:
    binary = Path("/opt/homebrew/Cellar/observal/1.17.0/bin/observal")

    info = install_detector._detect_from_path(binary, str(binary).lower())

    assert (info.method, info.managed_by) == (InstallMethod.SYSTEM_PACKAGE, "brew")


@pytest.mark.parametrize("exit_code", [0, 1])
def test_child_output_in_any_encoding_is_read_without_crashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, exit_code: int
) -> None:
    """Regression: on Windows the start check decoded a child's UTF-8 help text as cp1252, and the reader
    thread's UnicodeDecodeError traceback landed on stderr, breaking JSON mode's stderr contract.

    The child writes bytes that are valid in no encoding a decoder could assume (0x90 is undefined in
    cp1252, 0xff never appears in UTF-8), so a strict decode fails on every platform."""
    script = tmp_path / "child.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(bytes([0xe2, 0x94, 0x80, 0x90, 0xff]))\n"
        "sys.stderr.buffer.write(bytes([0x90, 0xff]) + b' boom')\n"
        f"sys.exit({exit_code})\n"
    )
    monkeypatch.setattr(install_detector, "version_argv", lambda _path: [sys.executable, str(script), "--version"])

    failure = auto_sync._start_failure_at(Path(sys.executable))

    assert failure is None if exit_code == 0 else failure.endswith(" boom")


@pytest.mark.parametrize(
    ("system", "command"),
    [
        ("Windows", "winget install --id Git.Git -e"),
        ("Darwin", "xcode-select --install"),
        ("Linux", "sudo apt install git"),
    ],
)
def test_a_missing_git_is_named_with_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch, system: str, command: str
) -> None:
    import platform

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(platform, "system", lambda: system)

    reason = version_check.missing_git_reason("1.17.0-dev.0")

    assert "needs git" in reason and command in reason
    # Releases come from PyPI and never need git.
    assert version_check.missing_git_reason("1.17.1") is None


def test_sync_stops_before_doing_anything_when_git_is_missing(stage, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version_check, "missing_git_reason", lambda version: f"{version} needs git: install it.")

    assert sync_to_server(SERVER) == "1.17.0-dev.0 needs git: install it."
    stage.execute.assert_not_called()
    assert stage.notices == []


def test_an_installer_missing_from_path_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """Started from a harness or GUI, PATH may lack the uv that installed the CLI."""
    from contextlib import nullcontext

    messages: list[str] = []
    monkeypatch.setattr(upgrade_executor.subprocess, "run", MagicMock(side_effect=FileNotFoundError("uv")))
    monkeypatch.setattr(upgrade_executor, "rprint", messages.append)

    with pytest.raises(typer.Exit):
        upgrade_executor._install_via_uv("1.17.1", "upgrade", lambda _m: nullcontext())

    assert messages == [
        "[red]uv is not on PATH, so it cannot change the CLI it installed; the installed CLI was left unchanged.[/red]"
    ]


def test_manual_upgrade_names_the_missing_git_before_changing_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import nullcontext

    run = MagicMock()
    messages: list[str] = []
    monkeypatch.setattr(upgrade_executor.subprocess, "run", run)
    monkeypatch.setattr(upgrade_executor, "rprint", messages.append)
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(typer.Exit):
        upgrade_executor._install_via_uv("1.17.0.dev0", "upgrade", lambda _m: nullcontext())

    run.assert_not_called()
    assert "needs git" in messages[0] and "left unchanged" in messages[0]


def _venv_copy(root: Path) -> Path:
    """A console script in ``root``/bin whose shebang names that environment's python."""
    (root / "bin").mkdir(parents=True)
    python = root / "bin" / "python"
    python.write_text("")
    script = root / "bin" / "observal"
    script.write_text(f"#!{python}\n")
    return script


@pytest.mark.skipif(os.name == "nt", reason="console scripts carry a shebang only on POSIX")
@pytest.mark.parametrize(
    ("variable", "default_dir", "manager_check"),
    [
        ("PIPX_HOME", ".local/share/pipx/venvs/observal-cli", "_is_pipx_path"),
        ("UV_TOOL_DIR", ".local/share/uv/tools/observal-cli", None),
    ],
)
def test_a_copy_outside_the_managers_current_home_is_installed_in_place(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variable: str, default_dir: str, manager_check: str | None
) -> None:
    """Second review: with PIPX_HOME (or UV_TOOL_DIR) set elsewhere, `pipx install --force` (or `uv tool install`)
    writes there, not to the copy found in the default home. That copy is now updated through its own python."""
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv(variable, str(tmp_path / "custom"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(install_detector, "_uv_owns_executable", lambda _path: False)
    script = _venv_copy(home / default_dir)
    current = InstallInfo(InstallMethod.UV_TOOL, tmp_path / "running" / "observal", True, "uv")

    other = install_detector._classify_other(script, script, current, Path(sys.executable))
    primary = install_detector._detect_from_path(script, str(script).lower())

    assert other.info.method is InstallMethod.PIP and other.interpreter == str(script.parent / "python")
    assert primary.method is InstallMethod.PIP
    monkeypatch.delenv(variable)
    # Without the override that default home is the manager's own again.
    expected = InstallMethod.PIPX if manager_check else InstallMethod.UV_TOOL
    assert install_detector._detect_from_path(script, str(script).lower()).method is expected


@pytest.mark.skipif(shutil.which("git") is None or os.name == "nt", reason="needs git and a POSIX shell")
def test_a_build_with_local_changes_reports_no_commit(tmp_path: Path) -> None:
    """Second review: a snapshot with local changes reported clean HEAD, so the CLI would build other source."""
    repo = tmp_path / "repo"
    for path in (
        "observal-server/version.py",
        "packages/observal-shared/x.py",
        "docker/entrypoint.sh",
        "docker/Dockerfile.api",
    ):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text("v1\n")
    script = Path(__file__).resolve().parent.parent / "scripts" / "docker-build-api.sh"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(f'#!/bin/sh\nprintf "%s\n" "$@" > "{tmp_path}/args"\ncat > /dev/null\n')
    fake_docker.chmod(0o755)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=repo, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()

    def build_commit() -> str:
        subprocess.run(["bash", str(script)], cwd=repo, env={**os.environ, "DOCKER": str(fake_docker)}, check=True)
        arguments = (tmp_path / "args").read_text().splitlines()
        return next(arg for arg in arguments if arg.startswith("OBSERVAL_BUILD_COMMIT=")).split("=", 1)[1]

    assert build_commit() == head
    (repo / "observal-server" / "version.py").write_text("v2, not committed\n")
    assert build_commit() == ""


def test_a_426_response_is_not_counted_as_a_completed_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """CodeRabbit: login counted its own 426 before handling it, so the sync then refused to re-run login."""
    monkeypatch.setattr(client, "_requests_completed", 0)
    rejected = httpx.Response(426, request=httpx.Request("POST", f"{SERVER}/api/v1/auth/login"))
    accepted = httpx.Response(200, request=httpx.Request("POST", f"{SERVER}/api/v1/auth/login"))

    client.record_completed_request(rejected)
    assert client._requests_completed == 0
    client.record_completed_request(accepted)
    client.record_completed_request()
    assert client._requests_completed == 2


def test_pipx_keeps_the_dependency_cap_when_the_path_has_spaces(tmp_path: Path) -> None:
    """CodeRabbit: a Windows profile with a space in its name dropped the cap and installed uncapped."""
    ceiling = tmp_path / "First Last" / "sync-constraints.txt"
    ceiling.parent.mkdir()
    ceiling.write_text("typer<=0.28.0\n")

    argv, env = upgrade_executor._with_ceiling(["pipx", "install", "observal-cli==1.17.1", "--force"], ceiling)

    assert argv == ["pipx", "install", "observal-cli==1.17.1", "--force"]
    location = env["PIP_CONSTRAINT"]
    assert not any(character.isspace() for character in location)  # pip splits this variable on whitespace
    assert location.startswith("file:") and location.endswith("/sync-constraints.txt")
