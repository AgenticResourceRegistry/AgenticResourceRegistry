# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Lock file management for Observal CLI.

Manages ~/.observal/lockfile.json, the canonical record of all agents,
MCPs, skills, hooks, and sandboxes installed via Observal, organized by harness.

The lock file is:
- Written by `observal agent pull` and component registry install commands
- Read on session push to resolve agent attribution and compute layer_hash
- Read by `observal outdated` to compare pinned versions against registry latest
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from loguru import logger as optic

from observal_cli.config import CONFIG_DIR

try:
    import fcntl
except ImportError:  # Windows has no fcntl; the lock uses msvcrt there
    fcntl = None
    import msvcrt
else:
    msvcrt = None

LOCKFILE_PATH = CONFIG_DIR / "lockfile.json"
_LOCKFILE_LOCK = CONFIG_DIR / "lockfile.lock"

# Schema version: bump when the structure changes in a breaking way
LOCK_VERSION = 2


# ---------------------------------------------------------------------------
# Read / Write primitives
# ---------------------------------------------------------------------------


def normalize_server_url(server_url: str) -> str:
    """Return the stable registry key for a server URL."""
    value = server_url.strip()
    parts = urlsplit(value if "://" in value else f"http://{value}")
    if not parts.hostname:
        raise ValueError("A configured server URL is required for lockfile operations")
    scheme = parts.scheme.lower()
    port = parts.port
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    host = parts.hostname.lower()
    netloc = host if port is None or default_port else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path.rstrip("/"), "", ""))


def current_registry_url() -> str:
    from observal_cli import config

    return normalize_server_url(str(config.get_or_exit(require_auth=False)["server_url"]))


def migrate_lockfile_v1(server_url: str | None = None) -> bool:
    """Assign a version 1 lockfile to its previously configured registry."""
    if not LOCKFILE_PATH.exists():
        return False
    try:
        data = json.loads(LOCKFILE_PATH.read_text())
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise RuntimeError(f"Cannot read {LOCKFILE_PATH}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid lockfile structure in {LOCKFILE_PATH}")
    if data.get("lock_version") != 1:
        return False
    registry_url = normalize_server_url(server_url) if server_url else current_registry_url()
    write_lockfile(
        {
            "lock_version": LOCK_VERSION,
            "updated_at": datetime.now(UTC).isoformat(),
            "registries": {
                registry_url: {
                    "server_url": registry_url,
                    "harnesses": data.get("harnesses", {}),
                }
            },
        }
    )
    return True


def read_lockfile() -> dict:
    """Read the complete multi-registry lockfile, migrating version 1 once."""
    migrate_lockfile_v1()
    if not LOCKFILE_PATH.exists():
        return _empty_lockfile()
    try:
        data = json.loads(LOCKFILE_PATH.read_text())
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise RuntimeError(f"Cannot read {LOCKFILE_PATH}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid lockfile structure in {LOCKFILE_PATH}")
    if data.get("lock_version") != LOCK_VERSION or not isinstance(data.get("registries"), dict):
        raise RuntimeError(f"Unsupported lockfile version in {LOCKFILE_PATH}")
    return data


@contextlib.contextmanager
def _exclusive_lock(path: Path):
    """Hold an exclusive cross-process lock on ``path``: flock on POSIX, msvcrt on Windows."""
    with open(path, "w") as handle:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        else:
            # Lock the first byte; LK_LOCK retries for about ten seconds, then raises OSError.
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)
            else:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _write_locked(data: dict) -> None:
    """Replace the lockfile while its cross-process lock is held."""
    data["updated_at"] = datetime.now(UTC).isoformat()
    data["lock_version"] = LOCK_VERSION
    descriptor, name = tempfile.mkstemp(prefix=".lockfile.", suffix=".tmp", dir=LOCKFILE_PATH.parent)
    tmp_path = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(json.dumps(data, indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(LOCKFILE_PATH)
        if os.name == "posix":
            directory = os.open(LOCKFILE_PATH.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        tmp_path.unlink(missing_ok=True)


def write_lockfile(data: dict) -> None:
    """Write the complete lockfile atomically with file locking."""
    LOCKFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(_LOCKFILE_LOCK):
        _write_locked(data)
    optic.debug("lockfile written: {}", LOCKFILE_PATH)


def update_lockfile(mutate: Any) -> Any:
    """Serialize the entire read/modify/write, including across different targets."""
    LOCKFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(_LOCKFILE_LOCK):
        if LOCKFILE_PATH.exists():
            try:
                data = json.loads(LOCKFILE_PATH.read_text())
            except (json.JSONDecodeError, OSError, UnicodeError) as exc:
                raise RuntimeError(f"Cannot read {LOCKFILE_PATH}: {exc}") from exc
            if not isinstance(data, dict):
                raise RuntimeError(f"Invalid lockfile structure in {LOCKFILE_PATH}")
            if data.get("lock_version") == 1:
                url = current_registry_url()
                data = {
                    "lock_version": LOCK_VERSION,
                    "registries": {url: {"server_url": url, "harnesses": data.get("harnesses", {})}},
                }
            if data.get("lock_version") != LOCK_VERSION or not isinstance(data.get("registries"), dict):
                raise RuntimeError(f"Unsupported lockfile version in {LOCKFILE_PATH}")
        else:
            data = _empty_lockfile()
        result = mutate(data)
        _write_locked(data)
        return result


def read_registry_lockfile(*, create: bool = False) -> tuple[dict, dict]:
    """Return the complete lockfile and the current registry section."""
    data = read_lockfile()
    server_url = current_registry_url()
    registry = data["registries"].get(server_url)
    if registry is None:
        registry = {"server_url": server_url, "harnesses": {}}
        if create:
            data["registries"][server_url] = registry
    return data, registry


def _empty_lockfile() -> dict:
    return {
        "lock_version": LOCK_VERSION,
        "updated_at": datetime.now(UTC).isoformat(),
        "registries": {},
    }


def local_registry_name(
    harness: str,
    component_type: str,
    namespace: str,
    slug: str,
    *,
    scope: str = "user",
    directory: str | None = None,
) -> str:
    """Use the bare slug unless another installed namespace already uses it."""
    data = read_lockfile()
    current_url = current_registry_url()
    matching_entries: list[tuple[str, dict]] = []
    for registry_url, registry in data.get("registries", {}).items():
        section = registry.get("harnesses", {}).get(harness, {})
        entries = section.get("agents", []) if component_type == "agent" else section.get("standalone", [])
        for entry in entries:
            if component_type != "agent" and entry.get("type") != component_type:
                continue
            if scope == "project" and directory and entry.get("directory") != directory:
                continue
            matching_entries.append((registry_url, entry))

    collision = any(
        entry.get("slug") == slug and (entry.get("namespace") not in (None, namespace) or registry_url != current_url)
        for registry_url, entry in matching_entries
    )
    if not collision:
        return slug
    # Local names become harness config keys and on-disk names, where a dot reads
    # as a file extension — flattened the same way the registry host is below.
    candidate = f"{namespace.replace('.', '-')}-{slug}"
    if not any(entry.get("local_name") == candidate for _, entry in matching_entries):
        return candidate
    host = urlsplit(current_url).hostname or "registry"
    return f"{host.replace('.', '-')}-{candidate}"


def _ensure_harness(data: dict, harness: str) -> dict:
    """Ensure the harness section exists in the lock file data."""
    harnesses = data.setdefault("harnesses", {})
    if harness not in harnesses:
        harnesses[harness] = {"agents": [], "standalone": []}
    else:
        # Ensure both keys exist
        harnesses[harness].setdefault("agents", [])
        harnesses[harness].setdefault("standalone", [])
    return harnesses[harness]


# ---------------------------------------------------------------------------
# Agent operations
# ---------------------------------------------------------------------------


def _record_capability_use(
    *,
    kind: str,
    source: str,
    harness: str,
    component_id: str,
    version: str | None,
    directory: str | None,
    namespace: str | None,
    slug: str | None,
) -> None:
    """Note an install in the capability lock so session upload can attribute it.

    Best effort: the lock is evidence, and a failure to write it must never
    break an install that already succeeded.
    """
    try:
        from observal_cli import capability_lock

        native_ref = f"{namespace}/{slug}@{version}" if namespace and slug and version else None
        capability_lock.record(
            kind=kind,
            mode=capability_lock.MODE_NEXT_SESSION,
            source=source,
            harness=harness,
            cwd=directory,
            component_id=component_id,
            native_ref=native_ref,
            version=version,
        )
    except Exception as exc:
        optic.debug("capability lock not updated for {} {}: {}", kind, component_id, exc)


def upsert_agent(
    harness: str,
    *,
    name: str,
    agent_id: str,
    version: str | None,
    scope: str = "project",
    directory: str | None = None,
    components: list[dict] | None = None,
    namespace: str | None = None,
    slug: str | None = None,
    local_name: str | None = None,
    lock_digest: str | None = None,
    lock_status: str | None = None,
) -> None:
    """Add or update an agent entry in the lock file.

    Matches on (harness, agent_id, directory) for project-scoped or
    (harness, agent_id) for user-scoped. ``components`` are the exact versions
    the server installed; ``lock_digest`` and ``lock_status`` describe the
    agent version's lock they were installed from.
    """
    optic.debug("upsert_agent: harness={}, name={}, version={}", harness, name, version)
    entry = {
        "name": name,
        "id": agent_id,
        "version": version,
        "pulled_at": datetime.now(UTC).isoformat(),
        "scope": scope,
    }
    if directory:
        entry["directory"] = directory
    if components:
        entry["components"] = components
    if namespace:
        entry["namespace"] = namespace
    if slug:
        entry["slug"] = slug
    if namespace and slug:
        entry["qualified_name"] = f"{namespace}/{slug}"
    if local_name:
        entry["local_name"] = local_name
    if lock_digest:
        entry["lock_digest"] = lock_digest
    if lock_status:
        entry["lock_status"] = lock_status

    def update(data: dict) -> None:
        url = current_registry_url()
        registry = data["registries"].setdefault(url, {"server_url": url, "harnesses": {}})
        agents = _ensure_harness(registry, harness)["agents"]
        existing_idx = _find_agent_idx(agents, agent_id, scope, directory)
        if existing_idx is not None:
            if any(c.get("folder_receipt") for c in agents[existing_idx].get("components", [])):
                raise RuntimeError("Pinned Agent owns verified skill folders; legacy pull cannot replace receipts")
            agents[existing_idx] = entry
        else:
            agents.append(entry)

    update_lockfile(update)
    _record_capability_use(
        kind="agent",
        source="pull",
        harness=harness,
        component_id=agent_id,
        version=version,
        directory=directory,
        namespace=namespace,
        slug=slug,
    )


def remove_agent(harness: str, agent_id: str, directory: str | None = None) -> bool:
    """Remove an agent entry. Returns True if found and removed."""
    data, registry = read_registry_lockfile(create=True)
    harness_section = _ensure_harness(registry, harness)
    agents = harness_section["agents"]

    for i, agent in enumerate(agents):
        if agent.get("id") == agent_id:
            if directory and agent.get("directory") != directory:
                continue
            agents.pop(i)
            write_lockfile(data)
            return True
    return False


def _find_agent_idx(agents: list[dict], agent_id: str, scope: str, directory: str | None) -> int | None:
    """Find index of matching agent entry."""
    for i, agent in enumerate(agents):
        if agent.get("id") == agent_id:
            if scope == "project" and directory:
                if agent.get("directory") == directory:
                    return i
            else:
                # User-scoped: match on id alone
                if agent.get("scope") != "project":
                    return i
    return None


# ---------------------------------------------------------------------------
# Standalone component operations
# ---------------------------------------------------------------------------


def upsert_standalone(
    harness: str,
    *,
    component_type: str,
    name: str,
    component_id: str,
    version: str | None,
    scope: str = "user",
    directory: str | None = None,
    integrity: str | None = None,
    namespace: str | None = None,
    slug: str | None = None,
    local_name: str | None = None,
    version_id: str | None = None,
    digest: str | None = None,
    requested_version: str | None = None,
) -> None:
    """Add or update a standalone component (MCP, skill, hook, etc.) in the lock file.

    ``version_id`` and ``digest`` identify the exact registry release that was
    installed; ``requested_version`` is set when the user pinned it explicitly.
    """
    optic.debug("upsert_standalone: harness={}, type={}, name={}", harness, component_type, name)
    entry: dict[str, Any] = {
        "type": component_type,
        "name": name,
        "id": component_id,
        "version": version,
        "scope": scope,
        "installed_at": datetime.now(UTC).isoformat(),
    }
    if directory:
        entry["directory"] = directory
    if integrity:
        entry["integrity"] = integrity
    if namespace:
        entry["namespace"] = namespace
    if slug:
        entry["slug"] = slug
    if namespace and slug:
        entry["qualified_name"] = f"{namespace}/{slug}"
    if local_name:
        entry["local_name"] = local_name
    if version_id:
        entry["version_id"] = version_id
    if digest:
        entry["digest"] = digest
    if requested_version:
        entry["requested_version"] = requested_version

    def update(data: dict) -> None:
        url = current_registry_url()
        registry = data["registries"].setdefault(url, {"server_url": url, "harnesses": {}})
        standalone = _ensure_harness(registry, harness)["standalone"]
        existing_idx = _find_standalone_idx(standalone, component_type, component_id, scope, directory)
        if existing_idx is not None:
            if component_type == "skill" and standalone[existing_idx].get("folder_receipt"):
                raise RuntimeError(
                    "A verified skill folder owns this installation; legacy delivery cannot replace its receipt"
                )
            standalone[existing_idx] = entry
        else:
            standalone.append(entry)

    update_lockfile(update)
    _record_capability_use(
        kind=component_type,
        source="install",
        harness=harness,
        component_id=component_id,
        version=version,
        directory=directory,
        namespace=namespace,
        slug=slug,
    )


def remove_standalone(harness: str, component_type: str, component_id: str, directory: str | None = None) -> bool:
    """Remove a standalone component entry. Returns True if found and removed."""
    data, registry = read_registry_lockfile(create=True)
    harness_section = _ensure_harness(registry, harness)
    standalone = harness_section["standalone"]

    for i, item in enumerate(standalone):
        if item.get("type") == component_type and item.get("id") == component_id:
            if directory and item.get("directory") != directory:
                continue
            standalone.pop(i)
            write_lockfile(data)
            return True
    return False


def _find_standalone_idx(
    standalone: list[dict],
    component_type: str,
    component_id: str,
    scope: str,
    directory: str | None,
) -> int | None:
    """Find index of matching standalone entry."""
    for i, item in enumerate(standalone):
        if item.get("type") == component_type and item.get("id") == component_id:
            if scope == "project" and directory:
                if item.get("directory") == directory:
                    return i
            else:
                if item.get("scope") != "project":
                    return i
    return None


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def get_agent_for_directory(harness: str, directory: str) -> dict | None:
    """Find the agent installed for a given harness + project directory.

    Used by session push to attribute sessions to agents.
    """
    _, registry = read_registry_lockfile()
    harness_section = registry.get("harnesses", {}).get(harness, {})
    for agent in harness_section.get("agents", []):
        if agent.get("directory") == directory:
            return agent
    return None


def installed_agent(harness: str, agent_id: str, *, scope: str, directory: str | None) -> dict | None:
    """The lockfile entry for an agent already installed in this harness and place."""
    _, registry = read_registry_lockfile()
    agents = registry.get("harnesses", {}).get(harness, {}).get("agents", [])
    index = _find_agent_idx(agents, agent_id, scope, directory)
    return agents[index] if index is not None else None


# Statuses that lockfile reconciliation assigns to an entry the registry could
# not confirm: "invalid" when the id is not even a UUID, "unavailable" when the
# server explicitly reported it as not found.
_UNCONFIRMED_REGISTRY_STATUSES = frozenset({"invalid", "unavailable"})


def agent_entry_is_registry_backed(entry: dict | None) -> bool:
    """Return True when a lockfile agent may be used to attribute a session.

    Sessions are attributed from the local lockfile, which can outlive the
    registry it was written against - agents get deleted, and a lockfile can
    carry ids from a server that no longer has them. Attributing to one of
    those produces a session tagged with an id nothing can resolve.

    Entries are trusted by default: a missing status only means reconciliation
    has not run, which is not evidence against the entry. Only a status that
    reconciliation actively set to "not found" disqualifies it.
    """
    if not entry:
        return False
    return str(entry.get("registry_status") or "") not in _UNCONFIRMED_REGISTRY_STATUSES


def get_agent_by_id(agent_id: str, harness: str | None = None) -> dict | None:
    """Find a lockfile agent by UUID, optionally scoped to one harness."""
    _, registry = read_registry_lockfile()
    for harness_name, harness_section in registry.get("harnesses", {}).items():
        if harness and harness_name != harness:
            continue
        for agent in harness_section.get("agents", []):
            if agent.get("id") == agent_id:
                return agent
    return None


def get_agent_by_name(
    name: str,
    harness: str,
    directory: str | None = None,
) -> dict | None:
    """Find one harness agent by its generated local name, name, or UUID.

    A matching project directory disambiguates project-scoped installs. Without
    that signal, a unique user-scoped or unique overall match is accepted;
    ambiguous matches fail closed.
    """
    _, registry = read_registry_lockfile()
    agents = registry.get("harnesses", {}).get(harness, {}).get("agents", [])
    matches = [agent for agent in agents if name in {agent.get("local_name"), agent.get("name"), agent.get("id")}]
    if directory:
        directory_matches = [agent for agent in matches if agent.get("directory") == directory]
        if len(directory_matches) == 1:
            return directory_matches[0]
        if len(directory_matches) > 1:
            return None
    user_matches = [agent for agent in matches if agent.get("scope") != "project"]
    if len(user_matches) == 1:
        return user_matches[0]
    if user_matches:
        return None
    return matches[0] if len(matches) == 1 else None


def get_all_entries(harness: str | None = None) -> list[dict]:
    """Get all lock file entries, optionally filtered by harness.

    Returns a flat list of entries with 'harness' and 'entry_type' fields added.
    Used by `observal outdated`.
    """
    data = read_lockfile()
    if not data["registries"]:
        return []
    server_url = current_registry_url()
    registry = data["registries"].get(server_url, {"server_url": server_url, "harnesses": {}})
    entries: list[dict] = []

    for harness_name, harness_section in registry.get("harnesses", {}).items():
        if harness and harness_name != harness:
            continue
        for agent in harness_section.get("agents", []):
            entries.append({**agent, "harness": harness_name, "entry_type": "agent"})
        for item in harness_section.get("standalone", []):
            entries.append({**item, "harness": harness_name, "entry_type": "standalone"})

    return entries


# ---------------------------------------------------------------------------
# Hash computation
# ---------------------------------------------------------------------------


def compute_lockfile_hash() -> str:
    """Compute a short hash for the current registry section."""
    if not LOCKFILE_PATH.exists():
        return "0" * 16
    _, registry = read_registry_lockfile()
    content = json.dumps(registry, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(content).hexdigest()[:16]


def compute_integrity(content: str) -> str:
    """Compute sha256 integrity hash for a file's content."""
    return f"sha256-{hashlib.sha256(content.encode()).hexdigest()}"


# ---------------------------------------------------------------------------
# Migration from .observal/agent markers
# ---------------------------------------------------------------------------


def migrate_agent_markers() -> int:
    """Migrate existing .observal/agent markers to the lock file.

    Scans common project directories for .observal/agent files,
    reads them, and creates lock file entries. Returns count of migrated entries.

    This is called once on first CLI run when lockfile.json doesn't exist.
    """
    if LOCKFILE_PATH.exists():
        return 0  # Already migrated

    optic.info("migrating .observal/agent markers to lockfile.json")
    migrated = 0
    markers_found: list[tuple[Path, dict]] = []

    # Scan sync_state.json for known project directories
    state_file = CONFIG_DIR / "sync_state.json"
    if state_file.exists():
        try:
            json.loads(state_file.read_text())
            # sync_state keys are session IDs, but we can look for project markers
            # in common directories. Better approach: scan home for .observal/agent files.
        except Exception:
            pass

    # Scan common locations for .observal/agent files
    home = Path.home()
    search_roots = []

    # Check common code directories
    for candidate in ["code", "projects", "dev", "workspace", "src", "repos"]:
        root = home / candidate
        if root.is_dir():
            search_roots.append(root)

    # Also check CWD and its parents
    cwd = Path.cwd()
    if cwd != home:
        search_roots.append(cwd)
        if cwd.parent != home and cwd.parent.exists():
            search_roots.append(cwd.parent)

    for root in search_roots:
        try:
            # Look up to 3 levels deep for .observal/agent files
            for marker in root.glob("**/.observal/agent"):
                # Limit depth
                rel = marker.relative_to(root)
                if len(rel.parts) > 5:  # .observal/agent = 2 parts + up to 3 dir levels
                    continue
                try:
                    marker_data = json.loads(marker.read_text())
                    markers_found.append((marker.parent.parent, marker_data))
                except (json.JSONDecodeError, OSError, UnicodeError):
                    continue
        except (OSError, PermissionError):
            continue

    if not markers_found:
        # Create empty lockfile so migration doesn't re-run
        write_lockfile(_empty_lockfile())
        return 0

    data = _empty_lockfile()
    registry_url = current_registry_url()
    registry = {"server_url": registry_url, "harnesses": {}}
    data["registries"][registry_url] = registry
    seen: set[str] = set()  # Deduplicate by (agent_id, directory)

    for project_dir, marker_data in markers_found:
        agent_id = marker_data.get("agent_id")
        if not agent_id:
            continue

        directory = str(project_dir.resolve())
        key = f"{agent_id}:{directory}"
        if key in seen:
            continue
        seen.add(key)

        # We don't know which harness was used, default to claude-code
        # (the marker was primarily written by claude-code hooks)
        harness = "claude-code"
        harness_section = _ensure_harness(registry, harness)

        harness_section["agents"].append(
            {
                "name": agent_id,  # Old markers stored ID as name too
                "id": agent_id,
                "version": marker_data.get("agent_version"),
                "pulled_at": marker_data.get("pulled_at", datetime.now(UTC).isoformat()),
                "scope": "project",
                "directory": directory,
                "components": [],
            }
        )
        migrated += 1

    write_lockfile(data)

    # Delete old marker files after successful migration
    for project_dir, _ in markers_found:
        marker_path = project_dir / ".observal" / "agent"
        try:
            marker_path.unlink(missing_ok=True)
            # Remove .observal dir if empty
            observal_dir = project_dir / ".observal"
            if observal_dir.is_dir() and not any(observal_dir.iterdir()):
                observal_dir.rmdir()
        except OSError:
            pass

    optic.info("migrated {} agent markers to lockfile.json", migrated)
    return migrated
