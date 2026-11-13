# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""How hook and MCP launchers start ``python <isolation> -m observal_cli.<module>``.

Launchers run in another process, usually from a project directory, with an
environment the CLI does not control, so importability is checked the way
they will run. The CLI's own process is not a guide: it may import
``observal_cli`` only through its working directory or ``PYTHONPATH``.

Nothing outside the interpreter's own installation, or the trusted package
root, may supply ``observal_cli``:

* Installed (imports in an isolated interpreter): launch with ``-I``. That
  ignores every ``PYTHON*`` variable, including an inherited ``PYTHONPATH``,
  skips user site-packages and keeps the working directory off ``sys.path``,
  which is the environment the check verified.
* Source checkout: launch with ``-P`` (no working directory) and an explicit
  ``PYTHONPATH`` set to the package root, which replaces rather than extends
  any inherited value.

The same rule applies to the CLI's own child processes (:func:`module_subprocess`),
which inherit the caller's working directory, often a project, and to installed
``observal`` executables the CLI runs (:func:`script_env`).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path, PureWindowsPath


def package_root() -> str:
    """The directory containing the ``observal_cli`` package in use."""
    return str(Path(__file__).resolve().parent.parent.parent)


@lru_cache(maxsize=1)
def importable_in_isolation() -> bool:
    """Whether ``sys.executable`` imports ``observal_cli`` with no PYTHONPATH and no working-directory help."""
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "import importlib.util; assert all(importlib.util.find_spec(m) is not None for m in ('observal_cli.hook_gate', 'observal_cli.hooks.session_push'))",
            ],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def isolation_flag() -> str:
    """``-I`` for an installed interpreter, else ``-P`` (Python >= 3.11) with an explicit PYTHONPATH."""
    return "-I" if importable_in_isolation() else "-P"


def pythonpath_env() -> dict[str, str]:
    """Environment a launcher needs to import ``observal_cli`` (empty when installed)."""
    return {} if importable_in_isolation() else {"PYTHONPATH": package_root()}


def posix_prefix() -> str:
    """Shell-quoted interpreter, with a ``PYTHONPATH=`` assignment when it is needed."""
    executable = shlex.quote(sys.executable)
    if importable_in_isolation():
        return executable
    return f"PYTHONPATH={shlex.quote(package_root())} {executable}"


def posix_module_command(module: str) -> str:
    """``<prefix> <isolation flag> -m <module>`` for a POSIX shell."""
    return f"{posix_prefix()} {isolation_flag()} -m {module}"


def module_subprocess(module: str, *args: str, options: tuple[str, ...] = ()) -> tuple[list[str], dict[str, str]]:
    """Argv and environment for running ``observal_cli.<module>`` as a child of this process.

    A bare ``sys.executable -m`` would put the child's working directory first
    on ``sys.path``. A process that is itself isolated (as installed hooks and
    MCP servers are) already proves ``-I`` works, so the probe is skipped.
    ``options`` are extra interpreter options placed before ``-m``.
    """
    env = dict(os.environ)
    if sys.flags.isolated:
        return [sys.executable, "-I", *options, "-m", module, *args], env
    env.update(pythonpath_env())
    return [sys.executable, isolation_flag(), *options, "-m", module, *args], env


def script_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for running an installed ``observal`` executable (console script or binary) as a child.

    Running a script never puts the working directory on ``sys.path``, but the
    ``PYTHON*`` variables still apply, and an inherited ``PYTHONPATH`` would
    supply its own ``observal_cli`` in place of the installed one. It is always
    dropped: the child must be the installation, not whatever the caller's
    environment adds. A process that is itself isolated (``-I``) ignored every
    ``PYTHON*`` variable and user site-packages, so its child does the same.
    """
    source = os.environ if base is None else base
    if sys.flags.isolated:
        env = {key: value for key, value in source.items() if not key.upper().startswith("PYTHON")}
        env["PYTHONNOUSERSITE"] = "1"
        return env
    return {key: value for key, value in source.items() if key.upper() != "PYTHONPATH"}


def _hex(text: str) -> str:
    """``text`` as a Python expression made of hex digits only: safe inside any shell's double quotes."""
    return f"bytes.fromhex('{text.encode().hex()}').decode()"


def _windows_bootstrap(module: str, env: dict[str, str] | None) -> str | None:
    """``-c`` code that sets ``env``, adds the package root when needed and runs ``module``, or None."""
    lines = []
    if env:
        lines.append("import os")
        lines.extend(f"os.environ[{_hex(key)}]={_hex(value)}" for key, value in env.items())
    if not importable_in_isolation():
        lines.append(f"import sys;sys.path.insert(0,{_hex(package_root())})")  # hex: no shell sees the path
    if not lines:
        return None
    lines.append(f"import runpy;runpy.run_module('{module}',run_name='__main__',alter_sys=True)")
    return ";".join(lines)


def windows_module_argv(module: str, env: dict[str, str] | None = None) -> list[str]:
    """Argv running ``module`` on Windows with ``-I``, needing no environment-variable syntax."""
    code = _windows_bootstrap(module, env)
    return [sys.executable, "-I", "-c", code] if code else [sys.executable, "-I", "-m", module]


def windows_module_command(module: str, env: dict[str, str] | None = None) -> str:
    """A hook command string for Windows that cmd.exe and Git Bash both read the same way.

    ``set "VAR=..." &&`` is cmd.exe-only (bash runs ``set`` and keeps going without
    the variable), and an unquoted ``C:\\Program Files`` interpreter splits. Instead the
    interpreter is double-quoted with forward slashes, runs with ``-I`` (no working
    directory, no inherited PYTHON* variables), and any package root or variable is set
    from hex-encoded ``-c`` code, so no value needs shell quoting.
    """
    code = _windows_bootstrap(module, env)
    interpreter = '"' + PureWindowsPath(sys.executable).as_posix() + '"'
    return f'{interpreter} -I -c "{code}"' if code else f"{interpreter} -I -m {module}"


def windows_powershell_module_command(module: str, env: dict[str, str] | None = None) -> str:
    """PowerShell form of the isolated Windows launcher, with literal arguments.

    PowerShell needs ``&`` to execute a quoted path. Single-quoted arguments
    cannot expand inherited variables or metacharacters in the bootstrap.
    """
    argv = windows_module_argv(module, env)
    return "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)


def module_command(module: str, env: dict[str, str] | None = None) -> str:
    """The hook command prefix running ``module`` (arguments are appended by the caller)."""
    if sys.platform == "win32":
        return windows_module_command(module, env)
    assignments = "".join(f"{key}={shlex.quote(value)} " for key, value in (env or {}).items())
    return f"{assignments}{posix_module_command(module)}"
