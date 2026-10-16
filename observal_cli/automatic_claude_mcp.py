# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Entry-level ownership of credential-free Claude Code user-scope MCP servers.

Claude Code rewrites ``~/.claude.json`` while it runs, so Observal never edits
that file. It drives Claude's own CLI (``claude mcp add/remove -s user``) and
owns only the single entry it created, proven by a private record of the exact
entry it saw afterward. A pasted or edited entry is never adopted or replaced.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from observal_cli import auto_update_policy, client, config, lockfile

RECORD_DIR = config.CONFIG_DIR / "managed-claude-mcp"
RECOVERY_FILE = "claude-mcp.json"
CLI_TIMEOUT = 20


class ClaudeMcpError(ValueError):
    """The Claude Code MCP entry needs explicit manual inspection."""


def config_path() -> Path:
    custom = os.environ.get("CLAUDE_CONFIG_DIR")
    home = Path.home()
    if custom and Path(custom).expanduser() != home / ".claude":
        raise ClaudeMcpError("Claude Code uses a different config directory; update manually.")
    return home / ".claude.json"


def normalize(entry: object) -> dict:
    """Comparable shape of a stdio entry; anything else is not ours to manage."""
    if (
        not isinstance(entry, dict)
        or entry.get("type") not in (None, "stdio")
        or not isinstance(entry.get("command"), str)
        or not entry["command"]
        or not isinstance(entry.get("args", []), list)
        or any(not isinstance(arg, str) for arg in entry.get("args", []))
        or entry.get("env") not in (None, {})
        or set(entry) - {"type", "command", "args", "env"}
    ):
        raise ClaudeMcpError("The Claude Code MCP entry is not a plain credential-free stdio server.")
    return {"command": entry["command"], "args": list(entry.get("args", []))}


def read_entry(name: str) -> dict | None:
    path = config_path()
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
        raise ClaudeMcpError("The Claude Code config is unsafe to read; update manually.")
    servers = json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ClaudeMcpError("The Claude Code user MCP section is malformed.")
    entry = servers.get(name)
    return None if entry is None else normalize(entry)


def _record_path(registry: str, component_id: str) -> Path:
    key = hashlib.sha256(f"{lockfile.normalize_server_url(registry)}\0{component_id}".encode()).hexdigest()
    return RECORD_DIR / f"{key}.json"


def load_record(registry: str, component_id: str) -> dict | None:
    path = _record_path(registry, component_id)
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ClaudeMcpError("The managed MCP record is not private; inspect it manually.")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or not isinstance(record.get("name"), str):
        raise ClaudeMcpError("The managed MCP record is malformed.")
    normalize({"command": record["entry"]["command"], "args": record["entry"]["args"]})
    return record


def _save_record(registry: str, component_id: str, name: str, entry: dict) -> None:
    RECORD_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _record_path(registry, component_id)
    body = json.dumps({"schema": 1, "name": name, "entry": entry}).encode()
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        directory = os.open(RECORD_DIR, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _run(*argv: str) -> None:
    claude = shutil.which("claude")
    if not claude:
        raise ClaudeMcpError("The `claude` command is not on PATH; install the MCP manually.")
    done = subprocess.run(
        [claude, "mcp", *argv],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=CLI_TIMEOUT,
        check=False,
    )
    if done.returncode != 0:
        raise ClaudeMcpError("Claude Code refused the MCP change; inspect it manually.")


def _add(name: str, entry: dict) -> None:
    _run("add", "-s", "user", name, "--", entry["command"], *entry["args"])


def parse_snippet(snippet: object, local_name: str) -> dict:
    """The exact `claude mcp add` the server generated, no shell involved."""
    command = snippet.get("command") if isinstance(snippet, dict) else None
    if (
        not isinstance(snippet, dict)
        or set(snippet) != {"command", "type"}
        or snippet["type"] != "shell_command"
        or not isinstance(command, list)
        or len(command) < 6
        or command[:5] != ["claude", "mcp", "add", local_name, "--"]
        or any(not isinstance(part, str) or not part for part in command)
    ):
        raise ClaudeMcpError("No exact credential-free stdio Claude Code MCP command was returned.")
    return {"command": command[5], "args": command[6:]}


def install(
    *,
    registry: str,
    component_id: str,
    name: str,
    namespace: str | None,
    slug: str | None,
    local_name: str,
    version: str,
    version_id: str | None,
    digest_value: str | None,
    requested_version: str | None,
    entry: dict,
) -> str:
    """Create (explicit) or replace (explicit/automatic) exactly the owned entry."""
    record = load_record(registry, component_id)
    current = read_entry(local_name)
    automatic = os.environ.get("OBSERVAL_AUTO_UPDATE_INSTALL") == "1"
    if record is None:
        if automatic:
            raise ClaudeMcpError("This MCP has no Observal ownership record; update manually.")
        if current is not None:
            raise ClaudeMcpError("A Claude Code MCP with this name already exists; Observal will not adopt it.")
        _add(local_name, entry)
    else:
        if record["name"] != local_name or current != record["entry"]:
            raise ClaudeMcpError("The Claude Code MCP entry changed since Observal installed it; update manually.")
        old_row = _guard_automatic(registry, component_id) if automatic else None
        if automatic and old_row is not None:
            # Always saved, even when the release generates the identical entry, so the
            # parent can verify success against the same plan recovery would use.
            _save_recovery(
                Path(os.environ["OBSERVAL_AUTO_UPDATE_RECOVERY_DIR"]),
                registry=registry,
                component_id=component_id,
                name=local_name,
                old=record["entry"],
                new=entry,
                old_row=old_row,
                new_row={
                    "id": component_id,
                    "name": name,
                    "namespace": namespace,
                    "slug": slug,
                    "local_name": local_name,
                    "version": version,
                    "version_id": str(version_id) if version_id else None,
                    "digest": digest_value,
                    "requested_version": requested_version,
                    "pin_known": True,
                },
            )
        if current != entry:
            _run("remove", "-s", "user", local_name)
            try:
                _add(local_name, entry)
            except (ClaudeMcpError, subprocess.SubprocessError, OSError) as error:
                _add(local_name, record["entry"])  # Put the verified original back.
                raise ClaudeMcpError(
                    "Claude Code refused to add the new MCP entry; the original entry was put back."
                ) from error
    if read_entry(local_name) != entry:
        raise ClaudeMcpError("Claude Code did not record exactly the requested MCP entry.")
    _save_record(registry, component_id, local_name, entry)
    lockfile.upsert_standalone(
        "claude-code",
        component_type="mcp",
        name=name,
        component_id=component_id,
        version=version,
        scope="user",
        namespace=namespace,
        slug=slug,
        local_name=local_name,
        version_id=str(version_id) if version_id else None,
        digest=digest_value,
        requested_version=requested_version,
        pin_known=True,
    )
    return str(config_path())


def _guard_automatic(registry: str, component_id: str) -> dict:
    marker = os.environ.get("OBSERVAL_AUTO_UPDATE_SHUTDOWN_MARKER")
    cutoff = float(os.environ.get("OBSERVAL_AUTO_UPDATE_NETWORK_CUTOFF", "inf"))
    rows = [
        row
        for row in lockfile.get_all_entries(harness="claude-code")
        if row.get("type") == "mcp" and row.get("scope") == "user" and row.get("id") == component_id
    ]
    if (
        len(rows) != 1
        or rows[0].get("pin_known") is not True
        or rows[0].get("requested_version")
        or rows[0].get("version") != os.environ.get("OBSERVAL_AUTO_UPDATE_EXPECTED_VERSION")
        or not auto_update_policy.policy_status(registry)["effective"]
        or (marker and Path(marker).exists())
        or time.monotonic() + 15 >= cutoff
        or not os.environ.get("OBSERVAL_AUTO_UPDATE_RECOVERY_DIR")
    ):
        raise ClaudeMcpError("Consent, pin intent, session or installed version changed.")
    client.end_startup_network_budget()
    return rows[0]


_ROW_FIELDS = (
    "id",
    "name",
    "namespace",
    "slug",
    "local_name",
    "version",
    "version_id",
    "digest",
    "requested_version",
    "pin_known",
)


def _save_recovery(
    root: Path,
    *,
    registry: str,
    component_id: str,
    name: str,
    old: dict,
    new: dict,
    old_row: dict,
    new_row: dict,
) -> None:
    """Durably save everything a stopped update could leave inconsistent: the entry,
    our ownership record and the installed-lock row. Never any other config."""
    from observal_cli import install_recovery

    if root.parent != install_recovery.BACKUP_DIR or root.exists() or root.is_symlink():
        raise ClaudeMcpError("No fresh private MCP recovery location is available.")
    if install_recovery.BACKUP_DIR.exists() and (
        install_recovery.BACKUP_DIR.is_symlink() or install_recovery.BACKUP_DIR.stat().st_mode & 0o077
    ):
        raise ClaudeMcpError("Recovery storage is not private.")
    body = json.dumps(
        {
            "schema": 3,
            "registry": registry,
            "component_id": component_id,
            "name": name,
            "old": old,
            "new": new,
            "old_row": {key: old_row.get(key) for key in _ROW_FIELDS},
            "new_row": {key: new_row.get(key) for key in _ROW_FIELDS},
        }
    ).encode()
    install_recovery.BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.mkdir(mode=0o700)
        install_recovery._write(root / RECOVERY_FILE, body)  # fsyncs the file and the directory
        install_recovery._sync(install_recovery.BACKUP_DIR)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def _load_recovery(root: Path) -> dict | None:
    try:
        record = json.loads((root / RECOVERY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = (
        isinstance(record, dict)
        and record.get("schema") == 3
        and all(isinstance(record.get(key), str) for key in ("registry", "component_id", "name"))
        and isinstance(record.get("old"), dict)
        and isinstance(record.get("new"), dict)
        and isinstance(record.get("old_row"), dict)
        and isinstance(record.get("new_row"), dict)
    )
    return record if ok else None


def _lock_row(component_id: str) -> dict | None:
    rows = [
        row
        for row in lockfile.get_all_entries(harness="claude-code")
        if row.get("type") == "mcp" and row.get("scope") == "user" and row.get("id") == component_id
    ]
    return rows[0] if len(rows) == 1 else None


def _bookkeeping_kinds(record: dict) -> tuple[str, str]:
    """Classify our ownership record and lock row as old, new (the planned target) or foreign.

    Anything else, including a missing or unreadable value, is foreign: another
    writer changed it, so recovery must not overwrite it.
    """
    try:
        saved = load_record(record["registry"], record["component_id"])
        row = _lock_row(record["component_id"])
    except (OSError, ValueError, KeyError, TypeError):
        return "foreign", "foreign"
    if saved is None or saved["name"] != record["name"]:
        record_kind = "foreign"
    elif saved["entry"] == record["old"]:
        record_kind = "old"
    elif saved["entry"] == record["new"]:
        record_kind = "new"
    else:
        record_kind = "foreign"
    if row is None:
        row_kind = "foreign"
    elif all(row.get(key) == record["old_row"].get(key) for key in _ROW_FIELDS):
        row_kind = "old"
    elif all(row.get(key) == record["new_row"].get(key) for key in _ROW_FIELDS):
        row_kind = "new"
    else:
        row_kind = "foreign"
    return record_kind, row_kind


def bookkeeping_is_planned(record: dict) -> bool:
    """Is our ownership record and lock row exactly the planned new values (a verified success)?"""
    record_kind, row_kind = _bookkeeping_kinds(record)
    # An identical-entry release makes the old and new ownership record equal, so the
    # record classifies as "old"; the lock row (its version differs) must still be exactly the plan.
    record_ok = record_kind == "new" or (record_kind == "old" and record["old"] == record["new"])
    return record_ok and row_kind == "new"


def bookkeeping_is_original(record: dict) -> bool:
    """Is our ownership record and the installed-lock row exactly what they were before?"""
    return _bookkeeping_kinds(record) == ("old", "old")


def _restore_bookkeeping(record: dict) -> None:
    row = record["old_row"]
    _save_record(record["registry"], record["component_id"], record["name"], record["old"])
    lockfile.upsert_standalone(
        "claude-code",
        component_type="mcp",
        name=row["name"],
        component_id=row["id"],
        version=row["version"],
        scope="user",
        namespace=row.get("namespace"),
        slug=row.get("slug"),
        local_name=row.get("local_name"),
        version_id=row.get("version_id"),
        digest=row.get("digest"),
        requested_version=row.get("requested_version"),
        pin_known=row.get("pin_known") is True,
    )


def recovery_state(root: Path) -> tuple[str, dict] | None:
    """Classify the entry against the saved plan: old, new, missing or foreign."""
    record = _load_recovery(root)
    if record is None:
        return None
    try:
        current = read_entry(record["name"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if current == record["old"]:
        return "old", record
    if current == record["new"]:
        return "new", record
    return ("missing" if current is None else "foreign"), record


def fully_original(root: Path) -> bool:
    """True only if the entry, ownership record and installed lock are all untouched."""
    state = recovery_state(root)
    return state is not None and state[0] == "old" and bookkeeping_is_original(state[1])


def restore_if_safe(root: Path) -> bool:
    """Put back our entry, our ownership record and our lock row; touch nothing else.

    Only an entry that is absent or exactly the planned new one is replaced, and
    only a record and lock row that are still the old or planned-new values are
    rewritten; anything foreign is left alone (nothing is changed at all) so the
    backup stays for manual inspection. Returns True only after all three are
    verified to be the originals.
    """
    state = recovery_state(root)
    if state is None or state[0] == "foreign":
        return False
    kind, record = state
    if "foreign" in _bookkeeping_kinds(record):
        # The record or lock row is neither the original nor our planned value.
        # Change nothing and keep the backup: the outcome needs manual inspection.
        return False
    try:
        if kind == "new":
            _run("remove", "-s", "user", record["name"])
        if kind in {"new", "missing"}:
            _add(record["name"], record["old"])
        if read_entry(record["name"]) != record["old"]:
            return False
        _restore_bookkeeping(record)
        return bookkeeping_is_original(record)
    except (ClaudeMcpError, subprocess.SubprocessError, OSError, ValueError, TypeError, KeyError):
        return False
