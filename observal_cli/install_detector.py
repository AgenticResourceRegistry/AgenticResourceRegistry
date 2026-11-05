# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Detect how the CLI was installed to determine upgrade strategy."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

INSTALLER_URL = "https://raw.githubusercontent.com/AgenticResourceRegistry/AgenticResourceRegistry/main/install.sh"
# Development builds (X.Y.Z-dev.N) come from main and are never published to PyPI.
SOURCE_REPO = "git+https://github.com/AgenticResourceRegistry/AgenticResourceRegistry"
SOURCE_SPEC = f"{SOURCE_REPO}@main"


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

    # Prefer the entry point actually invoked; PATH may contain another Observal
    # installation (for example a mise Python alongside a pipx/uv tool).
    invoked = Path(sys.argv[0])
    entry_point = invoked if invoked.name in {"observal", "observal.exe"} and invoked.is_file() else None
    binary_path = (entry_point or Path(sys.executable)).resolve()
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


def tag_spec(target_version: str, repo: str = SOURCE_REPO) -> str:
    """Requirement that builds ``target_version`` from its release tag (``vX.Y.Z[-rc.N]``)."""
    from packaging.version import Version

    v = Version(target_version)
    tag = f"v{v.base_version}"
    if v.pre:
        tag += f"-{ {'a': 'alpha', 'b': 'beta', 'rc': 'rc'}[v.pre[0]] }.{v.pre[1]}"
    return f"{repo}@{tag}"


def pip_interpreter(info: InstallInfo) -> str:
    """Read the console script's interpreter instead of assuming sys.executable owns it.

    An invoked entry point may belong to a different Python environment than
    the interpreter running this module (or the first `observal` on PATH).
    """
    try:
        with info.path.open("rb") as script:
            first_line = script.readline(4096).decode("utf-8").strip()
        if first_line.startswith("#!"):
            # Console scripts with long or spaced paths use a shell-wrapper
            # shebang. Only a Python executable is safe to use for `-m pip`.
            tokens = shlex.split(first_line[2:])
            if tokens:
                interpreter = tokens[0]
                name = Path(interpreter).name.lower()
                if re.fullmatch(r"(?:pythonw?|pypy)(?:[0-9.]+)?(?:\.exe)?", name) and Path(interpreter).is_file():
                    return interpreter
    except (OSError, UnicodeError, ValueError):
        pass
    return sys.executable


def pip_install_prefix(info: InstallInfo) -> list[str]:
    """Choose an installer for the CLI's Python, even when that venv has no pip."""
    interpreter = pip_interpreter(info)
    try:
        has_pip = (
            subprocess.run([interpreter, "-m", "pip", "--version"], capture_output=True, timeout=5).returncode == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        has_pip = False
    if not has_pip and shutil.which("uv"):
        return ["uv", "pip", "install", "--python", interpreter]
    return [interpreter, "-m", "pip", "install"]


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
        return shlex.join([*pip_install_prefix(info), package])
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

    uv_tool_dirs = [Path.home() / ".local" / "share" / "uv" / "tools"]
    if os.environ.get("XDG_DATA_HOME"):
        uv_tool_dirs.append(Path(os.environ["XDG_DATA_HOME"]) / "uv" / "tools")
    if os.environ.get("UV_TOOL_DIR"):
        uv_tool_dirs.append(Path(os.environ["UV_TOOL_DIR"]))
    if any(binary_path.is_relative_to(directory.resolve()) for directory in uv_tool_dirs) or _uv_owns_executable(
        binary_path
    ):
        return InstallInfo(
            method=InstallMethod.UV_TOOL,
            path=binary_path,
            writable=True,
            managed_by="uv",
        )

    return InstallInfo(
        method=InstallMethod.PIP,
        path=binary_path,
        writable=os.access(binary_path.parent, os.W_OK),
        managed_by="pip",
    )


def _uv_owns_executable(binary_path: Path) -> bool:
    """Match the actual entry point, not merely a similarly named installed UV tool.

    UV copies Windows launchers into a separate bin directory; environment path
    checks alone cannot identify them. `--show-paths` reports their exact path.
    """
    if not shutil.which("uv"):
        return False
    try:
        result = subprocess.run(["uv", "tool", "list", "--show-paths"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        match = re.fullmatch(r"\s*-\s+observal(?:\.exe)?\s+\((.+)\)\s*", line, flags=re.IGNORECASE)
        if match and Path(match.group(1)).resolve() == binary_path.resolve():
            return True
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
