# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Detect how the CLI was installed to determine upgrade strategy."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

INSTALLER_URL = "https://raw.githubusercontent.com/Observal/Observal/main/install.sh"
# Development builds (X.Y.Z-dev.N) come from main and are never published to PyPI.
SOURCE_SPEC = "git+https://github.com/Observal/Observal@main"


class InstallMethod(Enum):
    UV_TOOL = "uv_tool"
    PIPX = "pipx"
    PIP = "pip"
    BINARY = "binary"
    SYSTEM_PACKAGE = "system"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InstallInfo:
    method: InstallMethod
    path: Path
    writable: bool
    managed_by: str | None


_cached_info: InstallInfo | None = None


def detect() -> InstallInfo:
    """Detect how observal was installed. Result is cached per-process."""
    global _cached_info
    if _cached_info is not None:
        return _cached_info

    binary_path = Path(shutil.which("observal") or sys.executable).resolve()
    path_str = str(binary_path).lower()

    info = _detect_from_path(binary_path, path_str)
    if info.method == InstallMethod.PIPX:
        _write_install_metadata(info)
    _cached_info = info
    return info


def install_spec(target_version: str) -> str:
    """Return the pip-style requirement that installs ``target_version``.

    Published versions are pinned on PyPI. Development builds are not published,
    so they are built from the source repository instead.
    """
    from packaging.version import InvalidVersion, Version

    try:
        if Version(target_version).is_devrelease:
            return SOURCE_SPEC
    except InvalidVersion:
        pass
    return f"observal-cli=={target_version}"


def upgrade_command(target_version: str, install_info: InstallInfo | None = None) -> str:
    """Return the command users should run to install a specific CLI version."""
    info = install_info or detect()
    package = install_spec(target_version)

    if package == SOURCE_SPEC and info.method not in (InstallMethod.UV_TOOL, InstallMethod.PIPX, InstallMethod.PIP):
        return f"uv tool install --force {shlex.quote(package)}"
    if info.method == InstallMethod.UV_TOOL:
        return f"uv tool install --force {shlex.quote(package)}"
    if info.method == InstallMethod.PIPX:
        return f"pipx install --force {shlex.quote(package)}"
    if info.method == InstallMethod.PIP:
        return f"{shlex.quote(sys.executable)} -m pip install {shlex.quote(package)}"
    if info.method == InstallMethod.BINARY and info.managed_by == "curl":
        return f"curl -fsSL {INSTALLER_URL} | bash -s -- --version {_release_tag(target_version)}"
    if info.method == InstallMethod.SYSTEM_PACKAGE and info.managed_by:
        return f"{info.managed_by} upgrade observal"
    return f"observal self upgrade --version {target_version} --force"


def downgrade_command(target_version: str) -> str:
    """Return the command users should run to downgrade the CLI to a specific version."""
    return f"observal self downgrade --version {target_version} --force"


def _detect_from_path(binary_path: Path, path_str: str) -> InstallInfo:
    """Internal detection logic, separated for testability."""

    if path_str.startswith(("/usr/bin/", "/usr/sbin/")):
        return InstallInfo(
            method=InstallMethod.SYSTEM_PACKAGE,
            path=binary_path,
            writable=os.access(binary_path, os.W_OK),
            managed_by=_detect_system_pkg_mgr(),
        )

    if _is_pipx_path(binary_path):
        return InstallInfo(
            method=InstallMethod.PIPX,
            path=binary_path,
            writable=True,
            managed_by="pipx",
        )

    metadata = _read_install_metadata()
    managed_by = "curl" if metadata.get("method") == "curl" and _same_path(metadata.get("path"), binary_path) else None

    if getattr(sys, "frozen", False):
        return InstallInfo(
            method=InstallMethod.BINARY,
            path=binary_path,
            writable=os.access(binary_path, os.W_OK),
            managed_by=managed_by,
        )

    uv_tool_dir = Path.home() / ".local" / "share" / "uv" / "tools"
    if str(binary_path).startswith(str(uv_tool_dir)):
        return InstallInfo(
            method=InstallMethod.UV_TOOL,
            path=binary_path,
            writable=True,
            managed_by="uv",
        )

    if _check_uv_tool_list():
        return InstallInfo(
            method=InstallMethod.UV_TOOL,
            path=binary_path,
            writable=True,
            managed_by="uv",
        )

    if _check_pipx_list():
        return InstallInfo(
            method=InstallMethod.PIPX,
            path=binary_path,
            writable=True,
            managed_by="pipx",
        )

    return InstallInfo(
        method=InstallMethod.PIP,
        path=binary_path,
        writable=os.access(binary_path.parent, os.W_OK),
        managed_by="pip",
    )


def _check_uv_tool_list() -> bool:
    """Check if observal-cli is in uv tool list output."""
    try:
        r = subprocess.run(
            ["uv", "tool", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.returncode == 0 and "observal-cli" in r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _check_pipx_list() -> bool:
    """Check if observal-cli is in pipx list output."""
    try:
        r = subprocess.run(
            ["pipx", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.returncode == 0 and "observal-cli" in r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _is_pipx_path(binary_path: Path) -> bool:
    homes = [
        Path.home() / ".local" / "share" / "pipx",
        Path.home() / ".local" / "pipx",
    ]
    if pipx_home := os.environ.get("PIPX_HOME"):
        homes.append(Path(pipx_home))
    path = str(binary_path)
    return any(path.startswith(str(home / "venvs")) for home in homes)


def _install_metadata_path() -> Path:
    return Path.home() / ".observal" / "install.json"


def _read_install_metadata() -> dict:
    path = _install_metadata_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_install_metadata(info: InstallInfo) -> None:
    path = _install_metadata_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "method": info.managed_by if info.managed_by == "curl" else info.method.value,
        "manager": info.managed_by,
        "path": str(info.path),
    }
    path.write_text(json.dumps(data, indent=2))


def _same_path(value: object, path: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return Path(value).resolve() == path.resolve()
    except OSError:
        return False


def _release_tag(version: str) -> str:
    return version if version.startswith("v") else f"v{version}"


def _detect_system_pkg_mgr() -> str | None:
    """Detect which system package manager owns the observal binary."""
    checks = [
        (["dpkg", "-S", "observal"], "apt"),
        (["rpm", "-qf", "/usr/bin/observal"], "dnf"),
    ]
    for cmd, name in checks:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=3)
            if r.returncode == 0:
                return name
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return None
