# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Which harnesses the guarded startup updater supports, and how.

Every gate that used to spell out ``{"pi", "claude-code"}`` reads this table, so
adding a harness means proving one row here plus its exact file planner.

Notice channels differ by what each host can show the *user* without putting
registry text into model context:

* ``hook``: the host's own hook output reaches the user (Claude Code
  ``systemMessage`` on prompt submit; Codex ``systemMessage`` at session start).
* ``extension``: Pi's extension renders notices itself.
* ``cli``: the host has no user-only channel (Kiro stdout and Cursor
  ``additional_context`` both enter the model). Outcomes are shown on the
  user's next interactive ``observal`` command instead.
"""

from __future__ import annotations

# Harnesses whose user-scope agent profiles can be updated by the guarded pull.
AGENT_HARNESSES = frozenset({"pi", "claude-code", "kiro", "cursor", "codex"})

# Harnesses whose standalone registry-direct skills can be updated. Kiro and
# Cursor have no standalone skill install (the registry gives them no skills
# capability); their agents' bundled skills are part of the agent plan.
SKILL_HARNESSES = frozenset({"pi", "claude-code", "codex"})

# Managed standalone MCP installs. Codex, Kiro and Cursor keep MCPs in shared
# user configs; per-entry ownership with a before/after proof is deferred.
MCP_HARNESSES = frozenset({"pi", "claude-code"})

# Agent pulls planned by observal_cli.automatic_profile_plan. Their shared
# configs (mcp_config / hooks_config) are never owned by a single agent.
PROFILE_PLAN_HARNESSES = frozenset({"kiro", "cursor", "codex"})

# Harnesses whose session hooks launch the shared startup worker.
HOOK_BRIDGE_HARNESSES = frozenset({"claude-code", "kiro", "cursor", "codex"})

NOTICE_CHANNEL = {
    "pi": "extension",
    "claude-code": "hook",
    "codex": "hook",
    "kiro": "cli",
    "cursor": "cli",
}

DISPLAY_NAME = {
    "pi": "Pi",
    "claude-code": "Claude Code",
    "kiro": "Kiro",
    "cursor": "Cursor",
    "codex": "Codex",
}

# How a saved update reaches a session; none of these hosts reloads a profile mid-session.
_ACTIVATE = {
    "kiro": "Start a new Kiro session and select the agent to load it.",
    "cursor": "Start a new Cursor chat and select the agent to load it.",
    "codex": "Start a new Codex session to load it.",
}


def updated_reason(harness: str, item_type: str) -> str:
    """User-facing outcome for a verified update on one of the profile-plan harnesses."""
    what = {"agent": "profile", "skill": "skill"}.get(item_type, item_type)
    return f"Saved {DISPLAY_NAME[harness]} {what} updated and verified. {_ACTIVATE[harness]}"
