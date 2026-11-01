# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Copilot CLI hook specification for session telemetry.

Copilot CLI hooks live at ~/.copilot/hooks/*.json (user-level) or
.github/hooks/*.json (project-level). Each file is a JSON object with
version and hooks keys. Hooks receive JSON on stdin and fire synchronously.

Events: sessionStart, sessionEnd, userPromptSubmitted, preToolUse, postToolUse.
"""

from __future__ import annotations

import sys
from pathlib import Path

from observal_cli.shared.launcher import isolation_flag, windows_module_command, windows_powershell_module_command

COPILOT_CLI_HOOK_EVENTS = (
    "sessionStart",
    "sessionEnd",
    "userPromptSubmitted",
    "preToolUse",
    "postToolUse",
)

# Parent of the observal_cli package directory
_PKG_ROOT = str(Path(__file__).resolve().parent.parent.parent)


def _python_cmd() -> str:
    """Return python command with PYTHONPATH set if needed.

    Quotes the path to handle spaces in directory names.
    """
    # Checked in an isolated interpreter: the CLI's own process may import
    # observal_cli only through its working directory or PYTHONPATH.
    from observal_cli.shared.launcher import importable_in_isolation

    if importable_in_isolation():
        # Quote to handle spaces in paths
        return f'"{sys.executable}"'
    return f'PYTHONPATH="{_PKG_ROOT}" "{sys.executable}"'


def build_copilot_cli_hooks(agent_id: str = "") -> dict:
    """Return the complete hook file content for Copilot CLI.

    Produces a JSON-serializable dict matching the Copilot CLI hook file
    format: {"version": 1, "hooks": {"eventName": [{"type": "command", "bash": "...", "timeoutSec": 5}]}}

    Includes powershell key for Windows environments where Copilot CLI
    executes hooks via PowerShell rather than bash.

    When *agent_id* is supplied, ``OBSERVAL_AGENT_ID`` is prepended to both
    command forms so the session push hook can attribute events to a specific
    agent+version via the lockfile. This mirrors the per-agent attribution
    Kiro gets from build_kiro_hooks(). Copilot CLI project hooks live in
    ``.github/hooks/observal.json`` (one file per project), so the UUID
    identifies whichever agent was pulled into that project.
    """
    module = "observal_cli.hooks.session_push"
    env = {"OBSERVAL_AGENT_ID": agent_id} if agent_id else None
    if sys.platform == "win32":
        bash_cmd = f"{windows_module_command(module, env)} --harness copilot-cli"
    else:
        bash_cmd = f"{_python_cmd()} {isolation_flag()} -m {module} --harness copilot-cli"
        if agent_id:
            bash_cmd = f"OBSERVAL_AGENT_ID={agent_id} {bash_cmd}"
    ps_cmd = f"{windows_powershell_module_command(module, env)} --harness copilot-cli"

    hooks: dict[str, list[dict]] = {}
    for event in COPILOT_CLI_HOOK_EVENTS:
        hooks[event] = [{"type": "command", "bash": bash_cmd, "powershell": ps_cmd, "timeoutSec": 5}]
    return {"version": 1, "hooks": hooks}
