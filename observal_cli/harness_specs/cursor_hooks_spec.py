# SPDX-FileCopyrightText: 2026 Hussain Alsaibai <hussain.alsaibai@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cursor hook specification for session telemetry.

Cursor hooks live at ``~/.cursor/hooks.json`` (user scope) or
``.cursor/hooks.json`` (project scope). Each entry is a flat list of command
objects -- Cursor does not use the matcher-group shape that Claude Code and
Kiro use.

Cursor fires a broad set of agent-hook events. Only the four below are
needed for session telemetry:

* ``beforeSubmitPrompt`` -- the user submitted a prompt
* ``preToolUse``        -- a tool call is about to run
* ``postToolUse``       -- a tool call finished
* ``stop``              -- the agent finished its turn

The shared push hook reads the transcript JSONL incrementally, so every event
is a trigger to flush whatever new transcript lines exist. ``stop`` is the
final one: the adapter's ``is_session_final`` treats it as the session end, so
the hook finalizes the session and crash recovery skips it.

``preToolUse`` is a *permission* event. Cursor parses the hook's stdout and
blocks the tool call when it is not valid permission JSON, so the hook runs
through ``observal_cli.hooks.cursor_session_push``, a thin bridge that performs
the shared delivery and then answers ``{"continue": true, "permission":
"allow"}``. The hook only observes; it never denies a tool call.
"""

from __future__ import annotations

from observal_cli.shared.launcher import module_command

# The four Cursor events that drive session delivery.
CURSOR_HOOK_EVENTS = (
    "beforeSubmitPrompt",
    "preToolUse",
    "postToolUse",
    "stop",
)

# Events that gate the agent action. Cursor blocks the action when these hooks
# print anything that is not valid permission JSON, so they are kept separate
# from the plain observability events for documentation and tests.
CURSOR_PERMISSION_EVENTS = ("preToolUse",)


def build_cursor_push_command(agent_id: str = "") -> str:
    """Return the session-push shell command, optionally agent-attributed."""
    env = {"OBSERVAL_AGENT_ID": agent_id} if agent_id else None
    return f"{module_command('observal_cli.hooks.cursor_session_push', env)}"


def build_cursor_hooks(agent_id: str = "") -> dict:
    """Return the hooks mapping that ``doctor patch`` installs for Cursor.

    Cursor's hook config is a flat ``{"hooks": {"event": [{"command":
    ..., "type": "command"}]}}`` mapping -- no matcher groups.
    """
    cmd = build_cursor_push_command(agent_id)
    hooks: dict[str, list[dict]] = {}
    for event in CURSOR_HOOK_EVENTS:
        hooks[event] = [{"command": cmd, "type": "command"}]
    return {"hooks": hooks}
