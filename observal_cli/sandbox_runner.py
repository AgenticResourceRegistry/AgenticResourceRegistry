# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Santhosh Raja <santhoshpkraja2004@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""observal-sandbox-run: local sandbox executor."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime

from loguru import logger as optic

from observal_cli.config import load as load_config

MAX_LOG_BYTES = 64 * 1024  # 64KB truncation limit for logs


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _send_span(server_url: str, access_token: str, span: dict):
    """No-op: structured span telemetry was removed in favor of JSONL sessions."""
    return


def _truncate(text: str) -> str:
    return text[:MAX_LOG_BYTES] + "\n... [truncated at 64KB]" if len(text) > MAX_LOG_BYTES else text


def _missing_runtime(name: str) -> None:
    print(f"local-runtime-missing: {name} is not installed or not on PATH", file=sys.stderr)
    sys.exit(127)


def _require_bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        _missing_runtime(name)
    return path


def _run_subprocess(argv: list[str], timeout: int) -> None:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    output = _truncate((result.stdout or "") + (f"\n[stderr]\n{result.stderr}" if result.stderr else ""))
    print(output, end="")
    sys.exit(result.returncode)


def _docker_run(
    sandbox_id: str,
    image: str,
    command: str | None,
    timeout: int,
    env: dict | None,
    network_policy: str,
    resource_limits: dict,
):
    try:
        import docker
        import docker.errors
    except ImportError:
        print(
            "local-runtime-missing: Docker SDK not found. Install: pip install 'observal-cli[sandbox]'", file=sys.stderr
        )
        sys.exit(127)

    client = docker.from_env()
    start_time = _now_iso()
    wall_start = time.monotonic()
    container = None
    try:
        run_kwargs = {
            "image": image,
            "detach": True,
            "environment": env or {},
            "stdout": True,
            "stderr": True,
        }
        if command:
            run_kwargs["command"] = command
        if network_policy in {"none", "host", "bridge"}:
            run_kwargs["network_mode"] = network_policy
        elif network_policy == "restricted":
            # ponytail: restricted policy is local-runner only for now; use Docker's no-network mode until policy profiles exist.
            run_kwargs["network_mode"] = "none"
        if resource_limits.get("memory_mb"):
            run_kwargs["mem_limit"] = f"{int(resource_limits['memory_mb'])}m"
        if resource_limits.get("cpu_count"):
            run_kwargs["nano_cpus"] = int(float(resource_limits["cpu_count"]) * 1_000_000_000)

        container = client.containers.run(**run_kwargs)
        result = container.wait(timeout=timeout)
        wall_ms = int((time.monotonic() - wall_start) * 1000)

        exit_code = result.get("StatusCode", -1)
        logs = container.logs(stdout=True, stderr=True)
        if isinstance(logs, bytes):
            logs = logs.decode("utf-8", errors="replace")
        logs = _truncate(logs)

        container.reload()
        oom_killed = container.attrs.get("State", {}).get("OOMKilled", False)
        container_id = container.short_id
        print(logs, end="")

        access_token = os.environ.get("OBSERVAL_KEY", "")
        server_url = os.environ.get("OBSERVAL_SERVER", "")
        if not access_token or not server_url:
            cfg = load_config()
            access_token = access_token or cfg.get("access_token", "")
            server_url = server_url or cfg.get("server_url", "")

        _send_span(
            server_url,
            access_token,
            {
                "span_id": str(uuid.uuid4()),
                "trace_id": str(uuid.uuid4()),
                "parent_span_id": None,
                "type": "sandbox_exec",
                "name": f"sandbox:{image}",
                "method": "",
                "input": json.dumps({"image": image, "command": command, "sandbox_id": sandbox_id}),
                "output": logs,
                "error": None if exit_code == 0 else f"exit_code={exit_code}",
                "start_time": start_time,
                "end_time": _now_iso(),
                "latency_ms": wall_ms,
                "status": "success" if exit_code == 0 else "error",
                "harness": "",
                "metadata": {},
                "container_id": container_id,
                "exit_code": exit_code,
                "oom_killed": oom_killed,
            },
        )
        sys.exit(exit_code)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if container:
            try:
                container.remove(force=True)
            except Exception as exc:
                optic.warning("Failed to remove container: error={}", exc)


def _lxc_run(sandbox_id: str, image: str, command: str | None, timeout: int) -> None:
    lxc = _require_bin("lxc")
    name = f"observal-{sandbox_id[:8]}-{uuid.uuid4().hex[:8]}"
    subprocess.run([lxc, "launch", image, name, "--ephemeral"], check=True, timeout=timeout)
    try:
        _run_subprocess([lxc, "exec", name, "--", "sh", "-lc", command or "sh"], timeout)
    finally:
        subprocess.run([lxc, "delete", name, "--force"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _firecracker_run(runtime_config: dict, timeout: int) -> None:
    firecracker = _require_bin("firecracker")
    config_path = runtime_config.get("config_path")
    cleanup_path = None
    if not config_path:
        kernel = runtime_config.get("kernel_image_path")
        rootfs = runtime_config.get("rootfs_path")
        if not (kernel and rootfs):
            print("Firecracker requires runtime_config.config_path or kernel_image_path/rootfs_path", file=sys.stderr)
            sys.exit(2)
        cfg = {
            "boot-source": {
                "kernel_image_path": kernel,
                "boot_args": runtime_config.get("boot_args", "console=ttyS0 reboot=k panic=1 pci=off"),
            },
            "drives": [
                {
                    "drive_id": "rootfs",
                    "path_on_host": rootfs,
                    "is_root_device": True,
                    "is_read_only": bool(runtime_config.get("rootfs_read_only", False)),
                }
            ],
            "machine-config": runtime_config.get("machine_config", {"vcpu_count": 1, "mem_size_mib": 256}),
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
            json.dump(cfg, tmp)
            cleanup_path = tmp.name
        config_path = cleanup_path
    try:
        _run_subprocess([firecracker, "--config-file", str(config_path)], timeout)
    finally:
        if cleanup_path:
            try:
                os.unlink(cleanup_path)
            except OSError:
                pass


def _wasm_run(image: str, command: str | None, timeout: int, runtime_config: dict) -> None:
    wasmtime = _require_bin(runtime_config.get("runtime", "wasmtime"))
    module = runtime_config.get("module") or image
    if not module:
        print("WASM requires image or runtime_config.module", file=sys.stderr)
        sys.exit(2)
    argv = [wasmtime, "run"]
    for directory in runtime_config.get("preopen_dirs", ["."]):
        argv.extend(["--dir", str(directory)])
    argv.append(str(module))
    if command:
        argv.extend(shlex.split(command))
    _run_subprocess(argv, timeout)


def run_sandbox(
    sandbox_id: str,
    image: str,
    command: str | None = None,
    timeout: int = 300,
    env: dict | None = None,
    runtime_type: str = "docker",
    network_policy: str = "none",
    resource_limits: dict | None = None,
    runtime_config: dict | None = None,
):
    """Dispatch to the configured local sandbox runtime."""
    resource_limits = resource_limits or {}
    runtime_config = runtime_config or {}
    if runtime_type == "docker":
        return _docker_run(sandbox_id, image, command, timeout, env, network_policy, resource_limits)
    if runtime_type == "lxc":
        return _lxc_run(sandbox_id, image, command, timeout)
    if runtime_type == "firecracker":
        return _firecracker_run(runtime_config, timeout)
    if runtime_type == "wasm":
        return _wasm_run(image, command, timeout, runtime_config)
    print(f"Unsupported sandbox runtime_type: {runtime_type}", file=sys.stderr)
    sys.exit(2)


def main():
    """CLI entry point for observal-sandbox-run."""
    args = sys.argv[1:]
    sandbox_id = ""
    image = ""
    command = None
    timeout = 300
    env = {}
    runtime_type = "docker"
    network_policy = "none"
    resource_limits = {}
    runtime_config = {}

    i = 0
    while i < len(args):
        if args[i] == "--sandbox-id" and i + 1 < len(args):
            sandbox_id = args[i + 1]
            i += 2
        elif args[i] == "--image" and i + 1 < len(args):
            image = args[i + 1]
            i += 2
        elif args[i] == "--runtime-type" and i + 1 < len(args):
            runtime_type = args[i + 1]
            i += 2
        elif args[i] == "--command" and i + 1 < len(args):
            command = args[i + 1]
            i += 2
        elif args[i] == "--timeout" and i + 1 < len(args):
            timeout = int(args[i + 1])
            i += 2
        elif args[i] == "--network-policy" and i + 1 < len(args):
            network_policy = args[i + 1]
            i += 2
        elif args[i] == "--resource-limits" and i + 1 < len(args):
            resource_limits = json.loads(args[i + 1] or "{}")
            i += 2
        elif args[i] == "--runtime-config" and i + 1 < len(args):
            runtime_config = json.loads(args[i + 1] or "{}")
            i += 2
        elif args[i] == "--env" and i + 1 < len(args):
            k, _, v = args[i + 1].partition("=")
            env[k] = v.strip("\"'")
            i += 2
        elif args[i] == "--":
            command = " ".join(args[i + 1 :])
            break
        else:
            i += 1

    if not image and runtime_type in {"docker", "lxc", "wasm"} and not runtime_config.get("module"):
        print(
            "Usage: observal-sandbox-run --sandbox-id <id> --image <image> [--runtime-type docker|lxc|firecracker|wasm] [--command <cmd>] [--timeout <s>]",
            file=sys.stderr,
        )
        sys.exit(1)

    run_sandbox(sandbox_id, image, command, timeout, env, runtime_type, network_policy, resource_limits, runtime_config)


if __name__ == "__main__":
    main()
