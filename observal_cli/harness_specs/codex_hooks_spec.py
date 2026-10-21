# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Codex CLI hook specification for session telemetry.

Codex CLI uses the same hook format as Claude Code (PascalCase events,
hooks.json with matcher groups). Hooks live at ~/.codex/hooks.json.

Events: UserPromptSubmit, Stop (only 2 needed — session push reads JSONL incrementally).
"""

from __future__ import annotations

from observal_cli.shared.utils import hook_python_cmd

CODEX_HOOK_EVENTS = (
    "UserPromptSubmit",
    "Stop",
)


def build_codex_hooks() -> dict:
    """Return the complete hooks.json content for Codex CLI.

    Uses Claude Code hook format:
    {"hooks": {"EventName": [{"matcher": "", "hooks": [{"type": "command", "command": "..."}]}]}}
    """
    cmd = f"{hook_python_cmd()} -m observal_cli.hooks.session_push --harness codex"
    hooks: dict[str, list[dict]] = {}
    for event in CODEX_HOOK_EVENTS:
        hooks[event] = [{"matcher": "", "hooks": [{"type": "command", "command": cmd}]}]
    return {"hooks": hooks}
