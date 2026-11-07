# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Merge registry hook documents without installing telemetry hooks."""

from __future__ import annotations


def merge_hook_config(existing: dict, incoming: dict) -> dict:
    """Merge event mappings or named arrays, leaving the inputs untouched.

    Named entries belong to the registry hook file being installed. Refresh
    matching names in place, collapse duplicates of those names, and retain
    unrelated entries and top-level metadata.
    """
    hooks: dict | list
    incoming_hooks = incoming.get("hooks", {})
    existing_hooks = existing.get("hooks", [] if isinstance(incoming_hooks, list) else {})
    if isinstance(incoming_hooks, dict) and isinstance(existing_hooks, dict):
        hooks = dict(existing_hooks)
        for event, entries in incoming_hooks.items():
            current = hooks.get(event, [])
            if not isinstance(current, list) or not isinstance(entries, list):
                raise ValueError("The hook configuration event entries must be arrays.")
            current = list(current)
            for entry in entries:
                if entry not in current:
                    current.append(entry)
            hooks[event] = current
    elif isinstance(incoming_hooks, list) and isinstance(existing_hooks, list):
        replacements: dict[str, dict] = {}
        for entry in incoming_hooks:
            name = entry.get("name") if isinstance(entry, dict) else None
            if not isinstance(name, str) or not name.strip() or name in replacements:
                raise ValueError("The hook configuration entries must have unique non-empty names.")
            replacements[name] = entry
        hooks = []
        refreshed: set[str] = set()
        for entry in existing_hooks:
            name = entry.get("name") if isinstance(entry, dict) else None
            if isinstance(name, str) and name in replacements:
                if name not in refreshed:
                    hooks.append(replacements[name])
                    refreshed.add(name)
            else:
                hooks.append(entry)
        hooks.extend(entry for name, entry in replacements.items() if name not in refreshed)
    else:
        raise ValueError("The hook configuration has an invalid hooks object or array.")

    merged = dict(existing)
    merged["hooks"] = hooks
    if "version" in incoming:
        merged["version"] = incoming["version"]
    return merged
