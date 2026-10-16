# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Exact single-profile plan for the normal Claude Code agent pull writer."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from observal_cli import install_baseline, lockfile
from observal_cli.shared.utils import sanitize_name

MAX_BYTES = 2 * 1024 * 1024
DELEGATION_ARGS = ["-m", "observal_cli.delegation.mcp_server", "--harness", "claude-code"]


class ClaudePlanError(ValueError):
    """This release or local install needs explicit manual review."""


def profile(item: dict, old_files: dict[str, str]) -> Path:
    """Only an existing, solely owned user profile can be replaced."""
    name = item.get("local_name")
    if not isinstance(name, str) or not name or name != sanitize_name(name) or Path(name).name != name:
        raise ClaudePlanError("The saved Claude Code profile name is ambiguous; update manually.")
    claude_home = Path.home() / ".claude"
    configured_home = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured_home and Path(configured_home).expanduser() != claude_home:
        raise ClaudePlanError("Claude Code uses a different config directory; update manually.")
    file = claude_home / "agents" / f"{name}.md"
    if str(file) not in old_files or any(part.is_symlink() for part in (file, *file.parents)) or not file.is_file():
        raise ClaudePlanError("The agent does not own an existing Claude Code profile; update manually.")
    extras = [Path(name) for name in old_files if name != str(file)]
    if any(
        not any(path.is_relative_to(claude_home / part) for part in ("skills", "hooks"))
        or any(link.is_symlink() for link in (path, *path.parents))
        for path in extras
    ):
        raise ClaudePlanError("The agent owns files outside its profile, skills and hook scripts; update manually.")
    root = item.get("directory")
    if not isinstance(root, str) or not Path(root).is_absolute() or not Path(root).is_dir():
        raise ClaudePlanError("The original pull directory is unavailable; update manually.")
    # Another registry may have claimed this destination in its installed
    # record even if it has no baseline. Don't infer sole ownership from ours.
    registry = lockfile.current_registry_url()
    try:
        for url, section in lockfile.read_lockfile().get("registries", {}).items():
            for entry in section.get("harnesses", {}).get("claude-code", {}).get("agents", []):
                if entry.get("scope") != "user":
                    continue
                if (
                    lockfile.normalize_server_url(url) == registry
                    and entry.get("id") == item.get("id")
                    and entry.get("directory") == item.get("directory")
                    and entry.get("local_name") == name
                ):
                    continue
                local = entry.get("local_name")
                display = entry.get("name")
                if not isinstance(local, str) or not local:
                    # A legacy record without a local path cannot prove that
                    # it does not own this destination.
                    raise ClaudePlanError("Another agent has unknown Claude Code profile ownership; update manually.")
                if sanitize_name(local) == name or (isinstance(display, str) and sanitize_name(display) == name):
                    raise ClaudePlanError("Another installed agent claims this Claude Code profile; update manually.")
    except (OSError, RuntimeError, TypeError, KeyError, ValueError) as error:
        raise ClaudePlanError("Profile ownership cannot be checked; update manually.") from error
    return file


def _existing_delegation(snippet: dict, root: str) -> None:
    """The normal pull's only setup command must already be an exact no-op.

    Claude Code registers this MCP in its *local project* config, which is
    shared with other agents and is not in this profile's ownership baseline.
    Never execute setup or edit that config during an automatic profile update.
    """
    expected = {"command": sys.executable, "args": DELEGATION_ARGS, "env": {}}
    command = ["claude", "mcp", "add", "observal-agents", "--", sys.executable, *DELEGATION_ARGS]
    if snippet["mcp_config"] != {"observal-agents": expected} or snippet["mcp_setup_commands"] != [command]:
        raise ClaudePlanError("The release changes MCP registration; update manually.")
    custom = os.environ.get("CLAUDE_CONFIG_DIR")
    config_root = Path(custom).expanduser() if custom else Path.home()
    if not config_root.is_absolute():
        raise ClaudePlanError("The Claude Code config location is ambiguous; update manually.")
    settings = config_root / ".claude.json"
    if any(part.is_symlink() for part in (settings, *settings.parents)):
        raise ClaudePlanError("The Claude Code config crosses a symbolic link; update manually.")
    try:
        info = settings.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES or stat.S_IMODE(info.st_mode) & ~0o777:
            raise ClaudePlanError("The existing Claude Code MCP config is unsafe; update manually.")
        data = json.loads(settings.read_text(encoding="utf-8"))
        registered = data["projects"][root]["mcpServers"]["observal-agents"]
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
        raise ClaudePlanError(
            "The existing Claude Code MCP registration cannot be verified; update manually."
        ) from error
    if registered != {"type": "stdio", **expected}:
        raise ClaudePlanError("The existing Claude Code MCP registration differs; update manually.")


def plan(snippet: object, item: dict, old_files: dict[str, str]) -> tuple[dict[Path, bytes], dict[Path, int]]:
    """Reproduce the normal writer's outputs and modes, without invoking it.

    Only the profile plus already-owned registry-direct skill files and hook
    scripts are accepted; MCP changes, git skills and new paths stay manual.
    """
    from observal_cli import automatic_skill_plan
    from observal_cli.shared.utils import sanitize_name as clean

    file = profile(item, old_files)
    allowed = {"agent_profile", "mcp_config", "mcp_setup_commands", "scope", "skill_components", "hook_files"}
    if (
        not isinstance(snippet, dict)
        or not {"agent_profile", "scope"} <= set(snippet) <= allowed
        or snippet["scope"] != "user"
    ):
        raise ClaudePlanError("The release merges config or has unsupported files or setup steps; update manually.")
    if snippet.get("mcp_config") or snippet.get("mcp_setup_commands"):
        _existing_delegation(snippet, item["directory"])
    agent = snippet["agent_profile"]
    if (
        not isinstance(agent, dict)
        or set(agent) != {"path", "content"}
        or agent["path"] != f"~/.claude/agents/{item['local_name']}.md"
        or not isinstance(agent["content"], str)
    ):
        raise ClaudePlanError("The release changes the profile path or content type; update manually.")
    from observal_cli.cmd_pull import _pin_agent_profile_hooks, _resolve_hook_paths
    from observal_cli.install_recovery import atomic_text_mode, created_mode

    planned: dict[Path, bytes] = {file: _pin_agent_profile_hooks(_resolve_hook_paths(agent["content"])).encode("utf-8")}
    modes: dict[Path, int] = {file: atomic_text_mode(file.parent)}
    claude_home = Path.home() / ".claude"
    for component in snippet.get("skill_components") or []:
        name = component.get("name") if isinstance(component, dict) else None
        content = component.get("skill_md_content") if isinstance(component, dict) else None
        if (
            not isinstance(name, str)
            or not isinstance(content, str)
            or not content
            or component.get("git_url")
            or component.get("path")
        ):
            raise ClaudePlanError("A bundled skill needs a manual update (git source, custom path or no content).")
        try:
            automatic_skill_plan.unshared(
                lockfile.current_registry_url(), str(component.get("id", "")), name, "claude-code"
            )
        except automatic_skill_plan.SkillPlanError as error:
            raise ClaudePlanError(str(error)) from error
        target = claude_home / "skills" / clean(name) / "SKILL.md"
        planned[target] = content.encode("utf-8")
        modes[target] = target.stat().st_mode & 0o777 if target.is_file() else created_mode(claude_home)
        script, filename = component.get("script_content"), component.get("script_filename")
        if script is not None or filename is not None:
            if (
                not isinstance(script, str)
                or not script
                or not isinstance(filename, str)
                or Path(filename).name != filename
                or filename in {"", ".", ".."}
            ):
                raise ClaudePlanError("A bundled skill script is incomplete or unsafe.")
            path = target.parent / "scripts" / filename
            planned[path] = script.encode("utf-8")
            modes[path] = (
                0o755
                if path.suffix in {".sh", ".bash", ".py", ".rb"}
                else (path.stat().st_mode & 0o777 if path.is_file() else -1)
            )
    for hook in snippet.get("hook_files") or []:
        if (
            not isinstance(hook, dict)
            or not isinstance(hook.get("path"), str)
            or not isinstance(hook.get("content"), str)
        ):
            raise ClaudePlanError("A bundled hook script is malformed.")
        raw = hook["path"]
        path = (claude_home / raw[len("~/.claude/") :]) if raw.startswith("~/.claude/hooks/") else None
        if path is None or ".." in Path(raw).parts or path in planned:
            raise ClaudePlanError("A bundled hook script path needs a manual update.")
        planned[path] = hook["content"].encode("utf-8")
        modes[path] = 0o755 if hook.get("executable") else atomic_text_mode(next(d for d in path.parents if d.is_dir()))
    added = [path for path in planned if str(path) not in old_files]
    try:
        install_baseline.reject_foreign_creation(
            added,
            install_baseline._path(
                lockfile.current_registry_url(), "claude-code", item["id"], "user", item["directory"]
            ),
        )
    except install_baseline.BaselineError as error:
        raise ClaudePlanError(
            f"The release adds a file that already exists or belongs to another install: {error}"
        ) from error
    removed = [name for name in old_files if name not in {str(p) for p in planned} and name != str(file)]
    if str(file) not in {str(p) for p in planned} or any(
        not Path(name).is_relative_to(claude_home / "skills") and not Path(name).is_relative_to(claude_home / "hooks")
        for name in removed
    ):
        raise ClaudePlanError("The release would remove files outside its skills and hook scripts.")
    if any(part.is_symlink() for path in planned for part in (path, *path.parents)):
        raise ClaudePlanError("A planned file crosses a link; update manually.")
    if sum((path.stat().st_size if path.exists() else 0) + len(raw) for path, raw in planned.items()) > MAX_BYTES:
        raise ClaudePlanError("The profile exceeds the backup size limit; update manually.")
    return planned, modes
