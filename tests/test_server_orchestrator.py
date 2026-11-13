# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Isolated lifecycle tests for the embedded server orchestrator."""

from __future__ import annotations

import signal
import stat
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, call

import httpx
import pytest

from observal_cli.server import orchestrator as orchestrator_module
from observal_cli.server.orchestrator import Orchestrator, ServiceError

if TYPE_CHECKING:
    from pathlib import Path

LONG_OPTION = "-" * 2


class FakeConsole:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *values: object) -> None:
        self.messages.append(" ".join(str(value) for value in values))

    def text(self) -> str:
        return "\n".join(self.messages)


class Clock:
    def __init__(self) -> None:
        self.current = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds


def completed(returncode: int = 0, *, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def blocked(name: str):
    def fail(*args, **kwargs):
        raise AssertionError(f"unmocked boundary: {name}, args={args}, kwargs={kwargs}")

    return fail


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Redirect every path and block process, network, signal, and timer boundaries."""
    home = tmp_path / "user"
    root = home / ".observal"
    bins = {
        "postgres": root / "bin/postgres",
        "initdb": root / "bin/initdb",
        "pg_ctl": root / "bin/pg_ctl",
        "pg_isready": root / "bin/pg_isready",
        "createdb": root / "bin/createdb",
        "clickhouse": root / "bin/clickhouse",
        "redis_server": root / "bin/redis-server",
        "redis_cli": root / "bin/redis-cli",
    }
    pids = {
        "postgres": root / "run/postgres.pid",
        "clickhouse": root / "run/clickhouse.pid",
        "redis": root / "run/redis.pid",
        "api": root / "run/api.pid",
    }
    data = {
        "postgres": root / "data/pg",
        "clickhouse": root / "data/ch",
        "redis": root / "data/redis",
    }
    console = FakeConsole()

    constants = {
        "OBSERVAL_HOME": root,
        "CONFIG_DIR": root / "config",
        "DATA_DIR": root / "data",
        "KEYS_DIR": root / "keys",
        "LOG_DIR": root / "logs",
        "RUN_DIR": root / "run",
    }
    for name, value in constants.items():
        monkeypatch.setattr(orchestrator_module, name, value)

    monkeypatch.setattr(orchestrator_module, "get_bin_paths", lambda: bins.copy())
    monkeypatch.setattr(orchestrator_module, "get_pid_paths", lambda: pids.copy())
    monkeypatch.setattr(orchestrator_module, "get_data_paths", lambda: data.copy())
    monkeypatch.setattr(orchestrator_module.Path, "home", lambda: home)
    monkeypatch.setattr(orchestrator_module, "console", console)
    monkeypatch.setattr(orchestrator_module, "generate_secret", lambda length: f"secret-{length}")

    monkeypatch.setattr(orchestrator_module.subprocess, "run", blocked("subprocess.run"))
    monkeypatch.setattr(orchestrator_module.subprocess, "Popen", blocked("subprocess.Popen"))
    monkeypatch.setattr(orchestrator_module.httpx, "get", blocked("httpx.get"))
    monkeypatch.setattr(orchestrator_module.httpx, "post", blocked("httpx.post"))
    monkeypatch.setattr(orchestrator_module.os, "kill", blocked("os.kill"))
    monkeypatch.setattr(orchestrator_module.time, "sleep", blocked("time.sleep"))
    monkeypatch.setattr(orchestrator_module, "ensure_dirs", blocked("ensure_dirs"))
    monkeypatch.setattr(orchestrator_module, "generate_all_configs", blocked("generate_all_configs"))
    monkeypatch.setattr(orchestrator_module, "generate_pg_hba_conf", blocked("generate_pg_hba_conf"))

    return SimpleNamespace(
        home=home,
        root=root,
        bins=bins,
        pids=pids,
        data=data,
        console=console,
        **{name.lower(): value for name, value in constants.items()},
    )


class TestSecretsAndEnvironment:
    def test_first_run_generates_restricted_secrets(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        generate = MagicMock(side_effect=lambda length: f"generated-{length}")
        monkeypatch.setattr(orchestrator_module, "generate_secret", generate)

        orch = Orchestrator(port=9000, host="localhost")
        assert orch._secrets is None
        orch._secrets = orch._load_or_create_secrets()

        secrets_file = isolated_runtime.root / ".secrets"
        assert orch.port == 9000
        assert orch.host == "localhost"
        assert orch._secrets == {
            "POSTGRES_PASSWORD": "generated-24",
            "SECRET_KEY": "generated-32",
        }
        assert secrets_file.read_text() == "POSTGRES_PASSWORD=generated-24\nSECRET_KEY=generated-32\n"
        assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600
        assert generate.call_args_list == [call(24), call(32)]

    def test_existing_secrets_are_parsed_and_only_missing_values_are_added(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        isolated_runtime.root.mkdir(parents=True)
        secrets_file = isolated_runtime.root / ".secrets"
        secrets_file.write_text("ignored line\nEXTRA=a=b\nPOSTGRES_PASSWORD = existing\n")
        generate = MagicMock(return_value="new-secret")
        monkeypatch.setattr(orchestrator_module, "generate_secret", generate)

        orch = Orchestrator()
        orch._secrets = orch._load_or_create_secrets()

        assert orch._secrets == {
            "EXTRA": "a=b",
            "POSTGRES_PASSWORD": "existing",
            "SECRET_KEY": "new-secret",
        }
        assert secrets_file.read_text() == "EXTRA=a=b\nPOSTGRES_PASSWORD=existing\nSECRET_KEY=new-secret\n"
        assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600
        generate.assert_called_once_with(32)

    def test_complete_existing_secrets_are_not_rewritten(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        isolated_runtime.root.mkdir(parents=True)
        secrets_file = isolated_runtime.root / ".secrets"
        content = "POSTGRES_PASSWORD=postgres\nSECRET_KEY=jwt\n"
        secrets_file.write_text(content)
        secrets_file.chmod(0o640)
        generate = MagicMock()
        monkeypatch.setattr(orchestrator_module, "generate_secret", generate)

        orch = Orchestrator()
        orch._secrets = orch._load_or_create_secrets()

        assert orch._secrets == {"POSTGRES_PASSWORD": "postgres", "SECRET_KEY": "jwt"}
        assert secrets_file.read_text() == content
        assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o640
        generate.assert_not_called()

    def test_build_env_preserves_process_values_and_sets_embedded_configuration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRESERVED", "yes")
        orch = Orchestrator()

        env = orch._build_env()

        assert env["PRESERVED"] == "yes"
        assert env["DATABASE_URL"] == "postgresql+asyncpg://observal@127.0.0.1:5480/observal"
        assert env["CLICKHOUSE_URL"] == "clickhouse://default@127.0.0.1:8124/observal"
        assert env["REDIS_URL"] == "redis://127.0.0.1:6380"
        assert env["SECRET_KEY"] == "secret-32"
        assert env["JWT_KEY_DIR"] == str(orchestrator_module.KEYS_DIR)
        assert env["LOG_LEVEL"] == "INFO"
        assert env["OBSERVAL_EMBEDDED"] == "1"


class TestImmediateDeath:
    def test_live_process_passes_after_the_probe_delay(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleep = MagicMock()
        monkeypatch.setattr(orchestrator_module.time, "sleep", sleep)
        proc = MagicMock()
        proc.poll.return_value = None

        Orchestrator()._check_immediate_death(proc, "redis")

        sleep.assert_called_once_with(0.2)

    @pytest.mark.parametrize("with_log", [False, True])
    def test_dead_process_reports_exit_code_and_available_log(
        self,
        with_log: bool,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(orchestrator_module.time, "sleep", MagicMock())
        proc = MagicMock(returncode=7)
        proc.poll.return_value = 7
        if with_log:
            isolated_runtime.log_dir.mkdir(parents=True)
            (isolated_runtime.log_dir / "redis-startup.log").write_text("failed")

        with pytest.raises(ServiceError) as error:
            Orchestrator()._check_immediate_death(proc, "redis")

        assert "Redis exited immediately (code 7)" in str(error.value)
        assert ("Check logs:" in str(error.value)) is with_log


class TestPostgresLifecycle:
    def test_initialization_runs_initdb_and_writes_generated_configuration(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run = MagicMock(return_value=completed())
        generate_hba = MagicMock()

        def generate_configs() -> None:
            isolated_runtime.config_dir.mkdir(parents=True, exist_ok=True)
            (isolated_runtime.config_dir / "postgresql.conf").write_text("local config")

        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)
        monkeypatch.setattr(orchestrator_module, "generate_all_configs", generate_configs)
        monkeypatch.setattr(orchestrator_module, "generate_pg_hba_conf", generate_hba)
        orch = Orchestrator()

        orch._init_postgres()

        assert run.call_args.args[0] == [
            str(isolated_runtime.bins["initdb"]),
            "-D",
            str(isolated_runtime.data["postgres"]),
            "-U",
            "observal",
            f"{LONG_OPTION}auth=trust",
            f"{LONG_OPTION}encoding=UTF8",
            f"{LONG_OPTION}locale=C",
        ]
        assert run.call_args.kwargs == {"capture_output": True, "text": True}
        assert (isolated_runtime.data["postgres"] / "postgresql.conf").read_text() == "local config"
        generate_hba.assert_called_once_with()

    def test_initialization_surfaces_initdb_stderr(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            orchestrator_module.subprocess,
            "run",
            MagicMock(return_value=completed(1, stderr="permission denied")),
        )

        with pytest.raises(ServiceError, match="initdb failed") as error:
            Orchestrator()._init_postgres()

        assert "permission denied" in str(error.value)
        assert not (isolated_runtime.config_dir / "postgresql.conf").exists()

    def test_start_initializes_then_starts_waits_and_creates_database(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        isolated_runtime.log_dir.mkdir(parents=True)
        run = MagicMock(return_value=completed())
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)
        orch = Orchestrator()
        init = MagicMock()
        wait = MagicMock()
        ensure_database = MagicMock()
        monkeypatch.setattr(orch, "_init_postgres", init)
        monkeypatch.setattr(orch, "_wait_for_postgres", wait)
        monkeypatch.setattr(orch, "_ensure_database", ensure_database)

        orch.start_postgres()

        init.assert_called_once_with()
        assert run.call_args.args[0] == [
            str(isolated_runtime.bins["pg_ctl"]),
            "start",
            "-D",
            str(isolated_runtime.data["postgres"]),
            "-l",
            str(isolated_runtime.log_dir / "postgres.log"),
            "-o",
            f"-p 5480 -k {isolated_runtime.run_dir}",
            "-w",
            "-t",
            "30",
        ]
        wait.assert_called_once_with()
        ensure_database.assert_called_once_with()

    def test_start_skips_initialization_for_existing_cluster(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = MagicMock(return_value=completed())
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)
        orch = Orchestrator()
        monkeypatch.setattr(orch, "_pg_is_initialized", lambda: True)
        init = MagicMock()
        monkeypatch.setattr(orch, "_init_postgres", init)
        monkeypatch.setattr(orch, "_wait_for_postgres", MagicMock())
        monkeypatch.setattr(orch, "_ensure_database", MagicMock())

        orch.start_postgres()

        init.assert_not_called()

    def test_start_failure_includes_stderr_and_log_path(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            orchestrator_module.subprocess,
            "run",
            MagicMock(return_value=completed(2, stderr="address in use")),
        )
        orch = Orchestrator()
        monkeypatch.setattr(orch, "_pg_is_initialized", lambda: True)

        with pytest.raises(ServiceError) as error:
            orch.start_postgres()

        assert "address in use" in str(error.value)
        assert str(isolated_runtime.log_dir / "postgres.log") in str(error.value)

    def test_wait_retries_until_postgres_is_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = Clock()
        run = MagicMock(side_effect=[completed(1), completed(1), completed()])
        monkeypatch.setattr(orchestrator_module.time, "time", clock.time)
        monkeypatch.setattr(orchestrator_module.time, "sleep", clock.sleep)
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)

        Orchestrator()._wait_for_postgres(timeout=2)

        assert run.call_count == 3
        assert clock.sleeps == [0.5, 0.5]
        assert run.call_args.args[0][0].endswith("pg_isready")

    def test_wait_times_out_deterministically(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = Clock()
        run = MagicMock(return_value=completed(1))
        monkeypatch.setattr(orchestrator_module.time, "time", clock.time)
        monkeypatch.setattr(orchestrator_module.time, "sleep", clock.sleep)
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)

        with pytest.raises(ServiceError, match="within 1s"):
            Orchestrator()._wait_for_postgres(timeout=1)

        assert run.call_count == 2
        assert clock.sleeps == [0.5, 0.5]

    @pytest.mark.parametrize(
        ("result", "raises"),
        [
            (completed(), False),
            (completed(1, stderr="database already exists"), False),
            (completed(1, stderr="connection refused"), True),
        ],
    )
    def test_database_creation_handles_only_the_idempotent_error(
        self,
        result: SimpleNamespace,
        raises: bool,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run = MagicMock(return_value=result)
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)
        orch = Orchestrator()

        if raises:
            with pytest.raises(ServiceError, match="connection refused"):
                orch._ensure_database()
        else:
            orch._ensure_database()

        assert run.call_args.args[0] == [
            str(isolated_runtime.bins["createdb"]),
            "-h",
            "127.0.0.1",
            "-p",
            "5480",
            "-U",
            "observal",
            "observal",
        ]

    def test_stop_is_a_noop_before_initialization(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = MagicMock()
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)
        orch = Orchestrator()
        monkeypatch.setattr(orch, "_pg_is_initialized", lambda: False)

        orch.stop_postgres()

        run.assert_not_called()

    def test_stop_requests_fast_postgres_shutdown(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run = MagicMock(return_value=completed())
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)
        orch = Orchestrator()
        monkeypatch.setattr(orch, "_pg_is_initialized", lambda: True)

        orch.stop_postgres()

        run.assert_called_once_with(
            [
                str(isolated_runtime.bins["pg_ctl"]),
                "stop",
                "-D",
                str(isolated_runtime.data["postgres"]),
                "-m",
                "fast",
                "-t",
                "10",
            ],
            capture_output=True,
        )


class TestClickHouseLifecycle:
    def test_start_generates_config_creates_dirs_and_tracks_process(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        isolated_runtime.log_dir.mkdir(parents=True)
        isolated_runtime.run_dir.mkdir(parents=True)
        generate = MagicMock()
        proc = MagicMock(pid=4242)
        popen = MagicMock(return_value=proc)
        monkeypatch.setattr(orchestrator_module, "generate_all_configs", generate)
        monkeypatch.setattr(orchestrator_module.subprocess, "Popen", popen)
        orch = Orchestrator()
        immediate = MagicMock()
        wait = MagicMock()
        ensure_database = MagicMock()
        monkeypatch.setattr(orch, "_check_immediate_death", immediate)
        monkeypatch.setattr(orch, "_wait_for_clickhouse", wait)
        monkeypatch.setattr(orch, "_ensure_clickhouse_database", ensure_database)

        orch.start_clickhouse()

        generate.assert_called_once_with()
        for name in ("tmp", "user_files", "format_schemas"):
            assert (isolated_runtime.data["clickhouse"] / name).is_dir()
        command = popen.call_args.args[0]
        assert command == [
            str(isolated_runtime.bins["clickhouse"]),
            "server",
            f"{LONG_OPTION}config-file",
            str(isolated_runtime.config_dir / "clickhouse-config.xml"),
            f"{LONG_OPTION}pid-file",
            str(isolated_runtime.pids["clickhouse"]),
        ]
        assert popen.call_args.kwargs["stderr"] is subprocess.STDOUT
        assert popen.call_args.kwargs["start_new_session"] is True
        assert orch._processes["clickhouse"] is proc
        assert isolated_runtime.pids["clickhouse"].read_text() == "4242"
        immediate.assert_called_once_with(proc, "clickhouse")
        wait.assert_called_once_with()
        ensure_database.assert_called_once_with()
        orch._log_handles[0].close()

    @pytest.mark.parametrize("status_code", [200, 503])
    def test_create_database_posts_ddl_and_warns_on_non_success(
        self,
        status_code: int,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        post = MagicMock(return_value=SimpleNamespace(status_code=status_code))
        monkeypatch.setattr(orchestrator_module.httpx, "post", post)

        Orchestrator()._ensure_clickhouse_database()

        post.assert_called_once_with(
            "http://127.0.0.1:8124/",
            content="CREATE DATABASE IF NOT EXISTS observal",
            timeout=10,
        )
        assert ("returned 503" in isolated_runtime.console.text()) is (status_code == 503)

    def test_create_database_translates_connection_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            orchestrator_module.httpx,
            "post",
            MagicMock(side_effect=httpx.ConnectError("offline")),
        )

        with pytest.raises(ServiceError, match="became unreachable"):
            Orchestrator()._ensure_clickhouse_database()

    def test_wait_tolerates_connection_and_http_failures_until_healthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = Clock()
        get = MagicMock(
            side_effect=[
                httpx.ConnectError("offline"),
                SimpleNamespace(status_code=503),
                SimpleNamespace(status_code=200),
            ]
        )
        monkeypatch.setattr(orchestrator_module.time, "time", clock.time)
        monkeypatch.setattr(orchestrator_module.time, "sleep", clock.sleep)
        monkeypatch.setattr(orchestrator_module.httpx, "get", get)

        Orchestrator()._wait_for_clickhouse(timeout=2)

        assert get.call_count == 3
        assert clock.sleeps == [0.5, 0.5]

    def test_wait_reports_log_after_timeout(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clock = Clock()
        get = MagicMock(side_effect=httpx.ConnectError("offline"))
        monkeypatch.setattr(orchestrator_module.time, "time", clock.time)
        monkeypatch.setattr(orchestrator_module.time, "sleep", clock.sleep)
        monkeypatch.setattr(orchestrator_module.httpx, "get", get)

        with pytest.raises(ServiceError) as error:
            Orchestrator()._wait_for_clickhouse(timeout=1)

        assert "within 1s" in str(error.value)
        assert str(isolated_runtime.log_dir / "clickhouse-startup.log") in str(error.value)
        assert get.call_count == 2


class TestRedisLifecycle:
    def test_start_generates_config_and_tracks_process(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        isolated_runtime.run_dir.mkdir(parents=True)
        generate = MagicMock()
        proc = MagicMock(pid=5252)
        popen = MagicMock(return_value=proc)
        monkeypatch.setattr(orchestrator_module, "generate_all_configs", generate)
        monkeypatch.setattr(orchestrator_module.subprocess, "Popen", popen)
        orch = Orchestrator()
        immediate = MagicMock()
        wait = MagicMock()
        monkeypatch.setattr(orch, "_check_immediate_death", immediate)
        monkeypatch.setattr(orch, "_wait_for_redis", wait)

        orch.start_redis()

        generate.assert_called_once_with()
        popen.assert_called_once_with(
            [
                str(isolated_runtime.bins["redis_server"]),
                str(isolated_runtime.config_dir / "redis.conf"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        assert orch._processes["redis"] is proc
        assert isolated_runtime.pids["redis"].read_text() == "5252"
        immediate.assert_called_once_with(proc, "redis")
        wait.assert_called_once_with()

    def test_wait_retries_until_redis_returns_pong(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = Clock()
        run = MagicMock(
            side_effect=[
                completed(1),
                completed(stdout="loading"),
                completed(stdout="PONG\n"),
            ]
        )
        monkeypatch.setattr(orchestrator_module.time, "time", clock.time)
        monkeypatch.setattr(orchestrator_module.time, "sleep", clock.sleep)
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)

        Orchestrator()._wait_for_redis(timeout=1)

        assert run.call_count == 3
        assert clock.sleeps == [0.3, 0.3]

    def test_wait_times_out_without_pong(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clock = Clock()
        run = MagicMock(return_value=completed(stdout="NO"))
        monkeypatch.setattr(orchestrator_module.time, "time", clock.time)
        monkeypatch.setattr(orchestrator_module.time, "sleep", clock.sleep)
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)

        with pytest.raises(ServiceError) as error:
            Orchestrator()._wait_for_redis(timeout=0.5)

        assert str(isolated_runtime.log_dir / "redis.log") in str(error.value)
        assert run.call_count == 2


class TestPidShutdown:
    @pytest.mark.parametrize(
        ("service", "method_name", "poll_count", "wait_timeout"),
        [
            ("clickhouse", "stop_clickhouse", 20, 10),
            ("api", "stop_api", 10, 10),
        ],
    )
    def test_shutdown_escalates_stuck_pid_and_owned_process(
        self,
        service: str,
        method_name: str,
        poll_count: int,
        wait_timeout: int,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pid_file = isolated_runtime.pids[service]
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("321")
        kill = MagicMock()
        sleep = MagicMock()
        monkeypatch.setattr(orchestrator_module.os, "kill", kill)
        monkeypatch.setattr(orchestrator_module.time, "sleep", sleep)
        proc = MagicMock()
        proc.wait.side_effect = subprocess.TimeoutExpired("service", wait_timeout)
        orch = Orchestrator()
        orch._processes[service] = proc

        getattr(orch, method_name)()

        assert kill.call_args_list[0] == call(321, signal.SIGTERM)
        assert kill.call_args_list[1 : poll_count + 1] == [call(321, 0)] * poll_count
        assert kill.call_args_list[-1] == call(321, signal.SIGKILL)
        assert sleep.call_args_list == [call(0.5)] * poll_count
        assert not pid_file.exists()
        assert service not in orch._processes
        proc.terminate.assert_called_once_with()
        proc.wait.assert_called_once_with(timeout=wait_timeout)
        proc.kill.assert_called_once_with()

    def test_redis_shutdown_handles_disappeared_pid_and_process_timeout(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pid_file = isolated_runtime.pids["redis"]
        pid_file.parent.mkdir(parents=True)
        pid_file.write_text("654")
        calls: list[tuple[int, int]] = []

        def kill(pid: int, sig: int) -> None:
            calls.append((pid, sig))
            if sig == 0:
                raise ProcessLookupError

        monkeypatch.setattr(orchestrator_module.os, "kill", kill)
        monkeypatch.setattr(orchestrator_module.time, "sleep", MagicMock())
        proc = MagicMock()
        proc.wait.side_effect = subprocess.TimeoutExpired("redis", 5)
        orch = Orchestrator()
        orch._processes["redis"] = proc

        orch.stop_redis()

        assert calls == [(654, signal.SIGTERM), (654, 0)]
        assert not pid_file.exists()
        proc.terminate.assert_called_once_with()
        proc.wait.assert_called_once_with(timeout=5)
        proc.kill.assert_called_once_with()

    @pytest.mark.parametrize(
        ("service", "method_name", "live_checks"),
        [
            ("clickhouse", "stop_clickhouse", 0),
            ("redis", "stop_redis", 1),
            ("api", "stop_api", 0),
        ],
    )
    def test_shutdown_finishes_when_pid_disappears(
        self,
        service: str,
        method_name: str,
        live_checks: int,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pid_file = isolated_runtime.pids[service]
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("987")
        probes = 0

        def kill(pid: int, sig: int) -> None:
            nonlocal probes
            assert pid == 987
            if sig == 0:
                if probes == live_checks:
                    raise ProcessLookupError
                probes += 1

        sleep = MagicMock()
        monkeypatch.setattr(orchestrator_module.os, "kill", kill)
        monkeypatch.setattr(orchestrator_module.time, "sleep", sleep)

        getattr(Orchestrator(), method_name)()

        assert probes == live_checks
        assert sleep.call_count == live_checks
        assert not pid_file.exists()

    @pytest.mark.parametrize(
        ("service", "method_name"),
        [
            ("clickhouse", "stop_clickhouse"),
            ("redis", "stop_redis"),
            ("api", "stop_api"),
        ],
    )
    def test_shutdown_discards_invalid_pid_files(
        self,
        service: str,
        method_name: str,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pid_file = isolated_runtime.pids[service]
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("not-a-pid")
        kill = MagicMock()
        monkeypatch.setattr(orchestrator_module.os, "kill", kill)
        orch = Orchestrator()

        getattr(orch, method_name)()

        kill.assert_not_called()
        assert not pid_file.exists()


class TestApiLifecycle:
    @pytest.mark.parametrize("foreground", [True, False])
    def test_start_api_builds_command_and_tracks_pid(
        self,
        foreground: bool,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        isolated_runtime.run_dir.mkdir(parents=True)
        isolated_runtime.log_dir.mkdir(parents=True)
        server_dir = isolated_runtime.root / "server"
        env = {"SAFE": "1"}
        proc = MagicMock(pid=777)
        popen = MagicMock(return_value=proc)
        monkeypatch.setattr(orchestrator_module.subprocess, "Popen", popen)
        orch = Orchestrator(port=9123, host="127.0.0.2")
        monkeypatch.setattr(orch, "_build_env", lambda: env)
        monkeypatch.setattr(orch, "_find_server_dir", lambda: server_dir)
        monkeypatch.setattr(orch, "_find_python", lambda: "/venv/python")

        returned = orch.start_api(foreground=foreground)

        assert popen.call_args.args[0] == [
            "/venv/python",
            "-m",
            "uvicorn",
            "main:app",
            f"{LONG_OPTION}host",
            "127.0.0.2",
            f"{LONG_OPTION}port",
            "9123",
            f"{LONG_OPTION}log-level",
            "info",
        ]
        assert popen.call_args.kwargs["cwd"] == str(server_dir)
        assert popen.call_args.kwargs["env"] is env
        if foreground:
            assert returned is None
            assert "stdout" not in popen.call_args.kwargs
        else:
            assert returned is proc
            assert popen.call_args.kwargs["stderr"] is subprocess.STDOUT
            assert popen.call_args.kwargs["start_new_session"] is True
            assert orch._log_handles
            orch._log_handles[0].close()
        assert orch._processes["api"] is proc
        assert isolated_runtime.pids["api"].read_text() == "777"


class TestDependencyDiscovery:
    @pytest.mark.parametrize("nested", [False, True])
    def test_find_server_dir_in_bundle(
        self,
        nested: bool,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bundle = tmp_path / "bundle"
        server_dir = bundle / "observal-server" if nested else bundle
        server_dir.mkdir(parents=True)
        (server_dir / "main.py").write_text("app = None")
        monkeypatch.setattr(orchestrator_module.sys, "_MEIPASS", str(bundle), raising=False)

        assert Orchestrator()._find_server_dir() == server_dir

    def test_find_server_dir_in_source_tree(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(orchestrator_module.sys, "_MEIPASS", raising=False)
        repo = tmp_path / "repo"
        fake_module = repo / "observal_cli/server/orchestrator.py"
        server_dir = repo / "observal-server"
        server_dir.mkdir(parents=True)
        monkeypatch.setattr(orchestrator_module, "__file__", str(fake_module))

        assert Orchestrator()._find_server_dir() == server_dir

    def test_find_server_dir_reports_missing_dependency(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(orchestrator_module.sys, "_MEIPASS", raising=False)
        fake_module = tmp_path / "repo/observal_cli/server/orchestrator.py"
        monkeypatch.setattr(orchestrator_module, "__file__", str(fake_module))

        with pytest.raises(ServiceError, match="Cannot locate observal-server"):
            Orchestrator()._find_server_dir()

    def test_find_python_uses_bundle_executable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(orchestrator_module.sys, "_MEIPASS", "/bundle", raising=False)
        monkeypatch.setattr(orchestrator_module.sys, "executable", "/bundle/observal")

        assert Orchestrator()._find_python() == "/bundle/observal"

    @pytest.mark.parametrize("venv_exists", [False, True])
    def test_find_python_uses_project_venv_or_current_interpreter(
        self,
        venv_exists: bool,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delattr(orchestrator_module.sys, "_MEIPASS", raising=False)
        repo = tmp_path / "repo"
        fake_module = repo / "observal_cli/server/orchestrator.py"
        venv_python = repo / ".venv/bin/python"
        if venv_exists:
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text("")
        monkeypatch.setattr(orchestrator_module, "__file__", str(fake_module))
        monkeypatch.setattr(orchestrator_module.sys, "executable", "/usr/bin/python-test")

        expected = str(venv_python) if venv_exists else "/usr/bin/python-test"
        assert Orchestrator()._find_python() == expected


class TestFirstRunDetection:
    @pytest.mark.parametrize(("initialized", "first_run"), [(False, True), (True, False)])
    def test_first_run_is_the_inverse_of_postgres_initialization(
        self,
        initialized: bool,
        first_run: bool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        orch = Orchestrator()
        monkeypatch.setattr(orch, "_pg_is_initialized", lambda: initialized)

        assert orch._is_first_run() is first_run


class TestMigrations:
    def configure_migration_paths(self, orch: Orchestrator, server_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(orch, "_build_env", lambda: {"DB": "test"})
        monkeypatch.setattr(orch, "_find_server_dir", lambda: server_dir)
        monkeypatch.setattr(orch, "_find_python", lambda: "/python")

    def test_missing_alembic_configuration_fails_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        run = MagicMock()
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)
        orch = Orchestrator()
        self.configure_migration_paths(orch, tmp_path, monkeypatch)

        with pytest.raises(ServiceError, match="Alembic configuration"):
            orch.run_migrations()

        run.assert_not_called()

    def test_success_applies_postgres_and_clickhouse_migrations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "alembic.ini").write_text("[alembic]")
        run = MagicMock(side_effect=[completed(), completed()])
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)
        orch = Orchestrator()
        self.configure_migration_paths(orch, tmp_path, monkeypatch)

        orch.run_migrations()

        assert [item.args[0] for item in run.call_args_list] == [
            ["/python", "-m", "alembic", "upgrade", "head"],
            ["/python", "-m", "services.clickhouse.migrations"],
        ]

    @pytest.mark.parametrize("failure_index", [0, 1])
    def test_migration_failure_never_stamps_schema(
        self, failure_index: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "alembic.ini").write_text("[alembic]")
        results = [completed(), completed()]
        results[failure_index] = completed(1, stderr="migration crashed")
        run = MagicMock(side_effect=results)
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)
        orch = Orchestrator()
        self.configure_migration_paths(orch, tmp_path, monkeypatch)

        with pytest.raises(ServiceError, match="migration failed"):
            orch.run_migrations()

        assert not any("stamp" in item.args[0] for item in run.call_args_list)


class TestBootstrapAndCliConfiguration:
    def test_bootstrap_retries_liveness_and_creates_admin(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        get = MagicMock(
            side_effect=[
                httpx.ConnectError("offline"),
                httpx.ReadTimeout("slow"),
                SimpleNamespace(status_code=503),
                SimpleNamespace(status_code=200),
            ]
        )
        credentials = {
            "access_token": "access",
            "refresh_token": "refresh",
            "user": {"id": "user-id"},
        }
        post = MagicMock(return_value=SimpleNamespace(status_code=200, json=lambda: credentials))
        sleep = MagicMock()
        monkeypatch.setattr(orchestrator_module.time, "time", MagicMock(side_effect=[0, 0, 1, 2, 3]))
        monkeypatch.setattr(orchestrator_module.time, "sleep", sleep)
        monkeypatch.setattr(orchestrator_module.httpx, "get", get)
        monkeypatch.setattr(orchestrator_module.httpx, "post", post)

        assert Orchestrator(port=9123)._auto_bootstrap() == credentials

        assert get.call_count == 4
        assert sleep.call_args_list == [call(0.5), call(0.5), call(0.5)]
        post.assert_called_once_with("http://127.0.0.1:9123/api/v1/auth/bootstrap", timeout=10)
        assert "Admin user created" in isolated_runtime.console.text()

    def test_bootstrap_liveness_timeout_does_not_post(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        get = MagicMock(side_effect=httpx.ConnectError("offline"))
        post = MagicMock()
        monkeypatch.setattr(orchestrator_module.time, "time", MagicMock(side_effect=[0, 0, 61]))
        monkeypatch.setattr(orchestrator_module.time, "sleep", MagicMock())
        monkeypatch.setattr(orchestrator_module.httpx, "get", get)
        monkeypatch.setattr(orchestrator_module.httpx, "post", post)

        Orchestrator()._auto_bootstrap()

        get.assert_called_once_with("http://127.0.0.1:8000/livez", timeout=2)
        post.assert_not_called()
        assert "did not become ready" in isolated_runtime.console.text()

    @pytest.mark.parametrize(
        ("response", "message"),
        [
            (SimpleNamespace(status_code=400), ""),
            (SimpleNamespace(status_code=503), "Bootstrap returned 503"),
            (httpx.ConnectError("offline"), "Could not complete API bootstrap"),
            (httpx.ReadTimeout("slow"), "Could not complete API bootstrap"),
        ],
    )
    def test_bootstrap_handles_idempotent_warning_and_network_results(
        self,
        response: object,
        message: str,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(orchestrator_module.time, "time", MagicMock(side_effect=[0, 0]))
        monkeypatch.setattr(orchestrator_module.httpx, "get", lambda *args, **kwargs: SimpleNamespace(status_code=200))
        post = MagicMock(side_effect=response) if isinstance(response, Exception) else MagicMock(return_value=response)
        monkeypatch.setattr(orchestrator_module.httpx, "post", post)

        Orchestrator()._auto_bootstrap()

        assert message in isolated_runtime.console.text()

    def test_configure_cli_preserves_current_login_without_placeholder_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from observal_cli import config as cli_config

        save = MagicMock()
        remove = MagicMock()
        monkeypatch.setattr(
            cli_config, "load", lambda: {"server_url": "http://localhost:9000", "access_token": "token"}
        )
        monkeypatch.setattr(cli_config, "save", save)
        monkeypatch.setattr(cli_config, "remove", remove)

        Orchestrator(port=9000)._configure_cli(None)

        save.assert_not_called()
        remove.assert_not_called()

    def test_configure_cli_persists_real_bootstrap_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from observal_cli import config as cli_config

        save = MagicMock()
        remove = MagicMock()
        monkeypatch.setattr(cli_config, "load", lambda: {"server_url": "http://old", "access_token": "old"})
        monkeypatch.setattr(cli_config, "save", save)
        monkeypatch.setattr(cli_config, "remove", remove)
        credentials = {"access_token": "access", "refresh_token": "refresh", "user": {"id": "user-id"}}

        Orchestrator(port=9000)._configure_cli(credentials)

        remove.assert_called_once_with("access_token", "refresh_token", "api_key")
        save.assert_called_once_with(
            {
                "server_url": "http://localhost:9000",
                "access_token": "access",
                "refresh_token": "refresh",
                "user_id": "user-id",
            }
        )


class TestHookInstallation:
    def test_installs_available_claude_and_kiro_hooks(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from observal_cli import cmd_doctor, settings_reconciler
        from observal_cli.harness_specs import claude_code_hooks_spec

        (isolated_runtime.home / ".claude").mkdir(parents=True)
        (isolated_runtime.home / ".kiro/agents").mkdir(parents=True)
        desired = {"hooks": "desired"}
        get_desired = MagicMock(return_value=desired)
        reconcile = MagicMock(return_value=["changed"])
        patch_kiro = MagicMock(return_value=True)
        monkeypatch.setattr(claude_code_hooks_spec, "get_desired_hooks", get_desired)
        monkeypatch.setattr(settings_reconciler, "reconcile", reconcile)
        monkeypatch.setattr(cmd_doctor, "_patch_kiro", patch_kiro)

        Orchestrator()._install_hooks()

        reconcile.assert_called_once_with(desired, {}, dry_run=False)
        patch_kiro.assert_called_once_with(dry_run=False)
        assert "Claude Code hooks installed" in isolated_runtime.console.text()
        assert "Kiro hooks installed" in isolated_runtime.console.text()

    def test_optional_hook_failures_do_not_abort_server_start(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from observal_cli import cmd_doctor, settings_reconciler
        from observal_cli.harness_specs import claude_code_hooks_spec

        (isolated_runtime.home / ".claude").mkdir(parents=True)
        (isolated_runtime.home / ".kiro/agents").mkdir(parents=True)
        monkeypatch.setattr(claude_code_hooks_spec, "get_desired_hooks", lambda: {})
        monkeypatch.setattr(settings_reconciler, "reconcile", MagicMock(side_effect=PermissionError("read only")))
        monkeypatch.setattr(cmd_doctor, "_patch_kiro", MagicMock(side_effect=RuntimeError("broken")))

        warnings = Orchestrator()._install_hooks()

        assert "hooks installed" not in isolated_runtime.console.text()
        assert len(warnings) == 2
        assert all("doctor patch" in warning for warning in warnings)


class TestFullLifecycle:
    def prepare_start(
        self,
        orch: Orchestrator,
        monkeypatch: pytest.MonkeyPatch,
        calls: list[object],
        *,
        first_run: bool,
    ) -> None:
        monkeypatch.setattr(orchestrator_module, "ensure_dirs", lambda: calls.append("ensure_dirs"))
        monkeypatch.setattr(orch, "_is_first_run", lambda: first_run)
        for name in ("start_postgres", "start_clickhouse", "start_redis", "run_migrations"):
            monkeypatch.setattr(orch, name, lambda name=name: calls.append(name))

        def start_api(*, foreground: bool) -> None:
            calls.append(("start_api", foreground))

        monkeypatch.setattr(orch, "start_api", start_api)

        def bootstrap():
            calls.append("bootstrap")
            return {"access_token": "access", "refresh_token": "refresh"}

        monkeypatch.setattr(orch, "_auto_bootstrap", bootstrap)
        monkeypatch.setattr(orch, "_configure_cli", lambda credentials: calls.append(("configure_cli", credentials)))

        def install_hooks():
            calls.append("install_hooks")
            return []

        monkeypatch.setattr(orch, "_install_hooks", install_hooks)

    @pytest.mark.parametrize("foreground", [False, True])
    def test_start_all_runs_services_and_setup_in_order(
        self,
        foreground: bool,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[object] = []
        orch = Orchestrator()
        self.prepare_start(orch, monkeypatch, calls, first_run=True)
        api = MagicMock()
        if foreground:
            orch._processes["api"] = api

        orch.start_all(foreground=foreground)

        assert calls == [
            "ensure_dirs",
            "start_postgres",
            "start_clickhouse",
            "start_redis",
            "run_migrations",
            ("start_api", foreground),
            "bootstrap",
            ("configure_cli", {"access_token": "access", "refresh_token": "refresh"}),
            "install_hooks",
        ]
        assert isolated_runtime.keys_dir.is_dir()
        assert "First run" in isolated_runtime.console.text()
        if foreground:
            api.wait.assert_called_once_with()

    def test_keyboard_interrupt_stops_all_services(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[object] = []
        orch = Orchestrator()
        self.prepare_start(orch, monkeypatch, calls, first_run=False)
        api = MagicMock()
        api.wait.side_effect = KeyboardInterrupt
        orch._processes["api"] = api
        stop = MagicMock()
        monkeypatch.setattr(orch, "stop_all", stop)

        orch.start_all(foreground=True)

        stop.assert_called_once_with()

    @pytest.mark.parametrize("failure", [ServiceError("failed"), PermissionError("not executable")])
    def test_start_failure_cleans_up_and_exits(
        self,
        failure: Exception,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(orchestrator_module, "ensure_dirs", lambda: None)
        orch = Orchestrator()
        monkeypatch.setattr(orch, "_is_first_run", lambda: False)
        monkeypatch.setattr(orch, "start_postgres", MagicMock(side_effect=failure))
        stop = MagicMock()
        monkeypatch.setattr(orch, "stop_all", stop)

        with pytest.raises(ServiceError) as error:
            orch.start_all(foreground=False)

        assert str(failure) in str(error.value)
        stop.assert_called_once_with()
        assert str(failure) in isolated_runtime.console.text()
        assert "Cleaning up" in isolated_runtime.console.text()

    def test_stop_all_uses_reverse_order_and_closes_every_handle(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events: list[str] = []
        orch = Orchestrator()
        for method_name, label in (
            ("stop_api", "api"),
            ("stop_redis", "redis"),
            ("stop_clickhouse", "clickhouse"),
            ("stop_postgres", "postgres"),
        ):
            monkeypatch.setattr(orch, method_name, lambda label=label: events.append(label))
        good_handle = MagicMock()
        bad_handle = MagicMock()
        bad_handle.close.side_effect = OSError("already closed")
        orch._log_handles = [bad_handle, good_handle]

        logger = MagicMock()
        monkeypatch.setattr(orchestrator_module, "optic", logger)

        orch.stop_all()

        logger.debug.assert_called_once_with(
            "Failed to close log handle: error={}",
            bad_handle.close.side_effect,
        )

        assert events == ["api", "redis", "clickhouse", "postgres"]
        good_handle.close.assert_called_once_with()
        bad_handle.close.assert_called_once_with()
        assert orch._log_handles == []
        assert "All services stopped" in isolated_runtime.console.text()


class TestStatusAndReset:
    def test_status_reports_all_services_running(
        self,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run = MagicMock(side_effect=[completed(), completed(stdout="PONG\n")])
        get = MagicMock(side_effect=[SimpleNamespace(status_code=200), SimpleNamespace(status_code=200)])
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)
        monkeypatch.setattr(orchestrator_module.httpx, "get", get)
        isolated_runtime.bins["redis_cli"].parent.mkdir(parents=True, exist_ok=True)
        isolated_runtime.bins["redis_cli"].touch()
        orch = Orchestrator(port=9000)
        monkeypatch.setattr(orch, "_pg_is_initialized", lambda: True)

        statuses = orch.status()

        assert statuses == {
            "postgres": "running",
            "clickhouse": "running",
            "redis": "running",
            "api": "running",
        }
        assert run.call_args_list[0].args[0][0] == str(isolated_runtime.bins["pg_isready"])
        assert run.call_args_list[1].args[0][0] == str(isolated_runtime.bins["redis_cli"])
        assert get.call_args_list == [
            call("http://127.0.0.1:8124/ping", timeout=2),
            call("http://127.0.0.1:9000/livez", timeout=2),
        ]

    @pytest.mark.parametrize("api_connect_error", [False, True])
    def test_status_reports_uninitialized_and_unhealthy_services(
        self,
        api_connect_error: bool,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run = MagicMock(return_value=completed(stdout=""))
        api_result: object = httpx.ConnectError("offline") if api_connect_error else SimpleNamespace(status_code=503)
        get = MagicMock(side_effect=[httpx.ConnectError("offline"), api_result])
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)
        monkeypatch.setattr(orchestrator_module.httpx, "get", get)
        isolated_runtime.bins["redis_cli"].parent.mkdir(parents=True, exist_ok=True)
        isolated_runtime.bins["redis_cli"].touch()
        orch = Orchestrator()
        monkeypatch.setattr(orch, "_pg_is_initialized", lambda: False)

        statuses = orch.status()

        assert statuses == {
            "postgres": "not initialized",
            "clickhouse": "stopped",
            "redis": "stopped",
            "api": "stopped",
        }
        assert run.call_count == 1

    def test_status_reports_initialized_postgres_that_is_not_ready(
        self, isolated_runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = MagicMock(side_effect=[completed(1), completed(stdout="NO")])
        get = MagicMock(side_effect=[SimpleNamespace(status_code=503), SimpleNamespace(status_code=503)])
        monkeypatch.setattr(orchestrator_module.subprocess, "run", run)
        monkeypatch.setattr(orchestrator_module.httpx, "get", get)
        isolated_runtime.bins["redis_cli"].parent.mkdir(parents=True, exist_ok=True)
        isolated_runtime.bins["redis_cli"].touch()
        orch = Orchestrator()
        monkeypatch.setattr(orch, "_pg_is_initialized", lambda: True)

        assert orch.status()["postgres"] == "stopped"

    @pytest.mark.parametrize("running", [False, True])
    def test_is_running_detects_any_running_service(self, running: bool, monkeypatch: pytest.MonkeyPatch) -> None:
        orch = Orchestrator()
        statuses = {"postgres": "running" if running else "stopped", "api": "stopped"}
        monkeypatch.setattr(orch, "status", lambda: statuses)

        assert orch.is_running() is running

    @pytest.mark.parametrize("running", [False, True])
    def test_reset_stops_if_needed_and_removes_data_and_secrets(
        self,
        running: bool,
        isolated_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import shutil

        orch = Orchestrator()
        orch._secrets = orch._load_or_create_secrets()
        for path in (isolated_runtime.data["postgres"], isolated_runtime.data["redis"]):
            path.mkdir(parents=True)
        secrets_file = isolated_runtime.root / ".secrets"
        assert secrets_file.exists()
        rmtree = MagicMock()
        monkeypatch.setattr(shutil, "rmtree", rmtree)
        monkeypatch.setattr(orch, "is_running", lambda: running)
        stop = MagicMock()
        monkeypatch.setattr(orch, "stop_all", stop)

        orch.reset()

        if running:
            stop.assert_called_once_with()
        else:
            stop.assert_not_called()
        assert rmtree.call_args_list == [
            call(isolated_runtime.data["postgres"]),
            call(isolated_runtime.data["redis"]),
        ]
        assert not secrets_file.exists()
        assert "Reset complete" in isolated_runtime.console.text()
