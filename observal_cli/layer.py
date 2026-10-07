# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""harness layer scanning and hash computation.

Scans harness configuration directories to build a manifest of all files
that shape AI behavior (rules, agents, skills, MCP configs, hooks).
Computes a deterministic layer_hash from the manifest and manages
caching to avoid redundant file reads.

The layer_hash represents the FULL state the AI sees, not just what
Observal installed, but also user-created rules, custom agents, etc.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

try:
    from loguru import logger as optic
except ModuleNotFoundError:
    import logging

    optic = logging.getLogger(__name__)

from observal_cli.config import CONFIG_DIR

if TYPE_CHECKING:
    from collections.abc import Callable

# Cache for file hashes by mtime (avoids re-reading unchanged files)
_FILE_HASH_CACHE_PATH = CONFIG_DIR / ".file_hash_cache.json"
_LOCAL_SNAPSHOT_PATH = CONFIG_DIR / "layer_snapshot.json"
_LAST_UPLOADED_PATH = CONFIG_DIR / "layer_uploaded.json"

# Maximum file size to include content (skip very large files)
MAX_FILE_SIZE = 512 * 1024  # 512KB


# ---------------------------------------------------------------------------
# Per-harness file discovery configuration
# ---------------------------------------------------------------------------

# Maps harness name → dict of scope → list of (base_dir, glob_patterns)
# base_dir is relative to home (~) for user scope, or project root for project scope
HARNESS_LAYER_CONFIGS: dict[str, dict[str, list[tuple[str, list[str]]]]] = {
    "claude-code": {
        "user": [
            (
                "~/.claude",
                [
                    "CLAUDE.md",
                    "agents/*.md",
                    "skills/*/SKILL.md",
                    "settings.json",
                ],
            ),
            ("~", [".claude.json"]),
        ],
        "project": [
            (
                ".",
                [
                    ".mcp.json",
                    ".claude/CLAUDE.md",
                    ".claude/agents/*.md",
                    ".claude/skills/*/SKILL.md",
                    ".claude/settings.local.json",
                    "CLAUDE.md",
                ],
            ),
        ],
    },
    "cursor": {
        "user": [
            (
                "~/.cursor",
                [
                    "rules/*.mdc",
                    "mcp.json",
                    "hooks.json",
                    "agents/*.md",
                    "skills/*/SKILL.md",
                ],
            ),
        ],
        "project": [
            (
                ".",
                [
                    ".cursor/rules/*.mdc",
                    ".cursor/mcp.json",
                    ".cursor/hooks.json",
                    ".cursor/agents/*.md",
                    ".cursor/skills/*/SKILL.md",
                ],
            ),
        ],
    },
    "kiro": {
        "user": [
            (
                "~/.kiro",
                [
                    "agents/*.json",
                    "skills/*/SKILL.md",
                    "settings/mcp.json",
                ],
            ),
        ],
        "project": [
            (
                ".",
                [
                    ".kiro/agents/*.json",
                    ".kiro/skills/*/SKILL.md",
                    ".kiro/settings/mcp.json",
                ],
            ),
        ],
    },
    "pi": {
        "user": [
            (
                "~/.pi/agent",
                [
                    "AGENTS.md",
                    "SYSTEM.md",
                    "APPEND_SYSTEM.md",
                    "mcp.json",
                    "skills/*/SKILL.md",
                    "sandboxes/**/*",
                    "settings.json",
                    "agents/*/AGENTS.md",
                    "agents/*/SYSTEM.md",
                    "agents/*/APPEND_SYSTEM.md",
                    "agents/*/mcp.json",
                    "agents/*/skills/*/SKILL.md",
                    "agents/*/sandboxes/**/*",
                    # pi-mcp-adapter 3.x config; hash-only, for MCP verification.
                    "mcp-adapter.json",
                    # Registry hooks the Observal extension runs; hash-only, for hook verification.
                    "observal-hooks.json",
                    "agents/*/observal-hooks.json",
                ],
            ),
            # User-global MCP sources pi-mcp-adapter also loads (hash-only).
            ("~", [".config/mcp/mcp.json", ".agents/mcp.json", ".agents/mcp/mcp.json"]),
        ],
        "project": [
            (
                ".",
                [
                    "AGENTS.md",
                    ".mcp.json",
                    ".pi/mcp-adapter.json",
                    ".pi/SYSTEM.md",
                    ".pi/APPEND_SYSTEM.md",
                    ".pi/mcp.json",
                    ".pi/skills/*/SKILL.md",
                    ".pi/sandboxes/**/*",
                    ".pi/agents/*/AGENTS.md",
                    ".pi/agents/*/SYSTEM.md",
                    ".pi/agents/*/APPEND_SYSTEM.md",
                    ".pi/agents/*/mcp.json",
                    ".pi/agents/*/skills/*/SKILL.md",
                    ".pi/agents/*/sandboxes/**/*",
                ],
            ),
        ],
    },
    "copilot": {
        "user": [
            (
                "~/.copilot",
                [
                    "instructions.md",
                    "hooks/*.json",
                ],
            ),
        ],
        "project": [
            (
                ".",
                [
                    ".github/copilot-instructions.md",
                    ".github/agents/*.agent.md",
                    ".vscode/mcp.json",
                    ".github/hooks/*.json",
                    ".github/skills/*/SKILL.md",
                    ".github/copilot/settings.json",
                ],
            ),
        ],
    },
    "opencode": {
        "user": [
            (
                "~/.config/opencode",
                [
                    "agents/*.md",
                    "opencode.json",
                    "skills/*/SKILL.md",
                    "plugins/*.ts",
                ],
            ),
        ],
        "project": [
            (
                ".",
                [
                    ".opencode/agents/*.md",
                    "opencode.json",
                    ".opencode/skills/*/SKILL.md",
                    ".opencode/plugins/*.ts",
                ],
            ),
        ],
    },
    "codex": {
        "user": [
            (
                "~/.codex",
                [
                    "config.toml",
                    "hooks.json",
                ],
            ),
        ],
        "project": [
            (
                ".",
                [
                    "AGENTS.md",
                    ".codex/config.toml",
                    ".codex/hooks.json",
                    ".agents/skills/*/SKILL.md",
                ],
            ),
        ],
    },
    "goose": {
        "user": [
            (
                "~/.agents",
                [
                    "agents/*.md",
                    "skills/*/SKILL.md",
                    "plugins/*/plugin.json",
                    "plugins/*/hooks/hooks.json",
                ],
            ),
            (
                "~/.config/goose",
                [
                    "config.yaml",
                    ".goosehints",
                ],
            ),
        ],
        "project": [
            (
                ".",
                [
                    ".agents/agents/*.md",
                    ".agents/skills/*/SKILL.md",
                    ".agents/plugins/*/plugin.json",
                    ".agents/plugins/*/hooks/hooks.json",
                    ".goosehints",
                ],
            ),
        ],
    },
    "copilot-cli": {
        "user": [
            (
                "~/.copilot",
                [
                    "mcp-config.json",
                    "skills/*/SKILL.md",
                    "agents/*.agent.md",
                    "hooks/*.json",
                    "copilot-instructions.md",
                    "instructions/*.instructions.md",
                    "settings.json",
                ],
            ),
        ],
        "project": [
            (
                ".",
                [
                    ".github/copilot-instructions.md",
                    ".github/agents/*.agent.md",
                    ".mcp.json",
                    ".agents/skills/*/SKILL.md",
                    ".github/hooks/*.json",
                    ".github/copilot/settings.json",
                ],
            ),
        ],
    },
}


# ---------------------------------------------------------------------------
# File hash cache
# ---------------------------------------------------------------------------


def _load_hash_cache() -> dict[str, dict]:
    """Load {filepath: {mtime: float, hash: str}} from cache file."""
    if not _FILE_HASH_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_FILE_HASH_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_hash_cache(cache: dict[str, dict]) -> None:
    """Persist hash cache to disk."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _FILE_HASH_CACHE_PATH.write_text(json.dumps(cache))
    except OSError:
        pass


def _hash_file(path: Path, cache: dict[str, dict]) -> tuple[str, int]:
    """Hash a file, using cache if mtime hasn't changed.

    Returns (sha256_hex, file_size).
    """
    try:
        stat = path.stat()
        size = stat.st_size
        mtime = stat.st_mtime
    except OSError:
        return "", 0

    key = str(path)
    cached = cache.get(key)
    if cached and cached.get("mtime") == mtime:
        return cached["hash"], size

    # Read and hash the file
    try:
        content = path.read_bytes()
        file_hash = hashlib.sha256(content).hexdigest()
        cache[key] = {"mtime": mtime, "hash": file_hash}
        return file_hash, size
    except OSError:
        return "", 0


# ---------------------------------------------------------------------------
# Layer manifest building
# ---------------------------------------------------------------------------


def _resolve_base_dir(base_dir: str) -> Path:
    """Resolve a base directory string to an absolute Path."""
    if base_dir.startswith("~"):
        return Path(base_dir).expanduser()
    return Path(base_dir)


def _discover_files(harness: str, project_dir: str | None = None) -> list[tuple[Path, str]]:
    """Discover all harness config files for a given harness.

    Returns list of (absolute_path, relative_display_path) tuples.
    Scans both user and project scopes.
    """
    config = HARNESS_LAYER_CONFIGS.get(harness, {})
    found: list[tuple[Path, str]] = []
    seen: set[str] = set()

    for scope in ("user", "project"):
        scope_configs = config.get(scope, [])
        for base_dir, patterns in scope_configs:
            if scope == "project" and not project_dir:
                continue

            resolved_base = Path(project_dir) if scope == "project" else _resolve_base_dir(base_dir)

            if not resolved_base.is_dir():
                continue

            for pattern in patterns:
                glob_base = resolved_base

                try:
                    for match in glob_base.glob(pattern):
                        if match.is_file() and match.stat().st_size <= MAX_FILE_SIZE:
                            abs_path = match.resolve()
                            # Safety: ensure file is under the base dir (no symlink escapes)
                            try:
                                abs_path.relative_to(glob_base.resolve())
                            except ValueError:
                                continue  # Symlink pointing outside base dir
                            # Create display path relative to scope root
                            try:
                                rel = match.relative_to(glob_base)
                                display = f"{scope}:{rel}"
                            except ValueError:
                                display = f"{scope}:{match.name}"

                            if display not in seen:
                                seen.add(display)
                                found.append((abs_path, display))
                except (OSError, PermissionError):
                    continue

    return found


def build_layer_manifest(
    harness: str,
    project_dir: str | None = None,
    include_content: bool = False,
    registry_data: dict | None = None,
) -> list[dict[str, Any]]:
    """Build the layer manifest for a given harness.

    Args:
        harness: harness identifier (e.g. 'claude-code', 'cursor')
        project_dir: Optional project directory for project-scope scanning
        include_content: If True, include full file content in manifest entries

    Returns:
        Sorted list of manifest entries: [{path, hash, size, source, content?}]
    """
    cache = _load_hash_cache()
    files = _discover_files(harness, project_dir)

    # Safety cap: max 200 files per manifest. If an harness config dir
    # has more than this, something is wrong (e.g. node_modules matched).
    if len(files) > 200:
        optic.warning("layer scan found {} files, capping at 200", len(files))
        files = files[:200]

    manifest: list[dict[str, Any]] = []

    # Load lockfile to determine source (observal vs user)
    from observal_cli.lockfile import read_registry_lockfile

    if registry_data is None:
        _, registry_data = read_registry_lockfile()
    observal_files = _get_observal_managed_files(registry_data, harness, project_dir)

    for abs_path, display_path in files:
        # Hash the very same bytes that are included in the upload (no second
        # read after the file can change). Hash-only scans retain the mtime cache.
        if include_content:
            try:
                data = abs_path.read_bytes()
            except OSError:
                continue
            file_hash, size = hashlib.sha256(data).hexdigest(), len(data)
        else:
            file_hash, size = _hash_file(abs_path, cache)
            if not file_hash:
                continue

        entry: dict[str, Any] = {
            "path": display_path,
            "hash": f"sha256-{file_hash}",
            "size": size,
            "source": "observal" if display_path in observal_files else "user",
        }

        if include_content:
            # The harness adapter owns its sensitive shared settings paths.
            from observal_cli.harness import ensure_loaded, get_adapter

            ensure_loaded()
            try:
                redact_content = get_adapter(harness).redact_layer_content(display_path)
            except KeyError:
                redact_content = False
            if redact_content:
                entry["content"] = ""
            else:
                try:
                    entry["content"] = data.decode("utf-8")
                except UnicodeDecodeError:
                    continue

        manifest.append(entry)

    # Save updated cache
    _save_hash_cache(cache)

    verifications: list[dict | None] = []
    if harness == "pi":
        verifications.append(pi_mcp_verification_entry(registry_data, project_dir))
    if harness in SKILL_VERIFICATION_HARNESSES:
        verifications.append(skill_verification_entry(harness, registry_data, project_dir))
    if harness in HOOK_VERIFICATION_HARNESSES:
        verifications.append(hook_verification_entry(harness, registry_data, project_dir))
    for verification in verifications:
        if verification is not None:
            if include_content:
                verification["content"] = ""
            manifest.append(verification)

    # Sort deterministically for consistent hashing
    manifest.sort(key=lambda e: e["path"])
    return manifest


PI_MCP_VERIFICATION_PATH = "observal:mcp-verification"
PI_MCP_VERIFIER = "observal-pi-mcp-verification-v1"


SKILL_VERIFICATION_PATH = "observal:skill-verification"
# Harnesses whose skill presence (and location) is bound into the layer identity.
SKILL_VERIFICATION_HARNESSES = frozenset({"pi", "claude-code"})


HOOK_VERIFICATION_PATH = "observal:hook-verification"
# Harnesses whose hook bindings are verified and bound into the layer identity.
HOOK_VERIFICATION_HARNESSES = frozenset({"claude-code", "pi"})


def hook_verification_entry(harness: str, registry_data: dict | None, project_dir: str | None) -> dict | None:
    """Hash-only manifest entry binding pinned hook bindings and their current state to the layer identity.

    Hook configs and scripts are not in the hashed manifest (settings files can
    hold secrets), so the binding, its agent and its verification result are
    bound here instead. Absent unless a pinned hook carries ``hook_integrity``.
    """

    def state(component: dict, scope: str) -> str:
        fields = [hook_binding_sha256(component), _nfc(component.get("hook_agent"))]
        if hook_placement(component) == "gated_settings":
            # Only a gated placement adds a field, so frontmatter and settings hooks
            # keep the layer identity they had before gated placement existed.
            fields.append("gated_settings")
        return "|".join([*fields, _hook_status(harness, project_dir, component)])

    return _pin_verification_entry(
        registry_data,
        project_dir,
        "hook",
        "hook_integrity",
        f"observal-{harness}-hook-verification-v1",
        HOOK_VERIFICATION_PATH,
        require_integrity=True,
        extra=state,
        harness=harness,
    )


def _skill_verifier(harness: str) -> str:
    return f"observal-{harness}-skill-verification-v1"


def skill_file_fingerprint(path: Path) -> str | None:
    """``sha256-<hex>`` of an installed SKILL.md, in the layer manifest's hash format."""
    try:
        return f"sha256-{hashlib.sha256(path.read_bytes()).hexdigest()}"
    except OSError:
        return None


def skill_location_sha256(harness: str, scope: str, directory: str | None, alias: str) -> str:
    """SHA-256 of the absolute active SKILL.md path; skill evidence must name this exact file."""
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    try:
        location = get_adapter(harness).skill_location(scope, directory, alias) if alias else None
    except KeyError:
        location = None
    return hashlib.sha256(location.encode("utf-8")).hexdigest() if location else ""


def skill_verification_entry(harness: str, registry_data: dict | None, project_dir: str | None) -> dict | None:
    """Hash-only manifest entry binding a harness's pinned skill fingerprints to the layer identity.

    Present only when at least one pinned skill carries ``skill_integrity``, so
    layers without fingerprinted skills keep their existing hashes. For Pi it
    must match ``piSkillVerificationEntry`` in the Pi extension byte for byte.
    """

    def shadow_state(component: dict, scope: str) -> str:
        # The active file's absolute location, and same-named skills Pi could
        # load instead, live outside the hashed manifest; both must still
        # change the layer identity.
        from observal_cli.harness import ensure_loaded, get_adapter

        ensure_loaded()
        alias = _nfc(component.get("local_name"))
        paths = get_adapter(harness).skill_shadow_paths(scope, project_dir, alias) if alias else []
        location = skill_location_sha256(harness, scope, project_dir, alias)
        return "|".join([location, *(skill_file_fingerprint(path) or "" for path in paths)])

    return _pin_verification_entry(
        registry_data,
        project_dir,
        "skill",
        "skill_integrity",
        _skill_verifier(harness),
        SKILL_VERIFICATION_PATH,
        require_integrity=True,
        extra=shadow_state,
        harness=harness,
    )


def pi_mcp_verification_entry(registry_data: dict | None, project_dir: str | None) -> dict | None:
    """A synthetic, hash-only manifest entry binding Pi's MCP verification inputs to identity.

    The Pi extension reports MCP verification in ``drift``, which is not part
    of the v2 hash. Its inputs outside the hashed files are the pinned install
    fingerprints and the verifier revision. Hashing them here makes one layer
    hash imply one verification result, so re-pulling (new fingerprints) or a
    verifier change yields a new snapshot instead of a stale or conflicting one.
    Must match ``piMcpVerificationEntry`` in the Pi extension byte for byte.
    """
    return _pin_verification_entry(
        registry_data, project_dir, "mcp", "mcp_integrity", PI_MCP_VERIFIER, PI_MCP_VERIFICATION_PATH
    )


def _pin_verification_entry(
    registry_data: dict | None,
    project_dir: str | None,
    component_type: str,
    integrity_key: str,
    verifier: str,
    entry_path: str,
    *,
    require_integrity: bool = False,
    extra: Callable[[dict, str], str] | None = None,
    harness: str = "pi",
) -> dict | None:
    sections = registry_data.get("harnesses", {}) if isinstance(registry_data, dict) else {}
    section = sections.get(harness) if isinstance(sections, dict) else None
    if not isinstance(section, dict):
        return None
    directory = str(Path(project_dir).resolve()) if project_dir else None

    def included(item: dict) -> bool:
        if item.get("scope") == "user":
            return True
        raw = item.get("directory")
        return directory is not None and isinstance(raw, str) and str(Path(raw).resolve()) == directory

    rows: list[list[str]] = []
    agents = section.get("agents") if isinstance(section.get("agents"), list) else []
    standalone = section.get("standalone") if isinstance(section.get("standalone"), list) else []
    parents = [(agent, agent.get("components")) for agent in agents if isinstance(agent, dict) and included(agent)]
    parents.append((None, [item for item in standalone if isinstance(item, dict) and included(item)]))
    for parent, components in parents:
        for component in components if isinstance(components, list) else []:
            if not isinstance(component, dict) or component.get("type") != component_type:
                continue
            if require_integrity and not _nfc(component.get(integrity_key)):
                continue
            scope = _nfc(component.get("scope")) or (_nfc(parent.get("scope")) if parent else "") or "project"
            row = [
                _uuid_or_original(parent.get("id")) if parent else "",
                _uuid_or_original(component.get("id")),
                _nfc(component.get("local_name")),
                scope,
                _nfc(component.get(integrity_key)),
            ]
            if extra is not None:
                row.append(extra(component, scope))
            rows.append(row)
    if not rows:
        return None
    rows.sort(key=lambda row: [value.encode("utf-8") for value in row])
    data = json.dumps([verifier, rows], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "path": entry_path,
        "hash": f"sha256-{hashlib.sha256(data).hexdigest()}",
        "size": len(data),
        "source": "observal",
    }


def _get_observal_managed_files(lockfile_data: dict, harness: str, project_dir: str | None) -> set[str]:
    """Determine which display paths are managed by Observal from the lock file."""
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    try:
        adapter = get_adapter(harness)
    except KeyError:
        return set()
    return adapter.get_observal_managed_files(lockfile_data, project_dir)


# ---------------------------------------------------------------------------
# Multi-harness layer hash computation
# ---------------------------------------------------------------------------


def _detect_active_harnesses() -> list[str]:
    """Detect installed harnesses by asking CLI adapters for their home markers."""
    from pathlib import Path

    from observal_cli.harness import ensure_loaded, get_adapter
    from observal_shared.harness_registry import HARNESS_REGISTRY

    ensure_loaded()
    home = Path.home()
    active: list[str] = []
    for harness in HARNESS_REGISTRY:
        try:
            adapter = get_adapter(harness)
        except KeyError:
            continue
        if adapter.is_installed(home):
            active.append(harness)
    return active


def _nfc(value: Any) -> str:
    """Canonicalize a lockfile identity field without inventing missing values."""
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFC", value)


def _uuid_or_original(value: Any) -> str:
    text = _nfc(value)
    try:
        return str(UUID(text))
    except ValueError:
        return ""  # Invalid legacy IDs remain unknown; never transmit arbitrary strings.


def _pin_tuples(pins: dict) -> list[list[str]]:
    tuples: list[list[str]] = []
    for agent in pins["agents"]:
        parent_id, parent_version = agent["id"], agent["version"]
        harness, scope = agent["harness"], agent["scope"]
        tuples.append(
            [
                "agent",
                "agent",
                parent_id,
                parent_version,
                harness,
                scope,
                agent.get("local_name", ""),
                "",
                "",
                agent.get("qualified_name", ""),
                agent["name"],
            ]
        )
        for component in agent["components"]:
            tuples.append(
                [
                    "agent_component",
                    component["type"],
                    component["id"],
                    component["version"],
                    harness,
                    component["scope"],
                    component["local_name"],
                    parent_id,
                    parent_version,
                    component.get("qualified_name", ""),
                    component["name"],
                ]
            )
    for item in pins["standalone"]:
        tuples.append(
            [
                "standalone",
                item["type"],
                item["id"],
                item["version"],
                item["harness"],
                item["scope"],
                item["local_name"],
                "",
                "",
                item.get("qualified_name", ""),
                item["name"],
            ]
        )
    return sorted(tuples, key=lambda row: [value.encode("utf-8") for value in row])


def layer_hash_v2(harnesses: dict[str, list[dict]], pinned_versions: dict) -> str:
    """Hash the specified manifest and pins by the shared Phase 0.8 byte contract."""
    file_pairs: dict[str, str] = {}
    for harness, manifest in harnesses.items():
        for entry in manifest:
            path = _nfc(f"{harness}/{entry['path']}").replace("\\", "/")
            file_hash = _nfc(entry["hash"])
            if not re.fullmatch(r"sha256-[0-9a-f]{64}", file_hash):
                raise ValueError("Invalid layer manifest hash")
            if path in file_pairs and file_pairs[path] != file_hash:
                raise ValueError("Conflicting duplicate layer manifest path")
            file_pairs[path] = file_hash
    pairs = sorted(([path, value] for path, value in file_pairs.items()), key=lambda pair: pair[0].encode("utf-8"))
    serialized = json.dumps(
        ["observal-layer-v2", pairs, _pin_tuples(pinned_versions)], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return "v2_" + hashlib.sha256(serialized).hexdigest()[:60]


def _build_layer_snapshot(harness: str | None, project_dir: str | None, *, include_content: bool) -> dict:
    """Read registry once, then build matching manifest, pins, hash and payload."""
    from observal_cli.lockfile import read_registry_lockfile

    _, registry = read_registry_lockfile()
    pins = _extract_pinned_versions(registry, project_dir=project_dir, harness=harness)
    scan = (
        [harness]
        if harness
        else sorted(
            set(_detect_active_harnesses()) | {entry["harness"] for entry in [*pins["agents"], *pins["standalone"]]}
        )
    )
    harnesses: dict[str, list[dict]] = {}
    for name in scan:
        harnesses[name] = build_layer_manifest(
            name, project_dir, include_content=include_content, registry_data=registry
        )
    return {
        "hash": layer_hash_v2(harnesses, pins),
        "harnesses": harnesses,
        "lockfile_hash": hashlib.sha256(
            json.dumps(registry, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16],
        "pinned_versions": pins,
        "drift": _compute_drift(registry, harnesses, project_dir=project_dir),
    }


def compute_layer_hash(harness: str | None = None, project_dir: str | None = None) -> str:
    """Compute the v2 hash from one scoped registry read and matching manifests."""
    return _build_layer_snapshot(harness, project_dir, include_content=True)["hash"]


# ---------------------------------------------------------------------------
# Local snapshot management
# ---------------------------------------------------------------------------


def get_last_uploaded_hash() -> str:
    """Return only an acknowledged v2 upload, never a merely built local snapshot."""
    try:
        value = json.loads(_LAST_UPLOADED_PATH.read_text()).get("hash")
    except (OSError, ValueError, AttributeError):
        return ""
    return value if isinstance(value, str) and re.fullmatch(r"v2_[0-9a-f]{60}", value) else ""


def get_local_snapshot() -> dict | None:
    """Read the latest locally built snapshot (which may not have uploaded yet)."""
    try:
        if _LOCAL_SNAPSHOT_PATH.exists():
            return json.loads(_LOCAL_SNAPSHOT_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    return None


def save_local_snapshot(snapshot: dict) -> None:
    """Save the locally built snapshot, independently of upload acknowledgement.

    Writes only to ~/.observal/layer_snapshot.json. Overwrites in place (no history).
    Validates payload size before writing to prevent disk flooding.
    """
    try:
        serialized = json.dumps(snapshot, indent=2)
        # Safety: cap at 5MB. Typical snapshot is 10-50KB.
        if len(serialized) > 5 * 1024 * 1024:
            optic.warning("layer snapshot too large ({}KB), skipping save", len(serialized) // 1024)
            return
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _LOCAL_SNAPSHOT_PATH.write_text(serialized + "\n")
    except OSError:
        pass


def set_last_uploaded_hash(layer_hash: str, server_url: str = "", user_id: str = "") -> None:
    """Record an acknowledged v2 upload for the current server and user."""
    if not re.fullmatch(r"v2_[0-9a-f]{60}", layer_hash):
        return
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _LAST_UPLOADED_PATH.write_text(
            json.dumps({"hash": layer_hash, "server_url": server_url.rstrip("/"), "user_id": user_id}) + "\n"
        )
    except OSError:
        pass


def was_uploaded_for(layer_hash: str, server_url: str, user_id: str) -> bool:
    try:
        marker = json.loads(_LAST_UPLOADED_PATH.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(marker, dict) and marker == {
        "hash": layer_hash,
        "server_url": server_url.rstrip("/"),
        "user_id": user_id,
    }


def ensure_local_snapshot(harness: str | None = None, project_dir: str | None = None) -> str:
    """Generate the local snapshot if it doesn't exist or state changed. Returns the layer_hash.

    Scans ALL detected harnesses (not just one). Called after login, pull, or scan.
    Does NOT upload to server (that happens on session push).
    """
    payload = build_upload_payload(harness=harness, project_dir=project_dir)
    local = get_local_snapshot()
    if local != payload:
        save_local_snapshot(payload)
        optic.debug("local snapshot updated: hash={}", payload["hash"])
    return payload["hash"]


def needs_upload(current_hash: str) -> bool:
    """Check if the current hash differs from the last acknowledged upload."""
    return not re.fullmatch(r"v2_[0-9a-f]{60}", current_hash) or current_hash != get_last_uploaded_hash()


def diff_local(harness: str | None = None, project_dir: str | None = None) -> dict | None:
    """Diff current layer state against the local snapshot.

    Returns None if no local snapshot exists or nothing changed.
    Returns {added: [...], removed: [...], modified: [...]} if changed.
    """
    local = get_local_snapshot()
    if not local:
        return None

    # Build current state across all first-class harnesses
    harnesses_to_scan = [harness] if harness else _detect_active_harnesses()

    current_files: dict[str, dict] = {}
    for scan_harness in harnesses_to_scan:
        manifest = build_layer_manifest(scan_harness, project_dir, include_content=False)
        for entry in manifest:
            key = f"{scan_harness}/{entry['path']}"
            current_files[key] = entry

    # Flatten local snapshot (structured by harness)
    local_files: dict[str, dict] = {}
    local_harnesses = local.get("harnesses", {})
    if isinstance(local_harnesses, dict):
        for harness_name, files in local_harnesses.items():
            for f in files:
                key = f"{harness_name}/{f['path']}"
                local_files[key] = f
    # Backward compat: old flat "files" list
    elif "files" in local:
        local_files = {f["path"]: f for f in local.get("files", [])}

    local_paths = set(local_files.keys())
    current_paths = set(current_files.keys())

    added = [current_files[p] for p in current_paths - local_paths]
    removed = [local_files[p] for p in local_paths - current_paths]
    modified = []

    for path in local_paths & current_paths:
        if local_files[path]["hash"] != current_files[path]["hash"]:
            modified.append(
                {
                    "path": path,
                    "before_hash": local_files[path]["hash"],
                    "after_hash": current_files[path]["hash"],
                    "source": current_files[path].get("source", "user"),
                }
            )

    if not added and not removed and not modified:
        return None

    return {"added": added, "removed": removed, "modified": modified}


def build_upload_payload(harness: str | None = None, project_dir: str | None = None) -> dict:
    """Build the full layer snapshot payload for upload to the server.

    Scans all detected first-class harnesses. Structured by harness.
    Includes file contents for server-side diffing and insight analysis.
    """
    return _build_layer_snapshot(harness, project_dir, include_content=True)


def _extract_pinned_versions(lockfile_data: dict, project_dir: str | None = None, harness: str | None = None) -> dict:
    """Project v1/v2 lockfile records into bounded v2 identity fields."""
    agents: list[dict] = []
    standalone: list[dict] = []
    sections = lockfile_data.get("harnesses", {}) if isinstance(lockfile_data, dict) else {}
    if not isinstance(sections, dict):
        return {"schema_version": 2, "agents": agents, "standalone": standalone}
    directory = str(Path(project_dir).resolve()) if project_dir else None

    def included(item: dict) -> bool:
        if item.get("scope") == "user":
            return True
        raw_directory = item.get("directory")
        return (
            directory is not None and isinstance(raw_directory, str) and str(Path(raw_directory).resolve()) == directory
        )

    def common(item: dict, scope: str) -> dict:
        projected = {
            "type": _nfc(item.get("type")),
            "id": _uuid_or_original(item.get("id")),
            "name": _nfc(item.get("name")),
            "version": _nfc(item.get("version")),
            "scope": _nfc(item.get("scope")) or scope,
            "local_name": _nfc(item.get("local_name")),
        }
        qualified = _nfc(item.get("qualified_name"))
        if qualified:
            projected["qualified_name"] = qualified
        return projected

    for name, section in sections.items():
        if harness is not None and name != harness:
            continue
        if not isinstance(name, str) or not isinstance(section, dict):
            continue
        agent_entries = section.get("agents")
        if not isinstance(agent_entries, list):
            agent_entries = []
        standalone_entries = section.get("standalone")
        if not isinstance(standalone_entries, list):
            standalone_entries = []
        for agent in agent_entries:
            if not isinstance(agent, dict) or not included(agent):
                continue
            scope = _nfc(agent.get("scope")) or "project"
            components = []
            raw_components = agent.get("components")
            if isinstance(raw_components, list) and len(raw_components) > 128:
                raise ValueError("Too many installed components for one agent snapshot")
            for comp in raw_components if isinstance(raw_components, list) else []:
                if isinstance(comp, dict):
                    components.append(common(comp, scope))
            projected = common(agent, scope)
            projected.pop("type")
            projected["harness"] = _nfc(name)
            projected["components"] = components
            agents.append(projected)
        for item in standalone_entries:
            if isinstance(item, dict) and included(item):
                projected = common(item, "project")
                projected["harness"] = _nfc(name)
                standalone.append(projected)
    if len(agents) > 128 or len(standalone) > 512:
        raise ValueError("Too many installed pins for a layer snapshot")
    return {"schema_version": 2, "agents": agents, "standalone": standalone}


def mcp_entry_fingerprint(entry: dict) -> str:
    """Keep only locally hashed structural MCP settings, never credential fields."""
    if not isinstance(entry, dict):
        raise ValueError("Invalid MCP configuration")
    from urllib.parse import urlsplit, urlunsplit

    url = entry.get("url")
    parsed = urlsplit(url) if isinstance(url, str) else None
    safe_url = ""
    if parsed:
        # Keep the endpoint authority (host *and* port) but drop userinfo, query
        # and fragment, which can carry credentials. A port-only change is drift.
        host = parsed.hostname or ""
        authority = f"[{host}]" if ":" in host else host
        if parsed.port is not None:  # raises ValueError for an invalid port -> unverified
            authority = f"{authority}:{parsed.port}"
        safe_url = urlunsplit((parsed.scheme, authority, parsed.path, "", ""))
    structure = [entry.get("command", ""), entry.get("args", []), safe_url, entry.get("type", "")]
    return (
        "sha256-"
        + hashlib.sha256(
            json.dumps(structure, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )


def verify_installed_mcp(
    harness: str,
    scope: str,
    directory: str | None,
    alias: str,
    expected: str | None = None,
    *,
    written_config: Path | None = None,
) -> tuple[str, str | None]:
    """Fingerprint one installed MCP entry.

    *written_config* is the MCP file a pull just wrote. Adapters whose pull
    output is not the active config (Pi profiles) read the entry from there.
    """
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    try:
        adapter = get_adapter(harness)
        if written_config is not None:
            status, entry = adapter.read_pulled_mcp(scope, directory, alias, written_config)
        else:
            status, entry = adapter.read_installed_mcp(scope, directory, alias)
    except (KeyError, OSError, ValueError):
        return "unverified", None
    if status != "verified" or entry is None:
        return status, None
    try:
        fingerprint = mcp_entry_fingerprint(entry)
    except (ValueError, TypeError):
        return "unverified", None
    if expected is not None and expected != fingerprint:
        return "drifted", None
    return "verified", fingerprint


def _compute_drift(
    lockfile_data: dict, harnesses_section: dict[str, list[dict]], project_dir: str | None = None
) -> dict:
    """Verify installed MCP aliases and retained file integrities (fail closed)."""
    drifted: list[dict] = []
    mcp_verifications: list[dict] = []
    skill_verifications: list[dict] = []
    hook_verifications: list[dict] = []
    unverified = False
    directory = str(Path(project_dir).resolve()) if project_dir else None
    sections = lockfile_data.get("harnesses", {}) if isinstance(lockfile_data, dict) else {}
    if not isinstance(sections, dict):
        return {
            "is_canonical": None,
            "drifted_files": [],
            "mcp_verifications": [],
            "skill_verifications": [],
            "hook_verifications": [],
        }
    for harness_name, section in sections.items():
        if not isinstance(section, dict) or harness_name not in harnesses_section:
            continue
        actual_hashes = {f["path"]: f["hash"] for f in harnesses_section[harness_name]}
        agents = section.get("agents")
        standalone = section.get("standalone")
        agents = agents if isinstance(agents, list) else []
        standalone = standalone if isinstance(standalone, list) else []
        for parent in [*agents, {"components": standalone}]:
            if not isinstance(parent, dict):
                continue
            if (
                parent.get("components") is not standalone
                and parent.get("scope") == "project"
                and (directory is None or parent.get("directory") != directory)
            ):
                continue
            components = parent.get("components")
            for comp in components if isinstance(components, list) else []:
                if not isinstance(comp, dict):
                    continue
                if comp.get("scope", parent.get("scope")) == "project" and (
                    directory is None or comp.get("directory", parent.get("directory")) != directory
                ):
                    continue
                alias = comp.get("local_name")
                safe_alias = alias if isinstance(alias, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", alias) else ""
                if comp.get("type") == "mcp":
                    if not safe_alias or not comp.get("mcp_integrity"):
                        status = "unverified"
                    else:
                        status, _ = verify_installed_mcp(
                            harness_name,
                            comp.get("scope", parent.get("scope", "")),
                            comp.get("directory", parent.get("directory")),
                            safe_alias,
                            comp["mcp_integrity"],
                        )
                        if status == "verified":
                            from observal_cli.harness import get_adapter

                            scope = comp.get("scope", parent.get("scope", ""))
                            config_path = get_adapter(harness_name).mcp_manifest_path(scope)
                            if config_path and config_path not in actual_hashes:
                                # Oversize/inaccessible shared settings are not represented in v2.
                                status = "unverified"
                    mcp_verifications.append(
                        {
                            "harness": harness_name,
                            "component_id": _uuid_or_original(comp.get("id")),
                            "alias": safe_alias,
                            "scope": comp.get("scope", parent.get("scope", "")),
                            "parent_agent_id": _uuid_or_original(parent.get("id")),
                            "status": status,
                        }
                    )
                    if status == "unverified":
                        unverified = True
                    elif status != "verified":
                        drifted.append(
                            {
                                "harness": harness_name,
                                "component": _uuid_or_original(comp.get("id")),
                                "alias": safe_alias,
                                "status": status,
                            }
                        )
                if comp.get("type") == "skill":
                    scope = comp.get("scope", parent.get("scope", ""))
                    status = _skill_status(
                        harness_name,
                        scope,
                        project_dir,
                        safe_alias,
                        comp.get("skill_integrity"),
                        actual_hashes,
                    )
                    skill_verifications.append(
                        {
                            "harness": harness_name,
                            "component_id": _uuid_or_original(comp.get("id")),
                            "alias": safe_alias,
                            "scope": scope,
                            "parent_agent_id": _uuid_or_original(parent.get("id")),
                            "status": status,
                            "location_sha256": skill_location_sha256(harness_name, scope, project_dir, safe_alias),
                        }
                    )
                    if status == "drifted":
                        drifted.append(
                            {
                                "harness": harness_name,
                                "component": _uuid_or_original(comp.get("id")),
                                "alias": safe_alias,
                                "status": status,
                            }
                        )
                if comp.get("type") == "hook":
                    scope = comp.get("scope", parent.get("scope", ""))
                    status = _hook_status(harness_name, project_dir, comp)
                    hook_verifications.append(
                        {
                            "harness": harness_name,
                            "component_id": _uuid_or_original(comp.get("id")),
                            "alias": safe_alias,
                            "scope": scope,
                            "parent_agent_id": _uuid_or_original(parent.get("id")),
                            "status": status,
                            # The (event, command) Claude Code records verbatim when the hook runs.
                            "location_sha256": hook_binding_sha256(comp),
                            # Agent-scoped hooks run only while that agent is active.
                            "hook_agent": _nfc(comp.get("hook_agent")),
                            # Where an agent hook is placed decides when it can run: the agent
                            # file (interactive only) or settings.json behind its agent gate.
                            "hook_placement": hook_placement(comp),
                        }
                    )
                    if status == "drifted":
                        drifted.append(
                            {
                                "harness": harness_name,
                                "component": _uuid_or_original(comp.get("id")),
                                "alias": safe_alias,
                                "status": status,
                            }
                        )
                integrity = comp.get("integrity")
                if not integrity:
                    continue
                for path in _integrity_check_paths(harness_name, comp.get("type", ""), alias or comp.get("name", "")):
                    actual = actual_hashes.get(path)
                    if actual is None:
                        unverified = True
                    elif actual != integrity:
                        drifted.append(
                            {
                                "harness": harness_name,
                                "path": path,
                                "component": comp.get("name", ""),
                                "expected": integrity,
                                "actual": actual,
                            }
                        )
    return {
        "is_canonical": False if drifted else None if unverified else True,
        "drifted_files": drifted,
        "mcp_verifications": mcp_verifications,
        "skill_verifications": skill_verifications,
        "hook_verifications": hook_verifications,
    }


def hook_binding_sha256(component: dict) -> str:
    """SHA-256 of ``event NUL command``: what a hook's runs must name to be attributed to it."""
    event, command = component.get("hook_event"), component.get("hook_command")
    if not isinstance(event, str) or not isinstance(command, str) or not event or not command:
        return ""
    return hashlib.sha256(f"{event}\0{command}".encode()).hexdigest()


def hook_placement(component: dict) -> str:
    """``gated_settings``, ``frontmatter`` (an agent-file hook) or ``settings`` (a standalone settings hook)."""
    if component.get("hook_placement") == "gated_settings" and component.get("hook_agent"):
        return "gated_settings"
    return "frontmatter" if component.get("hook_agent") else "settings"


def _hook_status(harness: str, project_dir: str | None, component: dict) -> str:
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    try:
        return get_adapter(harness).verify_hook_binding(project_dir, component)
    except KeyError:
        return "unverified"


def _skill_status(
    harness: str,
    scope: str,
    directory: str | None,
    alias: str,
    expected: str | None,
    actual_hashes: dict[str, str],
) -> str:
    """Verify one installed skill against the file the harness actually loads (fail closed).

    ``verified`` needs the active SKILL.md in this snapshot to hash to the
    pull-time fingerprint, no same-named skill in the other scope with
    different content, and none in an unhashed location the harness also
    reads. A present file with different content is ``drifted``. An absent
    active file (for example an inactive ``/agent`` profile) is ``unverified``,
    never evidence of drift or use.
    """
    if not alias or not expected:
        return "unverified"
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    try:
        adapter = get_adapter(harness)
        active = adapter.skill_manifest_path(scope, alias)
        other = adapter.skill_manifest_path("project" if scope == "user" else "user", alias)
        shadows = adapter.skill_shadow_paths(scope, directory, alias)
    except KeyError:
        return "unverified"
    actual = actual_hashes.get(active) if active else None
    if actual is None:
        return "unverified"
    if actual != expected:
        return "drifted"
    if other and other in actual_hashes and actual_hashes[other] != expected:
        return "unverified"
    if any(path.exists() for path in shadows):
        return "unverified"
    return "verified"


def _integrity_check_paths(harness: str, comp_type: str, comp_name: str) -> list[str]:
    """Map a component type+name to expected file paths for integrity checking."""
    paths: list[str] = []
    if comp_type == "skill":
        paths.append(f"user:skills/{comp_name}/SKILL.md")
    # MCPs and hooks are in settings/mcp.json and hooks.json respectively,
    # not individually checkable via path.
    return paths
