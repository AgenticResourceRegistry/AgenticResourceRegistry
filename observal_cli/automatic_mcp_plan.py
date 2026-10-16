# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Whole-file ownership for credential-free, Pi user MCP registrations.

The regular MCP command historically prints snippets. Its explicit --managed
mode owns the *whole* global Pi MCP file, never adopts a pasted config and
never claims just one entry in someone else's settings.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from observal_cli import auto_update_policy, client, install_baseline, install_recovery, lockfile

IDENTITY = "standalone:pi-mcps"
VERSION = "managed"
MAX_BYTES = 2 * 1024 * 1024


class McpPlanError(ValueError):
    """The Pi MCP file needs explicit manual inspection."""


def destination() -> Path:
    return Path.home() / ".pi" / "agent" / "mcp.json"


def entries(registry: str) -> list[dict]:
    data = lockfile.read_lockfile()
    section = data.get("registries", {}).get(lockfile.normalize_server_url(registry), {})
    rows = section.get("harnesses", {}).get("pi", {}).get("standalone", [])
    return [row for row in rows if row.get("type") == "mcp" and row.get("scope") == "user"]


def digest(rows: list[dict]) -> str:
    identities = [
        {
            key: row.get(key)
            for key in ("id", "version", "version_id", "digest", "local_name", "requested_version", "pin_known")
        }
        for row in rows
    ]
    identities.sort(key=lambda row: str(row["id"]))
    return hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()


def verified(registry: str, rows: list[dict]) -> tuple[Path, dict]:
    path = destination()
    if not rows or any(not isinstance(row.get("local_name"), str) or not row["local_name"] for row in rows):
        raise McpPlanError("Managed MCP identities are incomplete; update manually.")
    names = [row["local_name"] for row in rows]
    if len(names) != len(set(names)) or any(part.is_symlink() for part in (path, *path.parents)):
        raise McpPlanError("The Pi MCP file has competing owners or a linked path.")
    files, paths = install_baseline.verified_manifest(
        registry=registry,
        harness="pi",
        agent_id=IDENTITY,
        scope="user",
        root=str(path.parent),
        version=VERSION,
        lock_digest=digest(rows),
    )
    if paths != [str(path)] or set(files) != {str(path)} or path.stat().st_size > MAX_BYTES:
        raise McpPlanError("The managed Pi MCP file set changed.")
    config = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(config, dict)
        or set(config) != {"mcpServers"}
        or not isinstance(config["mcpServers"], dict)
        or set(config["mcpServers"]) != set(names)
    ):
        raise McpPlanError("The Pi MCP file contains unmanaged entries; update manually.")
    return path, config


def saved_inputs(name: str) -> tuple[dict[str, str], dict[str, str]]:
    """Values the user already saved for one managed entry (used only by the updater)."""
    try:
        path = destination()
        if any(part.is_symlink() for part in (path, *path.parents)) or path.stat().st_size > MAX_BYTES:
            return {}, {}
        entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"][name]
    except (OSError, ValueError, KeyError, TypeError):
        return {}, {}
    if not isinstance(entry, dict):
        return {}, {}
    env, headers = entry.get("env"), entry.get("headers")
    return (
        {k: v for k, v in (env if isinstance(env, dict) else {}).items() if isinstance(v, str) and v},
        {k: v for k, v in (headers if isinstance(headers, dict) else {}).items() if isinstance(v, str) and v},
    )


def check_credentials(
    old: object, new: dict, *, required: set[str], required_headers: set[str], automatic: bool
) -> None:
    """Required values must be real; an automatic update may only carry saved values."""
    for field, names in (("env", required), ("headers", required_headers)):
        current = new.get(field) or {}
        if not isinstance(current, dict):
            raise McpPlanError(f"The MCP {field} block is malformed.")
        for name in names:
            value = current.get(name)
            if not isinstance(value, str) or not value or (value.startswith("<") and value.endswith(">")):
                raise McpPlanError(f"The MCP requires a value for {field} '{name}'; provide it manually.")
        if automatic:
            previous = old.get(field) if isinstance(old, dict) and isinstance(old.get(field), dict) else {}
            for name, value in current.items():
                if value != "" and previous.get(name) != value:
                    raise McpPlanError(
                        f"The release changes a saved value or adds a new one ({field} '{name}'); "
                        "install it manually to review."
                    )


def target(config: dict, name: str, entry: dict) -> bytes:
    updated = {"mcpServers": {**config["mcpServers"], name: entry}}
    raw = (json.dumps(updated, indent=2) + "\n").encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise McpPlanError("The managed Pi MCP file is too large.")
    return raw


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
    required: set[str] | None = None,
    required_headers: set[str] | None = None,
) -> Path:
    """The normal explicit writer; the startup child calls this same path."""
    path = destination()
    rows = entries(registry)
    automatic = os.environ.get("OBSERVAL_AUTO_UPDATE_INSTALL") == "1"
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise McpPlanError("The Pi MCP destination crosses a symbolic link.")
    previous = [row for row in rows if row.get("id") == component_id]
    if len(previous) > 1 or any(row.get("local_name") == local_name and row.get("id") != component_id for row in rows):
        raise McpPlanError("Another managed MCP claims this Pi name.")
    if rows:
        _, config = verified(registry, rows)
        if previous and previous[0].get("local_name") != local_name:
            raise McpPlanError("The MCP local name changed; inspect and install manually.")
        old_entry = config["mcpServers"].get(local_name) if previous else None
    else:
        old_entry = None
    if not rows:
        if path.exists() or path.is_symlink():
            raise McpPlanError("An existing pasted Pi MCP config cannot be silently adopted.")
        if install_baseline._path(registry, "pi", IDENTITY, "user", str(path.parent)).exists():
            raise McpPlanError("An earlier managed MCP record remains; inspect it before reinstalling.")
        config = {"mcpServers": {}}
    check_credentials(
        old_entry,
        entry,
        required=required or set(),
        required_headers=required_headers or set(),
        automatic=automatic and old_entry is not None,
    )
    install_baseline._reject_shared_ownership(
        {str(path): ""}, install_baseline._path(registry, "pi", IDENTITY, "user", str(path.parent))
    )
    raw = target(config, local_name, entry)
    if automatic:
        marker = os.environ.get("OBSERVAL_AUTO_UPDATE_SHUTDOWN_MARKER")
        cutoff = float(os.environ.get("OBSERVAL_AUTO_UPDATE_NETWORK_CUTOFF", "inf"))
        if (
            not previous
            or len(previous) != 1
            or previous[0].get("pin_known") is not True
            or previous[0].get("requested_version")
            or previous[0].get("version") != os.environ.get("OBSERVAL_AUTO_UPDATE_EXPECTED_VERSION")
            or not auto_update_policy.policy_status(registry)["effective"]
            or (marker and Path(marker).exists())
            or time.monotonic() + 15 >= cutoff
        ):
            raise McpPlanError("Consent, pin intent, session or installed version changed.")
        backup = os.environ.get("OBSERVAL_AUTO_UPDATE_RECOVERY_DIR")
        if not backup:
            raise McpPlanError("No private MCP recovery location is available.")
        client.end_startup_network_budget()
        old_files = install_baseline.verified_files(
            registry=registry,
            harness="pi",
            agent_id=IDENTITY,
            scope="user",
            root=str(path.parent),
            version=VERSION,
            lock_digest=digest(rows),
        )
        install_recovery.save(
            Path(backup),
            {path: raw},
            old_files,
            [lockfile.LOCKFILE_PATH, install_baseline._path(registry, "pi", IDENTITY, "user", str(path.parent))],
            expected_modes={path: 0o600},
        )
    # Saved credentials may be present. Write a private (0600) complete file; the single baseline belongs to all our MCP entries.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    install_recovery._write(path, raw)
    lockfile.upsert_standalone(
        "pi",
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
    install_baseline.capture(
        registry=registry,
        harness="pi",
        agent_id=IDENTITY,
        scope="user",
        root=str(path.parent),
        version=VERSION,
        lock_digest=digest(entries(registry)),
        written_paths=[str(path)],
    )
    return path
