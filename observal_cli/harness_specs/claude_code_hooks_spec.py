# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Declarative hook specification for Claude Code settings.

Defines the desired state of Observal-managed hooks. The reconciler
compares this spec against the user's current ~/.claude/settings.json
and applies non-destructive updates.

Session JSONL strategy: only 2 events are needed (UserPromptSubmit + Stop)
since we read the JSONL file incrementally rather than parsing individual
hook events.

Bump HOOKS_SPEC_VERSION whenever the hook definitions change.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path, PureWindowsPath

from observal_cli.shared.utils import OBSERVAL_METADATA_KEY

# Bump this when hook definitions change.
HOOKS_SPEC_VERSION = "12"


# Parent of the observal_cli package directory
_PKG_ROOT = str(Path(__file__).resolve().parent.parent.parent)


def _shell_path(path: str) -> str:
    """Quote a path for the shell Claude Code runs hooks in.

    Claude Code runs hook commands through bash on every platform (Git Bash on
    Windows), which strips the backslashes from a native Windows path, so
    Windows paths are written with forward slashes.
    """
    if sys.platform == "win32":
        path = PureWindowsPath(path).as_posix()
    return shlex.quote(path)


def _python_cmd() -> str:
    """Return python command with PYTHONPATH set if needed."""
    interpreter = _shell_path(sys.executable)
    try:
        import importlib.util

        if importlib.util.find_spec("observal_cli") is not None:
            return interpreter
    except Exception:
        pass
    return f"PYTHONPATH={_shell_path(_PKG_ROOT)} {interpreter}"


def get_desired_hooks() -> dict[str, list[dict]]:
    """Return the desired hooks spec for Claude Code settings.

    Only 2 events: UserPromptSubmit and Stop.  Both invoke the session
    push hook which reads the JSONL file incrementally.
    """
    meta = {OBSERVAL_METADATA_KEY: {"version": HOOKS_SPEC_VERSION}}
    cmd = f"{_python_cmd()} -m observal_cli.hooks.session_push --harness claude-code"

    hook_group: list[dict] = [{**meta, "hooks": [{"type": "command", "command": cmd}]}]

    return {
        "UserPromptSubmit": hook_group,
        "Stop": hook_group,
    }


def get_desired_env(*_args, **_kwargs) -> dict[str, str]:
    """Legacy stub - no env vars needed for session JSONL push.

    Old callers pass (server_url, hooks_token, ...) - ignored.
    Config now lives in ~/.observal/config.json.
    """
    return {}


# Keys in settings.env that Observal manages (for cleanup).
MANAGED_ENV_KEYS = frozenset(
    {
        "OBSERVAL_HOOKS_URL",
        "OBSERVAL_HOOKS_SPEC_VERSION",
        "OBSERVAL_USER_ID",
        "OBSERVAL_USERNAME",
        "OBSERVAL_AGENT_NAME",
    }
)
