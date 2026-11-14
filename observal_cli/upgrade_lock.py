# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Concurrency lock for upgrade operations.

Prevents two processes from changing the CLI (or the local server) at once.
Holding the lock means holding an exclusive OS lock on the lock file:
``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows. Taking it is one
atomic call, so two processes can never both hold it; the OS drops it when the
holder exits or crashes, so there is no stale lock to guess about; and only the
holder's own handle can release it.

The file itself is never deleted. A waiter may already have it open, and
deleting it would let a third process lock a new file at the same path while
that waiter locks the old one. Its contents (PID and start time) only describe
the holder in the error message.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from observal_cli.config import CONFIG_DIR

if TYPE_CHECKING:
    from pathlib import Path

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
# Windows locks are mandatory: a locked byte cannot be read by anyone else. The
# lock is taken on one byte far past the holder description, which stays readable.
_WINDOWS_LOCK_OFFSET = 1 << 30


_held: set[int] = set()
# Held locks now shared with an installer that is still running. Releasing one only
# closes this process's handle: unlocking would unlock the installer's copy too.
_handed_off: set[int] = set()


def held_lock_fds() -> tuple[int, ...]:
    """Descriptors of the locks this process holds, for an installer that must keep them if this process dies."""
    return tuple(sorted(_held))


def hand_off_held_locks() -> None:
    """Leave the held locks to the installer that shares them (POSIX); it releases them by exiting."""
    if os.name != "nt":
        _handed_off.update(_held)


class UpgradeLockError(RuntimeError):
    """Raised when another upgrade is already in progress."""


@dataclass(frozen=True)
class UpgradeLock:
    """A held upgrade lock. Pass it to :func:`release_lock`."""

    path: Path
    fd: int


def acquire_lock(scope: str) -> UpgradeLock:
    """Take the upgrade lock for ``scope`` ("cli" or "server") without waiting.

    Raises:
        UpgradeLockError: If another process holds the lock.
    """
    lock_path = CONFIG_DIR / f".{scope}-upgrade.lock"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Not inheritable (PEP 446): an installer started while the lock is held must
    # not keep it after this process is done.
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _lock(fd)
    except OSError:
        holder = _describe_holder(fd)
        os.close(fd)
        raise UpgradeLockError(f"Another upgrade is in progress{holder}.") from None

    try:
        _write_holder(fd, {"pid": os.getpid(), "timestamp": time.time(), "scope": scope})
    except OSError:
        pass  # The description is informational; the OS lock is what is held.
    _held.add(fd)
    return UpgradeLock(lock_path, fd)


def release_lock(lock: UpgradeLock) -> None:
    """Release a lock this process holds."""
    _held.discard(lock.fd)
    if lock.fd in _handed_off:
        _handed_off.discard(lock.fd)
        os.close(lock.fd)
        return
    try:
        _write_holder(lock.fd, None)
        _unlock(lock.fd)
    except OSError:
        pass  # Closing the handle releases the lock regardless.
    finally:
        os.close(lock.fd)


if sys.platform == "win32":
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _write_holder(fd: int, holder: dict | None) -> None:
    # Emptied on release, so an older CLI that checks the file's contents
    # treats it as a corrupt lock it may replace, not a live one.
    os.ftruncate(fd, 0)
    if holder is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(holder).encode())


def _describe_holder(fd: int) -> str:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        info = json.loads(os.read(fd, 4096) or b"{}")
        pid, started = int(info["pid"]), float(info["timestamp"])
    except (OSError, ValueError, TypeError, KeyError):
        return ""
    return f" (PID {pid}, started {_format_age(started)} ago)"


def _format_age(timestamp: float) -> str:
    """Format time elapsed since timestamp as human-readable string."""
    elapsed = max(0, int(time.time() - timestamp))
    if elapsed < SECONDS_PER_MINUTE:
        return f"{elapsed}s"
    elif elapsed < SECONDS_PER_HOUR:
        return f"{elapsed // SECONDS_PER_MINUTE}m"
    else:
        return f"{elapsed // SECONDS_PER_HOUR}h {(elapsed % SECONDS_PER_HOUR) // SECONDS_PER_MINUTE}m"
