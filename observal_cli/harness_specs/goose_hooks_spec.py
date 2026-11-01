# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Goose hook specification for session telemetry.

Goose follows the Open Plugins hooks spec: hooks are declared by a plugin
directory that goose discovers from ``~/.agents/plugins/<name>/`` (user) or
``<project>/.agents/plugins/<name>/`` (project).  Each plugin carries a
``plugin.json`` manifest and a ``hooks/hooks.json`` file::

    {"hooks": {"<Event>": [{"hooks": [{"type": "command", "command": "...", "timeout": 30}]}]}}

Omitting ``matcher`` runs the rule for every event of that type; ``matcher`` is
a regular expression, so a bare ``"*"`` would be silently skipped by goose.

Events used for telemetry:
  SessionStart      establishes the session boundary
  UserPromptSubmit  flushes newly persisted rows at the start of a turn
  Stop              flushes the turn once goose finishes it
  SessionEnd        final flush and integrity audit

The hook command never writes to stdout, so goose's ``PreToolUse``/``Stop``
block signals are never triggered and telemetry can never deny a tool call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from observal_cli.shared.launcher import posix_module_command
from observal_cli.shared.utils import resolve_goose_agents_home

if TYPE_CHECKING:
    from pathlib import Path

PLUGIN_NAME = "observal"

GOOSE_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "SessionEnd",
)

# goose defaults to 30s; keep it explicit so a slow first spool cannot be killed.
HOOK_TIMEOUT_SECONDS = 30


def plugin_dir(home: Path | None = None) -> Path:
    """Return the user-scope Observal plugin directory goose discovers."""
    return resolve_goose_agents_home(home) / "plugins" / PLUGIN_NAME


def hooks_file(home: Path | None = None) -> Path:
    """Return the user-scope ``hooks/hooks.json`` path for the Observal plugin."""
    return plugin_dir(home) / "hooks" / "hooks.json"


def manifest_file(home: Path | None = None) -> Path:
    """Return the user-scope ``plugin.json`` path for the Observal plugin."""
    return plugin_dir(home) / "plugin.json"


def hook_command() -> str:
    """Return the shell command goose runs for every Observal hook event."""
    # goose runs every hook through ``sh -c``, Windows included, so the POSIX form always.
    return f"{posix_module_command('observal_cli.hooks.session_push')} --harness goose"


def build_plugin_manifest() -> dict:
    """Return the ``plugin.json`` manifest for the Observal goose plugin."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        plugin_version = version("observal-cli")
    except PackageNotFoundError:
        plugin_version = "0.0.0"
    return {
        "name": PLUGIN_NAME,
        "version": plugin_version,
        "description": "Observal session telemetry for goose",
    }


def build_hooks() -> dict:
    """Return the ``hooks/hooks.json`` content for the Observal goose plugin."""
    command = hook_command()
    return {
        "hooks": {
            event: [{"hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECONDS}]}]
            for event in GOOSE_HOOK_EVENTS
        }
    }
