# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""File-based concurrency lock for upgrade operations.

Prevents two CLI processes from upgrading simultaneously.
Uses PID + timestamp to detect stale locks from crashed processes.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path  # noqa: TC003 (used at runtime)

from observal_cli.config import CONFIG_DIR

STALE_THRESHOLD = 1800  # 30 minutes; after this, assume lock is orphaned
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600


class UpgradeLockError(RuntimeError):
    """Raised when another upgrade is already in progress."""

    pass


def acquire_lock(scope: str) -> Path:
    """Acquire an upgrade lock for the given scope.

    Args:
        scope: Lock scope identifier (e.g., "cli", "server").

    Returns:
        Path to the lock file (pass to release_lock when done).

    Raises:
        UpgradeLockError: If another process holds the lock.
    """
    lock_path = CONFIG_DIR / f".{scope}-upgrade.lock"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            info = json.loads(lock_path.read_text())
            pid = info.get("pid")
            ts = info.get("timestamp", 0)

            # Check if lock is stale (>30 minutes old)
            if time.time() - ts > STALE_THRESHOLD:
                lock_path.unlink(missing_ok=True)
            elif pid and _pid_alive(pid):
                raise UpgradeLockError(
                    f"Another upgrade is in progress (PID {pid}, started {_format_age(ts)} ago). "
                    f"If this is stale, delete: {lock_path}"
                )
            else:
                # PID is dead, lock is orphaned
                lock_path.unlink(missing_ok=True)
        except (json.JSONDecodeError, OSError, KeyError):
            # Corrupt lock file, remove it
            lock_path.unlink(missing_ok=True)

    # Write new lock
    lock_data = {
        "pid": os.getpid(),
        "timestamp": time.time(),
        "scope": scope,
    }
    lock_path.write_text(json.dumps(lock_data))
    return lock_path


def release_lock(lock_path: Path) -> None:
    """Release the upgrade lock."""
    lock_path.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)  # Signal 0 = check existence, don't kill
        return True
    except (OSError, ProcessLookupError):
        return False


def _format_age(timestamp: float) -> str:
    """Format time elapsed since timestamp as human-readable string."""
    elapsed = int(time.time() - timestamp)
    if elapsed < SECONDS_PER_MINUTE:
        return f"{elapsed}s"
    elif elapsed < SECONDS_PER_HOUR:
        return f"{elapsed // SECONDS_PER_MINUTE}m"
    else:
        return f"{elapsed // SECONDS_PER_HOUR}h {(elapsed % SECONDS_PER_HOUR) // SECONDS_PER_MINUTE}m"
