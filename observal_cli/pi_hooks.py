# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Registry command hooks on Pi: the ``observal-hooks.json`` file the Observal extension runs.

Pi has no native command-hook settings. A user-scope pull writes the agent's
supported hooks to ``~/.pi/agent/agents/<agent>/observal-hooks.json``; ``/agent``
activates it as ``~/.pi/agent/observal-hooks.json``, which the extension reads
at session start and runs on ``tool_call`` / ``tool_result``
(docs/integrations/pi.md, "Registry hooks").

A pinned hook is bound by the exact (Pi event, command) the extension's run
receipts digest, the profile's agent, and a fingerprint of its entry. The
extension mirrors ``hook_status`` (``packages/pi-extension/extensions/observal.ts``,
``piHookStatus``) and adds one runtime check the CLI cannot make: that it loaded
exactly the bytes it verifies.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# Must match observal-server/services/harness/pi.py and the extension.
PI_HOOKS_SCHEMA = "observal-pi-hooks/v1"
PI_HOOKS_FILE = "observal-hooks.json"
# The layer display path of the file the extension loads.
ACTIVE_DISPLAY = f"user:{PI_HOOKS_FILE}"
PI_HOOK_EVENTS = frozenset({"tool_call", "tool_result"})
MAX_HOOKS_FILE_BYTES = 512 * 1024
MAX_HOOKS = 64
MAX_TIMEOUT = 600


def active_hooks_path() -> Path:
    return Path.home() / ".pi" / "agent" / PI_HOOKS_FILE


def parse_hooks(data: bytes) -> tuple[str, list[dict]] | None:
    """``(agent, entries)`` of a hooks file, or None when the extension would run none of it.

    Mirrors the extension's ``parsePiHooks``: one invalid entry invalidates the file,
    so a hook is never verified from a file the runner rejects.
    """
    if len(data) > MAX_HOOKS_FILE_BYTES:
        return None
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict) or parsed.get("schema") != PI_HOOKS_SCHEMA:
        return None
    agent, hooks = parsed.get("agent"), parsed.get("hooks")
    if not isinstance(agent, str) or not agent or not isinstance(hooks, list) or len(hooks) > MAX_HOOKS:
        return None
    entries: list[dict] = []
    for hook in hooks:
        if not isinstance(hook, dict):
            return None
        name, event, command, timeout = hook.get("name"), hook.get("event"), hook.get("command"), hook.get("timeout")
        if (
            not isinstance(name, str)
            or not name
            or event not in PI_HOOK_EVENTS
            or hook.get("type") != "command"
            or not isinstance(command, str)
            or not command.strip()
            or type(timeout) is not int
            or not 1 <= timeout <= MAX_TIMEOUT
        ):
            return None
        entries.append({"name": name, "event": event, "type": "command", "command": command, "timeout": timeout})
    return agent, entries


def entry_integrity(agent: str, entry: dict) -> str:
    """Fingerprint of one hook entry and the profile it belongs to (extension: ``piHookIntegrity``)."""
    fields = [PI_HOOKS_SCHEMA, agent, entry["name"], entry["event"], entry["type"], entry["command"], entry["timeout"]]
    data = json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"sha256-{hashlib.sha256(data).hexdigest()}"


def bind_pulled_hooks(written: Path, lock_components: list[dict]) -> list[str]:
    """Record each pinned hook's binding from the hooks file this pull wrote.

    Read back from disk, so the binding is exactly what the extension will load.
    A hook the file does not carry exactly once stays unbound (unverified).
    """
    try:
        parsed = parse_hooks(written.read_bytes())
    except OSError:
        parsed = None
    hooks = [c for c in lock_components if c.get("type") == "hook" and c.get("local_name")]
    if parsed is None:
        return ["Installed Pi hooks file could not be read back; its hooks will be unverified."] if hooks else []
    agent, entries = parsed
    warnings: list[str] = []
    for component in hooks:
        for key in [key for key in component if key.startswith("hook_")]:
            del component[key]  # a stale binding from an earlier pull must not survive
        matches = [entry for entry in entries if entry["name"] == component["local_name"]]
        if len(matches) != 1:
            continue  # not installed on Pi (the server warned) or ambiguous
        entry = matches[0]
        component.update(
            hook_event=entry["event"],
            hook_command=entry["command"],
            hook_agent="",
            hook_config=ACTIVE_DISPLAY,
            hook_profile=agent,
            hook_integrity=entry_integrity(agent, entry),
        )
    return warnings


def hook_status(component: dict, data: bytes | None) -> str:
    """``verified``, ``drifted`` or ``unverified`` for a pinned hook against the active hooks file's bytes."""
    event, command = component.get("hook_event"), component.get("hook_command")
    profile, integrity = component.get("hook_profile"), component.get("hook_integrity")
    if (
        sys.platform == "win32"  # the extension runs no hooks on Windows
        or component.get("hook_config") != ACTIVE_DISPLAY
        or not all(isinstance(value, str) and value for value in (event, command, profile, integrity))
        or data is None
    ):
        return "unverified"
    parsed = parse_hooks(data)
    if parsed is None:
        return "unverified"
    agent, entries = parsed
    if agent != profile:
        return "unverified"  # another profile is active
    matches = [entry for entry in entries if entry["event"] == event and entry["command"] == command]
    if len(matches) != 1:
        return "unverified"  # inactive, or a duplicate whose receipts name neither copy
    return "verified" if entry_integrity(agent, matches[0]) == integrity else "drifted"


def read_active_hooks() -> bytes | None:
    path = active_hooks_path()
    try:
        if not path.is_file() or path.stat().st_size > MAX_HOOKS_FILE_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None
