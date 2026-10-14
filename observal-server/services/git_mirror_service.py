# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Git mirroring and component discovery service."""

import fcntl
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger as optic

import services.dynamic_settings as ds
from services.ssrf_guard import is_private_url as _ssrf_is_private

_git_mirror_path = ds.get_sync("misc.git_mirror_base_path")
DEFAULT_MIRROR_BASE = Path(_git_mirror_path) if _git_mirror_path else Path(tempfile.gettempdir()) / "observal_mirrors"


@dataclass
class DiscoveredComponent:
    name: str
    path: str  # relative to repo root
    component_type: str  # mcp, skill, hook, prompt, sandbox
    description: str = ""


@dataclass
class SyncResult:
    success: bool
    components: list[DiscoveredComponent] = field(default_factory=list)
    commit_sha: str = ""
    error: str = ""


def _mirror_path(git_url: str, base: Path = DEFAULT_MIRROR_BASE) -> Path:
    """Content-addressed mirror directory."""
    optic.trace("computing mirror path for {}", git_url)
    url_hash = hashlib.sha256(git_url.encode()).hexdigest()[:16]
    return base / url_hash


@contextmanager
def _mirror_lock(mirror_dir: Path) -> Iterator[None]:
    """Hold an exclusive lock on one mirror checkout.

    flock locks belong to the open file, so this serializes syncs across worker
    threads and across uvicorn processes that share the mirror base. The lock
    file sits beside the mirror because a fresh clone deletes the mirror directory.
    """
    mirror_dir.parent.mkdir(parents=True, exist_ok=True)
    with open(mirror_dir.with_name(f"{mirror_dir.name}.lock"), "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _run_git(args: list[str], cwd: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a git command with timeout."""
    optic.trace("running git: {} (timeout={}s)", " ".join(args[:3]), timeout)
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def clone_or_update(git_url: str, branch: str = "main", base: Path = DEFAULT_MIRROR_BASE) -> Path:
    """Shallow clone or update a repo mirror. Returns the mirror directory path."""
    optic.trace("cloning {} (branch={})", git_url, branch)
    # Block SSRF via git clone (SEC-013).
    # Set ALLOW_INTERNAL_GIT_URLS=true for self-hosted GitLab / GitHub Enterprise.
    if (
        git_url.startswith(("http://", "https://"))
        and not ds.get_sync_bool("security.allow_internal_git_urls")
        and _ssrf_is_private(git_url)
    ):
        raise ValueError(f"Repository URL resolves to a private/internal address: {git_url}")
    base.mkdir(parents=True, exist_ok=True)
    mirror_dir = _mirror_path(git_url, base)

    if mirror_dir.exists() and (mirror_dir / ".git").exists():
        # Update existing mirror
        result = _run_git(["fetch", "origin", branch, "--depth", "1"], cwd=str(mirror_dir))
        if result.returncode != 0:
            raise RuntimeError(f"git fetch failed: {result.stderr.strip()}")
        result = _run_git(["reset", "--hard", f"origin/{branch}"], cwd=str(mirror_dir))
        if result.returncode != 0:
            raise RuntimeError(f"git reset failed: {result.stderr.strip()}")
    else:
        # Fresh shallow clone
        if mirror_dir.exists():
            shutil.rmtree(mirror_dir)
        result = _run_git(
            [
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                branch,
                git_url,
                str(mirror_dir),
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr.strip()}")

    return mirror_dir


def get_commit_sha(mirror_dir: Path) -> str:
    """Get the current HEAD commit SHA."""
    optic.trace("scanning mirror directory for components")
    result = _run_git(["rev-parse", "HEAD"], cwd=str(mirror_dir))
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def discover_components(mirror_dir: Path, component_type: str | None = None) -> list[DiscoveredComponent]:
    """Discover components using manifest (primary) or convention scan (fallback)."""
    optic.trace("discovering {} components in mirror", component_type)
    # Try manifest first
    for manifest_name in (".observal.json", "observal.json"):
        manifest_path = mirror_dir / manifest_name
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                return _parse_manifest(manifest, component_type, mirror_dir=mirror_dir)
            except (json.JSONDecodeError, KeyError) as e:
                optic.warning("Invalid manifest {}: {}", manifest_path, e)
                # Fall through to convention scan

    # Convention scan fallback
    return _scan_by_convention(mirror_dir, component_type)


def _safe_path(base: Path, rel: str) -> bool:
    """Check that a relative path stays within base (no traversal)."""
    optic.trace("resolving relative path")
    try:
        resolved = (base / rel).resolve()
        return resolved == base.resolve() or str(resolved).startswith(str(base.resolve()) + "/")
    except (OSError, ValueError):
        return False


def _parse_manifest(
    manifest: dict, component_type: str | None = None, mirror_dir: Path | None = None
) -> list[DiscoveredComponent]:
    """Parse .observal.json manifest."""
    optic.trace("parsing component manifest")
    components = []
    type_keys = {
        "mcp": "mcps",
        "skill": "skills",
        "hook": "hooks",
        "prompt": "prompts",
        "sandbox": "sandboxes",
    }

    types_to_scan = (
        {component_type: type_keys[component_type]} if component_type and component_type in type_keys else type_keys
    )

    for ctype, key in types_to_scan.items():
        for entry in manifest.get(key, []):
            comp_path = entry.get("path", "")
            # Reject path traversal attempts
            if mirror_dir and not _safe_path(mirror_dir, comp_path):
                optic.warning("Skipping component with unsafe path: {}", comp_path)
                continue
            components.append(
                DiscoveredComponent(
                    name=entry.get("name", comp_path.split("/")[-1]),
                    path=comp_path,
                    component_type=ctype,
                    description=entry.get("description", ""),
                )
            )

    return components


# Convention directories per component type
_CONVENTION_DIRS = {
    "mcp": ["src", "mcps", "servers"],
    "skill": ["skills"],
    "hook": ["hooks"],
    "prompt": ["prompts"],
    "sandbox": ["sandboxes"],
}

# Markers that identify a valid component directory
_COMPONENT_MARKERS = {
    "mcp": lambda p: any(f for f in p.rglob("*.py") if ".git" not in f.parts),
    "skill": lambda p: (p / "SKILL.md").exists(),
    "hook": lambda p: (p / "hook.json").exists(),
    "prompt": lambda p: any(p.glob("*.md")) or any(p.glob("*.txt")),
    "sandbox": lambda p: (p / "Dockerfile").exists(),
}


def _scan_by_convention(mirror_dir: Path, component_type: str | None = None) -> list[DiscoveredComponent]:
    """Discover components by scanning conventional directories."""
    optic.trace("discovering {} components in mirror", component_type)
    components = []
    types_to_scan = (
        {component_type: _CONVENTION_DIRS[component_type]}
        if component_type and component_type in _CONVENTION_DIRS
        else _CONVENTION_DIRS
    )

    for ctype, dirs in types_to_scan.items():
        marker = _COMPONENT_MARKERS.get(ctype, lambda p: True)
        for dir_name in dirs:
            base = mirror_dir / dir_name
            if not base.exists() or not base.is_dir():
                continue
            for item in sorted(base.iterdir()):
                if item.is_symlink() or not item.is_dir():
                    continue
                if not _safe_path(mirror_dir, str(item.relative_to(mirror_dir))):
                    continue
                if marker(item):
                    components.append(
                        DiscoveredComponent(
                            name=item.name,
                            path=str(item.relative_to(mirror_dir)),
                            component_type=ctype,
                        )
                    )

    return components


_FASTMCP_PATTERN = re.compile(r"FastMCP\(|from\s+mcp\.server\.fastmcp\s+import|from\s+fastmcp\s+import")


def validate_mcp_component(component_path: Path) -> tuple[bool, str]:
    """Validate an MCP component uses FastMCP. Returns (passed, detail)."""
    optic.trace("validating component at discovered path")
    for py_file in component_path.rglob("*.py"):
        if ".git" in py_file.parts or py_file.is_symlink():
            continue
        try:
            content = py_file.read_text(errors="ignore")
            if _FASTMCP_PATTERN.search(content):
                return True, f"FastMCP found in {py_file.name}"
        except Exception:
            continue
    return False, "No FastMCP usage found. MCP servers must use FastMCP."


def sync_source(
    git_url: str, component_type: str, branch: str = "main", base: Path = DEFAULT_MIRROR_BASE
) -> SyncResult:
    """Full sync pipeline: clone -> discover -> validate. Returns SyncResult."""
    optic.debug("mirroring {} components from {} (branch={})", component_type, git_url, branch)
    try:
        # Held until validation ends: another sync's reset would change files mid-scan.
        with _mirror_lock(_mirror_path(git_url, base)):
            mirror_dir = clone_or_update(git_url, branch=branch, base=base)
            commit_sha = get_commit_sha(mirror_dir)
            components = discover_components(mirror_dir, component_type)

            # Validate MCP components
            valid_components = []
            for comp in components:
                if comp.component_type == "mcp":
                    comp_path = mirror_dir / comp.path
                    passed, detail = validate_mcp_component(comp_path)
                    if not passed:
                        optic.warning("MCP validation failed for {}: {}", comp.name, detail)
                        continue  # Skip invalid MCPs
                valid_components.append(comp)

        return SyncResult(
            success=True,
            components=valid_components,
            commit_sha=commit_sha,
        )
    except Exception as e:
        optic.error("Sync failed for {}", git_url)
        return SyncResult(success=False, error=str(e))
