# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""CLI contracts for embedded lifecycle and Docker server recovery."""

from __future__ import annotations

import json
import socket
import subprocess
from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import httpx
import pytest
from rich.console import Console
from typer.main import get_command
from typer.testing import CliRunner

from observal_cli import client, cmd_server
from observal_cli.errors import CliError
from observal_cli.main import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


class RecordingConsole:
    def __init__(self) -> None:
        self.stream = StringIO()
        self.renderer = Console(file=self.stream, color_system=None, force_terminal=False, width=240)

    def print(self, *values, **kwargs) -> None:
        self.renderer.print(*values, **kwargs)

    @contextmanager
    def status(self, _message: str):
        yield

    def text(self) -> str:
        return self.stream.getvalue()


class SocketFactory:
    def __init__(self, available: list[bool]) -> None:
        self.available = iter(available)

    def __call__(self, *_args, **_kwargs):
        available = self.available

        class Socket:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def bind(self, _address):
                if not next(available):
                    raise OSError("busy")

        return Socket()


def completed(returncode: int = 0, *, stderr: str = "", stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stderr=stderr, stdout=stdout)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from observal_cli import upgrade_lock, version_check
    from observal_cli.server import backup, deps, orchestrator, updater

    root = tmp_path / ".observal"
    console = RecordingConsole()
    monkeypatch.setattr(cmd_server, "OBSERVAL_HOME", root)
    monkeypatch.setattr(cmd_server, "CONFIG_DIR", root / "config")
    monkeypatch.setattr(cmd_server, "LOG_DIR", root / "logs")
    monkeypatch.setattr(cmd_server, "console", console)
    monkeypatch.setattr(backup, "BACKUPS_DIR", root / "config" / "backups")
    monkeypatch.setattr(
        client, "get", MagicMock(side_effect=AssertionError("local server commands must not use API auth"))
    )
    return SimpleNamespace(
        root=root,
        console=console,
        backup=backup,
        deps=deps,
        orchestrator=orchestrator,
        updater=updater,
        version_check=version_check,
        upgrade_lock=upgrade_lock,
    )


def test_every_server_leaf_has_json_output() -> None:
    server = get_command(app).commands["server"]
    for name, command in server.commands.items():
        if name == "migrate":
            for leaf in command.commands.values():
                assert any(parameter.name == "output" for parameter in leaf.params)
        else:
            assert any(parameter.name == "output" for parameter in command.params), name


def test_start_json_requires_background(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = MagicMock()
    monkeypatch.setattr("observal_cli.server.orchestrator.Orchestrator", orchestrator)

    result = runner.invoke(app, ["server", "start", "--output", "json"])

    assert result.exit_code == 7
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "validation"
    orchestrator.assert_not_called()


def test_start_json_is_finite_and_suppresses_nested_output(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "socket", SocketFactory([True]))
    monkeypatch.setattr(isolated.deps, "all_installed", MagicMock(return_value=True))
    monkeypatch.setattr(isolated.updater, "check_for_update", MagicMock())
    service = MagicMock()
    service.is_running.return_value = False
    service.status.return_value = {
        "postgres": "running",
        "clickhouse": "running",
        "redis": "running",
        "api": "running",
    }
    service.start_all.side_effect = lambda **_kwargs: print("nested human progress")
    factory = MagicMock(return_value=service)
    monkeypatch.setattr(isolated.orchestrator, "Orchestrator", factory)

    result = runner.invoke(app, ["server", "start", "--background", "--output", "json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "status": "started",
        "mode": "embedded",
        "host": "0.0.0.0",
        "port": 8000,
        "background": True,
        "used_fallback_port": False,
        "services": [
            {"service": "postgres", "status": "running"},
            {"service": "clickhouse", "status": "running"},
            {"service": "redis", "status": "running"},
            {"service": "api", "status": "running"},
        ],
    }
    assert "nested human progress" not in result.stdout
    service.start_all.assert_called_once_with(foreground=False)


def test_start_reports_port_conflict_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "socket", SocketFactory([False, False, False, False, False]))

    result = runner.invoke(app, ["server", "start", "--background", "--output", "json"])

    assert result.exit_code == 6
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "conflict"


def test_start_reports_explicit_port_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "socket", SocketFactory([False]))

    result = runner.invoke(
        app,
        ["server", "start", "--port", "9000", "--background", "--output", "json"],
    )

    assert result.exit_code == 6
    assert json.loads(result.stderr)["error"]["resource"] == "TCP port 9000"


def test_restart_and_stop_json(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock()
    service.is_running.return_value = True
    service.status.side_effect = [
        {"postgres": "running", "clickhouse": "running", "redis": "running", "api": "running"},
        {"postgres": "stopped", "clickhouse": "stopped", "redis": "stopped", "api": "stopped"},
    ]
    monkeypatch.setattr(isolated.orchestrator, "Orchestrator", MagicMock(return_value=service))

    restarted = runner.invoke(app, ["server", "restart", "--background", "--output", "json"])
    stopped = runner.invoke(app, ["server", "stop", "--output", "json"])

    assert json.loads(restarted.stdout)["status"] == "restarted"
    assert json.loads(stopped.stdout)["status"] == "stopped"
    service.stop_all.assert_called()
    service.start_all.assert_called_once_with(foreground=False)


def test_lifecycle_commands_fail_when_final_state_is_not_verified(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock()
    service.is_running.return_value = False
    service.status.return_value = {
        "postgres": "running",
        "clickhouse": "running",
        "redis": "running",
        "api": "stopped",
    }
    monkeypatch.setattr(socket, "socket", SocketFactory([True]))
    monkeypatch.setattr(isolated.deps, "all_installed", MagicMock(return_value=True))
    monkeypatch.setattr(isolated.updater, "check_for_update", MagicMock())
    monkeypatch.setattr(isolated.orchestrator, "Orchestrator", MagicMock(return_value=service))

    result = runner.invoke(app, ["server", "start", "--background", "--output", "json"])

    assert result.exit_code == 9
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert error["result"]["expected"] == "running"
    assert error["result"]["services"][-1] == {"service": "api", "status": "stopped"}


def test_stop_fails_when_any_service_remains_running(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock()
    service.status.return_value = {
        "postgres": "stopped",
        "clickhouse": "stopped",
        "redis": "running",
        "api": "stopped",
    }
    monkeypatch.setattr(isolated.orchestrator, "Orchestrator", MagicMock(return_value=service))

    result = runner.invoke(app, ["server", "stop", "--output", "json"])

    assert result.exit_code == 9
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["result"]["expected"] == "stopped"


def test_lifecycle_rejects_incomplete_status_response(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock()
    service.status.return_value = {"api": "running"}
    monkeypatch.setattr(isolated.orchestrator, "Orchestrator", MagicMock(return_value=service))

    result = runner.invoke(app, ["server", "stop", "--output", "json"])

    assert result.exit_code == 9
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert "incomplete" in error["message"]
    assert error["result"] == {
        "expected": "stopped",
        "services": [{"service": "api", "status": "running"}],
    }


def test_status_json_can_report_unhealthy(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock(port=8123)
    service.status.return_value = {
        "postgres": "running",
        "clickhouse": "stopped",
        "redis": "running",
        "api": "stopped",
    }
    monkeypatch.setattr(isolated.orchestrator, "Orchestrator", MagicMock(return_value=service))

    result = runner.invoke(app, ["server", "status", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["healthy"] is False
    assert payload["services"][-1] == {"service": "api", "status": "stopped", "port": 8123}


def test_logs_json_snapshot_and_validation(isolated: SimpleNamespace) -> None:
    isolated.root.joinpath("logs").mkdir(parents=True)
    isolated.root.joinpath("logs/api.log").write_text("first\n[bold]literal[/bold]\nthird\n")

    result = runner.invoke(app, ["server", "logs", "api", "--lines", "2", "--output", "json"])
    invalid = runner.invoke(app, ["server", "logs", "worker", "--output", "json"])

    assert json.loads(result.stdout)["logs"][0]["lines"] == ["[bold]literal[/bold]", "third"]
    assert invalid.exit_code == 7
    assert invalid.stdout == ""


def test_follow_json_emits_json_lines(isolated: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    isolated.root.joinpath("logs").mkdir(parents=True)
    isolated.root.joinpath("logs/api.log").write_text("ready\n")
    process = MagicMock()
    process.stdout = iter(["one\n", "two\n"])
    monkeypatch.setattr(cmd_server.subprocess, "Popen", MagicMock(return_value=process))

    result = runner.invoke(app, ["server", "logs", "api", "--follow", "--output", "json"])

    assert result.exit_code == 0
    assert [json.loads(line)["line"] for line in result.stdout.splitlines()] == ["one", "two"]
    process.terminate.assert_called_once_with()


def test_reset_json_requires_force_and_reports_scope(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock()
    monkeypatch.setattr(isolated.orchestrator, "Orchestrator", MagicMock(return_value=service))

    refused = runner.invoke(app, ["server", "reset", "--output", "json"])
    accepted = runner.invoke(app, ["server", "reset", "--force", "--output", "json"])

    assert refused.exit_code == 7
    assert json.loads(accepted.stdout)["deleted"] == ["postgres", "clickhouse", "redis", "generated secrets"]
    service.reset.assert_called_once_with()


def test_config_json_contains_no_secrets(isolated: SimpleNamespace) -> None:
    result = runner.invoke(app, ["server", "config", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["mode"] == "embedded"
    assert "secret" not in result.stdout.lower()
    assert "token" not in result.stdout.lower()


def test_install_json_categorizes_checksum_failure(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(isolated.deps, "install_dependencies", MagicMock(side_effect=RuntimeError("checksum")))

    result = runner.invoke(app, ["server", "install", "--output", "json"])

    assert result.exit_code == 9
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["category"] == "unavailable"


def test_update_env_version_is_atomic_and_preserves_unrelated_values(tmp_path: Path) -> None:
    compose = tmp_path / "compose"
    compose.mkdir()
    env = compose / ".env"
    env.write_text("BEFORE=1\nOBSERVAL_VERSION=1.0.0\nAFTER=2\n")

    cmd_server._update_env_version(compose, "2.0.0")

    assert env.read_text() == "BEFORE=1\nOBSERVAL_VERSION=2.0.0\nAFTER=2\n"
    assert env.stat().st_mode & 0o777 == 0o600
    assert not list(compose.glob(".env.*"))


def prepare_compose(isolated: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, version: str = "1.0.0") -> Path:
    compose = isolated.root / "compose"
    compose.mkdir(parents=True)
    (compose / "compose.yml").write_text("services: {}\n")
    (compose / ".env").write_text(f"OBSERVAL_VERSION={version}\n")
    monkeypatch.setattr(cmd_server, "_find_compose_dir", lambda: compose)
    return compose


def test_upgrade_json_requires_force_but_dry_run_does_not(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    operation = MagicMock(return_value={"status": "planned", "changed": False})
    monkeypatch.setattr(cmd_server, "_server_upgrade", operation)

    refused = runner.invoke(app, ["server", "upgrade", "--version", "2.0.0", "--output", "json"])
    preview = runner.invoke(app, ["server", "upgrade", "--version", "2.0.0", "--dry-run", "--output", "json"])

    assert refused.exit_code == 7
    assert json.loads(preview.stdout)["status"] == "planned"
    operation.assert_called_once_with("2.0.0", False, True, False)
    assert client.get.call_count == 0


def completed_config(version: str, owner: str = "agenticresourceregistry") -> SimpleNamespace:
    api = f"ghcr.io/{owner}/observal-api:{version}"
    web = f"ghcr.io/{owner}/observal-web:{version}"
    return SimpleNamespace(
        returncode=0,
        stdout=json.dumps(
            {
                "services": {
                    "observal-api": {"image": api},
                    "observal-web": {"image": web},
                    "observal-init": {"image": api},
                    "observal-worker": {"image": api},
                }
            }
        ),
    )


def compose_result(args, **kwargs):
    if args[2] == "config":
        return completed_config(kwargs["env"]["OBSERVAL_VERSION"])
    return completed()


@pytest.mark.parametrize("bind, host", [(None, "localhost"), ("::", "[::1]")])
def test_upgrade_applies_backup_images_and_health_check(
    isolated, monkeypatch: pytest.MonkeyPatch, bind: str | None, host: str
) -> None:
    compose = prepare_compose(isolated, monkeypatch)
    if bind:
        (compose / ".env").write_text(f"OBSERVAL_VERSION=1.0.0\nOBSERVAL_BIND_ADDRESS={bind}\n")
    monkeypatch.setattr(isolated.version_check, "verify_server_image_exists", MagicMock(return_value=True))
    backup = isolated.root / "config/backups/v1.0.0-time"
    monkeypatch.setattr(isolated.backup, "create_backup", MagicMock(return_value=backup))
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", MagicMock(return_value="lock"))
    release = MagicMock()
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", release)
    run = MagicMock(side_effect=compose_result)
    monkeypatch.setattr(cmd_server.subprocess, "run", run)
    monkeypatch.setattr("time.sleep", MagicMock())
    health = MagicMock(return_value=SimpleNamespace(status_code=200))
    monkeypatch.setattr(httpx, "get", health)

    result = cmd_server._server_upgrade("2.0.0", False, False, True)

    assert result["status"] == "upgraded"
    assert result["backup"] == str(backup)
    assert (compose / ".env").read_text() == "OBSERVAL_VERSION=2.0.0\n" + (
        f"OBSERVAL_BIND_ADDRESS={bind}\n" if bind else ""
    )
    assert [call.args[0] for call in run.call_args_list] == [
        ["docker", "compose", "config", "--format", "json"],
        ["docker", "compose", "pull"],
        ["docker", "compose", "stop", "observal-api", "observal-worker", "observal-init"],
        ["docker", "compose", "ps", "--status", "running", "--services"],
        ["docker", "compose", "up", "-d", "--pull", "never", "--no-build"],
        ["docker", "compose", "restart", "observal-lb"],
    ]
    health.assert_called_once_with(
        f"http://{host}:8000/health", timeout=5, trust_env=False, headers={"Host": "localhost"}
    )
    release.assert_called_once_with("lock")


def test_failed_upgrade_health_check_stops_writers_without_restarting_old_version(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    compose = prepare_compose(isolated, monkeypatch)
    monkeypatch.setattr(isolated.version_check, "verify_server_image_exists", MagicMock(return_value=True))
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", MagicMock(return_value="lock"))
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())
    run = MagicMock(side_effect=compose_result)
    monkeypatch.setattr(cmd_server.subprocess, "run", run)
    monkeypatch.setattr("time.sleep", MagicMock())
    monkeypatch.setattr(httpx, "get", MagicMock(return_value=SimpleNamespace(status_code=502)))

    with pytest.raises(Exception) as error:
        cmd_server._server_upgrade("2.0.0", True, False, True)

    assert getattr(error.value, "category", None).value == "unavailable"
    assert (compose / ".env").read_text() == "OBSERVAL_VERSION=2.0.0\n"
    assert [call.args[0] for call in run.call_args_list] == [
        ["docker", "compose", "config", "--format", "json"],
        ["docker", "compose", "pull"],
        ["docker", "compose", "stop", "observal-api", "observal-worker", "observal-init"],
        ["docker", "compose", "ps", "--status", "running", "--services"],
        ["docker", "compose", "up", "-d", "--pull", "never", "--no-build"],
        ["docker", "compose", "restart", "observal-lb"],
        ["docker", "compose", "stop", "observal-api", "observal-worker", "observal-init"],
        ["docker", "compose", "ps", "--status", "running", "--services"],
    ]
    assert "manual" in error.value.remediation.lower()


def test_hung_load_balancer_restart_still_reports_the_failed_health_check(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    compose = prepare_compose(isolated, monkeypatch)
    monkeypatch.setattr(isolated.version_check, "verify_server_image_exists", MagicMock(return_value=True))
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", MagicMock(return_value="lock"))
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())

    def run(args, **kwargs):
        if args[2] == "config":
            return completed_config(kwargs["env"]["OBSERVAL_VERSION"])
        if args[2] == "restart":
            raise subprocess.TimeoutExpired(args, 120)
        return completed()

    monkeypatch.setattr(cmd_server.subprocess, "run", MagicMock(side_effect=run))
    monkeypatch.setattr("time.sleep", MagicMock())
    monkeypatch.setattr(httpx, "get", MagicMock(return_value=SimpleNamespace(status_code=502)))

    with pytest.raises(Exception) as error:
        cmd_server._server_upgrade("2.0.0", True, False, True)

    assert not isinstance(error.value, subprocess.SubprocessError)
    assert getattr(error.value, "category", None).value == "unavailable"
    assert (compose / ".env").read_text() == "OBSERVAL_VERSION=2.0.0\n"


def test_load_balancer_refresh_tolerates_missing_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_server.subprocess, "run", MagicMock(side_effect=FileNotFoundError("docker")))
    cmd_server._refresh_load_balancer(tmp_path)


@pytest.mark.parametrize(
    ("bind", "host"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("0.0.0.0", "localhost"),
        ("192.0.2.10", "192.0.2.10"),
        ("::", "[::1]"),
        ("2001:db8::10", "[2001:db8::10]"),
    ],
)
def test_health_url_targets_the_configured_load_balancer_bind(tmp_path: Path, bind: str, host: str) -> None:
    (tmp_path / ".env").write_text(f"API_HOST_PORT=41800\nOBSERVAL_BIND_ADDRESS={bind}\n")
    assert cmd_server._get_health_url(tmp_path) == f"http://{host}:41800/health"


def test_packaged_health_uses_api_port_not_source_compose_lb_port(tmp_path: Path) -> None:
    (tmp_path / "setup.sh").write_text("#!/bin/sh\n")
    (tmp_path / ".env").write_text("API_HOST_PORT=41800\nLB_HOST_PORT=38080\n")
    assert cmd_server._get_health_url(tmp_path) == "http://localhost:41800/health"


def test_invalid_bind_address_is_rejected_before_health_check(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("API_HOST_PORT=41800\nOBSERVAL_BIND_ADDRESS=not-an-ip\n")
    with pytest.raises(CliError) as error:
        cmd_server._get_health_url(tmp_path)
    assert error.value.category.value == "validation"


@pytest.mark.parametrize("owner", ["agenticresourceregistry", "observal"])
@pytest.mark.parametrize("bind, host", [(None, "localhost"), ("::", "[::1]")])
def test_rollback_is_confined_and_reports_clickhouse_unchanged(
    isolated, monkeypatch: pytest.MonkeyPatch, owner: str, bind: str | None, host: str
) -> None:
    compose = prepare_compose(isolated, monkeypatch, "2.0.0")
    if bind:
        (compose / ".env").write_text(f"OBSERVAL_VERSION=2.0.0\nOBSERVAL_BIND_ADDRESS={bind}\n")
    backup = isolated.root / "config/backups/v1.5.0-20260101T120000"
    backup.mkdir(parents=True)
    (backup / "pg.dump").write_bytes(b"backup")
    monkeypatch.setattr(isolated.backup, "list_backups", MagicMock(return_value=[{"path": str(backup)}]))
    restore = MagicMock()
    monkeypatch.setattr(isolated.backup, "restore_backup", restore)
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", MagicMock(return_value="lock"))
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())
    run = MagicMock(
        side_effect=lambda args, **kwargs: (
            completed_config(kwargs["env"]["OBSERVAL_VERSION"], owner) if args[2] == "config" else completed()
        )
    )
    monkeypatch.setattr(cmd_server.subprocess, "run", run)
    monkeypatch.setattr("time.sleep", MagicMock())
    health = MagicMock(return_value=SimpleNamespace(status_code=200))
    monkeypatch.setattr(httpx, "get", health)

    result = cmd_server._server_rollback(str(backup), True)

    assert result["postgres_restored"] is True
    assert result["clickhouse_restored"] is False
    restore.assert_called_once_with(backup, compose)
    assert (compose / ".env").read_text() == "OBSERVAL_VERSION=1.5.0\n" + (
        f"OBSERVAL_BIND_ADDRESS={bind}\n" if bind else ""
    )
    health.assert_called_once_with(
        f"http://{host}:8000/health", timeout=5, trust_env=False, headers={"Host": "localhost"}
    )
    assert [call.args[0] for call in run.call_args_list] == [
        ["docker", "compose", "config", "--format", "json"],
        ["docker", "compose", "pull", "observal-api", "observal-web", "observal-init", "observal-worker"],
        ["docker", "compose", "stop", "observal-api", "observal-worker", "observal-init"],
        ["docker", "compose", "ps", "--status", "running", "--services"],
        ["docker", "compose", "up", "-d", "--pull", "never", "--no-build"],
        ["docker", "compose", "restart", "observal-lb"],
    ]


def test_rollback_without_selected_backup_refuses_before_lock_or_restore(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare_compose(isolated, monkeypatch, "2.0.0")
    backup = isolated.root / "config/backups/v1.0.0-time"
    backup.mkdir(parents=True)
    (backup / "pg.dump").write_bytes(b"backup")
    lock = MagicMock()
    restore = MagicMock()
    run = MagicMock()
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", lock)
    monkeypatch.setattr(isolated.backup, "restore_backup", restore)
    monkeypatch.setattr(cmd_server.subprocess, "run", run)

    result = runner.invoke(app, ["server", "rollback", "--force", "--output", "json"])

    assert result.exit_code == 7
    assert "--from-backup" in json.loads(result.stderr)["error"]["remediation"]
    lock.assert_not_called()
    restore.assert_not_called()
    run.assert_not_called()


def test_rollback_rejects_external_backup(isolated, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prepare_compose(isolated, monkeypatch)
    outside = tmp_path / "v1.0.0-outside"
    outside.mkdir()
    (outside / "pg.dump").write_bytes(b"backup")

    with pytest.raises(Exception) as error:
        cmd_server._server_rollback(str(outside), True)

    assert getattr(error.value, "category", None).value == "permission"


def test_static_compose_refused_even_in_dry_run(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_compose(isolated, monkeypatch)
    monkeypatch.setattr(isolated.version_check, "verify_server_image_exists", MagicMock(return_value=True))
    run = MagicMock(return_value=completed_config("latest"))
    monkeypatch.setattr(cmd_server.subprocess, "run", run)
    backup = MagicMock()
    monkeypatch.setattr(isolated.backup, "create_backup", backup)

    result = runner.invoke(app, ["server", "upgrade", "--version", "2.0.0", "--dry-run", "--output", "json"])

    assert result.exit_code == 7, (result.stderr, repr(result.exception))
    assert "Compose" in json.loads(result.stderr)["error"]["message"]
    assert [c.args[0][2] for c in run.call_args_list] == ["config"]
    backup.assert_not_called()


def test_missing_upgrade_image_never_creates_backup(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_compose(isolated, monkeypatch)
    monkeypatch.setattr(isolated.version_check, "verify_server_image_exists", MagicMock(return_value=True))
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", MagicMock(return_value="lock"))
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())
    backup = MagicMock()
    monkeypatch.setattr(isolated.backup, "create_backup", backup)
    run = MagicMock(
        side_effect=lambda args, **kwargs: completed(1) if args[2] == "pull" else compose_result(args, **kwargs)
    )
    monkeypatch.setattr(cmd_server.subprocess, "run", run)

    result = runner.invoke(app, ["server", "upgrade", "--version", "2.0.0", "--force", "--output", "json"])

    assert result.exit_code == 9
    assert [c.args[0][2] for c in run.call_args_list] == ["config", "pull"]
    backup.assert_not_called()


def test_failed_upgrade_up_stops_writers_without_relaunch(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    compose = prepare_compose(isolated, monkeypatch)
    monkeypatch.setattr(isolated.version_check, "verify_server_image_exists", lambda _version: True)
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", lambda _kind: "lock")
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())
    run = MagicMock(
        side_effect=lambda args, **kwargs: completed(1) if args[2] == "up" else compose_result(args, **kwargs)
    )
    monkeypatch.setattr(cmd_server.subprocess, "run", run)

    result = runner.invoke(
        app, ["server", "upgrade", "--version", "2.0.0", "--skip-backup", "--force", "--output", "json"]
    )

    assert result.exit_code == 9
    assert "manual" in json.loads(result.stderr)["error"]["remediation"].lower()
    assert [c.args[0][2] for c in run.call_args_list] == ["config", "pull", "stop", "ps", "up", "stop", "ps"]
    assert (compose / ".env").read_text() == "OBSERVAL_VERSION=2.0.0\n"


def test_rollback_missing_images_does_not_restore(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    compose = prepare_compose(isolated, monkeypatch, "2.0.0")
    backup = isolated.root / "config/backups/v1.0.0-time"
    backup.mkdir(parents=True)
    (backup / "pg.dump").write_bytes(b"backup")
    monkeypatch.setattr(isolated.backup, "list_backups", lambda: [{"path": str(backup)}])
    restore = MagicMock()
    monkeypatch.setattr(isolated.backup, "restore_backup", restore)
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", MagicMock(return_value="lock"))
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())

    def run_command(args, **kwargs):
        if args[2] == "config":
            return completed_config(kwargs["env"]["OBSERVAL_VERSION"], "observal")
        return completed(1, stderr="manifest unknown: ghcr.io/observal/observal-web:1.0.0")

    run = MagicMock(side_effect=run_command)
    monkeypatch.setattr(cmd_server.subprocess, "run", run)

    result = runner.invoke(app, ["server", "rollback", "--from-backup", str(backup), "--force", "--output", "json"])

    assert result.exit_code == 9
    assert [c.args[0] for c in run.call_args_list] == [
        ["docker", "compose", "config", "--format", "json"],
        ["docker", "compose", "pull", "observal-api", "observal-web", "observal-init", "observal-worker"],
    ]
    assert (compose / ".env").read_text() == "OBSERVAL_VERSION=2.0.0\n"
    restore.assert_not_called()


def test_rollback_rejects_mismatched_compose_without_restore(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_compose(isolated, monkeypatch, "2.0.0")
    backup = isolated.root / "config/backups/v1.0.0-time"
    backup.mkdir(parents=True)
    (backup / "pg.dump").write_bytes(b"backup")
    monkeypatch.setattr(isolated.backup, "list_backups", lambda: [{"path": str(backup)}])
    restore = MagicMock()
    monkeypatch.setattr(isolated.backup, "restore_backup", restore)
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", lambda _kind: "lock")
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())
    bad = completed_config("1.0.0")
    data = json.loads(bad.stdout)
    data["services"]["observal-web"]["image"] = "ghcr.io/other/observal-web:1.0.0"
    bad.stdout = json.dumps(data)
    run = MagicMock(return_value=bad)
    monkeypatch.setattr(cmd_server.subprocess, "run", run)

    result = runner.invoke(app, ["server", "rollback", "--from-backup", str(backup), "--force", "--output", "json"])

    assert result.exit_code == 7
    assert [c.args[0][2] for c in run.call_args_list] == ["config"]
    restore.assert_not_called()


@pytest.mark.parametrize("action", ["upgrade", "rollback"])
@pytest.mark.parametrize(
    "key",
    [
        "OBSERVAL_VERSION",
        "COMPOSE_FILE",
        "COMPOSE_ENV_FILES",
        "COMPOSE_DISABLE_ENV_FILE",
        "API_HOST_PORT",
        "LB_HOST_PORT",
        "OBSERVAL_BIND_ADDRESS",
    ],
)
def test_external_compose_override_refused_before_mutation(
    isolated, monkeypatch: pytest.MonkeyPatch, action: str, key: str
) -> None:
    compose = prepare_compose(isolated, monkeypatch, "2.0.0" if action == "rollback" else "1.0.0")
    monkeypatch.setenv(key, "9.9.9" if key == "OBSERVAL_VERSION" else "alternate.env")
    monkeypatch.setattr(isolated.version_check, "verify_server_image_exists", lambda _: True)
    backup = isolated.root / "config/backups/v1.0.0-time"
    backup.mkdir(parents=True)
    (backup / "pg.dump").write_bytes(b"backup")
    monkeypatch.setattr(isolated.backup, "list_backups", lambda: [{"path": str(backup)}])
    create = MagicMock()
    restore = MagicMock()
    monkeypatch.setattr(isolated.backup, "create_backup", create)
    monkeypatch.setattr(isolated.backup, "restore_backup", restore)
    run = MagicMock(side_effect=compose_result)
    monkeypatch.setattr(cmd_server.subprocess, "run", run)
    args = ["--version", "2.0.0"] if action == "upgrade" else ["--from-backup", str(backup)]

    result = runner.invoke(app, ["server", action, *args, "--force", "--output", "json"])

    assert result.exit_code == 7, (result.stderr, repr(result.exception))
    assert json.loads(result.stderr)["error"]["category"] == "validation"
    assert (
        (compose / ".env").read_text().startswith("OBSERVAL_VERSION=" + ("2.0.0" if action == "rollback" else "1.0.0"))
    )
    assert all(c.args[0][2] not in ("pull", "up", "stop") for c in run.call_args_list)
    create.assert_not_called()
    restore.assert_not_called()


@pytest.mark.parametrize("action", ["upgrade", "rollback"])
@pytest.mark.parametrize(
    "prefix, separator", [("", "="), ("  ", "="), ("export ", "="), ("", ": "), ("  ", ": "), ("export ", ": ")]
)
def test_duplicate_env_version_refused_before_backup_or_restore(
    isolated, monkeypatch: pytest.MonkeyPatch, action: str, prefix: str, separator: str
) -> None:
    compose = prepare_compose(isolated, monkeypatch, "2.0.0" if action == "rollback" else "1.0.0")
    initial = (compose / ".env").read_text()
    duplicate = f"{prefix}OBSERVAL_VERSION{separator}9.9.9\n"
    (compose / ".env").write_text(initial + duplicate)
    monkeypatch.setattr(isolated.version_check, "verify_server_image_exists", lambda _: True)
    backup = isolated.root / "config/backups/v1.0.0-time"
    backup.mkdir(parents=True)
    (backup / "pg.dump").write_bytes(b"backup")
    monkeypatch.setattr(isolated.backup, "list_backups", lambda: [{"path": str(backup)}])
    create, restore = MagicMock(), MagicMock()
    monkeypatch.setattr(isolated.backup, "create_backup", create)
    monkeypatch.setattr(isolated.backup, "restore_backup", restore)
    lock = MagicMock(return_value="lock")
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", lock)
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())
    run = MagicMock(side_effect=compose_result)
    monkeypatch.setattr(cmd_server.subprocess, "run", run)

    options = ["--version", "2.0.0"] if action == "upgrade" else ["--from-backup", str(backup)]
    result = runner.invoke(app, ["server", action, *options, "--force", "--output", "json"])

    assert result.exit_code == 7, (result.stderr, repr(result.exception))
    assert run.call_count == 0
    lock.assert_not_called()
    create.assert_not_called()
    restore.assert_not_called()
    assert (compose / ".env").read_text() == initial + duplicate


@pytest.mark.parametrize("action", ["upgrade", "rollback"])
@pytest.mark.parametrize(
    "entry", ["OBSERVAL_VERSION: 9.9.9", " OBSERVAL_VERSION=9.9.9", "export OBSERVAL_VERSION=9.9.9"]
)
def test_noncanonical_env_version_refused_before_mutation(
    isolated, monkeypatch: pytest.MonkeyPatch, action: str, entry: str
) -> None:
    compose = prepare_compose(isolated, monkeypatch)
    (compose / ".env").write_text(entry + "\n")
    backup = isolated.root / "config/backups/v1.0.0-time"
    backup.mkdir(parents=True)
    (backup / "pg.dump").write_bytes(b"backup")
    lock = MagicMock()
    run = MagicMock()
    create = MagicMock()
    restore = MagicMock()
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", lock)
    monkeypatch.setattr(cmd_server.subprocess, "run", run)
    monkeypatch.setattr(isolated.backup, "create_backup", create)
    monkeypatch.setattr(isolated.backup, "restore_backup", restore)
    options = ["--version", "2.0.0"] if action == "upgrade" else ["--from-backup", str(backup)]

    result = runner.invoke(app, ["server", action, *options, "--force", "--output", "json"])

    assert result.exit_code == 7, (result.stderr, repr(result.exception))
    lock.assert_not_called()
    run.assert_not_called()
    create.assert_not_called()
    restore.assert_not_called()


def test_rollback_missing_init_refused_before_restore(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_compose(isolated, monkeypatch, "2.0.0")
    backup = isolated.root / "config/backups/v1.0.0-time"
    backup.mkdir(parents=True)
    (backup / "pg.dump").write_bytes(b"backup")
    monkeypatch.setattr(isolated.backup, "list_backups", lambda: [{"path": str(backup)}])
    restore = MagicMock()
    monkeypatch.setattr(isolated.backup, "restore_backup", restore)
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", lambda _: "lock")
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())
    config = completed_config("1.0.0")
    data = json.loads(config.stdout)
    del data["services"]["observal-init"]
    config.stdout = json.dumps(data)
    run = MagicMock(return_value=config)
    monkeypatch.setattr(cmd_server.subprocess, "run", run)

    result = runner.invoke(app, ["server", "rollback", "--from-backup", str(backup), "--force", "--output", "json"])

    assert result.exit_code == 7
    assert [c.args[0][2] for c in run.call_args_list] == ["config"]
    restore.assert_not_called()


def test_partial_restore_error_requires_manual_recovery_without_startup(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare_compose(isolated, monkeypatch, "2.0.0")
    backup = isolated.root / "config/backups/v1.0.0-time"
    backup.mkdir(parents=True)
    (backup / "pg.dump").write_bytes(b"backup")
    restore = MagicMock(side_effect=RuntimeError("secret-containing pg_restore error"))
    monkeypatch.setattr(isolated.backup, "restore_backup", restore)
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", lambda _: "lock")
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())
    run = MagicMock(side_effect=compose_result)
    monkeypatch.setattr(cmd_server.subprocess, "run", run)

    result = runner.invoke(app, ["server", "rollback", "--from-backup", str(backup), "--force", "--output", "json"])

    assert result.exit_code == 9
    payload = json.loads(result.stderr)["error"]
    assert "partially restored" in payload["remediation"]
    assert "secret-containing" not in result.stderr
    assert [c.args[0][2] for c in run.call_args_list] == ["config", "pull", "stop", "ps"]
    restore.assert_called_once_with(backup, isolated.root / "compose")


@pytest.mark.parametrize("failure", ["up", "up_exception", "health"])
def test_rollback_failure_stops_writers_after_restore(isolated, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    prepare_compose(isolated, monkeypatch, "2.0.0")
    backup = isolated.root / "config/backups/v1.0.0-time"
    backup.mkdir(parents=True)
    (backup / "pg.dump").write_bytes(b"backup")
    monkeypatch.setattr(isolated.backup, "list_backups", lambda: [{"path": str(backup)}])
    monkeypatch.setattr(isolated.backup, "restore_backup", MagicMock())
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", lambda _: "lock")
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())

    def run_command(args, **_kwargs):
        if args[2] == "config":
            return completed_config("1.0.0")
        if args[2] == "up" and failure == "up_exception":
            raise subprocess.TimeoutExpired(args, 300)
        return completed(1 if args[2] == failure else 0)

    run = MagicMock(side_effect=run_command)
    monkeypatch.setattr(cmd_server.subprocess, "run", run)
    monkeypatch.setattr("time.sleep", MagicMock())
    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: SimpleNamespace(status_code=502))

    result = runner.invoke(app, ["server", "rollback", "--from-backup", str(backup), "--force", "--output", "json"])

    assert result.exit_code == 9
    assert [c.args[0][2] for c in run.call_args_list].count("stop") == 2
    assert [c.args[0][2] for c in run.call_args_list][-2:] == ["stop", "ps"]


def test_upgrade_failed_stop_does_not_claim_writers_stopped(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_compose(isolated, monkeypatch)
    monkeypatch.setattr(isolated.version_check, "verify_server_image_exists", lambda _: True)
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", lambda _: "lock")
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())
    run = MagicMock(
        side_effect=lambda args, **kwargs: completed(1) if args[2] == "stop" else compose_result(args, **kwargs)
    )
    monkeypatch.setattr(cmd_server.subprocess, "run", run)
    result = runner.invoke(
        app, ["server", "upgrade", "--version", "2.0.0", "--skip-backup", "--force", "--output", "json"]
    )
    assert result.exit_code == 9
    assert "stopped" not in json.loads(result.stderr)["error"]["remediation"].lower()
    assert [c.args[0][2] for c in run.call_args_list] == ["config", "pull", "stop"]


@pytest.mark.parametrize("action", ["upgrade", "rollback"])
def test_writer_still_running_prevents_recreation_and_restore(
    isolated, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    current = "1.0.0" if action == "upgrade" else "2.0.0"
    compose = prepare_compose(isolated, monkeypatch, current)
    monkeypatch.setattr(isolated.version_check, "verify_server_image_exists", lambda _: True)
    backup = isolated.root / "config/backups/v1.0.0-time"
    backup.mkdir(parents=True)
    (backup / "pg.dump").write_bytes(b"backup")
    monkeypatch.setattr(isolated.backup, "list_backups", lambda: [{"path": str(backup)}])
    restore = MagicMock()
    monkeypatch.setattr(isolated.backup, "restore_backup", restore)
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", lambda _: "lock")
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())

    def run_command(args, **kwargs):
        if args[2] == "ps":
            return completed(stdout="observal-web\nobserval-worker\n")
        return compose_result(args, **kwargs)

    run = MagicMock(side_effect=run_command)
    monkeypatch.setattr(cmd_server.subprocess, "run", run)
    options = ["--version", "2.0.0", "--skip-backup"] if action == "upgrade" else ["--from-backup", str(backup)]

    result = runner.invoke(app, ["server", action, *options, "--force", "--output", "json"])

    assert result.exit_code == 9, (result.stderr, repr(result.exception))
    assert "writers" in json.loads(result.stderr)["error"]["message"].lower()
    assert [call.args[0] for call in run.call_args_list] == [
        ["docker", "compose", "config", "--format", "json"],
        (
            ["docker", "compose", "pull"]
            if action == "upgrade"
            else ["docker", "compose", "pull", "observal-api", "observal-web", "observal-init", "observal-worker"]
        ),
        ["docker", "compose", "stop", "observal-api", "observal-worker", "observal-init"],
        ["docker", "compose", "ps", "--status", "running", "--services"],
    ]
    assert (compose / ".env").read_text() == f"OBSERVAL_VERSION={current}\n"
    restore.assert_not_called()


def test_versions_json_uses_local_authority(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_compose(isolated, monkeypatch, "1.5.0")
    monkeypatch.setattr(
        isolated.version_check, "fetch_available_server_images", MagicMock(return_value=["2.0.0", "1.5.0"])
    )
    monkeypatch.setattr(
        isolated.backup,
        "list_backups",
        MagicMock(return_value=[{"name": "v1.5.0-time", "size_mb": 4, "path": "/managed"}]),
    )

    result = runner.invoke(app, ["server", "versions", "--output", "json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["current_version"] == "1.5.0"
    assert payload["versions"][0]["backup"]["size_mb"] == 4
    assert client.get.call_count == 0
