# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""CLI contracts for embedded lifecycle and Docker server recovery."""

from __future__ import annotations

import json
import socket
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


def completed(returncode: int = 0, *, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stderr=stderr)


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


def test_upgrade_applies_backup_images_and_health_check(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    compose = prepare_compose(isolated, monkeypatch)
    monkeypatch.setattr(isolated.version_check, "verify_server_image_exists", MagicMock(return_value=True))
    backup = isolated.root / "config/backups/v1.0.0-time"
    monkeypatch.setattr(isolated.backup, "create_backup", MagicMock(return_value=backup))
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", MagicMock(return_value="lock"))
    release = MagicMock()
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", release)
    run = MagicMock(side_effect=[completed(), completed()])
    monkeypatch.setattr(cmd_server.subprocess, "run", run)
    monkeypatch.setattr("time.sleep", MagicMock())
    monkeypatch.setattr(httpx, "get", MagicMock(return_value=SimpleNamespace(status_code=200)))

    result = cmd_server._server_upgrade("2.0.0", False, False, True)

    assert result["status"] == "upgraded"
    assert result["backup"] == str(backup)
    assert (compose / ".env").read_text() == "OBSERVAL_VERSION=2.0.0\n"
    assert run.call_args_list[0].args[0] == ["docker", "compose", "pull"]
    release.assert_called_once_with("lock")


def test_rollback_is_confined_and_reports_clickhouse_unchanged(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    compose = prepare_compose(isolated, monkeypatch, "2.0.0")
    backup = isolated.root / "config/backups/v1.5.0-20260101T120000"
    backup.mkdir(parents=True)
    (backup / "pg.dump").write_bytes(b"backup")
    monkeypatch.setattr(isolated.backup, "list_backups", MagicMock(return_value=[{"path": str(backup)}]))
    restore = MagicMock()
    monkeypatch.setattr(isolated.backup, "restore_backup", restore)
    monkeypatch.setattr(isolated.upgrade_lock, "acquire_lock", MagicMock(return_value="lock"))
    monkeypatch.setattr(isolated.upgrade_lock, "release_lock", MagicMock())
    monkeypatch.setattr(cmd_server.subprocess, "run", MagicMock(return_value=completed()))
    monkeypatch.setattr("time.sleep", MagicMock())
    monkeypatch.setattr(httpx, "get", MagicMock(return_value=SimpleNamespace(status_code=200)))

    result = cmd_server._server_rollback(None, True)

    assert result["postgres_restored"] is True
    assert result["clickhouse_restored"] is False
    restore.assert_called_once_with(backup, compose)
    assert (compose / ".env").read_text() == "OBSERVAL_VERSION=1.5.0\n"


def test_rollback_rejects_external_backup(isolated, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prepare_compose(isolated, monkeypatch)
    outside = tmp_path / "v1.0.0-outside"
    outside.mkdir()
    (outside / "pg.dump").write_bytes(b"backup")

    with pytest.raises(Exception) as error:
        cmd_server._server_rollback(str(outside), True)

    assert getattr(error.value, "category", None).value == "permission"


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
