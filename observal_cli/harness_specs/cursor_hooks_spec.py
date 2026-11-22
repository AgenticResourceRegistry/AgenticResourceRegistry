# SPDX-FileCopyrightText: 2026 Sanket Patel <sanket@the-algo.com>
# SPDX-License-Identifier: Apache-2.0

"""Cursor hook specification for session JSONL push.

Cursor reads a single ``~/.cursor/hooks.json``:

.. code-block:: json

    {
      "version": 1,
      "hooks": {
        "beforeSubmitPrompt": [{"command": "...", "type": "command"}],
        "stop": [{"command": "...", "type": "command"}]
      }
    }

Only 2 events are needed (``beforeSubmitPrompt`` and ``stop``) since the
session push hook reads the JSONL file incrementally.

Bump HOOKS_SPEC_VERSION whenever the hook definitions change.
"""

from __future__ import annotations

from observal_cli.shared.launcher import module_command

# Bump this when hook definitions change.
HOOKS_SPEC_VERSION = "1"

# Events Cursor fires that Observal pushes sessions on.
CURSOR_HOOK_EVENTS = ("beforeSubmitPrompt", "stop")

# hooks.json file format version Cursor expects.
CURSOR_HOOKS_FILE_VERSION = 1

# Substring identifying the current Observal command inside a hook entry.
_SESSION_PUSH_MARKER = "hooks.session_push --harness cursor"

# Substring identifying any Observal session-push command, current or legacy.
# Covers the retired ``observal_cli.hooks.cursor_session_push`` module (still
# listed in ``observal_cli.shared.utils``) along with every other
# ``*_session_push`` variant so old installs are cleaned up and replaced.
_SESSION_PUSH_MODULE_MARKER = "session_push"


def build_cursor_push_command() -> str:
    """Return the session-push shell command Cursor hooks run."""
    return f"{module_command('observal_cli.hooks.session_push')} --harness cursor"


def build_cursor_hooks() -> dict:
    """Return the desired ``~/.cursor/hooks.json`` content.

    Only 2 events: beforeSubmitPrompt and stop. Both invoke the session
    push hook which reads the JSONL file incrementally.
    """
    cmd = build_cursor_push_command()
    return {
        "version": CURSOR_HOOKS_FILE_VERSION,
        "hooks": {event: [{"command": cmd, "type": "command"}] for event in CURSOR_HOOK_EVENTS},
    }


def is_observal_hook_entry(entry: object) -> bool:
    """Return True when a hooks.json entry is an Observal session-push hook.

    Matches the current ``session_push --harness cursor`` command and the
    legacy ``cursor_session_push`` module so old installs are still cleaned
    up. Unrelated entries are rejected.
    """
    if not isinstance(entry, dict):
        return False
    return _SESSION_PUSH_MODULE_MARKER in str(entry.get("command", ""))


def is_current_push_hook_entry(entry: object) -> bool:
    """Return True when *entry* runs the current session-push command.

    Stricter than :func:`is_observal_hook_entry`: legacy ``*_session_push``
    variants count as Observal-owned but not current, so the patcher still
    rewrites them.
    """
    if not isinstance(entry, dict):
        return False
    return _SESSION_PUSH_MARKER in str(entry.get("command", ""))


def merge_cursor_hooks(existing: dict | None) -> dict:
    """Return hooks.json content with Observal entries refreshed, user entries kept.

    Non-Observal entries under every event are preserved; stale Observal
    entries (current or legacy) under the spec events are replaced with the
    desired ones. Idempotent: re-running over an already-correct file is a
    no-op.
    """
    desired = build_cursor_hooks()
    existing_hooks = existing.get("hooks") if isinstance(existing, dict) else None
    merged_hooks = dict(existing_hooks) if isinstance(existing_hooks, dict) else {}
    for event, desired_entries in desired["hooks"].items():
        current = merged_hooks.get(event, [])
        if not isinstance(current, list):
            current = []
        merged_hooks[event] = [h for h in current if not is_observal_hook_entry(h)] + desired_entries
    return {"version": desired["version"], "hooks": merged_hooks}
