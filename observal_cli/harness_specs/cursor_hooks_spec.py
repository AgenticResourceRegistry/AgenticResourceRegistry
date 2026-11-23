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

import re

from observal_cli.shared.launcher import module_command

# Bump this when hook definitions change.
HOOKS_SPEC_VERSION = "1"

# Events Cursor fires that Observal pushes sessions on.
CURSOR_HOOK_EVENTS = ("beforeSubmitPrompt", "stop")

# hooks.json file format version Cursor expects.
CURSOR_HOOKS_FILE_VERSION = 1

# Qualified name of the current session-push module. Matches both the ``-m``
# launcher form and the Windows ``-c`` bootstrap, which embeds the same dotted
# name inside ``runpy.run_module('observal_cli.hooks.session_push', ...)``.
# The lookbehind keeps ``pkg.observal_cli...`` from matching.
_CURRENT_PUSH_MODULE_RE = re.compile(r"(?<![\w.])observal_cli\.hooks\.session_push\b")

# Qualified name of any Observal session-push module, current or legacy.
# Covers the retired ``observal_cli.hooks.cursor_session_push`` module (still
# listed in ``observal_cli.shared.utils``) along with every other
# ``*_session_push`` variant so old installs are cleaned up and replaced.
# Requiring the ``observal_cli.hooks.`` prefix keeps unrelated commands that
# merely contain ``session_push`` (``python /home/me/tools/session_push.py``)
# from being treated as Observal-owned and deleted.
_SESSION_PUSH_MODULE_RE = re.compile(r"(?<![\w.])observal_cli\.hooks\.\w*session_push\b")


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

    Ownership is decided by the qualified ``observal_cli.hooks.`` module name,
    covering the current ``session_push`` module and every legacy
    ``*_session_push`` variant in either ``-m`` or ``-c`` bootstrap launcher
    form. Commands that merely contain ``session_push`` without the package
    prefix are user hooks and stay untouched.
    """
    if not isinstance(entry, dict):
        return False
    return _SESSION_PUSH_MODULE_RE.search(str(entry.get("command", ""))) is not None


def is_current_push_hook_entry(entry: object) -> bool:
    """Return True when *entry* runs the current session-push command.

    Stricter than :func:`is_observal_hook_entry`: requires the qualified
    ``observal_cli.hooks.session_push`` module together with ``--harness
    cursor``, so legacy ``*_session_push`` variants count as Observal-owned
    but not current, and the patcher still rewrites them. Module and flag are
    matched separately because the ``-c`` bootstrap form does not place them
    adjacent.
    """
    if not isinstance(entry, dict):
        return False
    command = str(entry.get("command", ""))
    return _CURRENT_PUSH_MODULE_RE.search(command) is not None and "--harness cursor" in command


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
