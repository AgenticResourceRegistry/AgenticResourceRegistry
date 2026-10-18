# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Generic hook install config generator for all supported harnesses.

This is SEPARATE from hook_config_generator.py which handles Observal's
own telemetry hooks (session_push). This module generates install config
for user-submitted registry hooks across all 8 harnesses.
"""

from __future__ import annotations

from loguru import logger as optic

from observal_shared.harness_registry import HARNESS_REGISTRY
from services.harness import ensure_loaded, get_adapter
from services.shared.utils import registry_item_slug
from services.shared.utils import sanitize_name as _sanitize_name


def generate_hook_install_config(
    hook_listing,
    harness: str,
    server_url: str = "http://localhost:8000",
    local_name: str | None = None,
) -> dict:
    """Generate a complete install response for a registry hook.

    Returns a dict compatible with HookInstallResponse:
      - config_snippet: harness-specific hook config
      - config_path: where the config lives
      - files: script files to write
      - requirements: install prerequisites
      - source_fetch: git fetch info for multi-file hooks
      - notes: human-readable notes
    """
    optic.debug("generating hook install config: hook={}, harness={}", hook_listing.name, harness)
    hook_name = _sanitize_name(local_name or registry_item_slug(hook_listing))
    ide_info = HARNESS_REGISTRY.get(harness)
    if not ide_info:
        return {
            "config_snippet": {},
            "config_path": "",
            "files": [],
            "requirements": [],
            "source_fetch": None,
            "notes": [f"harness '{harness}' is not recognized. Supported: {', '.join(HARNESS_REGISTRY.keys())}"],
        }

    ensure_loaded()
    adapter = get_adapter(harness)
    if adapter is None:
        raise ValueError(f"No adapter registered for harness: {harness!r}")

    hook_type = ide_info.get("hook_type")
    events_map = ide_info.get("hook_events_map", {})
    hooks = ide_info.get("hooks", {})
    hook_scripts_dir = ide_info.get("hook_scripts_dir", "")

    # Map canonical event to harness-specific event
    event = str(getattr(hook_listing, "event", "") or "")
    ide_event = events_map.get(event)

    if not ide_event:
        supported = [k for k, v in events_map.items() if v]
        return {
            "config_snippet": {},
            "config_path": "",
            "files": [],
            "requirements": [],
            "source_fetch": None,
            "notes": [
                f"Event '{event}' is not supported by {ide_info['display_name']}.",
                f"Supported events: {', '.join(supported)}",
            ],
        }

    # OpenCode uses plugins, not command hooks - manual setup only
    if hook_type == "plugin":
        return _generate_plugin_instructions(hook_listing, ide_info, ide_event, hook_name)

    # Build handler info
    handler_type = str(getattr(hook_listing, "handler_type", "command") or "command")
    handler_config = getattr(hook_listing, "handler_config", {}) or {}
    if handler_type == "http" and not adapter.supports_http_hooks:
        return {
            "config_snippet": {},
            "config_path": "",
            "files": [],
            "requirements": [],
            "source_fetch": None,
            "notes": [f"HTTP hooks are not supported by {ide_info['display_name']}; choose a compatible harness."],
        }
    command = handler_config.get("url", "") if handler_type == "http" else handler_config.get("command", "")
    timeout = handler_config.get("timeout")
    script_content = getattr(hook_listing, "script_content", None)
    script_filename = getattr(hook_listing, "script_filename", None)
    source_url = getattr(hook_listing, "source_url", None)
    source_path = getattr(hook_listing, "source_path", None)
    source_ref = getattr(hook_listing, "source_ref", None)
    resolved_sha = getattr(hook_listing, "resolved_sha", None)
    requirements = getattr(hook_listing, "requirements", None) or []

    # Determine script path and files to write
    files: list[dict] = []
    actual_command = command

    if handler_type == "command" and script_content and script_filename:
        # Tier 2: single-file script - write to harness's hooks dir
        script_path = f"{hook_scripts_dir}/{script_filename}"
        actual_command = script_path
        files.append(
            {
                "path": script_path,
                "content": script_content,
                "executable": True,
            }
        )

    # Build source_fetch for Tier 3 (multi-file git-sourced)
    source_fetch = None
    if handler_type == "command" and source_url and source_path and not script_content:
        source_fetch = {
            "url": source_url,
            "path": source_path,
            "ref": source_ref or "main",
            "sha": resolved_sha,
            "target_dir": f"{hook_scripts_dir}/{hook_name}",
        }
        actual_command = f"{hook_scripts_dir}/{hook_name}/{command}"

    config_snippet = adapter.format_hook_install_snippet(ide_event, handler_type, actual_command, timeout)

    # Determine config path
    config_path_val = ""
    if hooks:
        config_path_val = hooks.get("project", "") or hooks.get("user", "") or ""
        if "{name}" in config_path_val:
            config_path_val = config_path_val.replace("{name}", hook_name)

    return {
        "config_snippet": config_snippet,
        "config_path": config_path_val,
        "files": files,
        "requirements": requirements,
        "source_fetch": source_fetch,
        "notes": adapter.hook_install_notes(),
    }


def _generate_plugin_instructions(hook_listing, ide_info: dict, ide_event: str, hook_name: str) -> dict:
    """Generate manual setup instructions for plugin-based harnesses (OpenCode)."""
    optic.trace("hook_listing={}, ide_info={}", hook_listing, ide_info)
    handler_config = getattr(hook_listing, "handler_config", {}) or {}
    command = handler_config.get("command", "")

    return {
        "config_snippet": {
            "_manual_setup": True,
            "_instructions": [
                "OpenCode uses a plugin system for hooks.",
                f"Create a plugin file in .opencode/plugins/{hook_name}.ts",
                f"Register the '{ide_event}' event handler.",
                f"Command to execute: {command}",
            ],
            "event": ide_event,
            "command": command,
        },
        "config_path": f".opencode/plugins/{hook_name}.ts",
        "files": [],
        "requirements": getattr(hook_listing, "requirements", None) or [],
        "source_fetch": None,
        "notes": [
            "OpenCode requires a TypeScript plugin. See https://opencode.ai/docs/plugins/ for the plugin API.",
        ],
    }
