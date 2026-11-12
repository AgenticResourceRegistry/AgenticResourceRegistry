# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Faeka Ansari <97490463+fykaa@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Self-update mechanism for the Observal standalone binary.

Downloads the latest release, verifies checksums, and atomically
replaces the running binary. Supports version pinning and rollback.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

import httpx
from rich.console import Console

from observal_cli.server.constants import GITHUB_REPO, OBSERVAL_HOME, detect_platform

console = Console()

UPDATE_CHECK_CACHE = OBSERVAL_HOME / ".update-check"
UPDATE_CHECK_INTERVAL = 86400  # 24 hours


def get_current_version() -> str:
    """Get the version of the currently running binary."""
    from importlib.metadata import version

    try:
        return version("observal-cli")
    except Exception:
        return "0.0.0"


def _get_binary_path() -> Path:
    """Get the path to the currently running binary."""
    return Path(sys.executable).resolve()


def _get_backup_path() -> Path:
    """Get the path for the rollback backup."""
    return OBSERVAL_HOME / "bin" / "observal.bak"


def fetch_latest_version() -> str | None:
    """Query GitHub for the latest release version.

    Returns version tag (e.g. "v0.6.0") or None if request fails.
    Uses the unified version_check module.
    """
    from observal_cli.version_check import _fetch_from_github

    result = _fetch_from_github()
    if result:
        return f"v{result['latest_version']}"
    return None


def check_for_update(*, quiet: bool = False) -> str | None:
    """Check if an update is available.

    Uses a local cache to avoid hitting the API on every invocation.

    Args:
        quiet: If True, suppress all output.

    Returns:
        The latest version string if an update is available, None otherwise.
    """
    # Check cache
    if UPDATE_CHECK_CACHE.exists():
        try:
            cache_data = json.loads(UPDATE_CHECK_CACHE.read_text())
            if time.time() - cache_data.get("checked_at", 0) < UPDATE_CHECK_INTERVAL:
                latest = cache_data.get("latest_version")
                current = f"v{get_current_version()}"
                if latest and latest != current:
                    return latest
                return None
        except (json.JSONDecodeError, KeyError):
            pass

    latest = fetch_latest_version()
    if latest is None:
        return None

    # Update cache
    UPDATE_CHECK_CACHE.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_CHECK_CACHE.write_text(json.dumps({"latest_version": latest, "checked_at": time.time()}))

    current = f"v{get_current_version()}"
    if latest != current:
        if not quiet:
            console.print(
                f"[yellow]Update available:[/yellow] {current} → [bold]{latest}[/bold]. "
                f"Run: [cyan]observal self upgrade[/cyan]"
            )
        return latest

    return None


def _get_artifact_name() -> str:
    """Get the expected artifact name for this platform."""
    os_name, arch = detect_platform()
    ext = ".exe" if platform.system().lower() == "windows" else ""
    return f"observal-server-{os_name}-{arch}{ext}"


def _fetch_checksums(version: str) -> dict[str, str]:
    """Download checksums for a specific release."""
    url = f"https://github.com/{GITHUB_REPO}/releases/download/{version}/checksums.txt"
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=30)
        resp.raise_for_status()
        checksums = {}
        for line in resp.text.strip().splitlines():
            parts = line.split()
            if len(parts) == 2:
                checksums[parts[1]] = parts[0]
        return checksums
    except httpx.HTTPError:
        return {}


def _verify_binary(path: Path, checksums: dict[str, str], artifact_name: str) -> bool:
    """Verify SHA256 checksum of downloaded binary."""
    if artifact_name not in checksums:
        console.print("[yellow]Warning:[/yellow] No checksum available, skipping verification")
        return True

    sha256 = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)

    actual = sha256.hexdigest()
    expected = checksums[artifact_name]

    if actual != expected:
        console.print("[red]Checksum verification failed![/red]")
        console.print(f"  Expected: {expected}")
        console.print(f"  Got:      {actual}")
        return False

    return True


def update(*, version: str | None = None) -> bool:
    """Download and install the latest (or specified) version.

    Args:
        version: Specific version to install (e.g. "v0.6.0"). If None, uses latest.

    Returns:
        True if update was successful, False otherwise.
    """
    current = f"v{get_current_version()}"

    if version is None:
        version = fetch_latest_version()
        if version is None:
            console.print("[red]Error:[/red] Could not determine latest version")
            return False

    if version == current:
        console.print(f"[green]\u2713[/green] Already up to date ({current})")
        return True

    console.print(f"[blue]==>[/blue] Updating: {current} → {version}")

    artifact_name = _get_artifact_name()
    download_url = f"https://github.com/{GITHUB_REPO}/releases/download/{version}/{artifact_name}"

    # Download to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tmp:
        tmp_path = Path(tmp.name)

    try:
        console.print(f"[blue]==>[/blue] Downloading {artifact_name}...")
        with httpx.stream("GET", download_url, follow_redirects=True, timeout=300) as resp:
            resp.raise_for_status()
            with tmp_path.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)

        # Verify checksum
        checksums = _fetch_checksums(version)
        if checksums:
            if not _verify_binary(tmp_path, checksums, artifact_name):
                tmp_path.unlink(missing_ok=True)
                return False
            console.print("[blue]==>[/blue] Verified checksum")
        else:
            console.print("[yellow]==>[/yellow] Checksum unavailable; skipping verification")

        # Backup current binary
        current_binary = _get_binary_path()
        backup_path = _get_backup_path()
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        if current_binary.exists():
            shutil.copy2(str(current_binary), str(backup_path))

        # Atomic replace
        tmp_path.chmod(0o755)
        os.replace(str(tmp_path), str(current_binary))

        console.print(f"[green]\u2713[/green] Updated to {version}")
        console.print(f"  Binary: {current_binary}")
        console.print(f"  Backup: {backup_path}")
        console.print()
        console.print(
            "  If the server is running, restart it: [cyan]observal server stop && observal server start[/cyan]"
        )

        # Invalidate update check cache
        UPDATE_CHECK_CACHE.unlink(missing_ok=True)

        return True

    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error:[/red] Download failed (HTTP {e.response.status_code})")
        console.print(f"  URL: {download_url}")
        console.print(f"  Ensure version {version} exists and has server binaries.")
        tmp_path.unlink(missing_ok=True)
        return False

    except Exception as e:
        console.print(f"[red]Error:[/red] Update failed: {e}")
        tmp_path.unlink(missing_ok=True)
        return False


def rollback() -> bool:
    """Restore the previous binary version from backup.

    Returns:
        True if rollback succeeded, False otherwise.
    """
    backup_path = _get_backup_path()
    current_binary = _get_binary_path()

    if not backup_path.exists():
        console.print("[red]Error:[/red] No backup found. Cannot rollback.")
        return False

    try:
        os.replace(str(backup_path), str(current_binary))
        console.print("[green]\u2713[/green] Rolled back to previous version")
        console.print(f"  Binary: {current_binary}")
        return True
    except Exception as e:
        console.print(f"[red]Error:[/red] Rollback failed: {e}")
        return False
