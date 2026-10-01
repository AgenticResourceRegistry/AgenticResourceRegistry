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

from observal_cli.shared.launcher import isolation_flag, module_command

_OBSERVAL_HOOK_NAME = "observal-telemetry"


def _launcher(module: str) -> str:
    """The hook command prefix running ``module``.

    WSL (Linux under Windows): agy is a Windows binary, so the command needs the
    wsl.exe prefix. Otherwise the shared launcher (quoted POSIX form, or the
    shell-neutral Windows form).
    """
    import subprocess

    try:
        is_wsl = subprocess.run(["wslpath", "-w", "/"], capture_output=True).returncode == 0
    except Exception:
        is_wsl = False
    if is_wsl:
        return f"wsl.exe {sys.executable} {isolation_flag()} -m {module}"
    return module_command(module)


def build_antigravity_hooks(*_args, **_kwargs) -> dict:
    """Build the hooks.json content for Antigravity CLI telemetry.

    Uses PreInvocation (captures user prompt) and Stop (flushes session).
    Returns the full hooks.json dict, the caller writes it to disk.

    PreInvocation and Stop use the flat handler format (no matcher/hooks nesting)
    per the Antigravity hooks documentation.
    """
    cmd = _launcher("observal_cli.hooks.antigravity_session_push")
    return {
        _OBSERVAL_HOOK_NAME: {
            "PreInvocation": [{"type": "command", "command": cmd, "timeout": 30}],
            "Stop": [{"type": "command", "command": cmd, "timeout": 30}],
        }
    }
