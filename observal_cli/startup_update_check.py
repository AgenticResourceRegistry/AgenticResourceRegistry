# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Bounded, check-only notice writer for Pi's frozen startup path.

A cached check can never authorize a write; the opted-in apply workers
compare releases fresh under the registry gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import tempfile
import time
from pathlib import Path

from observal_cli import auto_update_policy, config, installed_updates
from observal_cli.errors import CliError

NOTICE_DIR = config.CONFIG_DIR / "update-notices"
CACHE_DIR = config.CONFIG_DIR / "update-check-cache"
CACHE_SECONDS = 24 * 60 * 60
MAX_ITEMS = 20
MAX_NOTE = 600
MAX_NOTICE_BYTES = 64 * 1024
MAX_CACHE_BYTES = 256 * 1024
MAX_OUTSTANDING = 50


def _fingerprint(registry: str, account: str, cwd: str, entries: list[dict]) -> str:
    inventory = [
        {
            key: item.get(key)
            for key in (
                "id",
                "type",
                "harness",
                "scope",
                "directory",
                "current_version",
                "requested_version",
                "lock_digest",
            )
        }
        for item in entries
    ]
    payload = json.dumps([registry, account, str(Path(cwd).resolve()), inventory], sort_keys=True)
    prefix = hashlib.sha256(f"{registry}\0{account}".encode()).hexdigest()[:16]
    return f"{prefix}-{hashlib.sha256(payload.encode()).hexdigest()}"


def invalidate_cache(registry: str, account: str) -> None:
    """Explicit refresh never leaves prior cached notices for this identity."""
    prefix = hashlib.sha256(f"{registry}\0{account}".encode()).hexdigest()[:16]
    for file in CACHE_DIR.glob(f"{prefix}-*.json"):
        file.unlink(missing_ok=True)


def _cached_or_compare(registry: str, account: str, cwd: str, entries: list[dict]) -> list[dict]:
    fingerprint = _fingerprint(registry, account, cwd, entries)
    target = CACHE_DIR / f"{fingerprint}.json"
    try:
        if time.time() - target.stat().st_mtime < CACHE_SECONDS and target.stat().st_size <= MAX_CACHE_BYTES:
            cached = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(cached, list):
                return cached
    except (OSError, ValueError):
        pass
    compared = installed_updates.compare(entries, verify_releases=True)
    # Never cache raw installed-state or release objects (they may contain
    # credentials or component configuration). Only persist notice fields.
    safe = [
        {
            **{
                key: item.get(key)
                for key in (
                    "id",
                    "qualified_name",
                    "type",
                    "harness",
                    "scope",
                    "directory",
                    "current_version",
                    "latest_version",
                    "outdated",
                    "release_verified",
                    "status",
                    "reason",
                )
            },
            "release": {
                "description": str((item.get("release") or {}).get("description") or "")[:MAX_NOTE],
                "changelog": str((item.get("release") or {}).get("changelog") or "")[:MAX_NOTE],
            }
            if item.get("release_verified")
            else None,
        }
        for item in compared
    ]
    # A future installer must ALWAYS re-fetch; this cache is notice-only.
    try:
        _write_json(target, safe, MAX_CACHE_BYTES)
        _prune_directory(CACHE_DIR, 100)
    except (OSError, ValueError):
        pass
    return safe


def _message(item: dict, *, enabled: bool) -> dict | None:
    if not item.get("outdated"):
        return None
    verified = item.get("release_verified") is True
    release = item.get("release") if verified else None
    release = release if isinstance(release, dict) else {}
    name = str(item.get("qualified_name") or item.get("id") or "item")[:180]
    status = "available" if verified else "unverified"
    reason = str(item.get("reason") or "")[:MAX_NOTE]
    if item.get("scope") != "user" or item.get("type") != "agent":
        reason = reason or "Automatic installation is not available for this item yet."
    elif enabled and not reason:
        reason = "Automatic installation is not active yet; update manually if you want this release now."
    target = shlex.quote(name)
    root = item.get("directory")
    if item.get("type") == "agent" and item.get("harness") == "pi" and item.get("scope") in {"user", "project"}:
        command = f"observal agent pull {target} --harness pi --upgrade --scope {item['scope']}"
        if root and item.get("scope") == "project":
            command += f" --dir {shlex.quote(str(root))}"
    elif (
        item.get("type") == "agent"
        and item.get("harness") == "claude-code"
        and item.get("scope") == "user"
        and isinstance(root, str)
        and root
    ):
        command = f"observal agent pull {target} --harness claude-code --scope user --dir {shlex.quote(root)} --upgrade"
    elif item.get("type") == "skill" and item.get("scope") == "user" and item.get("harness") in {"pi", "claude-code"}:
        command = f"observal registry skill install {target} --harness {item['harness']} --scope user"
    elif item.get("type") == "mcp" and item.get("scope") == "user" and item.get("harness") in {"pi", "claude-code"}:
        command = f"observal registry mcp install {target} --harness {item['harness']} --managed"
    else:
        command = None  # Unmanaged snippets and project installs need separate instructions.
    return {
        "name": name,
        "type": item.get("type"),
        "scope": item.get("scope"),
        "current_version": item.get("current_version"),
        "latest_version": item.get("latest_version"),
        "status": status,
        "reason": reason,
        "description": str(release.get("description") or "")[:MAX_NOTE],
        "changelog": str(release.get("changelog") or "")[:MAX_NOTE],
        "manual_command": command if verified else None,
    }


def _sync_directory(directory: Path) -> None:
    """Persist directory-entry changes on POSIX; Windows cannot fsync directories."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path: Path, data: object, max_bytes: int) -> None:
    body = json.dumps(data, ensure_ascii=False).encode()
    if len(body) > max_bytes:
        raise ValueError("Startup update result exceeds size limit")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".update-check-", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        _sync_directory(path.parent)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def _prune_directory(directory: Path, limit: int) -> None:
    rows = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in rows[limit:]:
        stale.unlink(missing_ok=True)


def check_pi(cwd: str, session_id: str, notice_key: str) -> None:
    """Write one cached Pi notice; never use it as install authorization."""
    if len(notice_key) != 64 or any(char not in "0123456789abcdef" for char in notice_key):
        raise ValueError("Invalid startup notice key")
    if len(session_id) > 256 or not session_id:
        raise ValueError("Invalid session identifier")
    registry = auto_update_policy.active_registry()
    account = auto_update_policy.active_account()
    payload: dict = {
        "schema": 1,
        "registry": registry,
        "account_id": account,
        "session_id": session_id,
        "harness": "pi",
        "checked_at": int(time.time()),
        "items": [],
        "warning": None,
        "effective_in_current_session": "unknown",  # check-only: never modifies active files
    }
    try:
        installed = installed_updates.inventory_for_context("pi", cwd)
        if installed:
            findings = _cached_or_compare(registry, account, cwd, installed)
            try:
                enabled = auto_update_policy.policy_status(registry)["effective"]
            except (auto_update_policy.PolicyError, OSError):
                enabled = False
                payload["warning"] = "Auto-update policy is unreadable; automatic installs are disabled."
            payload["items"] = [msg for item in findings if (msg := _message(item, enabled=enabled))][:MAX_ITEMS]
    except (CliError, OSError, ValueError, TypeError) as error:
        # Do not put registry errors, paths, credentials or raw payloads in the UI.
        payload["warning"] = f"Update check could not complete ({type(error).__name__}); run `observal outdated` later."
    _write_json(NOTICE_DIR / f"{notice_key}.json", payload, MAX_NOTICE_BYTES)
    _prune_directory(NOTICE_DIR, MAX_OUTSTANDING)
