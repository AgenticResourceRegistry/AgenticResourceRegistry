# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""observal pull: fetch agent config from the server and write harness files to disk."""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
import tomllib
from contextlib import contextmanager, nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit, urlunsplit

import tomlkit
import typer
import yaml
from loguru import logger as optic
from packaging.version import InvalidVersion, Version
from rich import print as rprint

from observal_cli import client, config
from observal_cli.constants import VALID_HARNESSES
from observal_cli.errors import CliError, ErrorCategory, fail
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.project_lock import PROJECT_LOCK_FILE
from observal_cli.prompts import password_input, select_one
from observal_cli.render import OutputMode, esc, output_json, spinner
from observal_shared.harness_registry import HARNESS_REGISTRY, get_scope_aware_harnesses

# Hook script names used as placeholders in server-generated agent configs.
# Resolved to absolute paths client-side before writing to disk.
_HOOK_SCRIPT_NAMES = ("observal-hook.sh", "observal-stop-hook.sh")


def _component_conflicts(harness: str, agent_name: str, components: list[dict]) -> list[str]:
    """Return installed component version conflicts for the incoming agent.

    Two agents in the same harness that pin different versions of one component
    write the same files, so the last pull wins. Components are matched by
    registry id; entries recorded before ids were stored fall back to the name.
    """
    from observal_cli.lockfile import read_registry_lockfile

    try:
        _, registry = read_registry_lockfile()
    except (OSError, RuntimeError) as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "Could not read the local installation lockfile.",
            operation="Pull agent",
            resource="Observal lockfile",
            remediation="Repair or remove the malformed lockfile, then retry.",
            detail=repr(error),
        )
    harness_section = registry.get("harnesses", {}).get(harness, {})
    other_agents = harness_section.get("agents", [])

    def key(component: dict) -> str:
        return component.get("id") or component.get("name", "")

    existing: dict[str, list[tuple[str, str]]] = {}
    for other in other_agents:
        if other.get("name") == agent_name:
            continue
        for component in other.get("components", []):
            component_version = component.get("version")
            if key(component) and component_version:
                existing.setdefault(key(component), []).append((component_version, other.get("name", "?")))

    conflicts: list[str] = []
    for component in components:
        component_version = component.get("version")
        if not key(component) or not component_version:
            continue
        for existing_version, existing_agent in existing.get(key(component), []):
            if existing_version != component_version:
                conflicts.append(
                    f"{component.get('type', 'component')} {component.get('name') or key(component)}: "
                    f"v{component_version} (this agent) vs v{existing_version} (from {existing_agent})"
                )
    return conflicts


def _resolve_hook_paths(content: str) -> str:
    """Replace hook script names with absolute paths in agent file content.

    Server-side config generator emits bare script names (observal-hook.sh)
    since it doesn't know the client's install path. This resolves them to
    the actual paths inside the installed package.

    Uses regex anchored to quoted command context so matches like
    ``"observal-hook.sh --agent-name foo"`` are resolved correctly,
    but comments or prose mentioning the script name are not affected.
    """
    import shutil

    hooks_dir = Path(__file__).parent / "hooks"
    for name in _HOOK_SCRIPT_NAMES:
        local = hooks_dir / name
        path = local.resolve().as_posix()
        if not local.is_file():
            # Fallback: check if it's on PATH
            found = shutil.which(name)
            if not found:
                continue
            path = Path(found).resolve().as_posix()
        # Match script name inside quotes with optional trailing args, replace only the script name
        pattern = rf'"{re.escape(name)}(?:\s+[^"]*)?'
        replacement = f'"{path}'
        content = re.sub(pattern, replacement, content)
    return content


def _pin_hook_interpreter(content: str) -> str:
    """Point bare ``python3 -m observal_cli.`` hook commands at this CLI's interpreter.

    The server cannot know how the CLI was installed, so it emits bare python3.
    Under ``uv tool install`` or pipx the system interpreter cannot import
    observal_cli and every hook fails. Windows accepts forward slashes, and a
    path without backslashes is safe inside JSON strings and YAML double-quoted
    frontmatter alike. Quote paths with spaces so the shell treats the
    interpreter as one executable. The function replacement keeps re.sub from
    reading the path as a template.
    """
    path = sys.executable.replace("\\", "/")
    interpreter = subprocess.list2cmdline([path]) if sys.platform == "win32" else shlex.quote(path)
    pattern = r"(?<![/\\\w.-])python3? -m observal_cli\."

    def rewrite(text: str, *, yaml_frontmatter: bool = False) -> str:
        def replace(match: re.Match[str]) -> str:
            command = f"{interpreter} -m observal_cli."
            if yaml_frontmatter:
                # Preserve the scalar's YAML quoting while adding shell quoting.
                # shlex.quote may introduce apostrophes even when the path has none.
                line_start = text.rfind("\n", 0, match.start()) + 1
                prefix = text[line_start : match.start()]
                if re.match(r"^[ \t]*(?:-[ \t]+)?command:[ \t]*'", prefix):
                    command = command.replace("'", "''")
                elif re.match(r'^[ \t]*(?:-[ \t]+)?command:[ \t]*"', prefix):
                    command = command.replace('"', r"\"")
            return command

        return re.sub(pattern, replace, text)

    # Rewrite decoded JSON values, not serialized JSON: a quoted Windows path
    # would otherwise introduce unescaped quotes into the JSON document.
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        return rewrite(content, yaml_frontmatter=True)

    def rewrite_value(value):
        if isinstance(value, str):
            return rewrite(value)
        if isinstance(value, dict):
            return {key: rewrite_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite_value(item) for item in value]
        return value

    return json.dumps(rewrite_value(parsed))


def _pin_agent_profile_hooks(content: str) -> str:
    """Pin executable commands in agent frontmatter without rewriting the agent's prose.

    Codex profiles are TOML, and other profiles can contain free-form instructions.
    Rewriting a command mentioned in quoted instructions can corrupt that file.
    """
    if not content.startswith("---\n"):
        return content
    frontmatter, separator, body = content.partition("\n---")
    if not separator:
        return content
    lines = frontmatter.splitlines(keepends=True)
    in_hooks = False
    block_indent: int | None = None
    content_indent: int | None = None
    for index, line in enumerate(lines):
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None:
            if line.strip():
                if indent <= block_indent or (content_indent is not None and indent < content_indent):
                    block_indent = None
                    content_indent = None
                else:
                    content_indent = indent if content_indent is None else content_indent
            if block_indent is not None:
                # Block scalar contents are shell script lines, not YAML quoted
                # scalars. Rewrite executable invocations, not comments or prose.
                if re.match(r"^[ \t]*(?:(?:[A-Za-z_]\w*=[^ \t]+|exec)[ \t]+)*python3? -m observal_cli\.", line):
                    lines[index] = _pin_hook_interpreter(line)
                continue
        if line.startswith("hooks:"):
            in_hooks = True
        elif line and not line[0].isspace():
            in_hooks = False
        command_field = re.match(r"^(\s+(?:-\s+)?command:[ \t]*)", line) if in_hooks else None
        if command_field:
            indicator = line[command_field.end() :].split("#", 1)[0].strip()
            if re.fullmatch(r"[|>](?:[+-]?[1-9]?|[1-9][+-]?)", indicator):
                block_indent = indent
                content_indent = None
                continue
            rewritten = _pin_hook_interpreter(line)
            if rewritten != line and not line[command_field.end() :].startswith(("'", '"')):
                # A shell-quoted path at the start of a bare YAML scalar is
                # parsed as a whole scalar; the following -m then breaks YAML.
                value = rewritten[command_field.end() :].rstrip("\r\n")
                newline = rewritten[command_field.end() + len(value) :]
                rewritten = rewritten[: command_field.end()] + json.dumps(value) + newline
            lines[index] = rewritten
    return "".join(lines) + separator + body


def _mcp_components(agent_detail: dict) -> list[tuple[str, str, str | None]]:
    """(listing id, display name, pinned version) for each MCP an agent version uses."""
    mcps: list[tuple[str, str, str | None]] = []
    for link in agent_detail.get("mcp_links", []):
        mcps.append((str(link["mcp_listing_id"]), link.get("mcp_name", ""), None))
    for link in agent_detail.get("component_links", []):
        if link.get("component_type") != "mcp":
            continue
        cid = str(link["component_id"])
        pinned = link.get("version_ref") or link.get("resolved_version")
        pinned = pinned if pinned and pinned != "latest" else None
        known = next((index for index, (mid, _name, _version) in enumerate(mcps) if mid == cid), None)
        if known is None:
            mcps.append((cid, link.get("component_name", ""), pinned))
        elif pinned:
            mcps[known] = (cid, mcps[known][1], pinned)
    return mcps


def _mcp_spec(listing_id: str, version: str | None, cache: dict | None) -> dict:
    """The MCP definition an install will use: the pinned version when there is one."""
    cache_key = (listing_id, version)
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    spec = None
    if version:
        try:
            spec = client.get(f"/api/v1/mcps/{listing_id}/versions/{version}")
        except CliError as error:
            # An unapproved pinned release is hidden from non-owners; its install
            # still falls back to the listing, so read the listing too.
            if error.category is not ErrorCategory.NOT_FOUND:
                raise
    if spec is None:
        spec = client.get(f"/api/v1/mcps/{listing_id}")
    if cache is not None:
        cache[cache_key] = spec
    return spec


def _component_input_definitions(listing: dict, field: str, kind: str, component: str) -> list[dict]:
    definitions = listing.get(field, [])
    if definitions is None:
        definitions = []
    if not isinstance(definitions, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip()
        for item in definitions
    ):
        fail(
            ErrorCategory.UNAVAILABLE,
            "The server returned invalid agent installation requirements.",
            operation="Pull agent",
            resource=component,
            remediation="Check server compatibility and retry.",
            result={"invalid_input_kind": kind, "component": component},
        )
    return definitions


def _collect_mcp_env_vars(
    agent_detail: dict,
    *,
    no_prompt: bool = False,
    env_overrides: dict[str, str] | None = None,
    spec_cache: dict | None = None,
    missing_inputs: list[dict[str, str]] | None = None,
) -> dict[str, dict[str, str]]:
    """Discover MCP env vars from agent components and prompt the user for values.

    When *no_prompt* is True, uses values from *env_overrides* for known vars
    and skips prompting entirely. Missing vars are omitted (server handles
    placeholders).

    Returns {mcp_listing_id: {VAR_NAME: value}} for all MCPs that have env vars.
    """
    env_values: dict[str, dict[str, str]] = {}
    _overrides = env_overrides or {}

    mcp_ids = _mcp_components(agent_detail)
    if not mcp_ids:
        return env_values

    # Read each MCP's environment variables from the version the agent pins
    for listing_id, display_name, pinned in mcp_ids:
        listing = _mcp_spec(listing_id, pinned, spec_cache)

        mcp_name = display_name or listing.get("name", listing_id[:8])
        ev_list = _component_input_definitions(
            listing,
            "environment_variables",
            "environment_variable",
            mcp_name,
        )
        if not ev_list:
            continue

        required = [ev for ev in ev_list if ev.get("required", True)]
        optional = [ev for ev in ev_list if not ev.get("required", True)]
        mcp_env: dict[str, str] = {}

        if no_prompt:
            # Non-interactive: use --env flag values for matching vars
            for ev in required + optional:
                if ev["name"] in _overrides:
                    mcp_env[ev["name"]] = _overrides[ev["name"]]
                elif ev.get("required", True) and missing_inputs is not None:
                    missing_inputs.append({"kind": "environment_variable", "name": ev["name"], "component": mcp_name})
        else:
            if required:
                rprint(f"\n[bold]{esc(mcp_name)}[/bold] requires {len(required)} environment variable(s):")
                for ev in required:
                    if ev["name"] in _overrides:
                        mcp_env[ev["name"]] = _overrides[ev["name"]]
                        rprint(f"  [green]\u2713[/green] {esc(ev['name'])} [dim](from --env)[/dim]")
                    else:
                        desc = f" [dim]({esc(ev['description'])})[/dim]" if ev.get("description") else ""
                        val = password_input(f"  {esc(ev['name'])}{desc}")
                        mcp_env[ev["name"]] = val

            if optional:
                rprint(f"\n[dim]{esc(mcp_name)}: {len(optional)} optional env var(s):[/dim]")
                for ev in optional:
                    if ev["name"] in _overrides:
                        mcp_env[ev["name"]] = _overrides[ev["name"]]
                        rprint(f"  [green]\u2713[/green] {esc(ev['name'])} [dim](from --env)[/dim]")
                    else:
                        desc = f" [dim]({esc(ev['description'])})[/dim]" if ev.get("description") else ""
                        val = password_input(f"  {esc(ev['name'])}{desc} (press Enter to skip)")
                        if val:
                            mcp_env[ev["name"]] = val

        if mcp_env:
            env_values[listing_id] = mcp_env

    # Warn about MCPs that had env vars but user skipped all of them
    return env_values


def _collect_mcp_headers(
    agent_detail: dict,
    *,
    no_prompt: bool = False,
    header_overrides: dict[str, str] | None = None,
    spec_cache: dict | None = None,
    missing_inputs: list[dict[str, str]] | None = None,
) -> dict[str, dict[str, str]]:
    """Discover MCP headers from agent components and prompt the user for values.

    When *no_prompt* is True, uses values from *header_overrides* for known headers
    and skips prompting entirely. Missing headers are omitted.

    Returns {mcp_listing_id: {Header-Name: value}} for all MCPs that have headers.
    """
    header_values: dict[str, dict[str, str]] = {}
    _overrides = header_overrides or {}

    mcp_ids = _mcp_components(agent_detail)
    if not mcp_ids:
        return header_values

    for listing_id, display_name, pinned in mcp_ids:
        listing = _mcp_spec(listing_id, pinned, spec_cache)

        mcp_name = display_name or listing.get("name", listing_id[:8])
        header_list = _component_input_definitions(listing, "headers", "header", mcp_name)
        if not header_list:
            continue

        required = [h for h in header_list if h.get("required", True)]
        optional = [h for h in header_list if not h.get("required", True)]
        mcp_hdrs: dict[str, str] = {}

        if no_prompt:
            for h in required + optional:
                if h["name"] in _overrides:
                    mcp_hdrs[h["name"]] = _overrides[h["name"]]
                elif h.get("required", True) and missing_inputs is not None:
                    missing_inputs.append({"kind": "header", "name": h["name"], "component": mcp_name})
        else:
            if required:
                rprint(f"\n[bold]{esc(mcp_name)}[/bold] requires {len(required)} header(s):")
                for h in required:
                    if h["name"] in _overrides:
                        mcp_hdrs[h["name"]] = _overrides[h["name"]]
                        rprint(f"  [green]\u2713[/green] {esc(h['name'])} [dim](from --header)[/dim]")
                    else:
                        desc = f" [dim]({esc(h['description'])})[/dim]" if h.get("description") else ""
                        val = password_input(f"  {esc(h['name'])}{desc}")
                        mcp_hdrs[h["name"]] = val

            if optional:
                rprint(f"\n[dim]{esc(mcp_name)}: {len(optional)} optional header(s):[/dim]")
                for h in optional:
                    if h["name"] in _overrides:
                        mcp_hdrs[h["name"]] = _overrides[h["name"]]
                        rprint(f"  [green]\u2713[/green] {esc(h['name'])} [dim](from --header)[/dim]")
                    else:
                        desc = f" [dim]({esc(h['description'])})[/dim]" if h.get("description") else ""
                        val = password_input(f"  {esc(h['name'])}{desc} (press Enter to skip)")
                        if val:
                            mcp_hdrs[h["name"]] = val

        if mcp_hdrs:
            header_values[listing_id] = mcp_hdrs

    return header_values


class _TomlConflict(ValueError):  # noqa: N818 - internal conflict category
    """Unsafe TOML input or concurrent modification (never include source values)."""


class _TomlPostWriteConflict(_TomlConflict):
    """A path changed after replacement; the pinned destination was already written."""

    def __init__(self, destination: str, status: str):
        super().__init__("Configuration path changed during pull")
        self.destination = destination
        self.status = status


_MISSING = object()


def _read_toml_bytes(path: Path, *, dir_fd: int | None = None) -> bytes | object:
    try:
        if dir_fd is None:
            return path.read_bytes()
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
        with os.fdopen(fd, "rb") as file:
            return file.read()
    except FileNotFoundError:
        return _MISSING


def _same_toml_value(left: object, right: object) -> bool:
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right):
        return True
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same_toml_value(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same_toml_value(a, b) for a, b in zip(left, right, strict=True))
    return type(left) is type(right) and left == right


def _check_toml_value(value: object) -> None:
    """Reject values that have no unambiguous TOML representation."""
    from datetime import date, datetime, time

    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise _TomlConflict("Invalid TOML configuration")
            _check_toml_value(child)
    elif isinstance(value, list):
        for child in value:
            _check_toml_value(child)
    elif type(value) not in (str, bool, int, float, date, datetime, time):
        raise _TomlConflict("Invalid TOML configuration")


def _inline_toml_table(values: dict) -> tomlkit.items.InlineTable:
    """Build nested inline values for an existing inline MCP root."""
    table = tomlkit.inline_table()
    for key, value in values.items():
        table[key] = _inline_toml_table(value) if isinstance(value, dict) else tomlkit.item(value)
    return table


def _trailing_toml_trivia(table: tomlkit.items.Table) -> list:
    """Find trivia before the next sibling header, even after a nested table."""
    trailing = []
    for key, item in reversed(table.value.body):
        if key is None:
            trailing.append(item)
        else:
            if not trailing and isinstance(item, tomlkit.items.Table):
                return _trailing_toml_trivia(item)
            break
    return list(reversed(trailing))


def _toml_document(content: dict, existing_text: str | None = None) -> str:
    """Replace named server subtrees, preserving everything else in the document."""
    if not isinstance(content, dict) or len(content) != 1:
        raise _TomlConflict("Invalid TOML configuration")
    root_key, incoming = next(iter(content.items()))
    if not isinstance(root_key, str) or not isinstance(incoming, dict):
        raise _TomlConflict("Invalid TOML configuration")
    for name, server in incoming.items():
        if not isinstance(name, str) or not isinstance(server, dict):
            raise _TomlConflict("Invalid TOML configuration")
        _check_toml_value(server)

    before = tomllib.loads(existing_text) if existing_text is not None else {}
    section = before.get(root_key, {})
    if not isinstance(section, dict) or any(not isinstance(server, dict) for server in section.values()):
        raise _TomlConflict("Invalid TOML configuration")
    if existing_text is not None and not incoming:
        return existing_text

    doc = tomlkit.parse(existing_text) if existing_text is not None else tomlkit.document()
    if root_key not in doc:
        doc[root_key] = tomlkit.table()
    root = doc[root_key]
    # Dotted assignments are still in the document's top-level key/value area.
    # An explicit server header here would capture following top-level keys.
    dotted_root = any(key is not None and key.key == root_key and key.is_dotted() for key, _ in doc._body)
    for name, server in incoming.items():
        if isinstance(root, tomlkit.items.InlineTable) or dotted_root:
            root[name] = _inline_toml_table(server)
            continue
        table = tomlkit.table()
        for key, value in server.items():
            if isinstance(value, dict):
                child = tomlkit.table()
                for child_key, child_value in value.items():
                    child[child_key] = tomlkit.item(child_value)
                table[key] = child
            else:
                table[key] = tomlkit.item(value)
        # TOMLKit attaches comments before the next table header to the end
        # of the previous table. Carry that boundary trivia across replacement.
        if name in root and isinstance(root[name], tomlkit.items.Table):
            for item in _trailing_toml_trivia(root[name]):
                table.add(item)
        root[name] = table

    rendered = tomlkit.dumps(doc)
    # TOMLKit preserves existing trivia but emits newly created tables with LF.
    # Only normalize when the source has a consistent CRLF convention; leave
    # mixed-newline documents and newly generated documents alone.
    if existing_text and "\r\n" in existing_text and "\n" not in existing_text.replace("\r\n", ""):
        rendered = rendered.replace("\r\n", "\n").replace("\n", "\r\n")
    after = tomllib.loads(rendered)
    expected = {**before, root_key: {**section, **incoming}}
    if not _same_toml_value(after, expected):
        raise _TomlConflict("Invalid TOML configuration")
    return rendered


def _dict_to_toml(d: dict) -> str:
    return _toml_document(d)


@contextmanager
def _toml_write_lock(path: Path):
    """Lock the effective TOML target, pinning its directory on POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        # Windows has no dir_fd replacement; keep the existing best-effort behavior.
        target = path.resolve() if path.is_symlink() else path
        lock_path = target.with_name(f".{target.name}.observal.lock")
        try:
            mode = lock_path.lstat()
        except FileNotFoundError:
            pass
        else:
            if (
                stat.S_ISLNK(mode.st_mode)
                or getattr(mode, "st_file_attributes", 0) & 0x400
                or (stat.S_ISREG(mode.st_mode) and mode.st_nlink > 1)
            ):
                raise _TomlConflict("Unsafe configuration lock path")
        # O_NOFOLLOW also protects the open on platforms that provide it.
        try:
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError as error:
            if lock_path.is_symlink():
                raise _TomlConflict("Unsafe configuration lock path") from error
            raise
        with os.fdopen(fd, "r+b") as lock:
            opened = os.fstat(fd)
            current = lock_path.lstat()
            if (
                stat.S_ISLNK(current.st_mode)
                or getattr(current, "st_file_attributes", 0) & 0x400
                or (stat.S_ISREG(current.st_mode) and current.st_nlink > 1)
                or (stat.S_ISREG(opened.st_mode) and opened.st_nlink > 1)
                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise _TomlConflict("Unsafe configuration lock path")
            import msvcrt

            lock.seek(0, 2)
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield target, None, None
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    target = path
    try:
        # Follow only the final component, so Codex configs intentionally
        # symlinked to another file keep working without replacing the link.
        for _ in range(40):
            try:
                mode = os.stat(target.name, dir_fd=directory, follow_symlinks=False).st_mode
            except FileNotFoundError:
                break
            if not stat.S_ISLNK(mode):
                break
            link = Path(os.readlink(target.name, dir_fd=directory))
            next_directory = os.open(link.parent, os.O_RDONLY | os.O_DIRECTORY, dir_fd=directory)
            target = link
            os.close(directory)
            directory = next_directory
        else:
            raise _TomlConflict("Too many configuration symlinks")

        # The directory inode cannot be renamed away from a competing writer's
        # lock file. Serialize on it first; never fall back to lock-file-only
        # locking on filesystems that do not support directory flock.
        fcntl.flock(directory, fcntl.LOCK_EX)
        try:
            name = f".{target.name}.observal.lock"
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(fd, "r+b") as lock:
                _check_toml_lock_inode(directory, name, fd)
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    _check_toml_lock_inode(directory, name, fd)
                    yield target, directory, fd
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            fcntl.flock(directory, fcntl.LOCK_UN)
    finally:
        os.close(directory)


def _check_toml_lock_inode(directory: int, name: str, lock_fd: int) -> None:
    """Refuse a replaced, linked, or non-regular named lock inode."""
    try:
        opened = os.fstat(lock_fd)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or opened.st_nlink != 1
            or named.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise _TomlConflict("Unsafe configuration lock path")
    except OSError as error:
        raise _TomlConflict("Unsafe configuration lock path") from error


def _check_toml_target(requested: Path, target: Path, dir_fd: int) -> None:
    """Refuse a requested path that no longer leads to the pinned target directory and name."""
    try:
        resolved = requested.resolve()
        current = resolved.parent.stat()
        pinned = os.fstat(dir_fd)
        if resolved.name != target.name or (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
            raise _TomlConflict("Configuration path changed during pull")
    except (OSError, RuntimeError) as error:
        raise _TomlConflict("Configuration path changed during pull") from error


def _check_install_toml_path(requested: Path | None, original: Path, allowed_dir: Path | None) -> None:
    """Keep the validated target fixed even if a parent is retargeted before opening it."""
    if requested is None:
        return
    try:
        if requested.resolve() != original or (allowed_dir is not None and not original.is_relative_to(allowed_dir)):
            raise _TomlConflict("Configuration path changed during pull")
    except (OSError, RuntimeError) as error:
        raise _TomlConflict("Configuration path changed during pull") from error


def _atomic_write_text(
    path: Path,
    content: str,
    *,
    expected_bytes: bytes | object = None,
    dir_fd: int | None = None,
    lock_fd: int | None = None,
    requested_path: Path | None = None,
    install_path: Path | None = None,
    original_target: Path | None = None,
    allowed_dir: Path | None = None,
) -> None:
    data = content.encode("utf-8")
    if dir_fd is not None:
        name = f".{path.name}.{secrets.token_hex(16)}"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
            if lock_fd is not None:
                _check_toml_lock_inode(dir_fd, f".{path.name}.observal.lock", lock_fd)
            if expected_bytes is not None and _read_toml_bytes(path, dir_fd=dir_fd) != expected_bytes:
                raise _TomlConflict("Configuration changed during pull")
            if requested_path is not None:
                _check_toml_target(requested_path, path, dir_fd)
            if install_path is not None:
                _check_toml_target(install_path, path, dir_fd)
                _check_install_toml_path(install_path, original_target, allowed_dir)
            os.replace(name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        finally:
            try:
                os.unlink(name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
        return

    temporary: Path | None = None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
            temporary = Path(file.name)
            file.write(data)
        if expected_bytes is not None and _read_toml_bytes(path) != expected_bytes:
            raise _TomlConflict("Configuration changed during pull")
        _check_install_toml_path(install_path, original_target, allowed_dir)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _toml_written_destination(target: Path, directory: int | None) -> str:
    """Describe the write by pinned directory identity, never a mutable pathname."""
    if directory is None:
        # Windows has no pinned dir_fd replacement; do not claim a path is authoritative.
        return f"unpinned directory (identity unavailable), filename={target.name!r} (not a pathname)"
    pinned = os.fstat(directory)
    return (
        f"pinned directory (device={pinned.st_dev}, inode={pinned.st_ino}), filename={target.name!r} (not a pathname)"
    )


def _write_toml_config(
    path: Path, content: dict, *, merge: bool, requested_path: Path | None = None, allowed_dir: Path | None = None
) -> str:
    def check_requested(target: Path, directory: int | None) -> None:
        _check_install_toml_path(requested_path, path, allowed_dir)
        if requested_path is not None and directory is not None:
            _check_toml_target(requested_path, target, directory)

    check_requested(path, None)
    with _toml_write_lock(path) as (target, directory, lock_fd):
        # The lexical install path and the validated canonical path must both
        # still lead to the pinned destination, even for a no-op merge.
        if directory is not None:
            _check_toml_target(path, target, directory)
        check_requested(target, directory)
        prior = _read_toml_bytes(target, dir_fd=directory)
        try:
            existing = prior.decode("utf-8") if prior is not _MISSING and merge else None
            rendered = _toml_document(content, existing)
        except (OSError, _TomlConflict):
            raise
        except Exception as error:
            raise _TomlConflict("Invalid TOML configuration") from error
        if directory is not None:
            _check_toml_target(path, target, directory)
        check_requested(target, directory)
        if prior is not _MISSING and merge and rendered == existing:
            return "merged"
        _atomic_write_text(
            target,
            rendered,
            expected_bytes=prior,
            dir_fd=directory,
            lock_fd=lock_fd,
            requested_path=path,
            install_path=requested_path,
            original_target=path,
            allowed_dir=allowed_dir,
        )
        status = "created" if prior is _MISSING else "merged" if merge else "updated"
        try:
            if directory is not None:
                _check_toml_target(path, target, directory)
            check_requested(target, directory)
        except _TomlConflict as error:
            # The replace completed against the pinned directory even if the
            # lexical path has since moved or points elsewhere.
            raise _TomlPostWriteConflict(_toml_written_destination(target, directory), status) from error
        return status


def _merge_yaml_config(path: Path, content: dict, root_key: str, *, existed: bool, merge: bool) -> str:
    """Write a YAML config, merging one section into the file already on disk.

    Goose keeps providers, global settings, and MCP extensions in a single
    ``config.yaml``, so an install must only touch its own section. An existing
    file that cannot be parsed is left untouched rather than overwritten.
    """
    import yaml

    existing: dict = {}
    if existed:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            raise
        except UnicodeError as error:
            raise ValueError(f"cannot merge unreadable YAML: {path}") from error
        try:
            loaded = yaml.safe_load(text) or {}
        except yaml.YAMLError as error:
            raise ValueError(f"cannot merge unreadable YAML: {path}") from error
        if not isinstance(loaded, dict):
            raise ValueError(f"cannot merge YAML whose top level is not a mapping: {path}")
        existing = loaded

    if merge and existed:
        section = existing.get(root_key)
        incoming = content.get(root_key, {})
        if section is not None and not isinstance(section, dict):
            raise ValueError(f"cannot merge non-mapping YAML section {root_key}: {path}")
        if not isinstance(incoming, dict):
            raise ValueError(f"incoming YAML section {root_key} is not a mapping")
        existing[root_key] = {**(section or {}), **incoming}
        payload = existing
    else:
        payload = {**existing, **content}
    _atomic_write_text(path, yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
    return "merged" if existed else "created"


def _write_file(
    path: Path,
    content: str | dict,
    *,
    merge_mcp: bool = False,
    requested_toml_path: Path | None = None,
    allowed_toml_dir: Path | None = None,
) -> str:
    """Write content to a file path, creating parent dirs as needed.

    If *merge_mcp* is True and the file already exists, merge the incoming
    dict into the existing one rather than overwriting.

    Returns a human-readable status string ("created", "updated", "merged").
    """
    optic.trace("path={}", path)
    _check_install_toml_path(requested_toml_path, path, allowed_toml_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()

    if isinstance(content, dict):
        # The merged section is the first mapping (mcpServers, hooks, ...). Configs
        # such as Cursor's hooks.json start with a scalar ("version": 1), which
        # must not be mistaken for the section.
        root_key = next(
            (key for key, value in content.items() if isinstance(value, dict)),
            next(iter(content), "mcpServers"),
        )
        if path.suffix == ".toml":
            return _write_toml_config(
                path, content, merge=merge_mcp, requested_path=requested_toml_path, allowed_dir=allowed_toml_dir
            )
        elif path.suffix in (".yaml", ".yml"):
            return _merge_yaml_config(path, content, root_key, existed=existed, merge=merge_mcp)
        else:
            if merge_mcp and existed:
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    raise
                except UnicodeError as error:
                    raise ValueError(f"cannot merge unreadable JSON: {path}") from error
                try:
                    existing = json.loads(text)
                except json.JSONDecodeError as error:
                    raise ValueError(f"cannot merge unreadable JSON: {path}") from error
                if not isinstance(existing, dict):
                    raise ValueError(f"cannot merge JSON whose top level is not an object: {path}")
                incoming_servers = content.get(root_key, {})
                section = existing.setdefault(root_key, {})
                if not isinstance(section, dict) or not isinstance(incoming_servers, dict):
                    raise ValueError(f"cannot merge non-object JSON section {root_key}: {path}")
                section.update(incoming_servers)
                for key, value in content.items():
                    if key != root_key and not isinstance(value, dict):
                        existing[key] = value
                _atomic_write_text(path, json.dumps(existing, indent=2) + "\n")
                return "merged"
            _atomic_write_text(path, json.dumps(content, indent=2) + "\n")
    else:
        _atomic_write_text(path, content)

    return "updated" if existed else "created"


def _write_file_checked(
    path: Path,
    content: str | dict,
    *,
    merge_mcp: bool = False,
    requested_toml_path: Path | None = None,
    allowed_toml_dir: Path | None = None,
) -> str:
    try:
        return _write_file(
            path,
            content,
            merge_mcp=merge_mcp,
            requested_toml_path=requested_toml_path,
            allowed_toml_dir=allowed_toml_dir,
        )
    except (ValueError, TypeError, UnicodeError) as error:
        fail(
            ErrorCategory.CONFLICT,
            f"Could not safely merge existing configuration: {path}.",
            operation="Pull agent",
            resource=str(path),
            remediation="Fix or back up the existing configuration, then retry.",
            detail=None if path.suffix == ".toml" else repr(error),
            result=(
                _pull_failure_result([(str(error.destination), error.status)], "write_files", failed_path=str(path))
                if isinstance(error, _TomlPostWriteConflict)
                else None
            ),
        )
    except OSError as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            f"Could not write generated configuration: {path}.",
            operation="Pull agent",
            resource=str(path),
            remediation="Check file permissions and available disk space.",
            detail=None if path.suffix == ".toml" else repr(error),
        )
    except Exception:
        if path.suffix != ".toml":
            raise
        fail(
            ErrorCategory.CONFLICT,
            f"Could not safely merge existing configuration: {path}.",
            operation="Pull agent",
            resource=str(path),
            remediation="Fix or back up the existing configuration, then retry.",
        )


def _rewrite_kiro_agent_profile(content: dict, agent_id: str | None = None) -> dict:
    """Prepare a Kiro agent profile for the hook format this machine can read.

    Telemetry hooks normally live in the standalone ``.kiro/hooks/observal.json``
    file, which every Kiro surface reads. Kiro IDE 1.0 loads an agent carrying
    inline ``hooks`` but never fires them, so Observal's inline hooks are
    stripped here and only re-added on machines that are provably legacy Kiro
    CLI 2.x with no IDE installed.

    Empty CLI-only tool fields are also dropped. That is what actually keeps an
    agent out of the IDE picker: ProfileLoader rejects any JSON profile carrying
    ``allowedTools`` or ``toolsSettings`` without a ``permissions`` block.

    Hook commands are also rewritten to the current Python interpreter: the
    server generates bare ``python3``, which won't find ``observal_cli`` when it
    is installed in a project-local virtual environment.
    """
    from observal_cli.harness.kiro import strip_ide_hostile_fields, use_inline_hooks
    from observal_cli.harness_specs.kiro_hooks_spec import build_kiro_hooks

    strip_ide_hostile_fields(content)

    hooks = content.get("hooks") or {}

    # Drop Observal-owned inline entries; keep whatever the user added.
    cleaned_hooks: dict = {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            cleaned_hooks[event] = entries
            continue
        # A truthy non-dict entry - a bare string, say - would raise on .get and
        # abort the whole pull. Malformed user entries are left untouched.
        kept = [h for h in entries if not (isinstance(h, dict) and "observal_cli" in str(h.get("command", "")))]
        if kept:
            cleaned_hooks[event] = kept

    if use_inline_hooks():
        for event, desired_entries in build_kiro_hooks(agent_id=agent_id or "").items():
            cleaned_hooks[event] = cleaned_hooks.get(event, []) + desired_entries

    if cleaned_hooks:
        content["hooks"] = cleaned_hooks
    else:
        content.pop("hooks", None)
    return content


def _rewrite_kiro_hooks(content: dict, agent_id: str | None = None) -> dict:
    """Backwards-compatible alias for the inline-hook rewrite.

    Retained so external callers keep working; new code should use
    :func:`_rewrite_kiro_agent_profile`.
    """
    return _rewrite_kiro_agent_profile(content, agent_id=agent_id)


def _rewrite_copilot_cli_hooks(content: dict, agent_id: str | None = None) -> dict:
    """Rewrite Copilot CLI hook commands to inject per-agent attribution.

    The server emits a generic ``.github/hooks/observal.json`` whose commands
    carry no agent identity, so sessions fall back to best-effort cwd matching
    and go unattributed for user-scope installs or when the project moves.
    Rebuilding the Observal hooks with ``build_copilot_cli_hooks(agent_id=...)``
    prepends ``OBSERVAL_AGENT_ID`` (both bash and powershell forms), which the
    session push hook resolves to an exact agent+version via the lockfile.

    User-added hooks in the file are preserved; only Observal's entries are
    replaced. Mirrors _rewrite_kiro_hooks().
    """
    hooks = content.get("hooks")
    if not hooks:
        return content

    from observal_cli.harness_specs.copilot_cli_hooks_spec import build_copilot_cli_hooks

    desired_hooks = build_copilot_cli_hooks(agent_id=agent_id or "")["hooks"]

    # Replace only Observal hooks, preserve any user-added hooks
    for event, desired_entries in desired_hooks.items():
        existing = hooks.get(event, [])
        cleaned = [
            h
            for h in existing
            if "copilot_cli_session_push" not in h.get("bash", "")
            and "hooks.session_push --harness copilot-cli" not in h.get("bash", "")
        ]
        hooks[event] = cleaned + desired_entries

    content["hooks"] = hooks
    return content


_SESSION_HOOK_INVOCATION = re.compile(r"-m\s+observal_cli\.hooks\.")


def _has_session_hook(hooks: object) -> bool:
    """Inspect executable fields inside hook entries, not descriptions or agent instructions."""
    pending = [hooks]
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        if not isinstance(node, (dict, list)) or id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, list):
            pending.extend(node)
            continue
        for key, value in node.items():
            if key in {"command", "bash", "powershell"} and isinstance(value, str):
                if _SESSION_HOOK_INVOCATION.search(value):
                    return True
            elif isinstance(value, (dict, list)):
                pending.append(value)
    return False


def _hook_section(content: object) -> object:
    """Extract real hook entries from generated JSON/YAML or Markdown frontmatter."""
    if isinstance(content, dict):
        return content.get("hooks")
    if not isinstance(content, str):
        return None
    try:
        if content.startswith("---\n"):
            frontmatter, separator, _body = content[4:].partition("\n---")
            parsed = yaml.safe_load(frontmatter) if separator else None
        else:
            try:
                parsed = json.loads(content)
            except ValueError:
                parsed = yaml.safe_load(content)
    except (ValueError, yaml.YAMLError):
        return None
    return parsed.get("hooks") if isinstance(parsed, dict) else None


def _reports_sessions(
    snippet: dict, *, target_dir: Path | None = None, is_user_scope: bool = False, dry_run: bool = False
) -> bool:
    """Report configured telemetry from actual hook fields, including hooks retained by a merge."""
    profile = snippet.get("agent_profile") or {}
    if _has_session_hook(_hook_section(profile.get("content"))):
        return True

    hooks_cfg = snippet.get("hooks_config") or {}
    incoming = _hook_section(hooks_cfg.get("content"))
    # _write_file merges only mappings; string content replaces the entire file
    # even when the snippet requests a merge.
    if (
        target_dir is None
        or not hooks_cfg.get("merge")
        or "path" not in hooks_cfg
        or not isinstance(hooks_cfg.get("content"), dict)
    ):
        return _has_session_hook(incoming)

    path = _resolve_path(hooks_cfg["path"], target_dir, allow_home=is_user_scope)
    if not dry_run:
        try:
            return _has_session_hook(_hook_section(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError):
            return _has_session_hook(incoming)

    # Dry runs do not write files. Project the same shallow hooks merge used by
    # _write_file: incoming event keys replace those events, others survive.
    try:
        existing = _hook_section(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        existing = None
    if isinstance(existing, dict) and isinstance(incoming, dict):
        return _has_session_hook({**existing, **incoming})
    return _has_session_hook(incoming)


def _reports_written_sessions(paths: list[str]) -> bool:
    """Inspect hook files actually left on disk, including a partial install."""
    for raw_path in paths:
        try:
            if _has_session_hook(_hook_section(Path(raw_path).read_text(encoding="utf-8"))):
                return True
        except (OSError, UnicodeError):
            continue
    return False


def _hook_destinations(snippet: dict, *, adapter, target_dir: Path, is_user_scope: bool) -> list[str]:
    """Resolved hook-config and agent-profile destinations; unsafe paths are skipped."""
    destinations: list[str] = []
    for key, allow_home in (
        ("hooks_config", is_user_scope),
        ("agent_profile", adapter.allow_home_agent_profile(is_user_scope)),
    ):
        entry = snippet.get(key)
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        try:
            destinations.append(str(_resolve_path(entry["path"], target_dir, allow_home=allow_home)))
        except CliError:
            # An escaping path is rejected by the write itself and never touched.
            continue
    return destinations


def _resolve_path(raw_path: str, target_dir: Path, *, allow_home: bool = False) -> Path:
    """Resolve a path from the config snippet relative to *target_dir*.

    By default, ``~/`` prefixes are mapped under *target_dir* (not the real
    home directory) so that the pull command always writes inside the project.
    When *allow_home* is True (e.g. user explicitly chose --scope user), real
    ``$HOME`` expansion is allowed.

    Raises typer.Exit if the resolved path escapes *target_dir* (and home
    expansion is not permitted).
    """
    optic.trace("raw_path={}, target_dir={}", raw_path, target_dir)
    if raw_path.startswith("~/") or raw_path.startswith("~\\"):
        if allow_home:
            return Path(raw_path).expanduser().resolve()
        resolved = (target_dir / raw_path[2:]).resolve()
    else:
        resolved = (target_dir / raw_path).resolve()

    if not resolved.is_relative_to(target_dir):
        fail(
            ErrorCategory.VALIDATION,
            f"Generated path escapes the target directory: {raw_path}.",
            operation="Pull agent",
            resource=raw_path,
            remediation="Use a safe target directory or report the invalid server config.",
        )

    return resolved


# harnesses that support a project vs user install scope (derived from registry)
_SCOPE_AWARE_HARNESSES = get_scope_aware_harnesses()


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _strict_mode(flag: bool | None) -> bool:
    """--strict/--no-strict wins; otherwise OBSERVAL_STRICT decides (for CI)."""
    import os

    if flag is not None:
        return flag
    return os.environ.get("OBSERVAL_STRICT", "").strip().lower() in _TRUE_VALUES


def _project_locked_agent(directory: Path, qualified_name: str, agent_id: str | None = None) -> dict | None:
    from observal_cli import project_lock

    try:
        return project_lock.locked_agent(directory, qualified_name, agent_id)
    except project_lock.ProjectLockError as error:
        fail(
            ErrorCategory.VALIDATION,
            f"{PROJECT_LOCK_FILE} in {directory} cannot be used.",
            operation="Pull agent",
            resource=str(directory / PROJECT_LOCK_FILE),
            remediation="Fix or restore the file from version control, then retry.",
            detail=repr(error),
        )


def _installed_agent(harness: str, agent_id: str, options: dict, directory: Path) -> dict | None:
    from observal_cli.lockfile import installed_agent

    try:
        return installed_agent(harness, agent_id, scope=options.get("scope", "project"), directory=str(directory))
    except (OSError, RuntimeError) as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            "Could not read the local installation lockfile.",
            operation="Pull agent",
            resource="Observal lockfile",
            remediation="Repair or remove the malformed lockfile, then retry.",
            detail=repr(error),
        )


def _locked_version_detail(
    agent_ref: str, version: str, resolved_from: str, *, qualified_name: str, directory: Path
) -> dict:
    """Fetch the agent version a pull will install.

    When that version came from a lock rather than from --version, a missing
    version means the lock is stale (or was written against another server), so
    say where it came from and how to move on instead of a bare "not found".
    """
    try:
        return client.get(f"/api/v1/agents/{agent_ref}/versions/{version}")
    except CliError as error:
        if error.category is not ErrorCategory.NOT_FOUND or resolved_from not in ("project-lock", "installed"):
            raise
        source = (
            str(directory / PROJECT_LOCK_FILE)
            if resolved_from == "project-lock"
            else "this machine's Observal lockfile"
        )
        fail(
            ErrorCategory.NOT_FOUND,
            f"{source} pins agent {qualified_name} to version {version}, which is not available on this server.",
            operation="Pull agent",
            resource=f"{qualified_name}@{version}",
            remediation=(
                "Pull with --upgrade to install the latest approved version, or --version to choose one; "
                "either updates the lock."
            ),
            request_id=error.request_id,
            http_status=error.http_status,
        )


def _require_supported_harness(harness: str, supported: list[str], *, allow: bool, label: str) -> None:
    """Refuse, before any prompt, a harness the agent version does not list as supported.

    The server refuses too; checking here first keeps the user from typing MCP secrets for an
    install that will not happen. An empty list puts no limit on harnesses.
    """
    if allow or not supported or harness in supported:
        return
    fail(
        ErrorCategory.VALIDATION,
        f"Agent {label} does not support {harness}; it supports {', '.join(supported)}.",
        operation="Pull agent",
        resource="harness",
        remediation=(
            f"Pull it for a supported harness (--harness {supported[0]}), or add --allow-unsupported-harness "
            "to install it anyway; some components may be missing or not loaded."
        ),
    )


def _target_version(
    *, requested: str | None, upgrade: bool, project_locked: dict | None, installed: dict | None
) -> tuple[str | None, str]:
    """The agent version a pull installs, and why. None means the latest approved."""
    if requested:
        return requested, "requested"
    if upgrade:
        return None, "upgrade"
    if project_locked and project_locked.get("version"):
        return str(project_locked["version"]), "project-lock"
    if installed and installed.get("version"):
        return str(installed["version"]), "installed"
    return None, "latest"


def _installed_components(lock: dict, planned: list[dict]) -> list[dict]:
    """Lockfile component entries from the server's install lock.

    Falls back to the planned components when the server returned no lock.
    """
    names = {component["id"]: component.get("name", "") for component in planned}
    entries = lock.get("components")
    if not isinstance(entries, list):
        return planned
    return [
        {
            "type": entry.get("type", "unknown"),
            "name": names.get(str(entry.get("id")), "") or entry.get("qualified_name", ""),
            "id": str(entry.get("id", "")),
            "version": entry.get("version"),
            "version_id": entry.get("version_id"),
            "digest": entry.get("digest"),
            "qualified_name": entry.get("qualified_name"),
            "source": entry.get("source"),
        }
        for entry in entries
        if isinstance(entry, dict)
    ]


def _record_hook_bindings(
    snippet: dict,
    lock_components: list[dict],
    target_dir: Path,
    is_user_scope: bool,
    *,
    gated: dict | None = None,
    settings_display: str = "",
) -> list[str]:
    """Record where each pinned hook was written, so a later snapshot can verify it.

    ``hook_command`` and ``hook_event`` are exactly what the agent file contains
    (the harness records the command verbatim when the hook runs). The agent
    file and script are stored as layer display paths; ``hook_integrity`` is the
    written script's fingerprint, or the command's when there is no script.

    ``gated`` (component local name -> ``agent_hooks.AgentHook``) marks hooks
    placed in ``settings_display`` behind the agent gate: their recorded command
    is the gated command (what Claude Code records), with the original kept so
    verification can require that the agent file no longer carries it.
    """
    import hashlib

    from observal_cli.agent_hooks import GATED_PLACEMENT
    from observal_cli.layer import skill_file_fingerprint

    bindings = {b.get("name"): b for b in snippet.get("hook_bindings") or [] if isinstance(b, dict)}
    profile = snippet.get("agent_profile") or {}
    profile_path = profile.get("path") if isinstance(profile, dict) else None
    config = ""
    if isinstance(profile_path, str):
        config = (
            f"user:{profile_path[len('~/.claude/') :]}"
            if profile_path.startswith("~/.claude/")
            else f"project:{profile_path}"
        )
    warnings: list[str] = []
    for component in lock_components:
        if component.get("type") != "hook" or not component.get("local_name"):
            continue
        binding = bindings.get(component["local_name"])
        if not binding or not config:
            continue  # HTTP or unbound hooks stay unverified
        script = binding.get("script")
        if script:
            fingerprint = skill_file_fingerprint(_resolve_path(script, target_dir, allow_home=is_user_scope))
            if not fingerprint:
                warnings.append("Installed hook script could not be fingerprinted; its presence will be unverified.")
                continue
        else:
            fingerprint = f"sha256-{hashlib.sha256(binding['command'].encode()).hexdigest()}"
        for key in [key for key in component if key.startswith("hook_")]:
            del component[key]  # a stale placement from an earlier pull must not survive
        component.update(
            hook_event=binding["event"],
            hook_command=binding["command"],
            hook_agent=binding.get("agent") or "",
            hook_config=config,
            hook_script=f"project:{script}" if script else "",
            hook_integrity=fingerprint,
        )
        hook = (gated or {}).get(component["local_name"])
        if hook is not None:
            if hook.event != binding["event"] or hook.command != binding["command"]:
                raise ValueError("gated hook does not match its binding")
            component.update(
                hook_command=hook.gated,
                hook_config=settings_display,
                hook_placement=GATED_PLACEMENT,
                hook_original_command=hook.command,
                hook_agent_profile=config,
                hook_gate_python=sys.executable,
            )
    return warnings


def _fingerprint_written_skills(
    snippet: dict, lock_components: list[dict], target_dir: Path, is_user_scope: bool
) -> list[str]:
    """Record ``skill_integrity`` for each pinned skill from the SKILL.md this pull wrote.

    The written file (a Pi profile's copy, for example) is what a later session
    snapshot must match before the skill counts as verified-present. A skill
    whose file cannot be read is left without a fingerprint, so it stays
    unverified rather than guessed. Returns warnings for the caller to show.
    """
    from observal_cli.cmd_skill import _sanitize_name
    from observal_cli.layer import skill_file_fingerprint

    written = {}
    for sc in snippet.get("skill_components") or []:
        if not isinstance(sc, dict):
            continue
        if isinstance(sc.get("path"), str):
            written[_sanitize_name(sc.get("name", ""))] = _resolve_path(
                sc["path"], target_dir, allow_home=is_user_scope
            )
        elif isinstance(sc.get("installed_skill_md"), str):
            # Written where the installer chose (Claude Code: .agents/skills, linked into .claude/skills).
            written[_sanitize_name(sc.get("name", ""))] = Path(sc["installed_skill_md"])
    warnings: list[str] = []
    for component in lock_components:
        if component.get("type") != "skill" or not component.get("local_name"):
            continue
        path = written.get(component["local_name"])
        fingerprint = skill_file_fingerprint(path) if path else None
        if fingerprint:
            component["skill_integrity"] = fingerprint
        else:
            warnings.append("Installed skill could not be fingerprinted; its presence will be unverified.")
    return warnings


def _progress(output: OutputMode | str, message: str | None = None):
    return nullcontext() if output == "json" else spinner(message)


def _pull_failure_result(
    written: list[tuple[str, str]],
    stage: str,
    *,
    setup_results: list[dict] | None = None,
    **state: object,
) -> dict:
    """Build a secret-free description of pull side effects completed before failure."""
    result: dict[str, object] = {
        "partial": bool(written) and not bool(state.get("dry_run")),
        "stage": stage,
        "files": [{"path": path, "status": status} for path, status in written],
    }
    if setup_results is not None:
        result["setup_commands"] = [
            {
                "executable": str(item.get("command", [""])[0]) if item.get("command") else "",
                "status": item.get("status"),
                "return_code": item.get("return_code"),
            }
            for item in setup_results
        ]
    result.update(state)
    return result


def _valid_setup_command(command: object) -> bool:
    return isinstance(command, list) and bool(command) and all(isinstance(argument, str) for argument in command)


# Setup-command flags whose value carries an MCP header or environment value.
_MASKED_SETUP_FLAGS = {"-H": ": ", "--header": ": ", "-e": "=", "--env": "="}


def _redact_url(argument: str) -> str:
    """Drop userinfo and query string from a URL argument; they can carry credentials."""
    parts = urlsplit(argument)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return argument
    host = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, host, parts.path, "<redacted>" if parts.query else "", ""))


def _display_setup_command(command: list[str]) -> list[str]:
    """Return a setup command with header, environment and URL credentials masked for output."""
    shown: list[str] = []
    separator = None
    for argument in command:
        if argument == "--":
            separator = None
        elif separator is not None and separator in argument:
            argument = f"{argument.split(separator, 1)[0]}{separator}<secret>"
        else:
            separator = _MASKED_SETUP_FLAGS.get(argument)
            argument = _redact_url(argument)
        shown.append(argument)
    return shown


def _run_reset_commands(commands: list, *, cwd: Path | None, adapter, scope: str) -> None:
    """Best-effort cleanup before setup commands; a failure means nothing was there to reset."""
    for command in commands:
        if not _valid_setup_command(command):
            continue
        try:
            native_command = adapter.prepare_mcp_setup_command(command, scope)
            subprocess.run(native_command, capture_output=True, text=True, timeout=60, cwd=cwd)
        except (OSError, subprocess.SubprocessError):
            continue


def _parse_assignments(values: list[str] | None, label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in values or []:
        key, separator, value = item.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if not separator or not key or not value:
            fail(
                ErrorCategory.VALIDATION,
                f"Invalid {label} assignment.",
                operation="Pull agent",
                resource=label,
                remediation=f"Use {label}=VALUE with a non-empty name and value.",
            )
        parsed[key] = value
    return parsed


def _validate_pull_inputs(harness: str, scope: str | None, version: str | None) -> tuple[str, str | None, str | None]:
    harness = harness.strip().lower()
    if harness not in VALID_HARNESSES:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown harness: {harness}.",
            operation="Pull agent",
            resource="target harness",
            remediation=f"Choose from: {', '.join(VALID_HARNESSES)}.",
        )
    if scope is not None:
        scope = scope.strip().lower()
        if scope not in {"project", "user"}:
            fail(
                ErrorCategory.VALIDATION,
                f"Unknown install scope: {scope}.",
                operation="Pull agent",
                resource="install scope",
                remediation="Choose project or user.",
            )
        if harness not in _SCOPE_AWARE_HARNESSES:
            fail(
                ErrorCategory.VALIDATION,
                f"Harness {harness} does not support an explicit install scope.",
                operation="Pull agent",
                resource="install scope",
                remediation="Remove --scope for this harness.",
            )
    if version is not None:
        try:
            version = str(Version(version))
        except InvalidVersion:
            fail(
                ErrorCategory.VALIDATION,
                f"Invalid semantic version: {version}.",
                operation="Pull agent",
                resource="agent version",
                remediation="Use a semantic version such as 1.2.3.",
            )
    return harness, scope, version


def _parse_model_overrides(values: list[str]) -> tuple[str | None, dict[str, str]]:
    """Parse one or more ``--model`` flags.

    Two grammars are accepted:

    * ``--model <value>`` - applies to the harness selected for this pull.
    * ``--model <harness>=<value>`` - explicit per-harness override (advanced; lets
      a single command target a specific harness without ambiguity).

    Returns ``(default_value, per_harness_overrides)``.
    """
    optic.trace("values={}", values)
    default: str | None = None
    overrides: dict[str, str] = {}
    for raw in values or []:
        if "=" in raw:
            harness_key, _, val = raw.partition("=")
            harness_key = harness_key.strip().lower()
            val = val.strip()
            if harness_key not in VALID_HARNESSES or not val:
                fail(
                    ErrorCategory.VALIDATION,
                    f"Invalid model override: {raw}.",
                    operation="Pull agent",
                    resource="model override",
                    remediation="Use MODEL or HARNESS=MODEL with a registered harness.",
                )
            overrides[harness_key] = val
        elif raw.strip():
            default = raw.strip()
        else:
            fail(
                ErrorCategory.VALIDATION,
                "Model override cannot be empty.",
                operation="Pull agent",
                resource="model override",
                remediation="Provide a model ID or remove the empty --model option.",
            )
    return default, overrides


def _agent_saved_model(agent_detail: dict | None, harness: str) -> str | None:
    """Return the model the agent has saved for a harness, if any.

    Per-harness override wins; otherwise the legacy ``model_name`` is used as
    the implicit default for Claude Code only. Mirrors the server-side
    server resolver rules so the CLI never re-prompts when the author has
    already chosen a model.
    """
    ensure_loaded()
    return get_adapter(harness).saved_model(agent_detail)


def _collect_install_options(
    harness: str,
    *,
    scope: str | None,
    model_default: str | None,
    model_overrides: dict[str, str],
    tools: str | None,
    no_prompt: bool,
    refresh_models: bool = False,
    agent_detail: dict | None = None,
    quiet: bool = False,
) -> dict:
    """Interactively collect harness-specific install options.

    Honors explicit ``--scope``/``--model``/``--tools`` flags; only prompts for
    what's missing when running in an interactive terminal and ``--no-prompt``
    isn't set. The model picker consults the registry-backed harness model data.

    When the agent already has a saved model for the target harness (set in the
    builder) and the user didn't pass ``--model``, the saved value is used
    silently - the picker is skipped so authoring decisions aren't undone
    by a stray Enter at the prompt.
    """
    optic.trace("harness={}", harness)
    import sys

    from observal_cli.render import format_model as _format_model
    from observal_shared.harness_registry import get_default_scope, has_model_selection

    opts: dict = {}
    interactive = sys.stdin.isatty() and not no_prompt

    if harness in _SCOPE_AWARE_HARNESSES:
        default_scope = get_default_scope(harness)
        if scope:
            opts["scope"] = scope
        elif interactive:
            project_label, user_label = _SCOPE_AWARE_HARNESSES[harness]
            labels = {"project": project_label, "user": user_label}
            choice = select_one("  Scope", [user_label, project_label], default=labels.get(default_scope, user_label))
            opts["scope"] = "user" if choice.startswith("user") else "project"
        else:
            opts["scope"] = default_scope

    if has_model_selection(harness):
        explicit = model_overrides.get(harness) or model_default
        saved = _agent_saved_model(agent_detail, harness)
        if explicit:
            opts["model"] = explicit
        elif saved:
            try:
                primary, _secondary, _ = _format_model({"model_id": saved})
                pretty = primary or saved
            except (KeyError, TypeError, ValueError):
                pretty = saved
            if not quiet:
                rprint(f"  [dim]Model:[/dim] {esc(pretty)} [dim](from agent)[/dim]")
            # Pass through the saved value so the server records the same
            # choice on the install download record. The resolver still
            # validates the candidate against the harness registry and falls
            # back gracefully if needed.
            opts["model"] = saved
        elif interactive:
            from observal_cli import model_catalog as _catalog

            catalog = _catalog.fetch_catalog(refresh=refresh_models)
            choices = _catalog.model_choices_for_picker(catalog, harness)
            choice_labels = [c[0] for c in choices] if choices else []
            choice_labels = ["auto (let the harness decide)", *choice_labels]
            picked = select_one("  Model", choice_labels, default="auto (let the harness decide)")
            if picked.startswith("auto"):
                opts["model"] = ""
            else:
                for label, model_id in choices:
                    if label == picked:
                        opts["model"] = model_id
                        break

    ensure_loaded()
    get_adapter(harness).apply_install_options(opts, tools)
    return opts


# Server-emitted launchers name a bare python3, which rarely imports an installed
# observal_cli but does import one from the working directory (often a project).
# They are rewritten to this CLI's interpreter, isolated with -I when it imports
# observal_cli on its own, else with -P and an explicit PYTHONPATH set to the
# package root (observal_cli.shared.launcher). On Windows they use the shell-neutral
# form instead: a quoted interpreter with -I, and the package root set from -c code.
_BARE_LAUNCHER = re.compile(r"(?<![/\\\w.-])python3? -m observal_cli\.")
_BARE_LAUNCHER_MODULE = re.compile(r"(?<![/\\\w.-])python3? -m (observal_cli(?:\.[A-Za-z_][A-Za-z0-9_]*)+)")


def rewrite_launcher_command(command: str) -> str:
    """Rewrite bare ``python3 -m observal_cli.`` launchers inside one shell command string."""
    from observal_cli.shared.launcher import isolation_flag, posix_prefix, windows_module_command

    if sys.platform == "win32":
        return _BARE_LAUNCHER_MODULE.sub(lambda match: windows_module_command(match[1]), command)
    return _BARE_LAUNCHER.sub(lambda _m: f"{posix_prefix()} {isolation_flag()} -m observal_cli.", command)


def rewrite_launchers_in_strings(value):
    """Apply ``rewrite_launcher_command`` to every string inside JSON-like content (never to its text)."""
    if isinstance(value, dict):
        return {key: rewrite_launchers_in_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite_launchers_in_strings(item) for item in value]
    return rewrite_launcher_command(value) if isinstance(value, str) else value


def _hook_commands(hooks: object) -> list:
    """Every ``command`` value inside a Claude Code-style ``hooks`` mapping."""
    found = []
    for groups in hooks.values() if isinstance(hooks, dict) else ():
        for group in groups if isinstance(groups, list) else ():
            for hook in (group.get("hooks") or []) if isinstance(group, dict) else ():
                if isinstance(hook, dict) and "command" in hook:
                    found.append(hook)
    return found


_COMMAND_LINE = re.compile(r"^(?P<lead>\s*(?:-\s+)?command:\s*)(?P<value>.+?)\s*$")


_MCP_SERVER_KEYS = ("mcp-servers", "mcpServers")


def _frontmatter_mcp_launcher(entry: object) -> dict | None:
    """The rewritten MCP entry if it launches observal_cli through a bare interpreter, else None.

    The fallback package root goes into the argv (``env PYTHONPATH=...``, or
    ``-I -c`` code on Windows), not an ``env:`` key, so it does not depend on the
    harness reading one.
    """
    if not isinstance(entry, dict):
        return None
    argv = [entry.get("command"), *(entry.get("args") or [])] if isinstance(entry.get("args") or [], list) else []
    if not argv or not all(isinstance(item, str) for item in argv):
        return None
    rewritten = rewrite_observal_interpreter(argv)
    if rewritten == argv:
        return None
    return {**entry, "command": rewritten[0], "args": rewritten[1:]}


def _top_level_block(lines: list[str], key: str) -> tuple[int, int] | None:
    """Line range of a top-level ``key:`` mapping and its indented children."""
    for start, line in enumerate(lines):
        if line.rstrip() == f"{key}:":
            end = start + 1
            while end < len(lines) and (not lines[end].strip() or lines[end][:1] in (" ", "\t")):
                end += 1
            return start, end
    return None


def _rewrite_mcp_block(lines: list[str], key: str, servers: dict) -> list[str]:
    """Re-emit only the changed server entries of a ``key:`` block, values as JSON (valid YAML)."""
    span = _top_level_block(lines, key)
    if span is None:
        return lines
    start, end = span
    out = lines[: start + 1]
    index = start + 1
    while index < end:
        line = lines[index]
        indent = len(line) - len(line.lstrip(" "))
        name = line.strip()[:-1] if line.strip().endswith(":") else None
        block_end = index + 1
        while block_end < end and (
            not lines[block_end].strip() or len(lines[block_end]) - len(lines[block_end].lstrip(" ")) > indent
        ):
            block_end += 1
        replacement = servers.get(name) if name is not None else None
        if replacement is None:
            out.extend(lines[index:block_end])
        else:
            out.append(line)
            out.extend(
                f"{' ' * (indent + 2)}{field}: {json.dumps(value, ensure_ascii=False)}"
                for field, value in replacement.items()
            )
        index = block_end
    return out + lines[end:]


def rewrite_frontmatter_hook_launchers(text: str) -> str:
    """Rewrite bare observal_cli launchers in a profile's YAML frontmatter, and nowhere else.

    Only hook ``command`` values and MCP server entries (``mcp-servers`` /
    ``mcpServers``) that launch ``observal_cli`` are rewritten; the body and
    every other value are left alone. Rewritten values are written as JSON,
    which is valid YAML for any interpreter path. If the result does not parse
    to exactly the original with only those values changed, the profile is
    returned unchanged.
    """
    import yaml

    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end == -1:
        return text
    head, rest = text[4:end], text[end:]
    try:
        original = yaml.safe_load(head)
    except yaml.YAMLError:
        return text
    if not isinstance(original, dict):
        return text
    expected = json.loads(json.dumps(original))
    targets = {hook["command"] for hook in _hook_commands(original.get("hooks")) if isinstance(hook["command"], str)}
    targets = {command for command in targets if rewrite_launcher_command(command) != command}
    servers: dict[str, dict[str, dict]] = {}
    for key in _MCP_SERVER_KEYS:
        block = original.get(key)
        for name, entry in block.items() if isinstance(block, dict) else ():
            replacement = _frontmatter_mcp_launcher(entry)
            if replacement is not None and isinstance(name, str):
                servers.setdefault(key, {})[name] = replacement
                expected[key][name] = json.loads(json.dumps(replacement))
    if not targets and not servers:
        return text
    lines = head.split("\n")
    for index, line in enumerate(lines):
        match = _COMMAND_LINE.match(line)
        if not match:
            continue
        try:
            value = yaml.safe_load(match["value"])
        except yaml.YAMLError:
            continue
        if value in targets:
            lines[index] = match["lead"] + json.dumps(rewrite_launcher_command(value), ensure_ascii=False)
    for key, replacements in servers.items():
        lines = _rewrite_mcp_block(lines, key, replacements)
    new_head = "\n".join(lines)
    try:
        rewritten = yaml.safe_load(new_head)
    except yaml.YAMLError:
        return text
    for hook in _hook_commands(expected.get("hooks")):
        if hook["command"] in targets:
            hook["command"] = rewrite_launcher_command(hook["command"])
    if rewritten != expected:
        return text
    return "---\n" + new_head + rest


def rewrite_observal_interpreter(value):
    """Point server-emitted ``python3 -m observal_cli.<module>`` launchers at this CLI's interpreter.

    Handles MCP entries (``command`` + ``args``) and argv lists such as
    ``claude mcp add`` setup commands. A bare ``python3`` rarely has
    ``observal_cli`` importable when the CLI was installed with uv or pipx.
    The rewritten launcher runs with ``-P``, and with ``PYTHONPATH`` (an MCP
    entry's ``env``, or ``env PYTHONPATH=...`` in argv) when needed.
    """
    from observal_cli.shared.launcher import isolation_flag, pythonpath_env, windows_module_argv

    extra_env = pythonpath_env()
    flag = isolation_flag()
    if isinstance(value, dict):
        out = {key: rewrite_observal_interpreter(item) for key, item in value.items()}
        args = out.get("args")
        if (
            out.get("command") in ("python3", "python")
            and isinstance(args, list)
            and len(args) >= 2
            and args[0] == "-m"
            and str(args[1]).startswith("observal_cli.")
        ):
            out["command"] = sys.executable
            out["args"] = [flag, *args]
            if extra_env:
                env = out.get("env") if isinstance(out.get("env"), dict) else {}
                out["env"] = {**env, **extra_env}
        return out
    if isinstance(value, list):
        items = [rewrite_observal_interpreter(item) for item in value]
        index = 0
        while index < len(items) - 2:
            if (
                items[index] in ("python3", "python")
                and items[index + 1] == "-m"
                and isinstance(items[index + 2], str)
                and items[index + 2].startswith("observal_cli.")
            ):
                if sys.platform == "win32":
                    # No `env` program on Windows: -I, with the package root set from -c when needed.
                    launcher = windows_module_argv(items[index + 2])
                    items[index : index + 3] = launcher
                    index += len(launcher) - 1
                else:
                    prefix = [f"{key}={val}" for key, val in extra_env.items()]
                    launcher = [*(["env", *prefix] if prefix else []), sys.executable, flag]
                    items[index : index + 1] = launcher
                    index += len(launcher) - 1
            index += 1
        return items
    return value


def _localize_hooks_content(content, adapter, agent_id: str):
    """A hooks config with resolved hook script paths and launchers on this CLI's interpreter."""
    if isinstance(content, str):
        return _pin_hook_interpreter(_resolve_hook_paths(content))
    if isinstance(content, dict):
        # Resolve hook paths inside JSON content (command fields)
        raw = _resolve_hook_paths(json.dumps(content))
        # Launchers are rewritten in the parsed strings, so no interpreter path can break the JSON.
        return adapter.rewrite_hooks(rewrite_launchers_in_strings(json.loads(raw)), agent_id=agent_id)
    return content


def _localize_agent_profile(agent_profile: dict, adapter, agent_id: str) -> None:
    """Point an agent profile's Observal launchers at this CLI's interpreter, in place."""
    if isinstance(agent_profile.get("content"), dict):
        agent_profile["content"] = adapter.rewrite_agent_profile(agent_profile["content"], agent_id=agent_id)
    elif isinstance(agent_profile.get("content"), str):
        # Claude Code and other markdown agents carry their hooks in frontmatter. Isolated
        # launchers first; any launcher that rewrite declines (a YAML block scalar, say)
        # is then pinned to this interpreter rather than left bare.
        agent_profile["content"] = _pin_agent_profile_hooks(
            rewrite_frontmatter_hook_launchers(_resolve_hook_paths(agent_profile["content"]))
        )


def localize_install_snippet(snippet: dict, *, adapter, agent_id: str) -> dict:
    """An agent install snippet as ``agent pull`` would write it, without writing anything.

    The server cannot know which interpreter has ``observal_cli`` installed, so
    it names a bare ``python3`` (``python`` on Windows) in telemetry hooks and
    in the sandbox and delegation MCP servers. A snippet printed for the user
    to copy (``observal agent install``) gets the same rewrites a pull applies:
    this CLI's interpreter with its isolation flag, resolved hook script paths
    and the harness adapter's hook and profile rewrites. The input is not
    modified.
    """
    import copy

    if not isinstance(snippet, dict):
        return snippet
    localized = rewrite_observal_interpreter(copy.deepcopy(snippet))
    hooks_cfg = localized.get("hooks_config")
    if isinstance(hooks_cfg, dict) and "content" in hooks_cfg:
        hooks_cfg["content"] = _localize_hooks_content(hooks_cfg["content"], adapter, agent_id)
    agent_profile = localized.get("agent_profile")
    if isinstance(agent_profile, dict):
        _localize_agent_profile(agent_profile, adapter, agent_id)
    return localized


def _preflight_existing_managed_skill_paths(
    snippet: dict,
    *,
    harness: str,
    target_dir: Path,
    is_user_scope: bool,
) -> None:
    """Legacy skill/config writers must not touch *any* recorded folder owner."""
    from observal_cli import lockfile, managed_skill
    from observal_cli.cmd_skill import _sanitize_name, _user_skill_dest

    data = lockfile.read_lockfile()
    receipts = [
        component["folder_receipt"]
        for registry in data.get("registries", {}).values()
        for section in registry.get("harnesses", {}).values()
        for owner in [*section.get("agents", []), *section.get("standalone", [])]
        for component in ([*owner.get("components", [])] if "components" in owner else [owner])
        if isinstance(component.get("folder_receipt"), dict)
    ]
    if not receipts:
        return
    paths: list[Path] = []
    for key in ("mcp_config", "hooks_config", "agent_profile", "steering_file", "hook_files", "prompt_files", "skills"):
        entry = snippet.get(key)
        for item in entry if isinstance(entry, list) else [entry]:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                paths.append(_resolve_path(item["path"], target_dir, allow_home=is_user_scope))
    for component in snippet.get("skill_components") or []:
        if component.get("bundle_version_id"):
            continue  # Managed bundle preflight performs its own ownership check.
        if component.get("path"):
            paths.append(_resolve_path(component["path"], target_dir, allow_home=is_user_scope).parent)
        else:
            name = _sanitize_name(component.get("name", "skill"))
            paths.append(_user_skill_dest(harness, name) if is_user_scope else target_dir / ".agents/skills" / name)
    for path in paths:
        candidate = path.absolute()
        for proof in receipts:
            root = Path(proof.get("target", "")).absolute()
            if candidate.is_relative_to(root) or candidate.resolve().is_relative_to(root.resolve()):
                raise managed_skill.ManagedSkillError(
                    f"Agent config or legacy skill targets a verified folder at {root}; no files were written"
                )


def _managed_agent_folders(
    snippet: dict,
    bundles: list[dict],
    lock: dict,
    *,
    harness: str,
    target_dir: Path,
    is_user_scope: bool,
    agent_id: str,
    agent_version: str,
    registry_url: str,
    allow_agent_pin_change: bool = False,
) -> list[tuple[dict, Path, dict, dict]]:
    """Validate every pinned destination and ownership before any Agent file write.

    The shared transaction's check runs under its per-target lock. It refuses
    receipt-less existing folders and foreign owners; never infer ownership
    from a matching SKILL.md or from the project pin alone.
    """
    from observal_cli import managed_skill
    from observal_cli.skill_folder import BundleValidationError, validate_bundle

    components = snippet.get("skill_components") or []
    pins = {str(c.get("id")): c for c in lock.get("components", []) if c.get("type") == "skill"}
    if lock.get("status") != "locked" or lock.get("problems"):
        raise managed_skill.ManagedSkillError("Agent skill pins are degraded; no files were written")
    by_id = {}
    for bundle in bundles:
        version_id = str(bundle.get("version_id", ""))
        pin = pins.get(str(bundle.get("listing_id")))
        if not version_id or version_id in by_id or not pin or str(pin.get("version_id")) != version_id:
            raise managed_skill.ManagedSkillError("Duplicate or unpinned Agent skill folder")
        try:
            by_id[version_id] = validate_bundle(
                bundle, expected_version_id=version_id, expected_digest=pin.get("digest")
            )
        except BundleValidationError as exc:
            raise managed_skill.ManagedSkillError(f"Invalid pinned Agent skill folder: {exc}") from exc
    requested = [str(c["bundle_version_id"]) for c in components if c.get("bundle_version_id")]
    if len(requested) != len(by_id) or set(requested) != set(by_id):
        raise managed_skill.ManagedSkillError("Missing, duplicate or unexpected Agent skill folder bundles")
    planned = []
    seen: set[str] = set()
    scope = "user" if is_user_scope else "project"
    for component in components:
        version_id = str(component.get("bundle_version_id") or "")
        if not version_id:
            continue
        bundle = by_id[version_id]
        if component.get("path") != bundle.skill_file_path:
            raise managed_skill.ManagedSkillError("Pinned skill folder path differs from Agent snippet")
        raw_path = component["path"]
        lexical = (
            Path(raw_path).expanduser()
            if is_user_scope and raw_path.startswith(("~/", "~\\"))
            else target_dir / raw_path.removeprefix("~/")
        )
        if any(part.is_symlink() for part in (lexical, *lexical.parents)):
            raise managed_skill.ManagedSkillError("Symlink in Agent skill destination hierarchy")
        destination = _resolve_path(raw_path, target_dir, allow_home=is_user_scope)
        target = destination.parent.absolute()
        if destination.name != "SKILL.md" or str(target).casefold() in seen:
            raise managed_skill.ManagedSkillError("Duplicate or invalid Agent skill folder destination")
        seen.add(str(target).casefold())
        proof = managed_skill.receipt(
            bundle,
            target,
            harness,
            scope,
            registry_url=registry_url,
            source="agent",
            agent_id=agent_id,
            agent_version=agent_version,
        )
        # Check all destinations, including backup location and previous receipts,
        # before writing any folder, MCP, profile, hook or project pin.
        managed_skill.transact(
            bundle,
            target,
            proof,
            record=lambda _data: None,
            check=True,
            allow_agent_pin_change=allow_agent_pin_change,
        )
        planned.append((bundle, target, proof, pins[str(bundle.listing_id)]))

    # A config write aimed inside a verified folder would invalidate its receipt
    # immediately after installation. Reject the whole plan before the first write.
    nonfolder = [snippet.get(key) for key in ("mcp_config", "hooks_config", "agent_profile", "steering_file")]
    nonfolder.extend(snippet.get(key) or [] for key in ("hook_files", "prompt_files", "skills"))
    for entry in nonfolder:
        for item in entry if isinstance(entry, list) else [entry]:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            path = _resolve_path(item["path"], target_dir, allow_home=is_user_scope)
            if any(path.is_relative_to(target) for _, target, _, _ in planned):
                raise managed_skill.ManagedSkillError("Agent config overlaps a pinned skill folder destination")
    return planned


def _record_managed_agent_component(
    data: dict,
    *,
    harness: str,
    scope: str,
    directory: Path,
    agent_id: str,
    agent_version: str,
    component: dict,
    proof: dict,
    allow_agent_pin_change: bool = False,
) -> None:
    from observal_cli.lockfile import _ensure_harness, _find_agent_idx

    registry = data.setdefault("registries", {}).setdefault(
        proof["registry_url"], {"server_url": proof["registry_url"], "harnesses": {}}
    )
    section = _ensure_harness(registry, harness)
    agents = section["agents"]
    index = _find_agent_idx(agents, agent_id, scope, str(directory))
    if index is None:
        agent = {
            "id": agent_id,
            "version": agent_version,
            "scope": scope,
            "directory": str(directory),
            "components": [],
        }
        agents.append(agent)
    else:
        agent = agents[index]
        if agent.get("version") != agent_version and not allow_agent_pin_change:
            raise RuntimeError("Agent pin changed during managed folder installation")
    matches = [
        c for c in agent.setdefault("components", []) if c.get("type") == "skill" and c.get("id") == component["id"]
    ]
    if len(matches) > 1:
        raise RuntimeError("Ambiguous Agent skill ownership in machine lockfile")
    if matches:
        matches[0].update({**component, "folder_receipt": proof})
    else:
        agent["components"].append({**component, "folder_receipt": proof})


def _sync_managed_agent_lock(
    *,
    harness: str,
    scope: str,
    directory: Path,
    agent_id: str,
    agent_version: str,
    metadata: dict,
    components: list[dict],
    proofs: list[dict],
) -> None:
    """Update ordinary Agent metadata without discarding transaction receipts."""
    from observal_cli import lockfile

    registry_url = lockfile.current_registry_url()

    def update(data: dict) -> None:
        section = lockfile._ensure_harness(
            data.setdefault("registries", {}).setdefault(registry_url, {"server_url": registry_url, "harnesses": {}}),
            harness,
        )
        agents = section["agents"]
        index = lockfile._find_agent_idx(agents, agent_id, scope, str(directory))
        old = agents[index] if index is not None else {}
        old_managed = {
            c.get("id"): c["folder_receipt"]["target"]
            for c in old.get("components", [])
            if c.get("type") == "skill" and c.get("folder_receipt")
        }
        new_managed = {proof["listing_id"]: proof["target"] for proof in proofs}
        if any(new_managed.get(listing_id) != target for listing_id, target in old_managed.items()):
            raise RuntimeError("Agent upgrade would leave a managed skill folder without its ownership receipt")
        new_components = []
        for component in components:
            matches = [p for p in proofs if p["listing_id"] == component.get("id")]
            if matches:
                current = [
                    c
                    for c in old.get("components", [])
                    if c.get("id") == component.get("id") and c.get("type") == "skill"
                ]
                if len(current) != 1 or current[0].get("folder_receipt") != matches[0]:
                    raise RuntimeError("Managed Agent receipt changed during pull")
                new_components.append({**component, "folder_receipt": matches[0]})
            else:
                new_components.append(component)
        entry = {**metadata, "components": new_components}
        if index is None:
            agents.append(entry)
        else:
            agents[index] = entry

    lockfile.update_lockfile(update)


def _restore_prior_managed_agent_entry(
    *,
    harness: str,
    scope: str,
    directory: Path,
    agent_id: str,
    previous: dict | None,
    expected_version: str,
) -> None:
    """After guarded folder rollback, restore prior machine pin under its lock."""
    from observal_cli import lockfile

    def restore(data: dict) -> None:
        registry = data.get("registries", {}).get(lockfile.current_registry_url(), {})
        section = registry.get("harnesses", {}).get(harness, {})
        agents = section.get("agents", [])
        index = lockfile._find_agent_idx(agents, agent_id, scope, str(directory))
        if index is None:
            if previous is not None:
                raise RuntimeError("Managed Agent lock entry disappeared during rollback")
            return
        current = agents[index]
        if current.get("version") != expected_version:
            raise RuntimeError("Managed Agent lock entry changed during rollback")
        if previous is None:
            if any(c.get("folder_receipt") for c in current.get("components", [])):
                raise RuntimeError("Managed Agent still owns a folder after rollback")
            agents.pop(index)
        else:
            previous_receipts = {
                c.get("id"): c.get("folder_receipt") for c in previous.get("components", []) if c.get("folder_receipt")
            }
            current_receipts = {
                c.get("id"): c.get("folder_receipt") for c in current.get("components", []) if c.get("folder_receipt")
            }
            if current_receipts != previous_receipts:
                raise RuntimeError("Managed Agent folder receipt changed during rollback")
            agents[index] = previous

    lockfile.update_lockfile(restore)


def _rollback_managed_agent_folders(applied: list[tuple[dict, dict]]) -> list[str]:
    """Best-effort guarded rollback; never delete a locally modified tree."""
    import shutil

    from observal_cli import lockfile, managed_skill

    errors = []
    for proof, outcome in reversed(applied):
        if outcome["action"] == "unchanged":
            continue
        target = Path(proof["target"])
        try:
            if outcome.get("backup_id") and outcome.get("backup"):
                managed_skill.restore_backup(outcome["backup_id"])
            else:
                key = __import__("hashlib").sha256(str(target).encode()).hexdigest()
                with lockfile._exclusive_lock(lockfile.CONFIG_DIR / f"skill-{key}.lock"):
                    managed_skill.verify_tree(target, proof)

                    def remove(
                        data: dict,
                        target: Path = target,
                        proof: dict = proof,
                        missing_prior: dict | None = outcome.get("missing_prior"),
                    ) -> None:
                        matches = managed_skill._records(data, target)
                        if len(matches) != 1 or matches[0][1].get("folder_receipt") != proof:
                            raise RuntimeError("Receipt changed; cannot remove initial folder")
                        if missing_prior is not None:
                            matches[0][1]["folder_receipt"] = missing_prior
                        else:
                            matches[0][1].pop("folder_receipt")

                    lockfile.update_lockfile(remove)
                    shutil.rmtree(target)
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(f"{target}: {exc}; keep backup and inspect before retrying")
    return errors


def _rollback_managed_agent_state(
    applied: list[tuple[dict, dict]],
    *,
    harness: str,
    scope: str,
    directory: Path,
    agent_id: str,
    agent_version: str,
    previous: dict | None,
) -> list[str]:
    """Undo receipts and the intermediate Agent entry, not just the folders."""
    errors = _rollback_managed_agent_folders(applied)
    if errors or not applied:
        return errors
    try:
        _restore_prior_managed_agent_entry(
            harness=harness,
            scope=scope,
            directory=directory,
            agent_id=agent_id,
            previous=previous,
            expected_version=str(previous["version"]) if previous else agent_version,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        errors.append(f"Agent lock entry could not be restored: {exc}; inspect before retrying")
    return errors


def _preflight_project_mcp_config(snippet: dict, *, harness: str, is_user_scope: bool) -> None:
    """Do not silently relocate a user-only MCP config into an unread project path."""
    spec = HARNESS_REGISTRY[harness]
    if is_user_scope or spec["mcp_install_mode"] != "user_only":
        return
    config = snippet.get("mcp_config")
    if isinstance(config, dict) and config.get("path") and config.get("content"):
        name = spec["display_name"]
        fail(
            ErrorCategory.VALIDATION,
            f"{name} MCP extensions require a user-level config; project scope cannot install them.",
            operation="Pull agent",
            resource=f"{name} MCP configuration",
            remediation="Pull with --scope user, or use an Agent without MCP extensions for project scope.",
        )


def write_install_snippet(
    snippet: dict,
    *,
    harness: str,
    adapter,
    target_dir: Path,
    agent_id: str,
    is_user_scope: bool,
    dry_run: bool = False,
    quiet: bool = False,
    skill_bundles: list[dict] | None = None,
    lock: dict | None = None,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Write every file an agent install snippet carries under *target_dir*.

    Shared by ``observal agent pull`` and delegation, which materializes an
    agent into a throwaway worktree. Returns ``(written, failed_skills)``;
    setup commands and install tracking stay with the caller.

    When *skill_bundles* is provided (complete folder installs), bundles are
    matched with skill_components by bundle_version_id and installed using
    the folder bundle installer. Legacy components without a matching bundle
    fall back to git or single-file registry_direct installs.
    """
    _preflight_project_mcp_config(snippet, harness=harness, is_user_scope=is_user_scope)
    written: list[tuple[str, str]] = []  # (path, status)

    def tracked_write(
        path: Path,
        content: str | dict,
        *,
        merge_mcp: bool = False,
        requested_toml_path: Path | None = None,
        allowed_toml_dir: Path | None = None,
    ) -> str:
        try:
            if requested_toml_path is None and allowed_toml_dir is None:
                return _write_file_checked(path, content, merge_mcp=merge_mcp)
            return _write_file_checked(
                path,
                content,
                merge_mcp=merge_mcp,
                requested_toml_path=requested_toml_path,
                allowed_toml_dir=allowed_toml_dir,
            )
        except CliError as error:
            if error.result is None:
                error.result = _pull_failure_result(written, "write_files", failed_path=str(path))
            elif written:
                error.result["files"] = [
                    *({"path": name, "status": status} for name, status in written),
                    *error.result.get("files", []),
                ]
                error.result["partial"] = True
            raise

    def tracked_skill_install(skill_name: str, installer, **kwargs):
        try:
            with redirect_stdout(StringIO()) if quiet else nullcontext():
                return installer(**kwargs)
        except CliError as error:
            if error.result is None:
                error.result = _pull_failure_result(
                    written,
                    "install_skills",
                    failed_skills=[skill_name],
                    installation_tracked=False,
                )
            raise
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            fail(
                ErrorCategory.UNAVAILABLE,
                f"Failed to install agent skill: {skill_name}.",
                operation="Pull agent",
                resource="agent skills",
                remediation="Check skill source access and local filesystem permissions, then retry.",
                detail=repr(error),
                result=_pull_failure_result(
                    written,
                    "install_skills",
                    failed_skills=[skill_name],
                    installation_tracked=False,
                ),
            )

    # Preflight all complete folders and pins before writing any activation
    # config. A missing or duplicate bundle must never fall back to SKILL.md.
    from observal_cli.skill_folder import BundleValidationError, validate_bundle

    skill_components = snippet.get("skill_components") or []
    bundle_by_version_id: dict[str, dict] = {}
    skill_locks = {
        str(component.get("id")): component
        for component in (lock or {}).get("components", [])
        if component.get("type") == "skill" and component.get("id")
    }
    if skill_bundles and ((lock or {}).get("problems") or (lock or {}).get("status") != "locked"):
        fail(
            ErrorCategory.CONFLICT,
            "Agent skill pins are degraded.",
            operation="Pull agent",
            resource="agent lock",
            remediation="Resolve pinned component problems before installing complete folders.",
        )
    for bundle in skill_bundles or []:
        version_id = str(bundle.get("version_id", ""))
        component = skill_locks.get(str(bundle.get("listing_id", "")))
        if (
            not version_id
            or version_id in bundle_by_version_id
            or not component
            or str(component.get("version_id")) != version_id
        ):
            fail(
                ErrorCategory.CONFLICT,
                "Duplicate or unpinned skill folder bundle.",
                operation="Pull agent",
                resource="agent skills",
                remediation="Refresh the Agent pin and try again.",
            )
        try:
            validate_bundle(bundle, expected_version_id=version_id, expected_digest=component.get("digest"))
        except BundleValidationError as error:
            fail(
                ErrorCategory.VALIDATION,
                "Invalid pinned skill folder bundle.",
                operation="Pull agent",
                resource="agent skills",
                remediation="Ask the server owner to investigate.",
                detail=str(error),
            )
        bundle_by_version_id[version_id] = bundle
    expected_versions = {str(sc["bundle_version_id"]) for sc in skill_components if sc.get("bundle_version_id")}
    if expected_versions != set(bundle_by_version_id):
        fail(
            ErrorCategory.CONFLICT,
            "Required skill folder bundles are missing or unexpected.",
            operation="Pull agent",
            resource="agent skills",
            remediation="Refresh the Agent install response; do not install partial folders.",
        )
    from observal_cli.skill_folder import detect_destination_collisions

    planned_destinations: set[str] = set()
    for skill in skill_components:
        version_id = skill.get("bundle_version_id")
        if not version_id:
            continue
        validated = validate_bundle(bundle_by_version_id[str(version_id)])
        if skill.get("path") != validated.skill_file_path:
            fail(
                ErrorCategory.CONFLICT,
                "Pinned skill folder path differs from Agent snippet.",
                operation="Pull agent",
                resource="agent skills",
                remediation="Refresh the Agent installation; do not activate an inconsistent folder.",
            )
        destination = _resolve_path(skill["path"], target_dir, allow_home=is_user_scope)
        # Two not-yet-written bundles can target the same folder. Check the
        # entire plan before installing the first one or writing activation.
        destination_key = str(destination.parent).casefold()
        if destination_key in planned_destinations:
            fail(
                ErrorCategory.CONFLICT,
                "Multiple Agent skill bundles target one folder.",
                operation="Pull agent",
                resource=str(destination.parent),
                remediation="Repair the Agent's pinned skill list; no folder was installed.",
            )
        planned_destinations.add(destination_key)
        if destination.name != "SKILL.md" or detect_destination_collisions(destination.parent, validated):
            fail(
                ErrorCategory.CONFLICT,
                "Agent skill folder destination is already occupied or invalid.",
                operation="Pull agent",
                resource=str(destination.parent),
                remediation="Resolve the destination without deleting existing files, then retry.",
            )

    # Materialize required complete folders before enabling the agent profile,
    # hooks or MCP configuration. A later write may still report partial state,
    # but no missing required folder is activated as a successful Agent pull.
    installed_bundle_versions: set[str] = set()
    if not dry_run:
        for skill in skill_components:
            version_id = skill.get("bundle_version_id")
            if not version_id:
                continue
            bundle = bundle_by_version_id[str(version_id)]
            dest = _resolve_path(skill["path"], target_dir, allow_home=is_user_scope).parent
            installed = _install_skill_bundle(
                sc_name=str(skill.get("name") or "skill"),
                bundle=bundle,
                lock_component=skill_locks[str(bundle["listing_id"])],
                skill_dest=dest,
                target_dir=target_dir,
                is_user_scope=is_user_scope,
                harness=harness,
                quiet=quiet,
            )
            if installed is None:
                fail(
                    ErrorCategory.UNAVAILABLE,
                    "Required skill folder could not be installed.",
                    operation="Pull agent",
                    resource="agent skills",
                    remediation="Repair the destination or filesystem before retrying.",
                    result=_pull_failure_result(
                        written,
                        "install_skills",
                        failed_skills=[str(skill.get("name") or "skill")],
                        installation_tracked=False,
                    ),
                )
            installed_bundle_versions.add(str(version_id))
            written.append((str(installed), "installed (complete folder)"))

    # ── mcp_config with path key (Cursor/VSCode/Gemini) ─
    mcp_cfg = snippet.get("mcp_config")
    if mcp_cfg and isinstance(mcp_cfg, dict) and "path" in mcp_cfg:
        allowed_dir = target_dir.resolve()
        p = _resolve_path(mcp_cfg["path"], target_dir, allow_home=is_user_scope)
        if dry_run:
            written.append((str(p), "would write"))
        else:
            # Keep the lexical install path as a second identity: _resolve_path
            # already followed any config symlink to its validated target.
            requested = None
            if p.suffix == ".toml" and isinstance(mcp_cfg["content"], dict):
                raw = mcp_cfg["path"]
                requested = (
                    Path(raw).expanduser()
                    if is_user_scope and raw.startswith(("~/", "~\\"))
                    else target_dir / (raw[2:] if raw.startswith(("~/", "~\\")) else raw)
                )
            status = tracked_write(
                p,
                mcp_cfg["content"],
                merge_mcp=True,
                requested_toml_path=requested,
                allowed_toml_dir=None if is_user_scope and mcp_cfg["path"].startswith(("~/", "~\\")) else allowed_dir,
            )
            written.append((str(p), status))

    # ── hooks_config (Cursor/VSCode/Copilot/OpenCode/Gemini) ─
    hooks_cfg = snippet.get("hooks_config")
    if hooks_cfg and isinstance(hooks_cfg, dict) and "path" in hooks_cfg:
        p = _resolve_path(hooks_cfg["path"], target_dir, allow_home=is_user_scope)
        content = _localize_hooks_content(hooks_cfg["content"], adapter, agent_id)
        if dry_run:
            written.append((str(p), "would write"))
        else:
            status = tracked_write(p, content, merge_mcp=hooks_cfg.get("merge", False))
            written.append((str(p), status))

    # ── agent_profile (Kiro, Cursor) ────────────────────────
    agent_profile = snippet.get("agent_profile")
    if agent_profile:
        # Rewrite hook commands to use the current Python interpreter
        # so they work regardless of which directory Kiro is launched from.
        _localize_agent_profile(agent_profile, adapter, agent_id)
        agent_profile_allow_home = adapter.allow_home_agent_profile(is_user_scope)
        p = _resolve_path(agent_profile["path"], target_dir, allow_home=agent_profile_allow_home)
        if dry_run:
            written.append((str(p), "would write"))
        else:
            status = tracked_write(p, agent_profile["content"])
            written.append((str(p), status))

    # ── steering_file (Kiro) ───────────────────────────
    steering_file = snippet.get("steering_file")
    if steering_file:
        p = _resolve_path(steering_file["path"], target_dir, allow_home=is_user_scope)
        if dry_run:
            written.append((str(p), "would write"))
        else:
            status = tracked_write(p, steering_file["content"])
            written.append((str(p), status))

    # ── hook_files (script files from hook components) ─────
    hook_files = snippet.get("hook_files") or []
    for hf in hook_files:
        p = _resolve_path(hf["path"], target_dir, allow_home=is_user_scope)
        if dry_run:
            written.append((str(p), "would write"))
        else:
            status = tracked_write(p, hf["content"])
            written.append((str(p), status))
            if hf.get("executable"):
                import os

                try:
                    os.chmod(p, 0o755)
                except OSError as error:
                    fail(
                        ErrorCategory.UNAVAILABLE,
                        f"Could not mark generated hook executable: {p}.",
                        operation="Pull agent",
                        resource=str(p),
                        remediation="Check file ownership and permissions.",
                        detail=repr(error),
                        result=_pull_failure_result(written, "mark_hook_executable", failed_path=str(p)),
                    )

    # ── prompt_files (native Copilot .github/prompts/*.prompt.md) ─
    for pf in snippet.get("prompt_files") or []:
        p = _resolve_path(pf["path"], target_dir, allow_home=is_user_scope)
        if dry_run:
            written.append((str(p), "would write"))
        else:
            existed = p.exists()
            tracked_write(p, pf["content"])
            written.append((str(p), "updated" if existed else "created"))

    # ── Direct skill files ─────────────────────────
    for sf in snippet.get("skills") or []:
        p = _resolve_path(sf["path"], target_dir, allow_home=is_user_scope)
        if dry_run:
            written.append((str(p), "would write"))
        else:
            status = tracked_write(p, sf["content"])
            written.append((str(p), status))

    # ── Skills ────────────────────────────────────
    # Three install modes:
    #   1. skill_bundles present → complete folder bundle from server
    #   2. git_url present → clone full skill directory from git
    #   3. skill_md_content present (registry_direct) → write SKILL.md + optional script
    from observal_cli.cmd_skill import _sanitize_name, install_skill_from_git, install_skill_registry_direct

    failed_skills: list[str] = []
    scope_str = "user" if is_user_scope else "project"

    for sc in skill_components:
        sc_name = _sanitize_name(sc.get("name", "skill"))
        git_url = sc.get("git_url")
        bundle_version_id = sc.get("bundle_version_id")
        skill_dest = None
        if sc.get("path"):
            skill_dest = _resolve_path(sc["path"], target_dir, allow_home=is_user_scope).parent

        # Check if we have a complete bundle for this skill
        bundle = bundle_by_version_id.get(str(bundle_version_id)) if bundle_version_id else None

        if dry_run:
            if bundle:
                mode = "would install (complete folder)"
            elif git_url:
                mode = "would clone"
            else:
                mode = "would write"
            written.append((str(skill_dest) if skill_dest else f"<skill:{sc_name}>", mode))
            continue

        # Bundles were installed before activation files were written.
        if bundle and str(bundle_version_id) in installed_bundle_versions:
            continue

        # Priority 2: Git clone
        if git_url:
            result_path = tracked_skill_install(
                sc_name,
                install_skill_from_git,
                name=sc.get("name", "skill"),
                git_url=git_url,
                skill_path=sc.get("skill_path", "/"),
                git_ref=sc.get("git_ref", "main"),
                harness=harness,
                scope=scope_str,
                skill_md_content=sc.get("skill_md_content"),
                cwd=target_dir,
                dest=skill_dest,
            )
            if result_path:
                written.append((str(result_path), "cloned"))
            else:
                failed_skills.append(sc_name)
                if not quiet:
                    rprint(f"[red]✗ Failed to install skill '{esc(sc_name)}'.[/red] Clone from {esc(git_url)} failed.")
        else:
            # Priority 3: Registry direct (SKILL.md + optional script)
            result_path = tracked_skill_install(
                sc_name,
                install_skill_registry_direct,
                name=sc.get("name", "skill"),
                skill_md_content=sc.get("skill_md_content"),
                script_content=sc.get("script_content"),
                script_filename=sc.get("script_filename"),
                harness=harness,
                scope=scope_str,
                cwd=target_dir,
                dest=skill_dest,
            )
            if result_path:
                written.append((str(result_path), "installed"))
            else:
                failed_skills.append(sc_name)
                if not quiet:
                    rprint(f"[red]✗ Failed to install skill '{esc(sc_name)}'.[/red] No content available.")
        if result_path and not sc.get("path"):
            # No server-given path: the installer chose the location. Remember the
            # SKILL.md it wrote so the pull can fingerprint it (_fingerprint_written_skills).
            sc["installed_skill_md"] = str(Path(result_path) / "SKILL.md")

    return written, failed_skills


def _validate_hook_flags(harness: str, hooks: str | None, on_unknown: str | None, force_hooks: bool) -> None:
    """Reject agent-hook placement flags that do not apply, before any network call."""
    from observal_cli.agent_hooks import ON_UNKNOWN, PLACEMENTS

    if hooks is not None and hooks not in PLACEMENTS:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown hook placement: {hooks}.",
            operation="Pull agent",
            resource="--hooks",
            remediation="Use --hooks=frontmatter or --hooks=settings.",
        )
    if on_unknown is not None and on_unknown not in ON_UNKNOWN:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown --on-unknown policy: {on_unknown}.",
            operation="Pull agent",
            resource="--on-unknown",
            remediation="Use --on-unknown=skip or --on-unknown=run.",
        )
    if (hooks is not None or on_unknown is not None or force_hooks) and harness != "claude-code":
        fail(
            ErrorCategory.VALIDATION,
            f"Harness {harness} does not support agent hook placement.",
            operation="Pull agent",
            resource="agent hook placement",
            remediation="Remove --hooks, --on-unknown and --force-hooks, or select claude-code.",
        )
    if on_unknown is not None and hooks == "frontmatter":
        fail(
            ErrorCategory.VALIDATION,
            "--on-unknown applies only to hooks placed in settings.json.",
            operation="Pull agent",
            resource="--on-unknown",
            remediation="Remove --on-unknown, or use --hooks=settings.",
        )


def _resolve_hook_placement(
    harness: str,
    hooks: str | None,
    on_unknown: str | None,
    installed: dict | None,
    existing: str | None = None,
) -> tuple[str, str, list[str]]:
    """(placement, on-unknown policy, warnings) for this pull; an earlier choice is kept.

    The choice is remembered in the local lockfile (``installed``), and also
    found in settings.json itself (``existing``: the policy of gated hooks this
    agent already owns there), so a teammate's committed settings or a failed
    lockfile write never silently moves the hooks back. The opt-in is offered
    only on POSIX. On an untested Claude Code version it is allowed with a
    warning: hook evidence from that version is reported as unverified, so a
    Claude Code update never blocks a pull.
    """
    from observal_cli import agent_hooks

    if harness != "claude-code":
        return "frontmatter", "skip", []
    previous = installed if isinstance(installed, dict) else {}
    recorded = previous.get("hook_placement") == "settings"
    remembered = recorded or existing is not None
    placement = hooks or ("settings" if remembered else "frontmatter")
    if placement != "settings":
        if on_unknown is not None:
            fail(
                ErrorCategory.VALIDATION,
                "--on-unknown applies only to hooks placed in settings.json.",
                operation="Pull agent",
                resource="--on-unknown",
                remediation="Add --hooks=settings, or remove --on-unknown.",
            )
        return "frontmatter", "skip", []
    remembered_policy = previous.get("hook_on_unknown") if recorded else existing
    policy = on_unknown or (remembered_policy if remembered_policy in agent_hooks.ON_UNKNOWN else "skip")
    found: list[str] = []
    if hooks is None and not recorded:
        found.append(
            "Found this agent's gated hooks in settings.json; keeping them there. "
            "Pull with --hooks=frontmatter to move them back to the agent file."
        )
    if sys.platform == "win32":
        fail(
            ErrorCategory.VALIDATION,
            "Agent hooks in settings.json are not supported on Windows.",
            operation="Pull agent",
            resource="agent hook placement",
            remediation="Pull with --hooks=frontmatter.",
        )
    version = agent_hooks.claude_code_version()
    if agent_hooks.is_tested_version(version):
        return "settings", policy, found
    # Never blocks: hook evidence from an untested version is reported as unverified
    # by the server instead, so the opt-in keeps working when Claude Code updates.
    seen = f"Claude Code {version}" if version else "no Claude Code version (is `claude` on PATH?)"
    return (
        "settings",
        policy,
        [
            *found,
            f"Gated agent hooks were tested only with Claude Code {agent_hooks.tested_range()}; found {seen}. "
            "They may not run as expected, and their Component Insights evidence is reported as unverified "
            "until this version is tested; pull with --hooks=frontmatter to move them back.",
        ],
    )


def _agent_hook_request(
    snippet: dict,
    lock_components: list[dict],
    *,
    placement: str,
    on_unknown: str,
    scope: str,
    target_dir: Path,
    installed: dict | None,
    force: bool,
):
    """The settings.json agent-hook request for a Claude Code pull, or None when there is nothing to manage.

    In frontmatter placement the request is empty, so a reconcile only removes
    groups this agent owned from an earlier ``--hooks=settings`` pull.
    """
    from observal_cli import agent_hooks

    profile = snippet.get("agent_profile") or {}
    profile_path = profile.get("path") if isinstance(profile, dict) else None
    agent = Path(profile_path).stem if isinstance(profile_path, str) and profile_path.endswith(".md") else ""
    previous = installed if isinstance(installed, dict) else {}
    remembered = previous.get("hook_placement") == "settings"
    owners = tuple(
        name
        for name in (previous.get("hook_gate_agent") if remembered else None,)
        if agent_hooks.valid_agent_name(name) and name != agent
    )
    path, display = agent_hooks.settings_location(scope, target_dir)
    hooks: list = []
    if placement == "settings":
        if snippet.get("hook_placement") != "settings":
            fail(
                ErrorCategory.VERSION,
                "This Observal server does not support placing agent hooks in settings.json.",
                operation="Pull agent",
                resource="agent hook placement",
                remediation="Upgrade the Observal server, or pull with --hooks=frontmatter.",
            )
        bindings = [b for b in snippet.get("hook_bindings") or [] if isinstance(b, dict)]
        if not agent_hooks.valid_agent_name(agent) or any(b.get("agent") != agent for b in bindings):
            fail(
                ErrorCategory.VALIDATION,
                "The agent's hooks cannot be tied to its Claude Code agent name.",
                operation="Pull agent",
                resource="agent hook placement",
                remediation="Pull with --hooks=frontmatter.",
            )
        try:
            hooks = agent_hooks.build_agent_hooks(agent, bindings, lock_components, on_unknown)
        except ValueError as error:
            fail(
                ErrorCategory.VALIDATION,
                str(error),
                operation="Pull agent",
                resource="agent hook placement",
                remediation="Pull with --hooks=frontmatter, and report the agent to its publisher.",
            )
    elif not agent_hooks.valid_agent_name(agent):
        return None
    return agent_hooks.AgentHookRequest(
        placement=placement,
        on_unknown=on_unknown,
        path=path,
        display=display,
        agent=agent,
        hooks=tuple(hooks),
        owners=owners,
        force=force,
    )


def _plan_agent_hooks(request, *, remembered: bool, dry_run: bool):
    """Plan before writing anything; refuse invalid settings and (outside a dry run) conflicts."""
    from observal_cli import agent_hooks

    try:
        plan = request.plan()
    except agent_hooks.SettingsError as error:
        if request.placement != "settings" and not remembered:
            return None  # nothing of this agent's can be there; leave the file alone
        fail(
            ErrorCategory.CONFLICT,
            f"{request.path}: {error}",
            operation="Pull agent",
            resource=str(request.path),
            remediation="Fix settings.json (it must be a JSON object), then pull again. Nothing was written.",
        )
    return plan


def _agent_hook_conflict_warnings(request, plan) -> list[str]:
    """Edited Observal-owned groups are left exactly as the user made them; the rest of the pull continues.

    Each stays in settings.json and keeps running as edited, but is not updated
    and verifies as drifted, so Component Insights does not count it.
    """
    if plan is None or not plan.conflicts:
        return []
    edited = ", ".join(f"{item['event']} ({item['component_id'] or 'unknown component'})" for item in plan.conflicts)
    return [
        f"{len(plan.conflicts)} Observal-owned agent hook(s) in {request.path} were edited locally and were left "
        f"as they are: {edited}. They were not updated, and Component Insights does not count them. Pass "
        '--force-hooks to replace them with the agent\'s hooks, or delete their "_observal" key to keep them '
        "as your own hooks."
    ]


def _apply_agent_hooks(
    request, written: list[tuple[str, str]], setup_results: list[dict], *, reports_sessions: bool = False
):
    """Re-plan from a fresh read and write settings.json atomically, before the lockfile.

    If the lockfile write then fails, the next pull recovers ownership from the
    groups' ``_observal`` metadata.
    """
    from observal_cli import agent_hooks

    def failure(stage: str) -> dict:
        return _pull_failure_result(
            written,
            stage,
            setup_results=setup_results,
            installation_tracked=False,
            reports_sessions=reports_sessions,
        )

    try:
        plan = request.plan()
    except agent_hooks.SettingsError as error:
        fail(
            ErrorCategory.CONFLICT,
            f"{request.path}: {error}",
            operation="Pull agent",
            resource=str(request.path),
            remediation="Fix settings.json (it must be a JSON object), then pull again.",
            result=failure("write_agent_hooks"),
        )
    if plan.changed:
        try:
            agent_hooks.write_settings(request.path, plan.data)
        except OSError as error:
            fail(
                ErrorCategory.UNAVAILABLE,
                f"Could not write {request.path}.",
                operation="Pull agent",
                resource=str(request.path),
                remediation="Check the file's permissions, then pull again.",
                detail=repr(error),
                result=failure("write_agent_hooks"),
            )
        written.append((str(request.path), "created" if plan.original is None else "updated"))
    return plan


def _agent_hooks_relevant(request, plan) -> bool:
    """Whether this pull placed, moved or found agent hooks in settings.json worth reporting."""
    if request is None or plan is None:
        return False
    return request.placement == "settings" or bool(plan.removed or plan.conflicts)


def _agent_hooks_output(request, plan, startup_ms: float | None) -> dict:
    from observal_cli.agent_hooks import BLOCKING_EVENTS

    return {
        "placement": request.placement,
        "on_unknown": request.on_unknown if request.placement == "settings" else None,
        **plan.summary(),
        "hooks": [
            {
                "name": hook.name,
                "event": hook.event,
                "component_id": hook.component_id,
                "command": hook.gated,
                "timeout": "unchanged (Claude Code default)",
                "can_block": hook.event in BLOCKING_EVENTS,
            }
            for hook in request.hooks
        ],
        "gate_startup_ms": startup_ms,
    }


def _render_agent_hooks(request, plan, startup_ms: float | None, *, dry_run: bool, http_hooks: int) -> None:
    """Human-readable plan: the settings.json diff, cost and caveats."""
    from observal_cli.agent_hooks import BLOCKING_EVENTS

    if request.placement != "settings" and not (plan.removed or plan.conflicts):
        return
    title = (
        "Agent hooks in settings.json" if request.placement == "settings" else "Agent hooks restored to the agent file"
    )
    rprint(f"\n[bold]{title}[/bold] ({esc(str(request.path))}):")
    names = {hook.component_id: hook.name for hook in request.hooks}
    verb = ("would add", "would remove") if dry_run else ("added", "removed")
    for item in plan.added:
        rprint(f"  [green]+ {verb[0]}[/green] {esc(item['event'])}: {esc(item['name'])}")
    for item in plan.removed:
        label = names.get(item["component_id"]) or item["component_id"] or "unknown component"
        suffix = " (edited, replaced with --force-hooks)" if item.get("edited") else ""
        rprint(f"  [red]- {verb[1]}[/red] {esc(item['event'])}: {esc(label)}{esc(suffix)}")
    for item in plan.kept:
        rprint(f"  [dim]= unchanged {esc(item['event'])}: {esc(item['name'])}[/dim]")
    for item in plan.conflicts:
        label = names.get(item["component_id"]) or item["component_id"] or "unknown component"
        rprint(
            f"  [yellow]! edited locally[/yellow] {esc(item['event'])}: {esc(label)} "
            "[dim](kept; --force-hooks replaces it)[/dim]"
        )
    if request.placement != "settings":
        return
    if request.hooks:
        cost = f"{startup_ms:g} ms" if startup_ms is not None else "not measured"
        rprint(f"  Gate startup on this machine: {esc(cost)} per hook, on every matching event, even when")
        rprint("  the agent is not active. Hooks on one event run in parallel. Timeouts are unchanged:")
        per_event: dict[str, int] = {}
        for hook in request.hooks:
            per_event[hook.event] = per_event.get(hook.event, 0) + 1
        for event, count in sorted(per_event.items()):
            rprint(f"    {esc(event)}: {count} gated hook{'s' if count != 1 else ''}, timeout Claude Code default")
        blocking = sorted({hook.name for hook in request.hooks if hook.event in BLOCKING_EVENTS})
        if blocking and request.on_unknown == "skip":
            rprint(
                f"  [yellow]⚠[/yellow]  {esc(', '.join(blocking))} can block an action. If Claude Code ever sends hook "
                "input the gate does not recognize, the hook is skipped (--on-unknown skip) and cannot block; "
                "--on-unknown run runs it instead, in every session."
            )
    rprint(
        "  [yellow]⚠[/yellow]  The agent file no longer lists these hooks; scripts that read its hooks: "
        "section will not find them. Pull with --hooks=frontmatter to move them back."
    )
    if http_hooks:
        rprint(
            f"  [dim]{http_hooks} HTTP hook(s) have no command to gate and stay in the agent file "
            "(interactive sessions only).[/dim]"
        )


def _install_skill_bundle(
    *,
    sc_name: str,
    bundle: dict,
    lock_component: dict | None,
    skill_dest: Path | None,
    target_dir: Path,
    is_user_scope: bool,
    harness: str,
    quiet: bool,
) -> Path | None:
    """Install a complete skill folder bundle for an agent.

    Args:
        sc_name: Skill name for error messages
        bundle: Complete folder bundle from server
        lock_component: Lock component for digest verification (optional)
        skill_dest: Explicit destination from skill_component path (optional)
        target_dir: Project directory
        is_user_scope: Whether this is a user-scope install
        quiet: Suppress output

    Returns:
        Path to installed SKILL.md, or None on failure
    """
    from observal_cli.cmd_skill import _is_path_safe, _user_skill_dest
    from observal_cli.skill_folder import (
        BundleInstallError,
        BundleValidationError,
        detect_destination_collisions,
        install_folder_bundle,
        validate_bundle,
    )

    # The selected pin, not the bundle's own untrusted version, is authoritative.
    expected_digest = lock_component.get("digest") if lock_component else None
    try:
        validated = validate_bundle(
            bundle,
            expected_version_id=lock_component.get("version_id") if lock_component else None,
            expected_digest=expected_digest,
        )
    except BundleValidationError as e:
        if not quiet:
            rprint(f"[red]✗ Bundle validation failed for '{esc(sc_name)}':[/red] {esc(str(e))}")
        return None

    # Determine target directory
    if skill_dest:
        final_dest = skill_dest
    elif is_user_scope:
        final_dest = _user_skill_dest(harness, validated.folder_name)
    else:
        final_dest = target_dir / validated.skill_file_path.removesuffix("/SKILL.md")
    expected_file = final_dest / "SKILL.md"

    try:
        declared_file = _resolve_path(validated.skill_file_path, target_dir, allow_home=is_user_scope)
    except (CliError, ValueError):
        return None
    if expected_file != declared_file or not _is_path_safe(
        final_dest, target_dir if not is_user_scope else final_dest.parent
    ):
        if not quiet:
            rprint(f"[red]✗ Skill folder destination does not match pinned path for '{esc(sc_name)}'.[/red]")
        return None

    # Check for collisions
    collisions = detect_destination_collisions(final_dest, validated)
    if collisions:
        if not quiet:
            rprint(f"[red]✗ Destination conflicts for '{esc(sc_name)}':[/red]")
            for collision in collisions:
                rprint(f"  • {esc(collision)}")
        return None

    # No ownership proof or explicit backed-up replacement exists yet: refuse.
    try:
        installed_path = install_folder_bundle(validated, final_dest)
        if not quiet:
            rprint(f"[green]✓ Installed skill folder:[/green] {esc(str(final_dest))}")
            rprint(f"  Files: {len(validated.files)}, Size: {validated.total_size:,} bytes")
        return installed_path
    except BundleInstallError as e:
        if not quiet:
            rprint(f"[red]✗ Installation failed for '{esc(sc_name)}':[/red] {esc(str(e))}")
        return None


def register_pull(app: typer.Typer):
    @app.command("pull")
    def pull(
        agent_id: str = typer.Argument(..., help="Agent ID, name, row number, or @alias"),
        harness: str = typer.Option(
            ...,
            "--harness",
            "-i",
            help="Target harness (cursor, kiro, claude-code, codex, copilot, copilot-cli, opencode, antigravity, pi)",
        ),
        directory: str = typer.Option(".", "--dir", "-d", help="Target directory for written files"),
        dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview files without writing"),
        scope: str | None = typer.Option(
            None, "--scope", help="Install scope: 'project' or 'user' for harnesses that support both"
        ),
        model: list[str] | None = typer.Option(
            None,
            "--model",
            help=(
                "Model override. Accepts '<value>' (applies to the selected --harness) or "
                "'<harness>=<value>' for explicit per-harness overrides. May be repeated."
            ),
        ),
        tools: str | None = typer.Option(None, "--tools", help="Comma-separated tool whitelist (Claude Code only)"),
        refresh_models: bool = typer.Option(
            False, "--refresh-models", help="Bust the local model catalog cache before showing the model picker"
        ),
        no_prompt: bool = typer.Option(False, "--no-prompt", "-y", help="Skip interactive prompts"),
        env: list[str] | None = typer.Option(
            None, "--env", "-e", help="Non-secret MCP environment setting (KEY=VALUE, repeatable)"
        ),
        header: list[str] | None = typer.Option(
            None, "--header", "-H", help="Non-secret MCP header setting (Header-Name=value, repeatable)"
        ),
        version: str | None = typer.Option(
            None, "--version", "-V", help="Install a specific version (e.g. '1.2.0'). Defaults to latest."
        ),
        upgrade: bool = typer.Option(
            False,
            "--upgrade",
            help="Install the latest approved agent version instead of the one already locked here",
        ),
        strict: bool | None = typer.Option(
            None,
            "--strict/--no-strict",
            help=(
                "Refuse to install unless every component matches the agent version's lock. "
                "Defaults to the OBSERVAL_STRICT environment variable."
            ),
        ),
        hooks: str | None = typer.Option(
            None,
            "--hooks",
            help=(
                "Claude Code only: where the agent's hooks go. 'frontmatter' (default) keeps them in the agent "
                "file, where Claude Code runs them only in interactive sessions; 'settings' moves them into "
                "settings.json behind an agent gate so they also run headless. Remembered for later pulls."
            ),
        ),
        on_unknown: str | None = typer.Option(
            None,
            "--on-unknown",
            help=(
                "With --hooks=settings: what a gated hook does when Claude Code's hook input is unrecognized, "
                "'skip' (default, keeps agent isolation) or 'run'."
            ),
        ),
        force_hooks: bool = typer.Option(
            False,
            "--force-hooks",
            help="Replace or remove Observal-owned agent hooks in settings.json that were edited locally.",
        ),
        allow_unsupported_harness: bool = typer.Option(
            False,
            "--allow-unsupported-harness",
            help="Install even when the agent version does not list --harness as supported (warns)",
        ),
        output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
    ):
        """Fetch agent config and write harness files to disk.

        Calls the server to generate an install config for the specified harness,
        then writes rules files, MCP configs, and agent files into the target
        directory.  Use --dry-run to preview without writing.

        Pulls are pinned. The first pull installs the latest approved agent
        version and records it in observal.lock in the project directory
        (commit it) and in this machine's lockfile. Later pulls, by anyone in
        that project, install the same agent version even after newer ones are
        approved. Move it deliberately with --upgrade or --version.

        Every component is installed at the exact version that agent version
        pinned. Components without a lock (agents released before pinning) fall
        back to their latest version with a warning; use --strict, or set
        OBSERVAL_STRICT=1, to refuse instead. The flag wins over the variable.

        The harness must be one the agent version lists as supported; pass
        --allow-unsupported-harness to install for another one anyway.

        Use --env KEY=VALUE and --header Header-Name=value only for non-secret
        settings because command arguments are visible to other processes. For
        credentials, omit --no-prompt and enter values interactively. When
        --no-prompt is set, prompts are skipped and only flag values are used.

        Claude Code runs hooks from an agent file only in interactive
        sessions. --hooks=settings moves the agent's command hooks into
        settings.json, each wrapped by a gate that runs it only while that
        agent is active, headless included (POSIX only; untested Claude Code
        versions warn). Preview with --dry-run first; --hooks=frontmatter moves
        them back.

        Examples:
          observal agent pull my-agent --harness claude-code --no-prompt
          observal agent pull my-agent --harness claude-code --no-prompt --upgrade
          observal agent pull my-agent --harness cursor --version 1.2.0 --strict
          observal agent pull my-agent --harness claude-code --hooks=settings --dry-run
        """
        harness, scope, version = _validate_pull_inputs(harness, scope, version)
        _validate_hook_flags(harness, hooks, on_unknown, force_hooks)
        if output == "json" and not no_prompt:
            fail(
                ErrorCategory.VALIDATION,
                "JSON mode cannot prompt for installation values.",
                operation="Pull agent",
                resource="agent installation",
                remediation="Add --no-prompt only when no secret values are required; otherwise use interactive table mode.",
            )
        if upgrade and version:
            fail(
                ErrorCategory.VALIDATION,
                "--upgrade and --version cannot be combined.",
                operation="Pull agent",
                resource="agent version",
                remediation="Use --version to install one version, or --upgrade for the latest approved one.",
            )
        env_overrides = _parse_assignments(env, "environment variable")
        header_overrides = _parse_assignments(header, "header")
        model_default, model_overrides = _parse_model_overrides(model or [])
        unused_model_harnesses = set(model_overrides) - {harness}
        if unused_model_harnesses:
            fail(
                ErrorCategory.VALIDATION,
                f"Model override does not target the selected harness: {sorted(unused_model_harnesses)[0]}.",
                operation="Pull agent",
                resource="model override",
                remediation=f"Use {harness}=MODEL or a bare MODEL value.",
            )
        from observal_shared.harness_registry import has_model_selection

        if (model_default or model_overrides) and not has_model_selection(harness):
            fail(
                ErrorCategory.VALIDATION,
                f"Harness {harness} does not support model selection.",
                operation="Pull agent",
                resource="model override",
                remediation="Remove --model for this harness.",
            )
        if tools and harness != "claude-code":
            fail(
                ErrorCategory.VALIDATION,
                f"Harness {harness} does not support --tools.",
                operation="Pull agent",
                resource="tool allowlist",
                remediation="Remove --tools or select claude-code.",
            )
        if refresh_models and no_prompt:
            fail(
                ErrorCategory.VALIDATION,
                "--refresh-models requires the interactive model picker.",
                operation="Pull agent",
                resource="model catalog",
                remediation="Remove --no-prompt or remove --refresh-models.",
            )

        resolved = client.resolve_registry_reference("agent", agent_id)
        target_dir = Path(directory).resolve()
        ensure_loaded()
        adapter = get_adapter(harness)

        strict = _strict_mode(strict)

        with _progress(output, "Fetching agent details..."):
            agent_detail = client.get(f"/api/v1/agents/{resolved}")

        if output != "json":
            rprint(f"\n[bold]Install options for [cyan]{esc(harness)}[/cyan]:[/bold]")
        if refresh_models:
            from observal_cli import model_catalog as _catalog

            _catalog.invalidate_cache()
        options = _collect_install_options(
            harness,
            scope=scope,
            model_default=model_default,
            model_overrides=model_overrides,
            tools=tools,
            no_prompt=no_prompt,
            refresh_models=refresh_models,
            agent_detail=agent_detail,
            quiet=output == "json",
        )
        is_user_scope = options.get("scope") == "user"
        if is_user_scope and output != "json":
            rprint("  [dim]Files will be written to your home directory (user scope).[/dim]")

        # Which agent version to install: an explicit --version, the latest with
        # --upgrade, otherwise whatever this project (observal.lock) or this
        # machine (lockfile.json) already installed. Only a first install, or a
        # deliberate --upgrade, picks up a newly approved version.
        qualified_name = agent_detail.get("qualified_name") or (
            f"{agent_detail.get('namespace', '')}/{agent_detail.get('slug') or agent_detail.get('name', '')}"
        )
        agent_uuid = str(agent_detail.get("id", resolved))
        locked_entry = None if is_user_scope else _project_locked_agent(target_dir, qualified_name, agent_uuid)
        installed_entry = _installed_agent(harness, agent_uuid, options, target_dir)
        version, resolved_from = _target_version(
            requested=version,
            upgrade=upgrade,
            project_locked=locked_entry,
            installed=installed_entry,
        )

        # MCP env vars and headers come from the versions that will be installed
        plan = agent_detail
        supported = agent_detail.get("supported_harnesses") or []
        if version:
            with _progress(output, f"Fetching agent version {version}..."):
                version_detail = _locked_version_detail(
                    resolved, version, resolved_from, qualified_name=qualified_name, directory=target_dir
                )
            supported = version_detail.get("supported_harnesses", supported) or []
            plan = {
                "component_links": [
                    {
                        "component_type": component.get("component_type"),
                        "component_id": component.get("component_id"),
                        "component_name": component.get("name", ""),
                        "version_ref": component.get("resolved_version"),
                    }
                    for component in version_detail.get("components", [])
                ]
            }

        _require_supported_harness(
            harness,
            list(supported),
            allow=allow_unsupported_harness,
            label=f"{qualified_name} {version or agent_detail.get('version', '')}".strip(),
        )

        spec_cache: dict = {}
        missing_inputs: list[dict[str, str]] = []
        env_values = _collect_mcp_env_vars(
            plan,
            no_prompt=no_prompt,
            env_overrides=env_overrides or None,
            spec_cache=spec_cache,
            missing_inputs=missing_inputs,
        )
        header_values = _collect_mcp_headers(
            plan,
            no_prompt=no_prompt,
            header_overrides=header_overrides or None,
            spec_cache=spec_cache,
            missing_inputs=missing_inputs,
        )
        if missing_inputs:
            fail(
                ErrorCategory.VALIDATION,
                "Agent installation requires values that are unavailable in non-interactive mode.",
                operation="Pull agent",
                resource=qualified_name,
                remediation="Use interactive table mode to enter credentials securely.",
                result={"needs_input": True, "inputs": missing_inputs},
            )

        from observal_cli.lockfile import local_registry_name

        namespace = agent_detail.get("namespace", "")
        slug = agent_detail.get("slug") or agent_detail.get("name", "agent")
        try:
            local_name = local_registry_name(
                harness,
                "agent",
                namespace,
                slug,
                scope=options.get("scope", "project"),
                directory=str(target_dir),
            )
        except (OSError, RuntimeError) as error:
            fail(
                ErrorCategory.UNAVAILABLE,
                "Could not read the local installation lockfile.",
                operation="Pull agent",
                resource="Observal lockfile",
                remediation="Repair or remove the malformed lockfile, then retry.",
                detail=repr(error),
            )
        options["local_name"] = local_name

        existing_hooks = None
        if harness == "claude-code" and hooks is None:
            from observal_cli.agent_hooks import existing_placement, expected_agent_name, settings_location

            existing_hooks = existing_placement(
                settings_location(options.get("scope", "project"), target_dir)[0],
                expected_agent_name(local_name),
            )
        hook_placement, hook_on_unknown, hook_warnings = _resolve_hook_placement(
            harness, hooks, on_unknown, installed_entry, existing_hooks
        )
        if hook_placement == "settings":
            options["hook_placement"] = "settings"

        planned_components = [
            {
                "type": link.get("component_type", "unknown"),
                "name": link.get("component_name", ""),
                "id": str(link.get("component_id", "")),
                "version": link.get("version_ref"),
            }
            for link in plan.get("component_links", [])
        ]
        conflict_warnings = _component_conflicts(
            harness,
            agent_name=agent_detail.get("name", resolved),
            components=planned_components,
        )

        with _progress(output, f"Pulling {harness} config for agent {resolved[:8]}..."):
            from observal_cli.skill_folder import SUPPORTED_FEATURE as SKILL_FOLDER_FEATURE

            install_body: dict = {
                "harness": harness,
                "env_values": env_values,
                "header_values": header_values,
                "options": options,
                "platform": sys.platform,
                # Advertise complete skill folder support
                "supported_features": [SKILL_FOLDER_FEATURE],
            }
            if version:
                install_body["version"] = version
            if strict:
                install_body["strict"] = True
            if allow_unsupported_harness:
                install_body["allow_unsupported_harness"] = True
            result = client.post_public(
                f"/api/v1/agents/{resolved}/install",
                install_body,
            )

        if strict and not isinstance(result.get("lock"), dict):
            # A server that predates component locks ignores the strict flag, so
            # nothing was checked. Refuse before any file is written.
            fail(
                ErrorCategory.VERSION,
                "This Observal server does not report component locks, so --strict cannot be enforced.",
                operation="Pull agent",
                resource="agent installation",
                remediation="Upgrade the Observal server, or pull without --strict (or with OBSERVAL_STRICT unset).",
            )

        # Record what the server installed, not what the agent's latest version lists.
        installed_version = result.get("version") or version or agent_detail.get("version")
        lock = result.get("lock") or {}
        lock_components = _installed_components(lock, planned_components)
        lock_warnings: list[str] = []
        if (
            resolved_from == "project-lock"
            and locked_entry.get("lock_digest")
            and lock.get("digest")
            and locked_entry["lock_digest"] != lock["digest"]
        ):
            mismatch = (
                f"Agent {qualified_name} {installed_version} no longer matches the lock digest recorded in "
                f"{PROJECT_LOCK_FILE}."
            )
            if strict:
                fail(
                    ErrorCategory.CONFLICT,
                    mismatch,
                    operation="Pull agent",
                    resource=str(target_dir / PROJECT_LOCK_FILE),
                    remediation="Ask the agent author or a reviewer to investigate before installing.",
                )
            lock_warnings.append(mismatch)

        snippet = result.get("config_snippet", {})
        # Selected-version aliases come from the generator after final sanitization.
        # Old servers have no proof of per-component aliases, so keep their
        # legacy lock entries unverified rather than deriving keys from slugs.
        if isinstance(result.get("component_pins"), list):
            pinned_by_id = {
                str(pin.get("id")): pin for pin in result["component_pins"] if isinstance(pin, dict) and pin.get("id")
            }
            for component in lock_components:
                pin = pinned_by_id.get(str(component.get("id")))
                if not pin:
                    continue
                # Keep the server's locked version UUID, digest and lock source;
                # the component pin response supplies the actual installed alias.
                component["name"] = pin.get("name") or component.get("name", "")
                component["local_name"] = pin.get("local_name", "")
                component["scope"] = options.get("scope", "project")
                component["qualified_name"] = pin.get("qualified_name") or component.get("qualified_name", "")
                if not isinstance(lock.get("components"), list):
                    component["version"] = pin.get("version")
                elif component.get("version") != pin.get("version"):
                    fail(
                        ErrorCategory.CONFLICT,
                        "Server component pin disagrees with the install lock.",
                        operation="Pull agent",
                        resource="component installation",
                        remediation="Update the server before installing this agent.",
                    )
            aliases = [pin["local_name"] for pin in lock_components if pin["type"] == "mcp" and pin["local_name"]]
            if len(aliases) != len(set(aliases)):
                fail(
                    ErrorCategory.CONFLICT,
                    "MCP aliases collide after generation.",
                    operation="Pull agent",
                    resource="harness MCP aliases",
                    remediation="Rename the conflicting component and retry.",
                )
        if not snippet:
            fail(
                ErrorCategory.UNAVAILABLE,
                "The server returned an empty agent configuration.",
                operation="Pull agent",
                resource="generated agent configuration",
                remediation="Check server compatibility and the agent's harness support.",
            )

        snippet = rewrite_observal_interpreter(snippet)
        _preflight_project_mcp_config(snippet, harness=harness, is_user_scope=is_user_scope)
        from observal_cli import managed_skill

        try:
            _preflight_existing_managed_skill_paths(
                snippet,
                harness=harness,
                target_dir=target_dir,
                is_user_scope=is_user_scope,
            )
        except (managed_skill.ManagedSkillError, OSError, RuntimeError) as error:
            fail(
                ErrorCategory.CONFLICT,
                str(error),
                operation="Pull agent",
                resource=qualified_name,
                remediation=getattr(error, "remediation", None)
                or "Choose another destination or perform an explicit safe migration first.",
            )

        if not result.get("skill_bundles"):
            from observal_cli import lockfile

            previous_agent = lockfile.installed_agent(
                harness, agent_uuid, scope=options.get("scope", "project"), directory=str(target_dir)
            )
            if previous_agent and any(
                component.get("folder_receipt") for component in previous_agent.get("components", [])
            ):
                fail(
                    ErrorCategory.CONFLICT,
                    "This pinned Agent owns verified skill folders; a nonfolder pull cannot replace their receipts.",
                    operation="Pull agent",
                    resource=qualified_name,
                    remediation="Keep the pinned folder release or perform an explicit manual migration first.",
                )

        # Agent hooks in settings.json (Claude Code): planned before any file is written,
        # so invalid settings or locally edited Observal groups refuse the pull cleanly.
        hook_request = None
        hook_plan = None
        if harness == "claude-code":
            hook_request = _agent_hook_request(
                snippet,
                lock_components,
                placement=hook_placement,
                on_unknown=hook_on_unknown,
                scope=options.get("scope", "project"),
                target_dir=target_dir,
                installed=installed_entry,
                force=force_hooks,
            )
        remembered_settings = existing_hooks is not None or (
            isinstance(installed_entry, dict) and installed_entry.get("hook_placement") == "settings"
        )
        if hook_request is not None:
            hook_plan = _plan_agent_hooks(hook_request, remembered=remembered_settings, dry_run=dry_run)
        gated_hooks = {hook.name: hook for hook in hook_request.hooks} if hook_request is not None else {}
        gate_startup_ms = None
        if dry_run and hook_request is not None and hook_request.hooks:
            from observal_cli.agent_hooks import measure_gate_startup

            gate_startup_ms = measure_gate_startup()

        def disclose_telemetry() -> None:
            if output != "json" and not dry_run:
                server_url = config.load().get("server_url") or "the Observal server"
                rprint(
                    "\n  [yellow]Telemetry:[/yellow] session hooks are present and may send prompts, "
                    f"tool calls and tool output to {esc(server_url)} when this agent is used."
                )

        managed_plan: list[tuple[dict, Path, dict, dict]] = []
        applied_folders: list[tuple[dict, dict]] = []
        prior_managed_entry: dict | None = None
        remaining_snippet = snippet
        if result.get("skill_bundles"):
            from copy import deepcopy

            from observal_cli import lockfile, managed_skill

            prior_managed_entry = deepcopy(
                lockfile.installed_agent(
                    harness, agent_uuid, scope=options.get("scope", "project"), directory=str(target_dir)
                )
            )
            try:
                managed_plan = _managed_agent_folders(
                    snippet,
                    result["skill_bundles"],
                    lock,
                    harness=harness,
                    target_dir=target_dir,
                    is_user_scope=is_user_scope,
                    agent_id=agent_uuid,
                    agent_version=str(installed_version),
                    registry_url=lockfile.current_registry_url(),
                    allow_agent_pin_change=bool(upgrade or version),
                )
                old_managed = {
                    component.get("id"): component["folder_receipt"]["target"]
                    for component in (prior_managed_entry or {}).get("components", [])
                    if component.get("type") == "skill" and component.get("folder_receipt")
                }
                new_managed = {proof["listing_id"]: proof["target"] for _, _, proof, _ in managed_plan}
                if any(new_managed.get(listing_id) != target for listing_id, target in old_managed.items()):
                    raise managed_skill.ManagedSkillError(
                        "Agent upgrade removes or relocates a managed skill folder; keep its pin or explicitly migrate it first"
                    )
                if not dry_run:
                    for bundle, target, proof, component in managed_plan:

                        def record(data: dict, component=component, proof=proof) -> None:
                            _record_managed_agent_component(
                                data,
                                harness=harness,
                                scope=options.get("scope", "project"),
                                directory=target_dir,
                                agent_id=agent_uuid,
                                agent_version=str(installed_version),
                                component=component,
                                proof=proof,
                                allow_agent_pin_change=bool(upgrade or version),
                            )

                        outcome = managed_skill.transact(
                            bundle,
                            target,
                            proof,
                            record=record,
                            allow_agent_pin_change=bool(upgrade or version),
                        )
                        applied_folders.append((proof, outcome))
            except managed_skill.ManagedSkillCommittedError as error:
                # The lock already advanced for this folder, so a rollback would contradict its receipt (and the
                # path may no longer lead to the tree). Keep every recorded folder and report a partial pull.
                fail(
                    ErrorCategory.CONFLICT,
                    f"Agent pull is partially applied: {error}",
                    operation="Pull agent",
                    resource="agent skills",
                    remediation=error.remediation
                    or "Repair the reported path, then run the pull again; recorded folders verify as unchanged.",
                    result={
                        "folder_recovery_errors": [],
                        "installation_tracked": True,
                        "committed_folders": [proof["target"] for proof, _ in applied_folders]
                        + [error.outcome["target"]],
                    },
                )
            except (managed_skill.ManagedSkillError, OSError, RuntimeError, ValueError) as error:
                recovery = _rollback_managed_agent_state(
                    applied_folders,
                    harness=harness,
                    scope=options.get("scope", "project"),
                    directory=target_dir,
                    agent_id=agent_uuid,
                    agent_version=str(installed_version),
                    previous=prior_managed_entry,
                )
                fail(
                    ErrorCategory.CONFLICT,
                    f"Could not safely install pinned Agent skill folders before config writes: {error}",
                    operation="Pull agent",
                    resource="agent skills",
                    remediation="Inspect the reported destination and retained backups; do not delete an unowned folder.",
                    detail=str(error),
                    result={"folder_recovery_errors": recovery, "installation_tracked": False},
                )

            # Delegation's write_install_snippet still uses its ephemeral installer.
            # Pull has already installed verified folders and must not install them twice.
            remaining_snippet = {
                **snippet,
                "skill_components": [
                    component
                    for component in snippet.get("skill_components", [])
                    if not component.get("bundle_version_id")
                ],
            }

        try:
            written, failed_skills = write_install_snippet(
                remaining_snippet,
                harness=harness,
                adapter=adapter,
                target_dir=target_dir,
                agent_id=str(agent_detail.get("id", resolved)),
                is_user_scope=is_user_scope,
                dry_run=dry_run,
                quiet=output == "json",
                lock=lock,
            )
            if not dry_run:
                written = [
                    (str(target / "SKILL.md"), outcome["action"] + " (complete folder)")
                    for (_, target, _, _), (_, outcome) in zip(managed_plan, applied_folders, strict=True)
                ] + written
            elif managed_plan:
                written = [
                    (str(target / "SKILL.md"), "would install (complete folder)") for _, target, _, _ in managed_plan
                ] + written
        except CliError as error:
            if applied_folders:
                recovery = _rollback_managed_agent_state(
                    applied_folders,
                    harness=harness,
                    scope=options.get("scope", "project"),
                    directory=target_dir,
                    agent_id=agent_uuid,
                    agent_version=str(installed_version),
                    previous=prior_managed_entry,
                )
                error.result = {
                    **(error.result or {}),
                    "folder_recovery_errors": recovery,
                    "non_folder_writes_may_remain": True,
                }
            # A failed write can also leave a pre-existing hook active when
            # this pull wrote no files at all. Inspect only files on disk, not
            # the proposed snippet, before describing session collection.
            if isinstance(error.result, dict) and not dry_run:
                paths = [item["path"] for item in error.result.get("files", []) if isinstance(item, dict)]
                # A failed replacement leaves the old hook file active even
                # though that path was never added to the written-files list.
                failed_path = error.result.get("failed_path")
                if isinstance(failed_path, str):
                    paths.append(failed_path)
                # An earlier write (for example an MCP config) can fail before the
                # hook or profile destinations are reached; inspect those too.
                paths.extend(
                    _hook_destinations(
                        snippet,
                        adapter=adapter,
                        target_dir=target_dir,
                        is_user_scope=is_user_scope,
                    )
                )
                error.result["reports_sessions"] = (
                    _reports_written_sessions(paths) or adapter.reports_sessions_outside_pull()
                )
                if error.result["reports_sessions"]:
                    disclose_telemetry()
            raise

        if dry_run and hook_plan is not None and hook_plan.changed:
            written.append((str(hook_request.path), "would write"))

        reports_sessions = (
            _reports_sessions(snippet, target_dir=target_dir, is_user_scope=is_user_scope, dry_run=True)
            if dry_run
            else _reports_written_sessions([path for path, _status in written])
        ) or adapter.reports_sessions_outside_pull()
        if reports_sessions:
            disclose_telemetry()

        if failed_skills:
            recovery = _rollback_managed_agent_state(
                applied_folders,
                harness=harness,
                scope=options.get("scope", "project"),
                directory=target_dir,
                agent_id=agent_uuid,
                agent_version=str(installed_version),
                previous=prior_managed_entry,
            )
            fail(
                ErrorCategory.UNAVAILABLE,
                f"Failed to install {len(failed_skills)} agent skill(s).",
                operation="Pull agent",
                resource="agent skills",
                remediation="Check skill source access and content, then retry.",
                detail=", ".join(failed_skills),
                result=_pull_failure_result(
                    written,
                    "install_skills",
                    failed_skills=failed_skills,
                    installation_tracked=False,
                    reports_sessions=reports_sessions,
                    folder_recovery_errors=recovery,
                    non_folder_writes_may_remain=True,
                ),
            )

        if not written:
            fail(
                ErrorCategory.UNAVAILABLE,
                "The generated agent configuration contained no writable files.",
                operation="Pull agent",
                resource="generated agent configuration",
                remediation="Check agent contents and harness support, then retry.",
            )

        warnings_list = (
            lock_warnings
            + conflict_warnings
            + hook_warnings
            + list(result.get("warnings") or [])
            + (snippet.get("_warnings") or [])
        )
        if dry_run and hook_request is not None:
            warnings_list.extend(_agent_hook_conflict_warnings(hook_request, hook_plan))

        # Run required harness registration before recording the pull as installed.
        setup_results: list[dict] = []
        setup_failures: list[str] = []
        setup_cmds = snippet.get("mcp_setup_commands") or []
        # Local-scope registrations (Claude Code) belong to the directory they run in.
        if setup_cmds and not dry_run:
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                fail(
                    ErrorCategory.UNAVAILABLE,
                    "Could not prepare target directory for MCP setup.",
                    operation="Pull agent",
                    resource=str(target_dir),
                    remediation="Check directory permissions.",
                    detail=repr(error),
                )
            setup_cwd = target_dir.resolve()
            _run_reset_commands(
                snippet.get("mcp_reset_commands") or [],
                cwd=setup_cwd,
                adapter=adapter,
                scope=options.get("scope", "project"),
            )
            for command in setup_cmds:
                # Never expose command arguments: MCP setup may carry secrets.
                if not _valid_setup_command(command):
                    setup_results.append({"command": [], "status": "failed", "return_code": None})
                    setup_failures.append("invalid setup command")
                    continue
                shown = _display_setup_command(command)
                try:
                    setup_command = adapter.prepare_mcp_setup_command(command, options.get("scope", "project"))
                    process = subprocess.run(setup_command, capture_output=True, text=True, timeout=60, cwd=setup_cwd)
                except FileNotFoundError:
                    setup_results.append({"command": shown, "status": "failed", "return_code": None})
                    setup_failures.append(f"{command[0]} not found")
                    continue
                except subprocess.TimeoutExpired:
                    setup_results.append({"command": shown, "status": "failed", "return_code": None})
                    setup_failures.append(f"{command[0]} timed out")
                    continue
                except OSError:
                    setup_results.append({"command": shown, "status": "failed", "return_code": None})
                    setup_failures.append(f"{command[0]} could not start")
                    continue
                status = "completed" if process.returncode == 0 else "failed"
                setup_results.append({"command": shown, "status": status, "return_code": process.returncode})
                if process.returncode != 0:
                    setup_failures.append(f"{command[0]} exited with code {process.returncode}")
        elif setup_cmds:
            for command in setup_cmds:
                if not _valid_setup_command(command):
                    setup_results.append({"command": [], "status": "failed", "return_code": None})
                    setup_failures.append("invalid setup command")
                else:
                    setup_results.append(
                        {"command": _display_setup_command(command), "status": "would_run", "return_code": None}
                    )

        if setup_failures:
            recovery = _rollback_managed_agent_state(
                applied_folders,
                harness=harness,
                scope=options.get("scope", "project"),
                directory=target_dir,
                agent_id=agent_uuid,
                agent_version=str(installed_version),
                previous=prior_managed_entry,
            )
            fail(
                ErrorCategory.UNAVAILABLE,
                f"Agent files were written, but {len(setup_failures)} MCP setup command(s) failed.",
                operation="Pull agent",
                resource="harness MCP registration",
                remediation="Fix the reported command and pull the agent again.",
                detail="; ".join(setup_failures),
                result=_pull_failure_result(
                    written,
                    "run_setup_commands",
                    setup_results=setup_results,
                    dry_run=dry_run,
                    installation_tracked=False,
                    reports_sessions=reports_sessions,
                    folder_recovery_errors=recovery,
                    non_folder_writes_may_remain=True,
                ),
            )

        # Record installation state only after files and setup commands succeed.
        project_lock_path: Path | None = None
        if not dry_run:
            agent_version = installed_version
            from observal_cli.layer import verify_installed_mcp

            if hook_request is not None and hook_plan is not None:
                hook_plan = _apply_agent_hooks(hook_request, written, setup_results, reports_sessions=reports_sessions)
                warnings_list.extend(_agent_hook_conflict_warnings(hook_request, hook_plan))

            warnings_list.extend(_fingerprint_written_skills(snippet, lock_components, target_dir, is_user_scope))
            warnings_list.extend(
                _record_hook_bindings(
                    snippet,
                    lock_components,
                    target_dir,
                    is_user_scope,
                    gated=gated_hooks if hook_plan is not None else None,
                    settings_display=hook_request.display if hook_request is not None else "",
                )
            )
            warnings_list.extend(adapter.bind_pulled_hooks(snippet, lock_components, target_dir, is_user_scope))

            # The MCP file this pull wrote (a Pi profile's mcp.json, for example).
            mcp_cfg = snippet.get("mcp_config")
            written_mcp_config = (
                _resolve_path(mcp_cfg["path"], target_dir, allow_home=is_user_scope)
                if isinstance(mcp_cfg, dict) and isinstance(mcp_cfg.get("path"), str)
                else None
            )
            for component in lock_components:
                if component.get("type") != "mcp" or not component.get("local_name"):
                    continue
                status, fingerprint = verify_installed_mcp(
                    harness,
                    options.get("scope", "project"),
                    str(target_dir),
                    component["local_name"],
                    written_config=written_mcp_config,
                )
                if status == "verified" and fingerprint:
                    component["mcp_integrity"] = fingerprint
                else:
                    warnings_list.append("Installed MCP entry could not be verified; attribution will be unavailable.")

            from observal_cli.lockfile import upsert_agent

            try:
                if managed_plan:
                    from datetime import UTC, datetime

                    metadata = {
                        "name": agent_detail.get("name", resolved),
                        "id": str(agent_uuid),
                        "version": agent_version,
                        "scope": options.get("scope", "project"),
                        "directory": str(target_dir),
                        "pulled_at": datetime.now(UTC).isoformat(),
                        "namespace": agent_detail.get("namespace"),
                        "slug": agent_detail.get("slug"),
                        "qualified_name": qualified_name,
                        "local_name": local_name,
                        "lock_digest": lock.get("digest"),
                        "lock_status": lock.get("status"),
                    }
                    if hook_placement == "settings" and hook_request is not None and hook_plan is not None:
                        metadata.update(
                            {
                                "hook_placement": "settings",
                                "hook_on_unknown": hook_on_unknown,
                                "hook_gate_agent": hook_request.agent,
                            }
                        )
                    _sync_managed_agent_lock(
                        harness=harness,
                        scope=options.get("scope", "project"),
                        directory=target_dir,
                        agent_id=agent_uuid,
                        agent_version=str(agent_version),
                        metadata=metadata,
                        components=lock_components,
                        proofs=[proof for _, _, proof, _ in managed_plan],
                    )
                else:
                    upsert_agent(
                        harness,
                        name=agent_detail.get("name", resolved),
                        agent_id=str(agent_uuid),
                        version=agent_version,
                        scope=options.get("scope", "project"),
                        directory=str(target_dir),
                        components=lock_components,
                        namespace=agent_detail.get("namespace"),
                        slug=agent_detail.get("slug"),
                        local_name=local_name,
                        lock_digest=lock.get("digest"),
                        lock_status=lock.get("status"),
                        **(
                            {
                                "hook_placement": "settings",
                                "hook_on_unknown": hook_on_unknown,
                                "hook_gate_agent": hook_request.agent,
                            }
                            if hook_placement == "settings" and hook_request is not None and hook_plan is not None
                            else {}
                        ),
                    )
            except (OSError, RuntimeError) as error:
                recovery = _rollback_managed_agent_state(
                    applied_folders,
                    harness=harness,
                    scope=options.get("scope", "project"),
                    directory=target_dir,
                    agent_id=agent_uuid,
                    agent_version=str(installed_version),
                    previous=prior_managed_entry,
                )
                fail(
                    ErrorCategory.UNAVAILABLE,
                    "Agent files were written, but installation tracking failed.",
                    operation="Pull agent",
                    resource="Observal lockfile",
                    remediation="Repair the local lockfile and pull the agent again.",
                    detail=repr(error),
                    result=_pull_failure_result(
                        written,
                        "update_lockfile",
                        setup_results=setup_results,
                        installation_tracked=False,
                        active_agent_persisted=False,
                        reports_sessions=reports_sessions,
                        folder_recovery_errors=recovery,
                        non_folder_writes_may_remain=True,
                    ),
                )

            if not is_user_scope:
                from observal_cli import project_lock

                try:
                    project_lock_path = project_lock.record_agent(
                        target_dir,
                        qualified_name,
                        project_lock.agent_entry(
                            agent_id=str(agent_uuid),
                            version=installed_version,
                            lock_digest=lock.get("digest"),
                            components=lock_components,
                        ),
                    )
                except (OSError, project_lock.ProjectLockError) as error:
                    recovery: list[str] = []
                    if managed_plan:
                        try:
                            current_pin = project_lock.locked_agent(target_dir, qualified_name, agent_uuid)
                            if current_pin and current_pin.get("version") == installed_version:
                                raise RuntimeError(
                                    "Project pin may have advanced; retain complete new folders for inspection"
                                )
                            recovery = _rollback_managed_agent_folders(applied_folders)
                            if not recovery:
                                _restore_prior_managed_agent_entry(
                                    harness=harness,
                                    scope=options.get("scope", "project"),
                                    directory=target_dir,
                                    agent_id=agent_uuid,
                                    previous=prior_managed_entry,
                                    expected_version=str(installed_version),
                                )
                        except (OSError, RuntimeError, project_lock.ProjectLockError) as restore_error:
                            recovery.append(str(restore_error))
                    fail(
                        ErrorCategory.UNAVAILABLE,
                        f"Agent files were written, but {PROJECT_LOCK_FILE} could not be updated.",
                        operation="Pull agent",
                        resource=str(target_dir / PROJECT_LOCK_FILE),
                        remediation="Check the file's permissions and contents, then pull again.",
                        detail=repr(error),
                        result=_pull_failure_result(
                            written,
                            "update_project_lock",
                            setup_results=setup_results,
                            installation_tracked=not managed_plan or bool(recovery),
                            active_agent_persisted=False,
                            reports_sessions=reports_sessions,
                            folder_recovery_errors=recovery,
                            non_folder_writes_may_remain=bool(managed_plan),
                        ),
                    )

            try:
                from observal_cli.layer import ensure_local_snapshot

                ensure_local_snapshot(project_dir=str(target_dir))
            except (OSError, RuntimeError, ValueError):
                warnings_list.append("Local layer snapshot could not be refreshed; run `observal doctor`.")

            try:
                adapter.persist_active_agent(str(agent_uuid), agent_detail.get("name", resolved), agent_version)
            except (OSError, RuntimeError) as error:
                fail(
                    ErrorCategory.UNAVAILABLE,
                    "Agent files and lockfile were updated, but active-agent state could not be persisted.",
                    operation="Pull agent",
                    resource=f"{harness} active-agent state",
                    remediation="Fix harness configuration permissions and pull the agent again.",
                    detail=repr(error),
                    result=_pull_failure_result(
                        written,
                        "persist_active_agent",
                        setup_results=setup_results,
                        installation_tracked=True,
                        active_agent_persisted=False,
                        reports_sessions=reports_sessions,
                    ),
                )

            from observal_cli.audit import emit_cli_audit

            emit_cli_audit(
                "agent.pull",
                resource_type="agent",
                resource_id=str(agent_uuid),
                resource_name=agent_detail.get("name", resolved),
                detail=f"harness={harness}",
                sensitivity="high",
            )

        if output == "json":
            output_json(
                {
                    "agent": {
                        "id": str(agent_detail.get("id", resolved)),
                        "qualified_name": agent_detail.get("qualified_name")
                        or (f"{namespace}/{slug}" if namespace else slug),
                        "version": installed_version,
                        "latest_version": agent_detail.get("version"),
                        "resolved_from": resolved_from,
                        "local_name": local_name,
                    },
                    "project_lock": str(project_lock_path) if project_lock_path else None,
                    "lock": {
                        "status": lock.get("status"),
                        "digest": lock.get("digest"),
                        "components": lock_components,
                        "problems": list(lock.get("problems") or []),
                    },
                    "harness": harness,
                    "scope": options.get("scope", "project"),
                    "dry_run": dry_run,
                    "target_directory": str(target_dir),
                    "files": [{"path": path, "status": status} for path, status in written],
                    "warnings": warnings_list,
                    "setup_commands": setup_results,
                    "reports_sessions": reports_sessions,
                    **(
                        {"agent_hooks": _agent_hooks_output(hook_request, hook_plan, gate_startup_ms)}
                        if _agent_hooks_relevant(hook_request, hook_plan)
                        else {}
                    ),
                }
            )
            return

        if dry_run:
            rprint("\n[bold yellow]Dry run[/bold yellow] - no files written:\n")
        else:
            rprint(
                f"\n[bold green]Pulled {esc(harness)} config[/bold green] "
                f"({len(written)} file{'s' if len(written) != 1 else ''}):\n"
            )
        for path, status in written:
            style = "dim" if dry_run else "green"
            rprint(f"  [{style}]{esc(status)}[/{style}]  {esc(path)}")
        if reports_sessions and dry_run:
            server_url = config.load().get("server_url") or "the Observal server"
            rprint(
                "\n  [yellow]Telemetry:[/yellow] session hooks would be present after this pull and may send prompts, "
                f"tool calls and tool output to {esc(server_url)} when this agent is used."
            )
        latest_version = agent_detail.get("version")
        source_label = {
            "requested": "requested with --version",
            "upgrade": "latest approved, --upgrade",
            "project-lock": f"locked in {PROJECT_LOCK_FILE}",
            "installed": "already installed here",
            "latest": "latest approved",
        }[resolved_from]
        rprint(
            f"\n[bold]Agent[/bold] {esc(qualified_name)} [cyan]v{esc(installed_version or '?')}[/cyan] ({source_label})"
        )
        if project_lock_path:
            rprint(
                f"  [dim]Recorded in {esc(str(project_lock_path))}; commit it so everyone installs this version.[/dim]"
            )
        if (
            latest_version
            and installed_version
            and latest_version != installed_version
            and resolved_from
            in (
                "project-lock",
                "installed",
            )
        ):
            rprint(f"  [dim]v{esc(latest_version)} is available; pull with --upgrade to move to it.[/dim]")
        if lock_components:
            label = {"locked": "[green]locked[/green]", "partial": "[yellow]partially locked[/yellow]"}.get(
                lock.get("status"), "[yellow]unlocked[/yellow]"
            )
            rprint(f"\n[bold]Components[/bold] ({label}, agent v{esc(installed_version or '?')}):")
            for component in lock_components:
                rprint(
                    f"  [dim]{esc(component['type'])}[/dim] {esc(component.get('qualified_name') or component['name'])}"
                    f" [cyan]v{esc(component.get('version') or '?')}[/cyan]"
                )
        if _agent_hooks_relevant(hook_request, hook_plan):
            gated_names = {hook.name for hook in hook_request.hooks}
            _render_agent_hooks(
                hook_request,
                hook_plan,
                gate_startup_ms,
                dry_run=dry_run,
                http_hooks=sum(
                    1
                    for component in lock_components
                    if component.get("type") == "hook" and component.get("local_name") not in gated_names
                ),
            )
        if warnings_list:
            rprint("")
            for warning in warnings_list:
                rprint(f"  [yellow]⚠[/yellow]  {esc(warning)}")
        if setup_results:
            title = "Would run these setup commands:" if dry_run else "Registered MCP servers:"
            rprint(f"\n[bold]{title}[/bold]")
            for setup in setup_results:
                command_text = " ".join(map(str, setup["command"]))
                marker = "$" if dry_run else "✓"
                rprint(f"  [green]{marker}[/green] {esc(command_text)}")
