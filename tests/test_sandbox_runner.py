# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Behavioral coverage for the local sandbox runtime dispatcher."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from observal_cli import sandbox_runner


@pytest.fixture(autouse=True)
def _isolated_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBSERVAL_KEY", raising=False)
    monkeypatch.delenv("OBSERVAL_SERVER", raising=False)


def _docker_sdk(container: MagicMock) -> tuple[MagicMock, MagicMock]:
    client = MagicMock()
    client.containers.run.return_value = container
    docker = MagicMock()
    docker.from_env.return_value = client
    return docker, client


def test_time_and_log_helpers_have_exact_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime(2026, 8, 9, 10, 11, 12, 987654, tzinfo=UTC)
    now = MagicMock(return_value=fixed)
    monkeypatch.setattr(sandbox_runner, "datetime", SimpleNamespace(now=now))

    assert sandbox_runner._now_iso() == "2026-08-09 10:11:12.987"
    now.assert_called_once_with(UTC)
    assert sandbox_runner._send_span("server", "token", {"span_id": "span"}) is None

    boundary = "x" * sandbox_runner.MAX_LOG_BYTES
    assert sandbox_runner._truncate(boundary) == boundary
    assert sandbox_runner._truncate(boundary + "tail") == boundary + "\n... [truncated at 64KB]"


def test_require_bin_returns_resolved_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    which = MagicMock(return_value="/opt/bin/wasmtime")
    monkeypatch.setattr(sandbox_runner.shutil, "which", which)

    assert sandbox_runner._require_bin("wasmtime") == "/opt/bin/wasmtime"
    which.assert_called_once_with("wasmtime")


def test_require_bin_reports_exact_missing_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    which = MagicMock(return_value=None)
    monkeypatch.setattr(sandbox_runner.shutil, "which", which)

    with pytest.raises(SystemExit) as raised:
        sandbox_runner._require_bin("lxc")

    assert raised.value.code == 127
    assert capsys.readouterr() == ("", "local-runtime-missing: lxc is not installed or not on PATH\n")
    which.assert_called_once_with("lxc")


def test_run_subprocess_captures_combines_emits_and_exits(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    completed = SimpleNamespace(stdout="standard output", stderr="standard error", returncode=23)
    run = MagicMock(return_value=completed)
    monkeypatch.setattr(sandbox_runner.subprocess, "run", run)

    with pytest.raises(SystemExit) as raised:
        sandbox_runner._run_subprocess(["/bin/tool", "arg"], 17)

    assert raised.value.code == 23
    assert capsys.readouterr() == ("standard output\n[stderr]\nstandard error", "")
    run.assert_called_once_with(["/bin/tool", "arg"], capture_output=True, text=True, timeout=17)


def test_run_subprocess_preserves_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    timeout = subprocess.TimeoutExpired(["tool"], 3)
    run = MagicMock(side_effect=timeout)
    monkeypatch.setattr(sandbox_runner.subprocess, "run", run)

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        sandbox_runner._run_subprocess(["tool"], 3)

    assert raised.value is timeout
    assert capsys.readouterr() == ("", "")
    run.assert_called_once_with(["tool"], capture_output=True, text=True, timeout=3)


def test_docker_missing_sdk_is_an_actionable_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setitem(sys.modules, "docker", None)

    with pytest.raises(SystemExit) as raised:
        sandbox_runner._docker_run("sandbox", "image", None, 10, None, "none", {})

    assert raised.value.code == 127
    assert capsys.readouterr() == (
        "",
        "local-runtime-missing: Docker SDK not found. Install: pip install 'observal-cli[sandbox]'\n",
    )


def test_docker_client_creation_failure_propagates_before_lifecycle_management(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failure = RuntimeError("daemon unavailable")
    docker = MagicMock()
    docker.from_env.side_effect = failure
    monkeypatch.setitem(sys.modules, "docker", docker)

    with pytest.raises(RuntimeError) as raised:
        sandbox_runner._docker_run("id", "image", None, 10, None, "none", {})

    assert raised.value is failure
    assert capsys.readouterr() == ("", "")
    docker.from_env.assert_called_once_with()


def test_docker_run_builds_limits_emits_logs_sends_span_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[object] = []
    container = MagicMock()
    container.wait.side_effect = lambda **kwargs: events.append(("wait", kwargs)) or {"StatusCode": 0}
    container.logs.side_effect = lambda **kwargs: events.append(("logs", kwargs)) or b"hello\xff"
    container.reload.side_effect = lambda: events.append("reload")
    container.remove.side_effect = lambda **kwargs: events.append(("remove", kwargs))
    container.short_id = "c0ffee"
    container.attrs = {"State": {"OOMKilled": True}}
    docker, client = _docker_sdk(container)

    def run_container(**kwargs):
        events.append(("run", kwargs))
        return container

    client.containers.run.side_effect = run_container
    monotonic = MagicMock(side_effect=[100.0, 100.125])
    now_iso = MagicMock(side_effect=["2026-08-09 10:00:00.000", "2026-08-09 10:00:00.125"])
    send_span = MagicMock(side_effect=lambda *_args: events.append("span"))
    load_config = MagicMock(side_effect=AssertionError("config must not be loaded when environment credentials exist"))
    monkeypatch.setitem(sys.modules, "docker", docker)
    monkeypatch.setattr(sandbox_runner.time, "monotonic", monotonic)
    monkeypatch.setattr(sandbox_runner, "_now_iso", now_iso)
    monkeypatch.setattr(sandbox_runner, "_send_span", send_span)
    monkeypatch.setattr(sandbox_runner, "load_config", load_config)
    monkeypatch.setattr(
        sandbox_runner.uuid,
        "uuid4",
        MagicMock(side_effect=[uuid.UUID(int=1), uuid.UUID(int=2)]),
    )
    monkeypatch.setenv("OBSERVAL_KEY", "environment-token")
    monkeypatch.setenv("OBSERVAL_SERVER", "https://registry.example")

    with pytest.raises(SystemExit) as raised:
        sandbox_runner._docker_run(
            "sandbox-id",
            "python:3.13",
            "python -m pytest",
            45,
            {"TOKEN": "value", "PATH": "/sandbox/bin"},
            "restricted",
            {"memory_mb": "512", "cpu_count": "1.5"},
        )

    assert raised.value.code == 0
    assert capsys.readouterr() == ("hello�", "")
    docker.from_env.assert_called_once_with()
    expected_run_kwargs = {
        "image": "python:3.13",
        "detach": True,
        "environment": {"TOKEN": "value", "PATH": "/sandbox/bin"},
        "stdout": True,
        "stderr": True,
        "command": "python -m pytest",
        "network_mode": "none",
        "mem_limit": "512m",
        "nano_cpus": 1_500_000_000,
    }
    assert events == [
        ("run", expected_run_kwargs),
        ("wait", {"timeout": 45}),
        ("logs", {"stdout": True, "stderr": True}),
        "reload",
        "span",
        ("remove", {"force": True}),
    ]
    send_span.assert_called_once_with(
        "https://registry.example",
        "environment-token",
        {
            "span_id": str(uuid.UUID(int=1)),
            "trace_id": str(uuid.UUID(int=2)),
            "parent_span_id": None,
            "type": "sandbox_exec",
            "name": "sandbox:python:3.13",
            "method": "",
            "input": json.dumps({"image": "python:3.13", "command": "python -m pytest", "sandbox_id": "sandbox-id"}),
            "output": "hello�",
            "error": None,
            "start_time": "2026-08-09 10:00:00.000",
            "end_time": "2026-08-09 10:00:00.125",
            "latency_ms": 125,
            "status": "success",
            "harness": "",
            "metadata": {},
            "container_id": "c0ffee",
            "exit_code": 0,
            "oom_killed": True,
        },
    )
    load_config.assert_not_called()


def test_docker_run_uses_config_fallback_and_error_defaults(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    container = MagicMock()
    container.wait.return_value = {}
    container.logs.return_value = "failed"
    container.short_id = "deadbeef"
    container.attrs = {}
    docker, client = _docker_sdk(container)
    send_span = MagicMock()
    load_config = MagicMock(return_value={"access_token": "config-token", "server_url": "https://config.example"})
    monkeypatch.setitem(sys.modules, "docker", docker)
    monkeypatch.setattr(sandbox_runner, "_send_span", send_span)
    monkeypatch.setattr(sandbox_runner, "load_config", load_config)
    monkeypatch.setattr(sandbox_runner.time, "monotonic", MagicMock(side_effect=[5.0, 5.0]))
    monkeypatch.setenv("OBSERVAL_KEY", "environment-token")

    with pytest.raises(SystemExit) as raised:
        sandbox_runner._docker_run("id", "image", None, 9, None, "invalid", {"memory_mb": 0, "cpu_count": 0})

    assert raised.value.code == -1
    assert capsys.readouterr() == ("failed", "")
    client.containers.run.assert_called_once_with(
        image="image",
        detach=True,
        environment={},
        stdout=True,
        stderr=True,
    )
    load_config.assert_called_once_with()
    assert send_span.call_args.args[:2] == ("https://config.example", "environment-token")
    span = send_span.call_args.args[2]
    assert span["error"] == "exit_code=-1"
    assert span["status"] == "error"
    assert span["exit_code"] == -1
    assert span["oom_killed"] is False
    container.remove.assert_called_once_with(force=True)


@pytest.mark.parametrize(
    ("network_policy", "expected_mode"),
    [("host", "host"), ("restricted", "none"), ("custom", None)],
)
def test_docker_network_policy_mapping_on_start_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    network_policy: str,
    expected_mode: str | None,
) -> None:
    container = MagicMock()
    docker, client = _docker_sdk(container)
    client.containers.run.side_effect = RuntimeError("start failed")
    monkeypatch.setitem(sys.modules, "docker", docker)

    with pytest.raises(SystemExit) as raised:
        sandbox_runner._docker_run("id", "image", None, 10, None, network_policy, {})

    assert raised.value.code == 1
    assert capsys.readouterr() == ("", "Error: start failed\n")
    kwargs = client.containers.run.call_args.kwargs
    if expected_mode is None:
        assert "network_mode" not in kwargs
    else:
        assert kwargs["network_mode"] == expected_mode
    container.remove.assert_not_called()


def test_docker_runtime_error_keeps_error_and_attempts_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    container = MagicMock()
    container.wait.side_effect = RuntimeError("wait failed")
    container.remove.side_effect = RuntimeError("cleanup failed")
    docker, _client = _docker_sdk(container)
    monkeypatch.setitem(sys.modules, "docker", docker)

    with pytest.raises(SystemExit) as raised:
        sandbox_runner._docker_run("id", "image", None, 10, None, "none", {})

    assert raised.value.code == 1
    assert capsys.readouterr() == ("", "Error: wait failed\n")
    container.remove.assert_called_once_with(force=True)


def test_docker_interrupt_is_preserved_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    container = MagicMock()
    interrupt = KeyboardInterrupt()
    container.wait.side_effect = interrupt
    docker, _client = _docker_sdk(container)
    monkeypatch.setitem(sys.modules, "docker", docker)

    with pytest.raises(KeyboardInterrupt) as raised:
        sandbox_runner._docker_run("id", "image", None, 10, None, "none", {})

    assert raised.value is interrupt
    assert capsys.readouterr() == ("", "")
    container.remove.assert_called_once_with(force=True)


def test_docker_rejects_invalid_resource_limit_before_start(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    container = MagicMock()
    docker, client = _docker_sdk(container)
    monkeypatch.setitem(sys.modules, "docker", docker)

    with pytest.raises(SystemExit) as raised:
        sandbox_runner._docker_run("id", "image", None, 10, None, "none", {"memory_mb": "many"})

    assert raised.value.code == 1
    assert capsys.readouterr() == ("", "Error: invalid literal for int() with base 10: 'many'\n")
    client.containers.run.assert_not_called()


@pytest.mark.parametrize(("command", "expected_command"), [(None, "sh"), ("echo hello", "echo hello")])
def test_lxc_builds_launch_exec_and_delete_commands_in_order(
    monkeypatch: pytest.MonkeyPatch,
    command: str | None,
    expected_command: str,
) -> None:
    events: list[object] = []
    require_bin = MagicMock(return_value="/opt/lxc")
    run = MagicMock(side_effect=lambda argv, **kwargs: events.append(("subprocess", argv, kwargs)))
    worker_exit = SystemExit(7)

    def execute(argv, timeout):
        events.append(("execute", argv, timeout))
        raise worker_exit

    monkeypatch.setattr(sandbox_runner, "_require_bin", require_bin)
    monkeypatch.setattr(sandbox_runner.uuid, "uuid4", MagicMock(return_value=SimpleNamespace(hex="12345678abcdef")))
    monkeypatch.setattr(sandbox_runner.subprocess, "run", run)
    monkeypatch.setattr(sandbox_runner, "_run_subprocess", execute)

    with pytest.raises(SystemExit) as raised:
        sandbox_runner._lxc_run("abcdef12-rest", "images:ubuntu/24.04", command, 31)

    assert raised.value is worker_exit
    name = "observal-abcdef12-12345678"
    assert events == [
        (
            "subprocess",
            ["/opt/lxc", "launch", "images:ubuntu/24.04", name, "--ephemeral"],
            {"check": True, "timeout": 31},
        ),
        ("execute", ["/opt/lxc", "exec", name, "--", "sh", "-lc", expected_command], 31),
        (
            "subprocess",
            ["/opt/lxc", "delete", name, "--force"],
            {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL},
        ),
    ]
    require_bin.assert_called_once_with("lxc")


def test_lxc_launch_failure_is_preserved_without_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = subprocess.TimeoutExpired(["lxc", "launch"], 4)
    run = MagicMock(side_effect=failure)
    execute = MagicMock()
    monkeypatch.setattr(sandbox_runner, "_require_bin", MagicMock(return_value="lxc"))
    monkeypatch.setattr(sandbox_runner.uuid, "uuid4", MagicMock(return_value=SimpleNamespace(hex="abcdef12")))
    monkeypatch.setattr(sandbox_runner.subprocess, "run", run)
    monkeypatch.setattr(sandbox_runner, "_run_subprocess", execute)

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        sandbox_runner._lxc_run("id", "image", "command", 4)

    assert raised.value is failure
    assert run.call_count == 1
    execute.assert_not_called()


def test_lxc_delete_failure_replaces_workload_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    workload_exit = SystemExit(9)
    cleanup_failure = RuntimeError("delete failed")
    run = MagicMock(side_effect=[None, cleanup_failure])
    monkeypatch.setattr(sandbox_runner, "_require_bin", MagicMock(return_value="lxc"))
    monkeypatch.setattr(sandbox_runner.uuid, "uuid4", MagicMock(return_value=SimpleNamespace(hex="abcdef12")))
    monkeypatch.setattr(sandbox_runner.subprocess, "run", run)
    monkeypatch.setattr(sandbox_runner, "_run_subprocess", MagicMock(side_effect=workload_exit))

    with pytest.raises(RuntimeError) as raised:
        sandbox_runner._lxc_run("id", "image", "command", 8)

    assert raised.value is cleanup_failure
    assert run.call_count == 2


def test_lxc_interrupt_runs_delete_then_preserves_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    interrupt = KeyboardInterrupt()
    run = MagicMock(side_effect=lambda *_args, **_kwargs: events.append("launch" if not events else "delete"))

    def execute(*_args, **_kwargs):
        events.append("execute")
        raise interrupt

    monkeypatch.setattr(sandbox_runner, "_require_bin", MagicMock(return_value="lxc"))
    monkeypatch.setattr(sandbox_runner.uuid, "uuid4", MagicMock(return_value=SimpleNamespace(hex="abcdef12")))
    monkeypatch.setattr(sandbox_runner.subprocess, "run", run)
    monkeypatch.setattr(sandbox_runner, "_run_subprocess", execute)

    with pytest.raises(KeyboardInterrupt) as raised:
        sandbox_runner._lxc_run("id", "image", "command", 8)

    assert raised.value is interrupt
    assert events == ["launch", "execute", "delete"]


def test_firecracker_requires_a_complete_generated_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sandbox_runner, "_require_bin", MagicMock(return_value="/bin/firecracker"))

    with pytest.raises(SystemExit) as raised:
        sandbox_runner._firecracker_run({"kernel_image_path": "/kernel"}, 10)

    assert raised.value.code == 2
    assert capsys.readouterr() == (
        "",
        "Firecracker requires runtime_config.config_path or kernel_image_path/rootfs_path\n",
    )


def test_firecracker_uses_existing_config_without_temporary_file(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = MagicMock(return_value=None)
    named_temporary_file = MagicMock(side_effect=AssertionError("temporary file must not be created"))
    unlink = MagicMock()
    monkeypatch.setattr(sandbox_runner, "_require_bin", MagicMock(return_value="/opt/firecracker"))
    monkeypatch.setattr(sandbox_runner, "_run_subprocess", execute)
    monkeypatch.setattr(sandbox_runner.tempfile, "NamedTemporaryFile", named_temporary_file)
    monkeypatch.setattr(sandbox_runner.os, "unlink", unlink)

    assert sandbox_runner._firecracker_run({"config_path": Path("machine.json")}, 22) is None

    execute.assert_called_once_with(["/opt/firecracker", "--config-file", "machine.json"], 22)
    named_temporary_file.assert_not_called()
    unlink.assert_not_called()


@pytest.mark.parametrize("cleanup_error", [None, OSError("already removed")])
def test_firecracker_writes_exact_generated_config_and_always_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    cleanup_error: OSError | None,
) -> None:
    events: list[object] = []
    temporary = io.StringIO()
    temporary.name = "/tmp/firecracker-config.json"
    context = MagicMock()
    context.__enter__.return_value = temporary
    named_temporary_file = MagicMock(return_value=context)
    worker_exit = SystemExit(124)

    def execute(argv, timeout):
        events.append(("execute", argv, timeout))
        raise worker_exit

    def unlink(path):
        events.append(("unlink", path))
        if cleanup_error:
            raise cleanup_error

    monkeypatch.setattr(sandbox_runner, "_require_bin", MagicMock(return_value="firecracker"))
    monkeypatch.setattr(sandbox_runner.tempfile, "NamedTemporaryFile", named_temporary_file)
    monkeypatch.setattr(sandbox_runner, "_run_subprocess", execute)
    monkeypatch.setattr(sandbox_runner.os, "unlink", unlink)

    with pytest.raises(SystemExit) as raised:
        sandbox_runner._firecracker_run(
            {
                "kernel_image_path": "/images/vmlinux",
                "rootfs_path": "/images/rootfs.ext4",
                "boot_args": "console=ttyS0 quiet",
                "rootfs_read_only": 1,
                "machine_config": {"vcpu_count": 2, "mem_size_mib": 512},
            },
            19,
        )

    assert raised.value is worker_exit
    named_temporary_file.assert_called_once_with("w", suffix=".json", delete=False)
    assert json.loads(temporary.getvalue()) == {
        "boot-source": {"kernel_image_path": "/images/vmlinux", "boot_args": "console=ttyS0 quiet"},
        "drives": [
            {
                "drive_id": "rootfs",
                "path_on_host": "/images/rootfs.ext4",
                "is_root_device": True,
                "is_read_only": True,
            }
        ],
        "machine-config": {"vcpu_count": 2, "mem_size_mib": 512},
    }
    assert events == [
        ("execute", ["firecracker", "--config-file", "/tmp/firecracker-config.json"], 19),
        ("unlink", "/tmp/firecracker-config.json"),
    ]


def test_wasm_builds_argument_vector_without_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    require_bin = MagicMock(return_value="/opt/wasmer")
    execute = MagicMock(return_value=None)
    monkeypatch.setattr(sandbox_runner, "_require_bin", require_bin)
    monkeypatch.setattr(sandbox_runner, "_run_subprocess", execute)

    assert (
        sandbox_runner._wasm_run(
            "ignored.wasm",
            "--flag 'two words' plain",
            27,
            {
                "runtime": "wasmer",
                "module": Path("module.wasm"),
                "preopen_dirs": [Path("src"), "path with space"],
            },
        )
        is None
    )

    require_bin.assert_called_once_with("wasmer")
    execute.assert_called_once_with(
        [
            "/opt/wasmer",
            "run",
            "--dir",
            "src",
            "--dir",
            "path with space",
            "module.wasm",
            "--flag",
            "two words",
            "plain",
        ],
        27,
    )


def test_wasm_uses_default_runtime_directory_and_image(monkeypatch: pytest.MonkeyPatch) -> None:
    require_bin = MagicMock(return_value="wasmtime")
    execute = MagicMock()
    monkeypatch.setattr(sandbox_runner, "_require_bin", require_bin)
    monkeypatch.setattr(sandbox_runner, "_run_subprocess", execute)

    sandbox_runner._wasm_run("runner.wasm", None, 5, {})

    require_bin.assert_called_once_with("wasmtime")
    execute.assert_called_once_with(["wasmtime", "run", "--dir", ".", "runner.wasm"], 5)


def test_wasm_reports_missing_module_after_runtime_lookup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    require_bin = MagicMock(return_value="wasmtime")
    execute = MagicMock()
    monkeypatch.setattr(sandbox_runner, "_require_bin", require_bin)
    monkeypatch.setattr(sandbox_runner, "_run_subprocess", execute)

    with pytest.raises(SystemExit) as raised:
        sandbox_runner._wasm_run("", None, 5, {})

    assert raised.value.code == 2
    assert capsys.readouterr() == ("", "WASM requires image or runtime_config.module\n")
    require_bin.assert_called_once_with("wasmtime")
    execute.assert_not_called()


def test_wasm_preserves_command_parse_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = MagicMock()
    monkeypatch.setattr(sandbox_runner, "_require_bin", MagicMock(return_value="wasmtime"))
    monkeypatch.setattr(sandbox_runner, "_run_subprocess", execute)

    with pytest.raises(ValueError, match="No closing quotation"):
        sandbox_runner._wasm_run("runner.wasm", "'unterminated", 5, {})

    execute.assert_not_called()


@pytest.mark.parametrize(
    ("runtime_type", "target", "expected_args"),
    [
        (
            "docker",
            "_docker_run",
            ("sandbox", "image", "command", 12, {"KEY": "value"}, "bridge", {"cpu_count": 2}),
        ),
        ("lxc", "_lxc_run", ("sandbox", "image", "command", 12)),
        ("firecracker", "_firecracker_run", ({"config_path": "config.json"}, 12)),
        ("wasm", "_wasm_run", ("image", "command", 12, {"config_path": "config.json"})),
    ],
)
def test_run_sandbox_dispatches_exact_runtime_contract(
    monkeypatch: pytest.MonkeyPatch,
    runtime_type: str,
    target: str,
    expected_args: tuple,
) -> None:
    handlers = {
        name: MagicMock(return_value=f"{name}-result")
        for name in ("_docker_run", "_lxc_run", "_firecracker_run", "_wasm_run")
    }
    for name, handler in handlers.items():
        monkeypatch.setattr(sandbox_runner, name, handler)

    result = sandbox_runner.run_sandbox(
        "sandbox",
        "image",
        "command",
        12,
        {"KEY": "value"},
        runtime_type,
        "bridge",
        {"cpu_count": 2},
        {"config_path": "config.json"},
    )

    assert result == f"{target}-result"
    handlers[target].assert_called_once_with(*expected_args)
    for name, handler in handlers.items():
        if name != target:
            handler.assert_not_called()


def test_run_sandbox_normalizes_optional_configs_for_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    docker_run = MagicMock(return_value="done")
    monkeypatch.setattr(sandbox_runner, "_docker_run", docker_run)

    assert sandbox_runner.run_sandbox("id", "image", resource_limits=None, runtime_config=None) == "done"
    docker_run.assert_called_once_with("id", "image", None, 300, None, "none", {})


def test_run_sandbox_rejects_unsupported_runtime(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        sandbox_runner.run_sandbox("id", "image", runtime_type="process")

    assert raised.value.code == 2
    assert capsys.readouterr() == ("", "Unsupported sandbox runtime_type: process\n")


def test_main_parses_every_option_and_passes_environment_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    run_sandbox = MagicMock(return_value=None)
    monkeypatch.setattr(sandbox_runner, "run_sandbox", run_sandbox)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "observal-sandbox-run",
            "ignored",
            "--sandbox-id",
            "sandbox-id",
            "--image",
            "runner.wasm",
            "--runtime-type",
            "wasm",
            "--command",
            "old command",
            "--timeout",
            "41",
            "--network-policy",
            "restricted",
            "--resource-limits",
            '{"memory_mb": 256}',
            "--runtime-config",
            '{"module": "module.wasm"}',
            "--env",
            'TOKEN="first"',
            "--env",
            "PATH=/untrusted/bin",
            "--env",
            "NO_EQUALS",
            "--env",
            "TOKEN='last'",
            "unknown-value",
        ],
    )

    assert sandbox_runner.main() is None

    run_sandbox.assert_called_once_with(
        "sandbox-id",
        "runner.wasm",
        "old command",
        41,
        {"TOKEN": "last", "PATH": "/untrusted/bin", "NO_EQUALS": ""},
        "wasm",
        "restricted",
        {"memory_mb": 256},
        {"module": "module.wasm"},
    )


def test_main_command_delimiter_replaces_option_command_and_stops_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    run_sandbox = MagicMock(return_value=None)
    monkeypatch.setattr(sandbox_runner, "run_sandbox", run_sandbox)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "observal-sandbox-run",
            "--sandbox-id",
            "id",
            "--image",
            "image",
            "--command",
            "old",
            "--",
            "echo",
            "hello world",
            "--timeout",
            "1",
        ],
    )

    sandbox_runner.main()

    run_sandbox.assert_called_once_with(
        "id", "image", "echo hello world --timeout 1", 300, {}, "docker", "none", {}, {}
    )


@pytest.mark.parametrize("runtime_type", ["docker", "lxc", "wasm"])
def test_main_requires_image_for_image_based_runtimes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runtime_type: str,
) -> None:
    run_sandbox = MagicMock()
    monkeypatch.setattr(sandbox_runner, "run_sandbox", run_sandbox)
    monkeypatch.setattr(sys, "argv", ["observal-sandbox-run", "--runtime-type", runtime_type])

    with pytest.raises(SystemExit) as raised:
        sandbox_runner.main()

    assert raised.value.code == 1
    assert capsys.readouterr() == (
        "",
        "Usage: observal-sandbox-run --sandbox-id <id> --image <image> "
        "[--runtime-type docker|lxc|firecracker|wasm] [--command <cmd>] [--timeout <s>]\n",
    )
    run_sandbox.assert_not_called()


@pytest.mark.parametrize(
    ("runtime_type", "runtime_config"),
    [("firecracker", {}), ("wasm", {"module": "runner.wasm"})],
)
def test_main_allows_runtime_config_driven_execution_without_image(
    monkeypatch: pytest.MonkeyPatch,
    runtime_type: str,
    runtime_config: dict,
) -> None:
    run_sandbox = MagicMock(return_value=None)
    monkeypatch.setattr(sandbox_runner, "run_sandbox", run_sandbox)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "observal-sandbox-run",
            "--sandbox-id",
            "id",
            "--runtime-type",
            runtime_type,
            "--runtime-config",
            json.dumps(runtime_config),
        ],
    )

    sandbox_runner.main()

    run_sandbox.assert_called_once_with("id", "", None, 300, {}, runtime_type, "none", {}, runtime_config)


@pytest.mark.parametrize(
    ("option", "value", "error_type"),
    [
        ("--timeout", "never", ValueError),
        ("--resource-limits", "{", json.JSONDecodeError),
        ("--runtime-config", "{", json.JSONDecodeError),
    ],
)
def test_main_preserves_numeric_and_json_parse_failures(
    monkeypatch: pytest.MonkeyPatch,
    option: str,
    value: str,
    error_type: type[Exception],
) -> None:
    run_sandbox = MagicMock()
    monkeypatch.setattr(sandbox_runner, "run_sandbox", run_sandbox)
    monkeypatch.setattr(sys, "argv", ["observal-sandbox-run", "--image", "image", option, value])

    with pytest.raises(error_type):
        sandbox_runner.main()

    run_sandbox.assert_not_called()
