# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Non-mutating exact Pi file plan for the normal agent pull startup mode."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from observal_cli.shared.utils import sanitize_name

MAX_BYTES = 2 * 1024 * 1024


class InstallSkipError(ValueError):
    """The release needs explicit manual installation; no managed file was written."""


def _safe_path(raw: str, root: Path, directory: Path, *, may_create: bool = False) -> Path:
    if not isinstance(raw, str) or not raw:
        raise InstallSkipError("The server returned an invalid Pi path.")
    if raw.startswith("~/"):
        candidate = Path.home() / raw[2:]
    elif raw.startswith("~"):
        raise InstallSkipError("The server returned an unsupported home path.")
    else:
        candidate = directory / raw
    if not candidate.is_absolute() or any(part.is_symlink() for part in (candidate, *candidate.parents)):
        raise InstallSkipError("A generated Pi path contains a symbolic link.")
    target = candidate.resolve()
    if not target.is_relative_to(root) or not (target.is_file() or (may_create and not target.exists())):
        raise InstallSkipError("The release changes the managed profile location or creates a file.")
    return target


def _credential_free(entry: object, agent_id: object) -> bool:
    """A plain local stdio server. The only env value is the agent's own public id."""
    env = entry.get("env", {}) if isinstance(entry, dict) else None
    return (
        isinstance(entry, dict)
        and set(entry) <= {"command", "args", "type", "env"}
        and isinstance(env, dict)
        and (not env or (isinstance(agent_id, str) and agent_id and env == {"OBSERVAL_AGENT_ID": agent_id}))
        and entry.get("type") in (None, "stdio")
        and isinstance(entry.get("command"), str)
        and bool(entry["command"])
        and isinstance(entry.get("args", []), list)
        and all(isinstance(arg, str) and "${" not in arg and "$" not in arg for arg in entry.get("args", []))
    )


def _same_credentials(key: str, old: object, new: object, agent_id: object) -> None:
    """A kept MCP may only carry forward the secrets the user already saved."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise InstallSkipError(f"The MCP '{key}' has an unsupported saved entry; update manually.")
    for field in ("env", "headers"):
        previous, current = old.get(field) or {}, new.get(field) or {}
        if not isinstance(previous, dict) or not isinstance(current, dict):
            raise InstallSkipError(f"The MCP '{key}' has an unsupported {field} block; update manually.")
        for name, value in current.items():
            if field == "env" and name == "OBSERVAL_AGENT_ID":
                if value != agent_id:
                    raise InstallSkipError(f"The MCP '{key}' names a different agent; update manually.")
            elif value != "" and previous.get(name) != value:
                raise InstallSkipError(
                    f"The release changes a saved value or adds a new one for MCP '{key}' ({field} '{name}'); "
                    "run `observal agent pull` manually to review it."
                )


def plan_pi_files(snippet: object, item: dict, old_files: dict[str, str]) -> dict[Path, bytes]:
    """Compute the complete *file* plan before any mutation.

    Do not call the manual pull writer: it merges files, invokes git/setup
    commands and can create paths before discovering a later conflict.
    """
    if not isinstance(snippet, dict) or set(snippet) - {"agent_profile", "skill_components", "mcp_config"}:
        raise InstallSkipError("The Pi release requires an unsupported config merge or setup action.")
    local_name = item.get("local_name")
    if not isinstance(local_name, str) or local_name in {"", ".", ".."} or Path(local_name).name != local_name:
        raise InstallSkipError("The installed Pi profile name is unknown.")
    root = (Path.home() / ".pi" / "agent" / "agents" / local_name).resolve()
    if not root.is_dir() or root.is_symlink():
        raise InstallSkipError("The managed Pi profile is unavailable.")
    directory = Path(item["directory"]).resolve()
    if not directory.is_dir():
        raise InstallSkipError("The original pull directory no longer exists.")
    profile = snippet.get("agent_profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("content"), str):
        raise InstallSkipError("The Pi release has no directly writable agent profile.")
    profile_path = _safe_path(profile.get("path"), root, directory)
    if profile_path != root / "AGENTS.md":
        raise InstallSkipError("The Pi release changes its agent profile path.")
    planned: dict[Path, bytes] = {profile_path: profile["content"].encode()}
    mcp = snippet.get("mcp_config")
    mcp_file = root / "mcp.json"
    if mcp is not None:
        if not isinstance(mcp, dict) or not isinstance(mcp.get("content"), dict):
            raise InstallSkipError("The Pi release has an unsupported MCP config.")
        mcp_path = _safe_path(mcp.get("path"), root, directory, may_create=True)
        wanted = mcp["content"].get("mcpServers")
        if (
            mcp_path != mcp_file
            or set(mcp["content"]) != {"mcpServers"}
            or not isinstance(wanted, dict)
            or not wanted
            or any(not isinstance(key, str) or not key for key in wanted)
        ):
            raise InstallSkipError("The Pi release changes its MCP config path or shape; update manually.")
        current: dict = {}
        if str(mcp_path) in old_files:
            try:
                previous_mcp = mcp_path.read_bytes()
                current_mcp = json.loads(previous_mcp)
            except (OSError, UnicodeError, ValueError) as error:
                raise InstallSkipError("The managed Pi MCP config cannot be checked.") from error
            if (
                hashlib.sha256(previous_mcp).hexdigest() != old_files[str(mcp_path)]
                or not isinstance(current_mcp, dict)
                or set(current_mcp) != {"mcpServers"}
                or not isinstance(current_mcp["mcpServers"], dict)
            ):
                raise InstallSkipError("The Pi MCP config was edited since Observal wrote it; update manually.")
            current = current_mcp["mcpServers"]
        for key in set(wanted) & set(current):
            _same_credentials(key, current[key], wanted[key], item.get("id"))
        for key in set(wanted) - set(current):
            if not _credential_free(wanted[key], item.get("id")):
                raise InstallSkipError(
                    f"The release adds MCP '{key}', which needs credentials or a remote URL; add it manually."
                )
        # The normal writer only merges and cannot drop an MCP. The exact
        # target is the release's own set of servers; apply rewrites the file
        # to these bytes if the merge left a dropped entry behind.
        raw = (json.dumps({"mcpServers": wanted}, indent=2) + "\n").encode("utf-8")
        unchanged = str(mcp_path) in old_files and current == wanted
        planned[mcp_path] = previous_mcp if unchanged else raw
    skills = snippet.get("skill_components", [])
    if not isinstance(skills, list):
        raise InstallSkipError("The release has invalid skill components.")
    for component in skills:
        if not isinstance(component, dict) or component.get("git_url"):
            raise InstallSkipError("Git skills require a manual pull.")
        content = component.get("skill_md_content")
        name = component.get("name")
        if not isinstance(content, str) or not content or not isinstance(name, str):
            raise InstallSkipError("The release requires an unsupported skill source.")
        target = _safe_path(component.get("path"), root, directory, may_create=True)
        if target != root / "skills" / sanitize_name(name) / "SKILL.md" or target in planned:
            raise InstallSkipError("The release changes or duplicates a managed skill path.")
        planned[target] = content.encode()
        script = component.get("script_content")
        filename = component.get("script_filename")
        if script is not None or filename is not None:
            if (
                not isinstance(script, str)
                or not script
                or not isinstance(filename, str)
                or filename in {"", ".", ".."}
                or Path(filename).name != filename
            ):
                raise InstallSkipError("The skill's registry script is incomplete or unsafe.")
            script_path = _safe_path(str(target.parent / "scripts" / filename), root, directory, may_create=True)
            if script_path in planned:
                raise InstallSkipError("The release duplicates a managed script path.")
            planned[script_path] = script.encode()
    skills_root = root / "skills"
    kept = {str(path) for path in planned}
    if any(
        name not in kept and not Path(name).is_relative_to(skills_root) and Path(name) != mcp_file for name in old_files
    ):
        raise InstallSkipError("The release would remove the agent profile; update manually.")
    if str(mcp_file) in old_files and mcp is None:
        # Every MCP was dropped. Only an unedited, Observal-written file goes.
        try:
            if hashlib.sha256(mcp_file.read_bytes()).hexdigest() != old_files[str(mcp_file)]:
                raise InstallSkipError("The Pi MCP config was edited since Observal wrote it; update manually.")
        except OSError as error:
            raise InstallSkipError("The managed Pi MCP config cannot be checked.") from error
    if sum((path.stat().st_size if path.exists() else 0) + len(data) for path, data in planned.items()) > MAX_BYTES:
        raise InstallSkipError("The automatic installation exceeds its file-size limit.")
    return planned
