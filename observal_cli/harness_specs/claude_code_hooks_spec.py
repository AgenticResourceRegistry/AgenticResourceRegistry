# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Declarative hook specification for Claude Code settings.

Defines the desired state of Observal-managed hooks. The reconciler
compares this spec against the user's current ~/.claude/settings.json
and applies non-destructive updates.

Session JSONL strategy: only 2 events are needed (UserPromptSubmit + Stop)
since we read the JSONL file incrementally rather than parsing individual
hook events.

Bump HOOKS_SPEC_VERSION whenever the hook definitions change.
"""

from __future__ import annotations

from observal_cli.shared.launcher import module_command
from observal_cli.shared.utils import OBSERVAL_METADATA_KEY

# Bump this when hook definitions change.
HOOKS_SPEC_VERSION = "13"


def get_desired_hooks() -> dict[str, list[dict]]:
    """Return the desired hooks spec for Claude Code settings.

    Only 2 events: UserPromptSubmit and Stop.  Both invoke the session
    push hook which reads the JSONL file incrementally.
    """
    meta = {OBSERVAL_METADATA_KEY: {"version": HOOKS_SPEC_VERSION}}
    cmd = f"{module_command('observal_cli.hooks.session_push')} --harness claude-code"

    hook_group: list[dict] = [{**meta, "hooks": [{"type": "command", "command": cmd}]}]

    return {
        "UserPromptSubmit": hook_group,
        "Stop": hook_group,
    }


def get_desired_env(*_args, **_kwargs) -> dict[str, str]:
    """Legacy stub - no env vars needed for session JSONL push.

    Old callers pass (server_url, hooks_token, ...) - ignored.
    Config now lives in ~/.observal/config.json.
    """
    return {}


# Keys in settings.env that Observal manages (for cleanup).
MANAGED_ENV_KEYS = frozenset(
    {
        "OBSERVAL_HOOKS_URL",
        "OBSERVAL_HOOKS_SPEC_VERSION",
        "OBSERVAL_USER_ID",
        "OBSERVAL_USERNAME",
        "OBSERVAL_AGENT_NAME",
    }
)
