# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Codex CLI hook specification for session telemetry.

Codex CLI uses the same hook format as Claude Code (PascalCase events,
hooks.json with matcher groups). Hooks live at ~/.codex/hooks.json.

Events: UserPromptSubmit, Stop (only 2 needed — session push reads JSONL incrementally).
"""

from __future__ import annotations

import sys
from pathlib import Path

from observal_cli.shared.launcher import isolation_flag

CODEX_HOOK_EVENTS = (
    "UserPromptSubmit",
    "Stop",
)

_PKG_ROOT = str(Path(__file__).resolve().parent.parent.parent)


def _python_cmd() -> str:
    """Return python command with PYTHONPATH set if needed."""
    # Checked in an isolated interpreter: the CLI's own process may import
    # observal_cli only through its working directory or PYTHONPATH.
    from observal_cli.shared.launcher import importable_in_isolation

    if importable_in_isolation():
        return sys.executable
    if sys.platform == "win32":
        return f'set "PYTHONPATH={_PKG_ROOT}" && {sys.executable}'
    return f"PYTHONPATH={_PKG_ROOT} {sys.executable}"


def build_codex_hooks() -> dict:
    """Return the complete hooks.json content for Codex CLI.

    Uses Claude Code hook format:
    {"hooks": {"EventName": [{"matcher": "", "hooks": [{"type": "command", "command": "..."}]}]}}
    """
    cmd = f"{_python_cmd()} {isolation_flag()} -m observal_cli.hooks.session_push --harness codex"
    hooks: dict[str, list[dict]] = {}
    for event in CODEX_HOOK_EVENTS:
        hooks[event] = [{"matcher": "", "hooks": [{"type": "command", "command": cmd}]}]
    return {"hooks": hooks}
