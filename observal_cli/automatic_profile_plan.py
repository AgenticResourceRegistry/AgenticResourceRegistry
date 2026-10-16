# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Exact file plan for guarded Kiro, Cursor and Codex agent pulls.

The normal ``agent pull`` writer stays the only writer. This module predicts
its output by calling the writer's own transforms (``_render_file``, the CLI
adapter's profile and hook rewrites, ``_resolve_path``), so a plan cannot drift
from what the writer would do.

Each pull touches two kinds of files:

* **Owned**: the agent profile, its bundled registry-direct skills and hook
  scripts. Planned byte-for-byte with modes, backed up, and verified after.
* **Shared**: merged harness configs (Codex ``config.toml`` and ``hooks.json``,
  Cursor ``mcp.json`` and ``hooks.json``, Kiro ``hooks/observal.json``). No
  single agent owns them and the host edits them itself (Codex records hook
  trust in ``config.toml``). An automatic pull may proceed only when rendering
  them would leave their parsed content unchanged; the writer then skips them.
  Any real change stays a manual pull.
"""

from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path

from observal_cli import install_baseline, lockfile
from observal_cli.shared.utils import sanitize_name
from observal_cli.startup_harnesses import PROFILE_PLAN_HARNESSES

MAX_BYTES = 2 * 1024 * 1024
SHARED_KEYS = ("mcp_config", "hooks_config")
_EXECUTABLE_SUFFIXES = {".sh", ".bash", ".py", ".rb"}

# User-scope shared configs, relative to the home directory, and the
# project-scope equivalents relative to the pull directory.
_SHARED = {
    "kiro": {"user": ("~/.kiro/hooks/observal.json",), "project": (".kiro/hooks/observal.json",)},
    "cursor": {
        "user": ("~/.cursor/mcp.json", "~/.cursor/hooks.json"),
        "project": (".cursor/mcp.json", ".cursor/hooks.json"),
    },
    "codex": {
        "user": ("~/.codex/config.toml", "~/.codex/hooks.json"),
        "project": (".codex/config.toml", ".codex/hooks.json"),
    },
}
# Directories an agent's bundled hook scripts may live in (user scope).
_HOOK_DIRS = {"kiro": ".kiro/hooks", "cursor": ".cursor/hooks", "codex": ".codex/hooks"}
_ALLOWED_KEYS = {
    "kiro": {"agent_profile", "hooks_config", "skill_components", "hook_files", "scope"},
    "cursor": {"agent_profile", "mcp_config", "hooks_config", "skill_components", "hook_files", "scope"},
    "codex": {"agent_profile", "mcp_config", "hooks_config", "skill_components", "scope"},
}


class ProfilePlanError(ValueError):
    """This release or local install needs explicit manual review."""


def shared_config_paths(harness: str, scope: str, root: str | Path) -> frozenset[str]:
    """Merged configs no single agent owns, as absolute paths."""
    if harness not in _SHARED:
        return frozenset()
    base = Path(root)
    return frozenset(
        str(Path(raw).expanduser().resolve() if raw.startswith("~/") else (base / raw).resolve())
        for raw in _SHARED[harness]["project" if scope == "project" else "user"]
    )


def _linked(path: Path) -> bool:
    return any(part.is_symlink() for part in (path, *path.parents))


def _parsed(path: Path, text: str) -> object:
    if path.suffix == ".toml":
        return tomllib.loads(text)
    if path.suffix == ".json":
        return json.loads(text)
    return text


def _resolve(raw: object, root: Path, *, allow_home: bool) -> Path:
    from observal_cli.cmd_pull import _resolve_path
    from observal_cli.errors import CliError

    if not isinstance(raw, str) or not raw:
        raise ProfilePlanError("The release has a file without a path; update manually.")
    try:
        return _resolve_path(raw, root, allow_home=allow_home)
    except CliError as error:
        raise ProfilePlanError("The release writes outside its install location; update manually.") from error


def _claimed_elsewhere(harness: str, item: dict, name: str) -> None:
    """Another installed agent may claim this profile even without its own baseline."""
    registry = lockfile.current_registry_url()
    try:
        for url, section in lockfile.read_lockfile().get("registries", {}).items():
            for entry in section.get("harnesses", {}).get(harness, {}).get("agents", []):
                if entry.get("scope") != "user":
                    continue
                if (
                    lockfile.normalize_server_url(url) == registry
                    and entry.get("id") == item.get("id")
                    and entry.get("directory") == item.get("directory")
                ):
                    continue
                # Cursor writes "user" profiles under the pull directory, so
                # only an agent pulled into the same directory can collide.
                if harness == "cursor" and entry.get("directory") != item.get("directory"):
                    continue
                local = entry.get("local_name")
                display = entry.get("name")
                if not isinstance(local, str) or not local:
                    raise ProfilePlanError("Another agent has unknown profile ownership; update manually.")
                if sanitize_name(local) == name or (isinstance(display, str) and sanitize_name(display) == name):
                    raise ProfilePlanError("Another installed agent claims this profile; update manually.")
    except (OSError, RuntimeError, TypeError, KeyError, ValueError) as error:
        if isinstance(error, ProfilePlanError):
            raise
        raise ProfilePlanError("Profile ownership cannot be checked; update manually.") from error


def _skill_destination_unshared(harness: str, registry: str, component_id: str, target: Path) -> None:
    """Codex's ``~/.agents/skills`` is also other harnesses' default; check them all."""
    from observal_cli.cmd_skill import _user_skill_dest

    try:
        for url, section in lockfile.read_lockfile().get("registries", {}).items():
            for other, data in section.get("harnesses", {}).items():
                for entry in data.get("standalone", []):
                    if entry.get("type") != "skill" or entry.get("scope") != "user":
                        continue
                    names = [entry.get("local_name"), entry.get("name")]
                    claims = {
                        _user_skill_dest(other, sanitize_name(value))
                        for value in names
                        if isinstance(value, str) and value
                    }
                    if target in claims and not (
                        other == harness
                        and lockfile.normalize_server_url(url) == lockfile.normalize_server_url(registry)
                        and entry.get("id") == component_id
                    ):
                        raise ProfilePlanError("Another installed skill uses this skill folder; update manually.")
    except (OSError, RuntimeError, TypeError, KeyError) as error:
        raise ProfilePlanError("Skill ownership cannot be checked; update manually.") from error


def owned_profile(harness: str, item: dict, files: dict[str, str]) -> Path:
    """Preflight: the saved profile this agent must already solely own."""
    from observal_cli.harness import ensure_loaded, get_adapter
    from observal_shared.harness_registry import HARNESS_REGISTRY

    name = item.get("local_name")
    root = item.get("directory")
    if not isinstance(name, str) or not name or name != sanitize_name(name) or Path(name).name != name:
        raise ProfilePlanError("The saved profile name is ambiguous; update manually.")
    if not isinstance(root, str) or not Path(root).is_absolute() or not Path(root).is_dir():
        raise ProfilePlanError("The original pull directory is unavailable; update manually.")
    ensure_loaded()
    raw = HARNESS_REGISTRY[harness]["agent_profile"]["user"].format(name=name)
    path = _resolve(raw, Path(root), allow_home=get_adapter(harness).allow_home_agent_profile(True))
    if str(path) not in files or _linked(path) or not path.is_file():
        raise ProfilePlanError("The agent does not own an existing profile; update manually.")
    _claimed_elsewhere(harness, item, name)
    return path


def _check_shared_unchanged(
    harness: str, key: str, config: object, root: Path, agent_id: str, shared: frozenset[str]
) -> None:
    """The writer's own merge must reproduce the current parsed content exactly."""
    from observal_cli.cmd_pull import _pin_hook_interpreter, _render_file, _resolve_hook_paths
    from observal_cli.harness import ensure_loaded, get_adapter

    if not isinstance(config, dict) or set(config) - {"path", "content", "merge"}:
        raise ProfilePlanError("The release has an unsupported shared config; update manually.")
    path = _resolve(config.get("path"), root, allow_home=True)
    if str(path) not in shared:
        raise ProfilePlanError("The release writes a config outside the known shared files; update manually.")
    content = copy.deepcopy(config.get("content"))
    if key == "hooks_config":
        ensure_loaded()
        if isinstance(content, str):
            content = _pin_hook_interpreter(_resolve_hook_paths(content))
        elif isinstance(content, dict):
            content = json.loads(_pin_hook_interpreter(_resolve_hook_paths(json.dumps(content))))
            content = get_adapter(harness).rewrite_hooks(content, agent_id=agent_id)
        merge = bool(config.get("merge", False))
    else:
        merge = True
    if _linked(path) or not path.is_file():
        raise ProfilePlanError(f"The release would create or replace {path.name}; run `observal agent pull` manually.")
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ProfilePlanError(f"{path.name} is too large to check; update manually.")
        current = path.read_text(encoding="utf-8")
        rendered, _merged = _render_file(path, content, merge_mcp=merge)
        unchanged = _parsed(path, rendered) == _parsed(path, current)
    except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError) as error:
        raise ProfilePlanError(f"The shared {path.name} cannot be checked; update manually.") from error
    if not unchanged:
        raise ProfilePlanError(
            f"The release changes the shared {path.name}, which other agents and the host also use; "
            "run `observal agent pull` manually."
        )


def plan(
    snippet: object, item: dict, old_files: dict[str, str], *, harness: str, agent_id: str
) -> tuple[dict[Path, bytes], dict[Path, int], list[str]]:
    """Return planned owned bytes, their modes, and the shared keys the writer must skip.

    ``old_files`` are the verified owned files from the agent's baseline,
    without shared configs.
    """
    from observal_cli.cmd_pull import _pin_agent_profile_hooks, _render_file, _resolve_hook_paths
    from observal_cli.cmd_skill import _sanitize_name, _user_skill_dest
    from observal_cli.harness import ensure_loaded, get_adapter
    from observal_cli.install_recovery import atomic_text_mode, created_mode

    if harness not in PROFILE_PLAN_HARNESSES:
        raise ProfilePlanError("This harness has no guarded profile plan.")
    if (
        not isinstance(snippet, dict)
        or not {"agent_profile", "scope"} <= set(snippet) <= _ALLOWED_KEYS[harness]
        or snippet["scope"] != "user"
    ):
        raise ProfilePlanError("The release has unsupported files, setup steps or warnings; update manually.")
    root_raw = item.get("directory")
    if not isinstance(root_raw, str) or not Path(root_raw).is_absolute() or not Path(root_raw).is_dir():
        raise ProfilePlanError("The original pull directory is unavailable; update manually.")
    root = Path(root_raw)
    name = item.get("local_name")
    if not isinstance(name, str) or not name or name != sanitize_name(name) or Path(name).name != name:
        raise ProfilePlanError("The saved profile name is ambiguous; update manually.")
    ensure_loaded()
    adapter = get_adapter(harness)
    shared = shared_config_paths(harness, "user", root)

    # ── Owned profile ──────────────────────────────────────────
    agent = snippet["agent_profile"]
    if not isinstance(agent, dict) or set(agent) != {"path", "content"}:
        raise ProfilePlanError("The release changes the profile shape; update manually.")
    profile = _resolve(agent["path"], root, allow_home=adapter.allow_home_agent_profile(True))
    if (
        profile.stem != name
        or str(profile) not in old_files
        or _linked(profile)
        or not profile.is_file()
        or str(profile) in shared
    ):
        raise ProfilePlanError("The agent does not own an existing profile at this path; update manually.")
    _claimed_elsewhere(harness, item, name)
    content = copy.deepcopy(agent["content"])
    if isinstance(content, dict):
        content = adapter.rewrite_agent_profile(content, agent_id=agent_id)
    elif isinstance(content, str):
        content = _pin_agent_profile_hooks(_resolve_hook_paths(content))
    else:
        raise ProfilePlanError("The release changes the profile content type; update manually.")
    if harness == "kiro" and isinstance(content, dict):
        for server in (content.get("mcpServers") or {}).values():
            # Kiro keeps an agent's MCPs inside its own profile, so they may be
            # added or dropped automatically, but never with credentials.
            if not isinstance(server, dict) or server.get("env") or server.get("headers"):
                raise ProfilePlanError("A Kiro agent MCP carries credentials or headers; update manually.")
    text, _merged = _render_file(profile, content)
    planned: dict[Path, bytes] = {profile: text.encode("utf-8")}
    modes: dict[Path, int] = {profile: atomic_text_mode(profile.parent)}

    # ── Shared configs: must be an exact no-op ───────────────────
    drop: list[str] = []
    for key in SHARED_KEYS:
        if key in snippet:
            _check_shared_unchanged(harness, key, snippet[key], root, agent_id, shared)
            drop.append(key)

    # ── Bundled registry-direct skills ─────────────────────────
    registry = lockfile.current_registry_url()
    for component in snippet.get("skill_components") or []:
        if not isinstance(component, dict):
            raise ProfilePlanError("A bundled skill is malformed; update manually.")
        skill_name = component.get("name")
        body = component.get("skill_md_content")
        if (
            not isinstance(skill_name, str)
            or not isinstance(body, str)
            or not body
            or component.get("git_url")
            or component.get("path")
            or component.get("bundle_version_id")
        ):
            raise ProfilePlanError(
                "A bundled skill needs a manual update (folder, git source, custom path or no content)."
            )
        folder = _user_skill_dest(harness, _sanitize_name(skill_name))
        _skill_destination_unshared(harness, registry, str(component.get("id", "")), folder)
        target = folder / "SKILL.md"
        if target in planned:
            raise ProfilePlanError("Two bundled skills share a folder; update manually.")
        planned[target] = body.encode("utf-8")
        modes[target] = target.stat().st_mode & 0o777 if target.is_file() else created_mode(folder)
        script, filename = component.get("script_content"), component.get("script_filename")
        if script is not None or filename is not None:
            if (
                not isinstance(script, str)
                or not script
                or not isinstance(filename, str)
                or Path(filename).name != filename
                or filename in {"", ".", ".."}
            ):
                raise ProfilePlanError("A bundled skill script is incomplete or unsafe.")
            path = folder / "scripts" / filename
            planned[path] = script.encode("utf-8")
            modes[path] = (
                0o755
                if path.suffix in _EXECUTABLE_SUFFIXES
                else (path.stat().st_mode & 0o777 if path.is_file() else created_mode(folder))
            )

    # ── Bundled hook scripts ───────────────────────────────────
    hook_dir = Path.home() / _HOOK_DIRS[harness]
    for hook in snippet.get("hook_files") or []:
        if not isinstance(hook, dict) or not isinstance(hook.get("content"), str):
            raise ProfilePlanError("A bundled hook script is malformed.")
        path = _resolve(hook.get("path"), root, allow_home=True)
        if (
            path.parent != hook_dir
            or str(path) in shared
            or path in planned
            or (harness == "kiro" and path.suffix == ".json")  # Kiro reads every hooks/*.json as config.
        ):
            raise ProfilePlanError("A bundled hook script path needs a manual update.")
        planned[path] = hook["content"].encode("utf-8")
        modes[path] = 0o755 if hook.get("executable") else atomic_text_mode(next(d for d in path.parents if d.is_dir()))

    # ── Whole-plan checks ──────────────────────────────────────
    added = [path for path in planned if str(path) not in old_files]
    try:
        install_baseline.reject_foreign_creation(
            added, install_baseline._path(registry, harness, item["id"], "user", root_raw)
        )
    except install_baseline.BaselineError as error:
        raise ProfilePlanError(
            f"The release adds a file that already exists or belongs to another install: {error}"
        ) from error
    # A release may drop bundled skills or hook scripts, never anything else.
    skills_root = _user_skill_dest(harness, "_").parent
    removed = [Path(n) for n in old_files if n not in {str(p) for p in planned}]
    if any(path == profile or not (path.is_relative_to(skills_root) or path.parent == hook_dir) for path in removed):
        raise ProfilePlanError("The release would remove files outside its skills and hook scripts.")
    if any(_linked(path) for path in planned):
        raise ProfilePlanError("A planned file crosses a link; update manually.")
    if sum((path.stat().st_size if path.exists() else 0) + len(raw) for path, raw in planned.items()) > MAX_BYTES:
        raise ProfilePlanError("The profile exceeds the backup size limit; update manually.")
    return planned, modes, drop
