# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Antigravity CLI hook specification for session telemetry push.

Hooks are configured in hooks.json at:
  ~/.gemini/config/hooks.json  (global)
  .agents/hooks.json           (workspace)

Schema for PreToolUse/PostToolUse (matcher-based):
  {
    "<hook-name>": {
      "PreToolUse": [
        {
          "matcher": "<tool-name-or-*>",
          "hooks": [
            {"type": "command", "command": "<cmd>", "timeout": 30}
          ]
        }
      ]
    }
  }

Schema for PreInvocation/PostInvocation/Stop (flat handler list):
  {
    "<hook-name>": {
      "PreInvocation": [
        {"type": "command", "command": "<cmd>", "timeout": 30}
      ]
    }
  }

Events used for telemetry:
  PreInvocation  - fires before each model call
  Stop           - fires when the execution loop terminates

Input/Output contract:
  Hooks receive JSON on stdin (includes conversationId, transcriptPath, etc.)
  Hooks must return JSON on stdout (e.g. {} for PreInvocation, {"decision": ""} for Stop)
"""

from __future__ import annotations

import sys
from pathlib import Path

from observal_cli.shared.launcher import isolation_flag

_PKG_ROOT = str(Path(__file__).resolve().parent.parent.parent)

_OBSERVAL_HOOK_NAME = "observal-telemetry"


def _python_cmd() -> str:
    """Return python command with PYTHONPATH set if needed.

    Handles three platforms:
      - Native Windows: set "PYTHONPATH=..." && python.exe
      - WSL (Linux under Windows): wsl.exe /path/to/python
      - macOS / Linux: PYTHONPATH=... python (or bare python if importable)
    """
    import subprocess

    # WSL: agy is a Windows binary, so hook commands need wsl.exe prefix
    try:
        is_wsl = subprocess.run(["wslpath", "-w", "/"], capture_output=True).returncode == 0
    except Exception:
        is_wsl = False

    if is_wsl:
        return f"wsl.exe {sys.executable}"

    # Check if observal_cli is importable without PYTHONPATH
    # Checked in an isolated interpreter: the CLI's own process may import
    # observal_cli only through its working directory or PYTHONPATH.
    from observal_cli.shared.launcher import importable_in_isolation

    if importable_in_isolation():
        return sys.executable

    # Not importable: set PYTHONPATH to the package root
    if sys.platform == "win32":
        return f'set "PYTHONPATH={_PKG_ROOT}" && {sys.executable}'
    return f"PYTHONPATH={_PKG_ROOT} {sys.executable}"


def build_antigravity_hooks(*_args, **_kwargs) -> dict:
    """Build the hooks.json content for Antigravity CLI telemetry.

    Uses PreInvocation (captures user prompt) and Stop (flushes session).
    Returns the full hooks.json dict, the caller writes it to disk.

    PreInvocation and Stop use the flat handler format (no matcher/hooks nesting)
    per the Antigravity hooks documentation.
    """
    cmd = f"{_python_cmd()} {isolation_flag()} -m observal_cli.hooks.antigravity_session_push"
    return {
        _OBSERVAL_HOOK_NAME: {
            "PreInvocation": [{"type": "command", "command": cmd, "timeout": 30}],
            "Stop": [{"type": "command", "command": cmd, "timeout": 30}],
        }
    }
