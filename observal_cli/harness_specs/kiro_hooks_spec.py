# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Kiro harness hook specification for session JSONL push.

Two hook formats exist, and Observal has to speak both:

* **Standalone v1 files** (``.kiro/hooks/*.json``) — introduced in Kiro IDE 1.0
  and CLI 3.0, read by *every* Kiro surface. Triggers are PascalCase.
* **Inline agent hooks** (``hooks`` inside ``.kiro/agents/<name>.json``) — the
  CLI 2.x format. Kiro IDE 1.0 loads such an agent normally but never fires the
  hooks, so the agent is visible and silent.

Observal therefore writes the standalone file by default and only falls back to
inline hooks when the machine has a legacy CLI 2.x and no IDE to read them.

What *does* hide an agent from the IDE picker is unrelated to hooks: see
:func:`observal_cli.harness.kiro.strip_ide_hostile_fields`.

Only 2 events are needed either way: prompt submit and stop (the push reads the
session JSONL incrementally).
"""

from __future__ import annotations

import sys

from observal_cli.shared.utils import hook_python_cmd

# CLI 2.x inline trigger names.
KIRO_HOOK_EVENTS = ("userPromptSubmit", "stop")

# Standalone v1 trigger names (IDE 1.0 / CLI 3.0).
KIRO_V1_HOOK_TRIGGERS = ("UserPromptSubmit", "Stop")

# Name prefix that identifies an Observal-owned entry inside a v1 hooks file.
KIRO_V1_HOOK_NAME_PREFIX = "observal-session-push"

# Basename of the standalone hooks file Observal owns.
KIRO_V1_HOOK_FILENAME = "observal.json"


def build_kiro_push_command(agent_id: str = "") -> str:
    """Return the session-push shell command, optionally agent-attributed."""
    cmd = f"{hook_python_cmd()} -m observal_cli.hooks.session_push --harness kiro"
    if not agent_id:
        return cmd
    if sys.platform == "win32":
        return f'set "OBSERVAL_AGENT_ID={agent_id}" && {cmd}'
    return f"OBSERVAL_AGENT_ID={agent_id} {cmd}"


def build_kiro_hooks(*args, **kwargs) -> dict:
    """Build the legacy inline hooks dict for a Kiro CLI 2.x agent config.

    Only 2 events: userPromptSubmit and stop.
    Accepts optional agent_id for per-agent attribution.

    Kiro IDE 1.0 never fires inline hooks, so this must only be written when
    :func:`observal_cli.harness.kiro.use_inline_hooks` says the machine is
    legacy-CLI-only and has no other way to read them.
    """
    agent_id = kwargs.get("agent_id", "") or (args[2] if len(args) > 2 else "")
    cmd = build_kiro_push_command(agent_id)
    return {
        "userPromptSubmit": [{"command": cmd}],
        "stop": [{"command": cmd}],
    }


def build_kiro_hooks_file() -> dict:
    """Build the standalone v1 hooks file content read by IDE 1.0 and CLI 3.0.

    Deliberately carries no ``OBSERVAL_AGENT_ID``. There is exactly one of these
    files per scope and Kiro runs it for every session whatever agent is active,
    so a per-agent id here is meaningless: with two locked agents each would
    rewrite the other's copy and ``observal doctor patch`` would never converge.

    Nothing is lost. :meth:`KiroAdapter.resolve_session_agent_identity` reads the
    active agent from the session's companion metadata and never falls through
    to ``OBSERVAL_AGENT_ID``, so the variable was already dead for this harness.
    """
    cmd = build_kiro_push_command()
    return {
        "version": "v1",
        "hooks": [
            {
                "name": f"{KIRO_V1_HOOK_NAME_PREFIX}-{trigger.lower()}",
                "trigger": trigger,
                "action": {"type": "command", "command": cmd},
            }
            for trigger in KIRO_V1_HOOK_TRIGGERS
        ],
    }


def is_observal_v1_hook(entry: object) -> bool:
    """Return True when a v1 hooks-file entry is one Observal owns."""
    if not isinstance(entry, dict):
        return False
    if str(entry.get("name", "")).startswith(KIRO_V1_HOOK_NAME_PREFIX):
        return True
    action = entry.get("action")
    command = action.get("command", "") if isinstance(action, dict) else ""
    return "observal_cli.hooks" in str(command)


def merge_kiro_hooks_file(existing: dict | None) -> dict:
    """Return a v1 hooks file with Observal entries refreshed, user entries kept.

    Idempotent: the Observal entries do not depend on which agent triggered the
    refresh, so re-running over an already-correct file is a no-op.
    """
    desired = build_kiro_hooks_file()
    if not isinstance(existing, dict):
        return desired
    # "hooks" comes from server content on pull and from arbitrary JSON on disk
    # in doctor. A dict would iterate as keys and a scalar would raise, either
    # way corrupting or aborting the merge, so anything not a list is ignored.
    raw_hooks = existing.get("hooks")
    if not isinstance(raw_hooks, list):
        raw_hooks = []
    preserved = [h for h in raw_hooks if not is_observal_v1_hook(h)]
    merged = dict(existing)
    merged["version"] = existing.get("version") or "v1"
    merged["hooks"] = preserved + desired["hooks"]
    return merged
