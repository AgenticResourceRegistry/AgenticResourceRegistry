# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""VS Code Copilot hook specification for session telemetry.

VS Code Copilot hooks live at .github/hooks/*.json (workspace) or
~/.copilot/hooks/*.json (user-level). Each file is a JSON object with
version 1 and a hooks key mapping event names to command arrays.

Key findings from testing:
- VS Code on Windows uses the "command" field (not "bash" or "powershell")
- The "command" value is executed via PowerShell
- For paths with spaces, use a .ps1 wrapper script invoked via
  powershell -ExecutionPolicy Bypass -File
- PascalCase event names produce VS Code compatible payloads with
  hook_event_name (snake_case) and session_id fields in stdin

Events: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop.
"""

from __future__ import annotations

COPILOT_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
)


def build_copilot_hooks(hooks_dir: str = ".github/hooks", bash_cmd: str | None = None) -> dict:
    """Return the complete hook file content for VS Code Copilot.

    Produces a JSON-serializable dict matching the Copilot hook format.
    Includes both:
    - "bash": for Copilot CLI on Linux/WSL (uses the resolved Python path)
    - "command": for VS Code on Windows (PowerShell wrapper)

    Args:
        hooks_dir: Relative path to the hooks directory (for the .ps1 script reference).
        bash_cmd: The bash command for Linux/WSL. If None, uses sys.executable.
    """

    ps1_path = f"{hooks_dir}/run_hook.ps1"
    win_cmd = f"powershell -ExecutionPolicy Bypass -File {ps1_path}"

    if bash_cmd is None:
        from observal_cli.shared.launcher import posix_module_command

        bash_cmd = f"{posix_module_command('observal_cli.hooks.session_push')} --harness copilot --json-response"

    hooks: dict[str, list[dict]] = {}
    for event in COPILOT_HOOK_EVENTS:
        hooks[event] = [{"type": "command", "bash": bash_cmd, "command": win_cmd, "timeoutSec": 10}]
    return {"version": 1, "hooks": hooks}


def build_copilot_run_hook_ps1(python_path: str) -> str:
    """Return the PowerShell bridge to the shared acknowledged session hook."""
    from observal_cli.shared.launcher import isolation_flag, pythonpath_env

    env = "".join(f'$env:{key} = "{value}"\n' for key, value in pythonpath_env().items())
    flag = isolation_flag()
    return f"""# Observal session push hook for VS Code Copilot.
$stdinData = [Console]::In.ReadToEnd()
{env}$python = "{python_path}"
$stdinData | & $python {flag} -m observal_cli.hooks.session_push --harness copilot --json-response 2>$null
"""
