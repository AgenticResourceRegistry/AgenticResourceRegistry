# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Constants for standalone server management.

Paths, ports, dependency versions, and download URLs.
"""

from __future__ import annotations

import platform
from pathlib import Path

# ── Base directories ────────────────────────────────────────────

OBSERVAL_HOME = Path.home() / ".observal"
BIN_DIR = OBSERVAL_HOME / "bin"
DATA_DIR = OBSERVAL_HOME / "data"
CONFIG_DIR = OBSERVAL_HOME / "config"
LOG_DIR = OBSERVAL_HOME / "logs"
RUN_DIR = OBSERVAL_HOME / "run"
KEYS_DIR = OBSERVAL_HOME / "keys"

# ── Service ports (non-standard to avoid conflicts) ─────────────

POSTGRES_PORT = 5480
CLICKHOUSE_HTTP_PORT = 8124
CLICKHOUSE_TCP_PORT = 9100
REDIS_PORT = 6380
API_PORT = 8000

# ── Dependency versions ────────────────────────────────────────

POSTGRES_VERSION = "18"
CLICKHOUSE_VERSION = "26.4"
REDIS_VERSION = "8.0"

# ── GitHub repo for downloads ──────────────────────────────────

GITHUB_REPO = "AgenticResourceRegistry/AgenticResourceRegistry"
DEPS_RELEASE_TAG = "deps/v1"

# ── Platform detection ──────────────────────────────────────────


def detect_platform() -> tuple[str, str]:
    """Detect OS and architecture.

    Returns:
        Tuple of (os, arch) e.g. ("linux", "x64"), ("macos", "arm64").
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        os_name = "macos"
    elif system == "linux":
        os_name = "linux"
    else:
        raise RuntimeError(f"Unsupported OS: {system}. Only Linux and macOS are supported.")

    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}. Only x64 and arm64 are supported.")

    return os_name, arch


# ── Download URLs ──────────────────────────────────────────────


def get_dep_urls() -> dict[str, str]:
    """Get download URLs for dependency binaries based on current platform."""
    os_name, arch = detect_platform()

    base = f"https://github.com/{GITHUB_REPO}/releases/download/{DEPS_RELEASE_TAG}"

    # ClickHouse uses different naming
    ch_arch = "amd64" if arch == "x64" else "aarch64"
    ch_os = "linux" if os_name == "linux" else "macos"

    return {
        "postgres": f"{base}/pg-{os_name}-{arch}.tar.gz",
        "clickhouse": f"{base}/clickhouse-{ch_os}-{ch_arch}.tar.gz",
        "redis": f"{base}/redis-{os_name}-{arch}.tar.gz",
    }


# ── Service binary paths ──────────────────────────────────────


def get_bin_paths() -> dict[str, Path]:
    """Get expected paths for service binaries."""
    return {
        "postgres": BIN_DIR / "postgres",
        "initdb": BIN_DIR / "initdb",
        "pg_ctl": BIN_DIR / "pg_ctl",
        "pg_isready": BIN_DIR / "pg_isready",
        "createdb": BIN_DIR / "createdb",
        "clickhouse": BIN_DIR / "clickhouse",
        "redis_server": BIN_DIR / "redis-server",
        "redis_cli": BIN_DIR / "redis-cli",
    }


# ── PID file paths ────────────────────────────────────────────


def get_pid_paths() -> dict[str, Path]:
    """Get PID file paths for each service."""
    return {
        "postgres": RUN_DIR / "postgres.pid",
        "clickhouse": RUN_DIR / "clickhouse.pid",
        "redis": RUN_DIR / "redis.pid",
        "api": RUN_DIR / "api.pid",
    }


# ── Data directory paths ──────────────────────────────────────


def get_data_paths() -> dict[str, Path]:
    """Get data directory paths for each service."""
    return {
        "postgres": DATA_DIR / "pg",
        "clickhouse": DATA_DIR / "ch",
        "redis": DATA_DIR / "redis",
    }
