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
ENTRY_POINT_NAMES = frozenset({"observal", "observal.exe"})
_PYTHON_NAME = re.compile(r"(?:pythonw?|pypy)(?:[0-9.]+)?(?:\.exe)?")


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
    # Windows launchers are invoked without their `.exe` suffix in argv[0].
    invoked = Path(sys.argv[0])
    entry_point = next(
        (
            candidate
            for candidate in (invoked, invoked.with_name(f"{invoked.name}.exe"))
            if candidate.name.lower() in ENTRY_POINT_NAMES and candidate.is_file()
        ),
        None,
    )
    interpreter_mode = entry_point is None and not getattr(sys, "frozen", False)
    if interpreter_mode:
        # Started as `python -m observal_cli...`: the CLI lives in this interpreter's
        # environment. Not resolved: a virtualenv's python is a symlink to its base
        # interpreter (often under /usr/bin or a Homebrew Cellar), which owns nothing here.
        binary_path = Path(sys.executable).absolute()
    else:
        binary_path = (entry_point or Path(sys.executable)).resolve()
    path_str = str(binary_path).lower()

    info = _detect_from_path(binary_path, path_str, interpreter_mode=interpreter_mode)
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


def install_specs(target_version: str, source_refs: list[str] | None = None, repo: str = SOURCE_REPO) -> list[str]:
    """Requirements to try, in order, until one installs ``target_version``.

    A release is pinned on PyPI, then built from its tag if PyPI lacks it. A
    development build exists only as source: ``source_refs`` are the published
    commits found to declare it. With none there is nothing to try. Building
    `main` instead would replace the CLI with whatever version `main` is on now.
    """
    spec = install_spec(target_version)
    if spec != SOURCE_SPEC:
        return [spec, tag_spec(target_version, repo)]
    return [f"{repo}@{ref}" for ref in source_refs or []]


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
    return script_interpreter(info.path) or sys.executable


def script_interpreter(script_path: Path) -> str | None:
    """The Python that owns a console script, or None when it cannot be read from the script."""
    try:
        with script_path.open("rb") as script:
            first_line = script.readline(4096).decode("utf-8").strip()
        if first_line.startswith("#!"):
            # Console scripts with long or spaced paths use a shell-wrapper
            # shebang. Only a Python executable is safe to use for `-m pip`.
            tokens = shlex.split(first_line[2:])
            if tokens:
                interpreter = tokens[0]
                name = Path(interpreter).name.lower()
                if _PYTHON_NAME.fullmatch(name) and Path(interpreter).is_file():
                    return interpreter
    except (OSError, UnicodeError, ValueError):
        pass
    # Windows launchers are binaries; the interpreter sits beside or above `Scripts`.
    if script_path.suffix.lower() == ".exe" and script_path.parent.name.lower() == "scripts":
        for candidate in (script_path.parent / "python.exe", script_path.parent.parent / "python.exe"):
            if candidate.is_file():
                return str(candidate)
    return None


def pip_install_prefix(info: InstallInfo, interpreter: str | None = None) -> list[str]:
    """Choose an installer for the CLI's Python, even when that venv has no pip."""
    interpreter = interpreter or pip_interpreter(info)
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


def _detect_from_path(binary_path: Path, path_str: str, *, interpreter_mode: bool = False) -> InstallInfo:
    """Internal detection logic, separated for testability.

    ``interpreter_mode`` means ``binary_path`` is the Python running the CLI
    as a module, not an `observal` executable. A system package or Homebrew
    formula owns an `observal` executable; a Python under /usr/bin or a Cellar
    says nothing about who installed the CLI into it, so those checks are skipped.
    """

    # Homebrew keeps every formula under a Cellar (macOS and Linuxbrew).
    homebrew = "/cellar/" in path_str
    if not interpreter_mode and (homebrew or path_str.startswith(("/usr/bin/", "/usr/sbin/"))):
        return InstallInfo(
            method=InstallMethod.SYSTEM_PACKAGE,
            path=binary_path,
            writable=os.access(binary_path, os.W_OK),
            managed_by="brew" if homebrew else _detect_system_pkg_mgr(),
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

    # Only the directory `uv tool install` would write to now: with UV_TOOL_DIR set, a copy
    # in a default directory is installed into in place, through its own interpreter.
    if os.environ.get("UV_TOOL_DIR"):
        uv_tool_dirs = [Path(os.environ["UV_TOOL_DIR"])]
    else:
        uv_tool_dirs = [Path.home() / ".local" / "share" / "uv" / "tools"]
        if os.environ.get("XDG_DATA_HOME"):
            uv_tool_dirs.append(Path(os.environ["XDG_DATA_HOME"]) / "uv" / "tools")
        if os.environ.get("APPDATA"):  # Windows
            uv_tool_dirs.append(Path(os.environ["APPDATA"]) / "uv" / "tools")
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


def is_python_interpreter(path: Path) -> bool:
    """Whether ``path`` names a Python interpreter rather than an Observal entry point or standalone binary."""
    return bool(_PYTHON_NAME.fullmatch(path.name.lower()))


def version_argv(path: Path) -> list[str]:
    """Command that prints the CLI version of the installation at ``path``.

    An entry point or a standalone binary (`observal-linux-x64` and the like)
    takes CLI options directly. Only a Python interpreter is given `-m`.
    """
    if not is_python_interpreter(path):
        return [str(path), "--version"]
    # Isolated: report what that interpreter has installed, not an `observal_cli`
    # found in the working directory or on an inherited PYTHONPATH.
    return [str(path), "-I", "-m", "observal_cli.main", "--version"]


# The whole version token, so a canonical prerelease such as `1.17.0rc1` keeps its suffix.
_VERSION_TOKEN = re.compile(r"(?<![\w.])v?(\d+(?:\.\d+){2}[0-9A-Za-z.+-]*)")


def reported_version(output: str) -> str | None:
    """The version token in `observal --version` output, unvalidated, or None when there is none."""
    match = _VERSION_TOKEN.search(output or "")
    return match.group(1) if match else None


def installed_version(path: Path) -> str | None:
    """Version the installation at ``path`` reports now, or None when it cannot run or the output is not a version."""
    from packaging.version import InvalidVersion, Version

    from observal_cli.shared import launcher

    try:
        result = subprocess.run(
            version_argv(path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=launcher.script_env(),  # the installation's version, not an inherited PYTHONPATH's
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    token = reported_version(result.stdout) if result.returncode == 0 else None
    try:
        return token if token and Version(token) else None
    except InvalidVersion:
        return None


@dataclass(frozen=True)
class OtherInstall:
    """A second Observal CLI on this machine. ``info`` is None when no installer we can drive owns it."""

    path: Path
    info: InstallInfo | None
    interpreter: str | None = None


def find_other_installs(current: InstallInfo) -> list[OtherInstall]:
    """Other Observal CLIs reachable on PATH, besides the running one.

    Several copies at different versions are a recurring source of breakage:
    hooks and shells resolve whichever comes first on PATH. Only real
    installations are returned; a second launcher of the running installation
    (uv and pipx expose one outside their environment) is not a copy.
    """
    names = ("observal.exe", "observal") if os.name == "nt" else ("observal",)
    directories = [Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    directories.append(Path.home() / ".local" / "bin")
    # Unresolved on purpose: two virtual environments resolve to the same base Python.
    current_python = Path(sys.executable)
    seen = {current.path.resolve()}
    found: list[OtherInstall] = []
    for directory in directories:
        for name in names:
            candidate = directory / name
            try:
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            other = _classify_other(candidate, resolved, current, current_python)
            if other is not None:
                found.append(other)
    return found


def _classify_other(candidate: Path, resolved: Path, current: InstallInfo, current_python: Path) -> OtherInstall | None:
    path_str = str(resolved).lower()
    if path_str.startswith(("/usr/bin/", "/usr/sbin/")) or "/cellar/" in path_str:
        return OtherInstall(path=candidate, info=None)
    if _is_pipx_path(resolved):
        method = InstallMethod.PIPX
    elif _uv_owns_executable(resolved) or _uv_owns_executable(candidate):
        method = InstallMethod.UV_TOOL
    else:
        interpreter = script_interpreter(resolved)
        if interpreter is None:
            return OtherInstall(path=candidate, info=None)
        if Path(interpreter).parent == current_python.parent:
            return None  # another launcher of the running installation
        info = InstallInfo(method=InstallMethod.PIP, path=resolved, writable=True, managed_by="pip")
        return OtherInstall(path=candidate, info=info, interpreter=interpreter)
    if method == current.method:
        here, there = _environment_root(current.path), _environment_root(resolved)
        if here is None or there is None or here == there:
            return None  # a second launcher of the running installation
        # The same manager, another home (a different PIPX_HOME or UV_TOOL_DIR). Its
        # installer would write to this process's home, not that one: report it instead.
        return OtherInstall(path=candidate, info=None)
    return OtherInstall(
        path=candidate, info=InstallInfo(method=method, path=resolved, writable=True, managed_by=method.value)
    )


def _environment_root(path: Path) -> Path | None:
    """The virtual environment holding ``path`` (the nearest ancestor with a `pyvenv.cfg`), when there is one."""
    try:
        resolved = path.resolve()
    except OSError:
        return None
    return next((parent for parent in resolved.parents if (parent / "pyvenv.cfg").is_file()), None)


def _uv_owns_executable(binary_path: Path) -> bool:
    """Match the actual entry point, not merely a similarly named installed UV tool.

    UV copies Windows launchers into a separate bin directory; environment path
    checks alone cannot identify them. `--show-paths` reports their exact path.
    """
    if not shutil.which("uv"):
        return False
    try:
        result = subprocess.run(
            ["uv", "tool", "list", "--show-paths"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
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
    """Whether ``binary_path`` lives in the pipx home that `pipx install` would write to now.

    With ``PIPX_HOME`` set, pipx installs only there, so a copy in a default home
    is not one this process's `pipx install` can replace: it is classified by its
    interpreter instead and installed into in place.
    """
    if pipx_home := os.environ.get("PIPX_HOME"):
        homes = [Path(pipx_home)]
    else:
        homes = [
            Path.home() / ".local" / "share" / "pipx",
            Path.home() / ".local" / "pipx",
            Path.home() / "Library" / "Application Support" / "pipx",  # macOS, pipx < 1.3
            Path.home() / "pipx",  # Windows
        ]
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
