# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unified version check against GitHub Releases and connected server.

Used by:
  - CLI post-command hook (notification banner)
  - Version enforcement gate (hard block on mismatch)
  - `observal self upgrade` (resolve latest)
  - `observal server upgrade` (resolve latest)
  - Auto-update on startup (minor/patch only)

Two check modes:
  - Server mode: CLI is connected to a server. The server_version from
    /api/v1/config/version is the canonical target. CLI must match it.
  - GitHub mode: no server configured. Check GitHub Releases API for latest.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from observal_cli.config import CONFIG_DIR
from observal_cli.config import load as load_config

CACHE_FILE = CONFIG_DIR / "version_cache.json"
GITHUB_REPO_DEFAULT = "Observal/Observal"
GITHUB_API_BASE = "https://api.github.com/repos"
GHCR_API_BASE = "https://ghcr.io/v2/observal"
CHECK_INTERVAL_DEFAULT = 86400  # 24 hours
CHECK_TIMEOUT = 3  # seconds, must never block CLI
MAX_RESPONSE_SIZE = 1_048_576  # 1MB
ASSET_NAME_RE = re.compile(r"^observal-[a-z]+-[a-z0-9]+(\.exe)?$")

# Hard floor: versioning didn't exist before 1.0.0, never allow going below this.
VERSION_FLOOR = "1.0.0"


@dataclass(frozen=True)
class UpdateAvailable:
    """Represents a version mismatch that the user should act on."""

    current: str
    latest: str  # target version (could be newer OR older for server)
    release_url: str
    published_at: str
    source: str  # "server" or "github"
    direction: str = "upgrade"  # "upgrade" or "downgrade"


def get_current_version() -> str:
    """Get installed CLI version via importlib.metadata."""
    try:
        from importlib.metadata import version

        return version("observal-cli")
    except Exception:
        return "0.0.0"


def check_version_floor(target: str) -> bool:
    """Return True if target version is at or above the version floor.

    Rejects any version below 1.0.0 since versioning didn't exist before that.
    """
    from packaging.version import InvalidVersion, Version

    try:
        return Version(target) >= Version(VERSION_FLOOR)
    except InvalidVersion:
        return False


def _github_repo() -> str:
    """Get configured GitHub repo (allows override if repo moves)."""
    cfg = load_config()
    return cfg.get("update_check_repo") or GITHUB_REPO_DEFAULT


def _is_newer(latest: str, current: str) -> bool:
    """Semver comparison using packaging.version."""
    from packaging.version import InvalidVersion, Version

    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


# ── Cache integrity ─────────────────────────────────────────────


def _machine_key() -> bytes:
    """Derive a stable machine-local key for HMAC."""
    for path in [Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")]:
        if path.exists():
            try:
                return path.read_bytes().strip()
            except OSError:
                continue
    # macOS: IOPlatformUUID
    try:
        r = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if "IOPlatformUUID" in r.stdout:
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', r.stdout)
            if m:
                return m.group(1).encode()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    # Fallback: hostname. In containers where hostname changes between runs,
    # this simply invalidates the cache and triggers a fresh version check.
    return socket.gethostname().encode()


def _cache_hmac(data: bytes) -> str:
    """HMAC for cache integrity (keyed by machine-id)."""
    key = _machine_key()
    return _hmac.new(key, data, hashlib.sha256).hexdigest()[:16]


# ── Cache read/write ────────────────────────────────────────────


def _read_cache() -> dict | None:
    """Read and verify cache integrity. Returns None if missing/corrupt/tampered.

    Safe against concurrent writes: on POSIX, _write_cache uses atomic rename.
    On Windows where rename is not atomic, a partial read will produce invalid
    JSON which is caught here and treated as a cache miss.
    """
    try:
        if not CACHE_FILE.exists():
            return None
        raw = CACHE_FILE.read_text()
        if not raw.strip():
            return None  # Empty file (partial write on Windows)
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        # Verify HMAC if present (skip for legacy caches without it)
        stored_hmac = data.pop("_hmac", None)
        if stored_hmac:
            payload = json.dumps(data, sort_keys=True).encode()
            expected = _cache_hmac(payload)
            if not _hmac.compare_digest(stored_hmac, expected):
                return None  # Tampered - treat as missing
        return data
    except (json.JSONDecodeError, OSError, ValueError, UnicodeDecodeError):
        return None


def _write_cache(data: dict) -> None:
    """Atomically write cache with HMAC integrity tag."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Compute HMAC on the data without the _hmac key
    clean = {k: v for k, v in data.items() if k != "_hmac"}
    payload = json.dumps(clean, sort_keys=True).encode()
    clean["_hmac"] = _cache_hmac(payload)

    tmp = CACHE_FILE.with_suffix(".tmp")
    old_umask = os.umask(0o077)
    try:
        tmp.write_text(json.dumps(clean, indent=2))
        tmp.replace(CACHE_FILE)  # atomic on POSIX
    finally:
        os.umask(old_umask)


def _should_check(cache: dict | None, interval: int) -> bool:
    """Determine if a fresh check is needed based on cache staleness."""
    if cache is None:
        return True
    last_checked = cache.get("last_checked")
    if not last_checked:
        return True
    try:
        last_ts = datetime.fromisoformat(last_checked).timestamp()
    except (ValueError, TypeError):
        return True
    now = time.time()
    # Guard against clock skew: if last_checked is in the future, re-check
    if last_ts > now:
        return True
    return (now - last_ts) >= interval


# ── Fetch from server (server mode) ────────────────────────


def _fetch_from_server(server_url: str, token: str) -> dict | None:
    """Check connected server for its version (the canonical target for CLI).

    Returns dict with latest_version, release_url, source="server" or None.
    The server_version IS the target - CLI must match it.
    """
    try:
        resp = httpx.get(
            f"{server_url}/api/v1/config/version",
            timeout=CHECK_TIMEOUT,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": f"observal-cli/{get_current_version()}",
            },
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None

        # Server version is the canonical target - CLI must match it
        server_ver = data.get("server_version")
        if not server_ver:
            return None

        from packaging.version import InvalidVersion, Version

        try:
            Version(server_ver)
        except InvalidVersion:
            return None

        return {
            "latest_version": server_ver,
            "release_url": "",
            "published_at": "",
            "source": "server",
            "server_version": server_ver,
        }
    except (httpx.HTTPError, json.JSONDecodeError, KeyError):
        return None


# ── Fetch from GitHub (community mode) ─────────────────────────


def _fetch_from_github(include_pre: bool = False) -> dict | None:
    """Fetch latest release from GitHub Releases API.

    Returns dict with latest_version, release_url, etc. or None on failure.
    """
    repo = _github_repo()
    url = f"{GITHUB_API_BASE}/{repo}/releases"
    url += "?per_page=1" if include_pre else "/latest"

    try:
        resp = httpx.get(
            url,
            timeout=CHECK_TIMEOUT,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"observal-cli/{get_current_version()}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            follow_redirects=False,
        )
        if resp.status_code != 200:
            return None
        if len(resp.content) > MAX_RESPONSE_SIZE:
            return None

        data = resp.json()
        if include_pre and isinstance(data, list):
            data = data[0] if data else None
        if not data or not isinstance(data, dict):
            return None

        tag = data.get("tag_name", "").lstrip("v")
        from packaging.version import InvalidVersion, Version

        try:
            Version(tag)
        except InvalidVersion:
            return None

        return {
            "latest_version": tag,
            "release_url": data.get("html_url", ""),
            "published_at": data.get("published_at", ""),
            "prerelease": data.get("prerelease", False),
            "source": "github",
        }
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError):
        return None


# ── Fetch from GHCR (server image versions) ────────────────────


def fetch_available_server_images() -> list[str]:
    """List available server image tags from GHCR.

    Used by `observal server versions` and `server upgrade` to verify
    an image exists before attempting to pull.
    """
    try:
        # GHCR requires a token even for public images
        # First get an anonymous token
        token_resp = httpx.get(
            "https://ghcr.io/token?scope=repository:observal/observal-api:pull",
            timeout=10,
            headers={"User-Agent": f"observal-cli/{get_current_version()}"},
        )
        if token_resp.status_code != 200:
            return []
        token = token_resp.json().get("token", "")
        if not token:
            return []

        # List tags
        resp = httpx.get(
            f"{GHCR_API_BASE}/observal-api/tags/list",
            timeout=10,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.oci.image.index.v1+json",
                "User-Agent": f"observal-cli/{get_current_version()}",
            },
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        tags = data.get("tags", [])
        # Filter to semver-like tags (exclude "latest", "sha-xxx", etc.)
        from packaging.version import InvalidVersion, Version

        versions = []
        for tag in tags:
            clean = tag.lstrip("v")
            try:
                Version(clean)
                versions.append(clean)
            except InvalidVersion:
                continue
        return sorted(versions, key=lambda v: Version(v), reverse=True)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError):
        return []


def verify_server_image_exists(version: str) -> bool:
    """Check if a specific server image tag exists on GHCR.

    Used before `docker compose pull` to fail fast if image doesn't exist.
    """
    try:
        token_resp = httpx.get(
            "https://ghcr.io/token?scope=repository:observal/observal-api:pull",
            timeout=10,
            headers={"User-Agent": f"observal-cli/{get_current_version()}"},
        )
        if token_resp.status_code != 200:
            return False
        token = token_resp.json().get("token", "")

        resp = httpx.head(
            f"{GHCR_API_BASE}/observal-api/manifests/{version}",
            timeout=10,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.oci.image.index.v1+json",
                "User-Agent": f"observal-cli/{get_current_version()}",
            },
        )
        return resp.status_code == 200
    except (httpx.HTTPError, json.JSONDecodeError, KeyError):
        return False


# ── Resolve update source ───────────────────────────────────────


def _resolve_update_source() -> dict | None:
    """Determine check mode and fetch the target version.

    - If server_url configured + reachable: server mode (server)
    - Otherwise: GitHub mode (community)
    """
    cfg = load_config()
    server_url = cfg.get("server_url", "").rstrip("/")
    token = cfg.get("access_token", "")

    # Try server mode first (enterprise: match the server version)
    if server_url and token:
        result = _fetch_from_server(server_url, token)
        if result:
            return result

    # Fall back to GitHub mode
    return _fetch_from_github()


# ── Public API ──────────────────────────────────────────────────


def maybe_check() -> UpdateAvailable | None:
    """Check for updates if enough time has passed since last check.

    Returns UpdateAvailable if a newer version exists, None otherwise.
    Non-blocking: silently returns None on any failure. Never takes >3s.
    """
    try:
        cfg = load_config()
        if not cfg.get("update_check", True):
            return None
        if os.environ.get("OBSERVAL_NO_UPDATE_CHECK"):
            return None

        interval = int(cfg.get("update_check_interval", CHECK_INTERVAL_DEFAULT))
        cache = _read_cache()

        if not _should_check(cache, interval):
            # Use cached result
            if cache and cache.get("latest_version"):
                current = get_current_version()
                target = cache["latest_version"]
                source = cache.get("source", "github")
                if _is_newer(target, current):
                    return UpdateAvailable(
                        current=current,
                        latest=target,
                        release_url=cache.get("release_url", ""),
                        published_at=cache.get("published_at", ""),
                        source=source,
                        direction="upgrade",
                    )
                elif source == "server" and target != current:
                    # Server: server recommends an older/different version
                    return UpdateAvailable(
                        current=current,
                        latest=target,
                        release_url="",
                        published_at=cache.get("published_at", ""),
                        source=source,
                        direction="downgrade",
                    )
            return None

        # Fetch fresh data
        release = _resolve_update_source()
        now_iso = datetime.now(UTC).isoformat()

        if release is None:
            # Write last_attempted so we don't retry every invocation
            _write_cache({**(cache or {}), "last_checked": now_iso, "fetch_failed": True})
            return None

        # Update cache
        _write_cache(
            {
                "last_checked": now_iso,
                "latest_version": release["latest_version"],
                "release_url": release.get("release_url", ""),
                "published_at": release.get("published_at", ""),
                "source": release.get("source", "github"),
                "server_version": release.get("server_version", ""),
                "fetch_failed": False,
            }
        )

        current = get_current_version()
        target = release["latest_version"]
        source = release.get("source", "github")
        if _is_newer(target, current):
            return UpdateAvailable(
                current=current,
                latest=target,
                release_url=release.get("release_url", ""),
                published_at=release.get("published_at", ""),
                source=source,
                direction="upgrade",
            )
        elif source == "server" and target != current:
            # Server: server recommends a different (likely older) version
            return UpdateAvailable(
                current=current,
                latest=target,
                release_url="",
                published_at=release.get("published_at", ""),
                source=source,
                direction="downgrade",
            )
        return None
    except Exception:
        # Never crash the CLI for a version check
        return None


def fetch_all_releases(include_pre: bool = False) -> list[dict]:
    """Fetch all releases from GitHub for --list. Paginated, longer timeout."""
    repo = _github_repo()
    results: list[dict] = []
    for page in range(1, 11):  # Safety cap: 10 pages = 100 releases
        try:
            resp = httpx.get(
                f"{GITHUB_API_BASE}/{repo}/releases?per_page=10&page={page}",
                timeout=15,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"observal-cli/{get_current_version()}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            if not data:
                break
            for r in data:
                if not include_pre and r.get("prerelease"):
                    continue
                tag = r.get("tag_name", "").lstrip("v")
                results.append(
                    {
                        "version": tag,
                        "published_at": r.get("published_at", ""),
                        "prerelease": r.get("prerelease", False),
                        "url": r.get("html_url", ""),
                    }
                )
        except Exception:
            break
    return results


# ── Version enforcement ─────────────────────────────────────────────


def check_version_compatibility(server_url: str) -> None:
    """Enforce exact CLI/server version match.

    The server version is the canonical target. CLI pings the server to
    get its version, then requires an exact match. Shows the correct
    upgrade or downgrade command based on which direction the mismatch is.

    Reads from the local version cache first (populated by auto-update check)
    to avoid a duplicate network call. Falls back to a fresh fetch if needed.
    """
    import typer
    from packaging.version import InvalidVersion, Version
    from rich import print as rprint

    cli_ver_str = get_current_version()
    if cli_ver_str == "0.0.0":
        return  # dev install, skip check

    # Use cache if fresh (< 60s), otherwise fetch live from the server.
    server_ver = None
    cache = _read_cache()
    if cache and cache.get("server_version") and cache.get("source") == "server" and not _should_check(cache, 60):
        server_ver = cache["server_version"]

    if not server_ver:
        try:
            resp = httpx.get(f"{server_url.rstrip('/')}/api/v1/config/version", timeout=5)
            if resp.status_code != 200:
                return
            data = resp.json()
            server_ver = data.get("server_version")
        except Exception:
            return  # server unreachable or doesn't support this endpoint

    if not server_ver or server_ver == "dev":
        return  # dev server, skip enforcement

    try:
        cli_v = Version(cli_ver_str)
        srv_v = Version(server_ver)
    except InvalidVersion:
        return

    if cli_v == srv_v:
        return

    from observal_cli.install_detector import upgrade_command

    if cli_v > srv_v:
        install_command = f"observal self downgrade --version {server_ver}"
        rprint(
            f"\n[bold red]\u2716 CLI version {cli_ver_str} is ahead of server {server_ver}.[/bold red]\n"
            f"  Downgrade the CLI to match your server:\n\n"
            f"    [cyan]{install_command}[/cyan]\n"
        )
    else:
        install_command = upgrade_command(server_ver)
        rprint(
            f"\n[bold red]\u2716 CLI version {cli_ver_str} is behind server {server_ver}.[/bold red]\n"
            f"  Upgrade the CLI to match your server:\n\n"
            f"    [cyan]{install_command}[/cyan]\n"
        )
    raise typer.Exit(1)
