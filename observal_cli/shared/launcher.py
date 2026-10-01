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
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path


def package_root() -> str:
    """The directory containing the ``observal_cli`` package in use."""
    return str(Path(__file__).resolve().parent.parent.parent)


@lru_cache(maxsize=1)
def importable_in_isolation() -> bool:
    """Whether ``sys.executable`` imports ``observal_cli`` with no PYTHONPATH and no working-directory help."""
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", "import observal_cli"],
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
