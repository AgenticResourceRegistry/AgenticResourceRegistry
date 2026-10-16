# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Declarative hook specification for Claude Code settings.

Defines the desired state of Observal-managed hooks. The reconciler
compares this spec against the user's current ~/.claude/settings.json
and applies non-destructive updates.

Session JSONL delivery uses UserPromptSubmit + Stop. A separate SessionStart
hook starts a consent-gated detached worker; a fast UserPromptSubmit hook
delivers its result as a user-visible systemMessage without model context.
SessionEnd marks shutdown so no new installer can start after the host closes.

Bump HOOKS_SPEC_VERSION whenever the hook definitions change.
"""

from __future__ import annotations

import sys
from pathlib import Path

from observal_cli.shared.utils import OBSERVAL_METADATA_KEY

# Bump this when hook definitions change.
HOOKS_SPEC_VERSION = "13"


# Parent of the observal_cli package directory
_PKG_ROOT = str(Path(__file__).resolve().parent.parent.parent)


def _python_cmd() -> str:
    """Return python command with PYTHONPATH set if needed."""
    try:
        import importlib.util

        if importlib.util.find_spec("observal_cli") is not None:
            return sys.executable
    except Exception:
        pass
    if sys.platform == "win32":
        return f'set "PYTHONPATH={_PKG_ROOT}" && {sys.executable}'
    return f"PYTHONPATH={_PKG_ROOT} {sys.executable}"


def get_desired_hooks() -> dict[str, list[dict]]:
    """Return telemetry hooks plus a detached, consent-gated startup bridge."""
    meta = {OBSERVAL_METADATA_KEY: {"version": HOOKS_SPEC_VERSION}}
    cmd = f"{_python_cmd()} -m observal_cli.hooks.session_push --harness claude-code"

    hook_group: list[dict] = [{**meta, "hooks": [{"type": "command", "command": cmd}]}]
    notice_cmd = f"{_python_cmd()} -m observal_cli.hooks.claude_updates"

    return {
        "SessionStart": [{**meta, "hooks": [{"type": "command", "command": notice_cmd, "timeout": 5}]}],
        "UserPromptSubmit": [
            {
                **meta,
                "hooks": [
                    {"type": "command", "command": cmd},
                    {"type": "command", "command": notice_cmd, "timeout": 5},
                ],
            },
        ],
        "Stop": hook_group,
        "SessionEnd": [{**meta, "hooks": [{"type": "command", "command": notice_cmd, "timeout": 5}]}],
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
