# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Local, registry-scoped auto-update consent and the shared install/policy gate.

The Pi startup runner holds ``registry_gate`` while its normal agent-pull
subprocess takes ``pi_install_lock``. It runs only in the explicit apply pilot,
under the outer registry/account worker gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from observal_cli import config
from observal_cli.lockfile import normalize_server_url

if TYPE_CHECKING:
    from collections.abc import Iterator

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt
else:
    msvcrt = None

POLICY_VERSION = 2
POLICY_PATH = config.CONFIG_DIR / "auto-update-policy.json"
GATE_DIR = config.CONFIG_DIR / "auto-update-gates"
GATE_TIMEOUT_SECONDS = 90.0  # bounds lock acquisition, not an in-progress installer


class PolicyError(ValueError):
    """Malformed or unsupported preference data: fail closed for automatic installs."""


class GateBusyError(TimeoutError):
    """An install or another preference change still holds the registry gate."""


class AccountUnavailableError(ValueError):
    """No locally authenticated account can be trusted for update consent."""


class LegacyPolicyError(PolicyError):
    """Unscoped v1 consent cannot be assigned safely to an account."""


def active_account() -> str:
    """Use the stored login identity only with its own locally stored token.

    Environment-only or overridden tokens may belong to a different account.
    Never let them inherit a previously stored account's grants.
    """
    persisted = config.load_persisted()
    effective = config.load()
    user_id = persisted.get("user_id")
    token = persisted.get("access_token")
    if (
        not isinstance(user_id, str)
        or not user_id.strip()
        or not isinstance(token, str)
        or not token
        or effective.get("access_token") != token
        or effective.get("user_id") != user_id
    ):
        raise AccountUnavailableError("Sign in to Observal locally to manage automatic updates")
    return user_id.strip()


def active_registry() -> str:
    """Require an explicitly configured registry, without requiring network or login."""
    url = config.load().get("server_url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("No active Observal registry is configured")
    return normalize_server_url(url)


def _read_policy() -> dict:
    try:
        raw = POLICY_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"version": POLICY_VERSION, "registries": {}}
    try:
        data = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError("The local auto-update policy is malformed") from exc
    if not isinstance(data, dict) or type(data.get("version")) is not int:
        raise PolicyError("The local auto-update policy uses an unsupported schema")
    if data["version"] == 1:
        raise LegacyPolicyError("Legacy unscoped consent is disabled; run `observal unfreeze` to opt in again")
    if data["version"] != POLICY_VERSION:
        raise PolicyError("The local auto-update policy uses an unsupported schema")
    registries = data.get("registries")
    if not isinstance(registries, dict):
        raise PolicyError("The local auto-update policy has invalid registries")
    for url, section in registries.items():
        if not isinstance(url, str) or not isinstance(section, dict) or not isinstance(section.get("accounts"), dict):
            raise PolicyError("The local auto-update policy has invalid accounts")
        for account_id, grant in section["accounts"].items():
            if (
                not isinstance(account_id, str)
                or not account_id
                or not isinstance(grant, dict)
                or type(grant.get("enabled")) is not bool
                or not isinstance(grant.get("projects"), dict)
                or any(not isinstance(root, str) or value is not True for root, value in grant["projects"].items())
            ):
                raise PolicyError("The local auto-update policy has invalid preferences")
    return data


def _write_policy(data: dict) -> None:
    POLICY_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    serialized = json.dumps(data, indent=2, sort_keys=True) + "\n"
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".auto-update-policy-", dir=POLICY_PATH.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, POLICY_PATH)
        temporary = None
        if os.name != "nt":
            fd = os.open(POLICY_PATH.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def _try_acquire(handle) -> bool:
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except (BlockingIOError, OSError) as exc:
        # POSIX EACCES/EAGAIN and Windows lock-violation are contention.
        import errno

        if getattr(exc, "errno", None) not in {errno.EACCES, errno.EAGAIN} and getattr(exc, "winerror", None) != 33:
            raise
        return False


@contextmanager
def _file_gate(identity: str, *, timeout: float) -> Iterator[None]:
    GATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = hashlib.sha256(identity.encode()).hexdigest() + ".lock"
    fd = os.open(GATE_DIR / name, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+b", buffering=0) as handle:
        if msvcrt is not None and os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")  # Windows locks a byte of the file.
        deadline = time.monotonic() + timeout
        while not _try_acquire(handle):
            if time.monotonic() >= deadline:
                raise GateBusyError("An installation or policy change is still in progress")
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def registry_gate(registry: str, *, timeout: float = GATE_TIMEOUT_SECONDS) -> Iterator[None]:
    """Serialize policy changes and complete auto-installs. Acquire first."""
    with _file_gate(normalize_server_url(registry), timeout=timeout):
        yield


@contextmanager
def apply_worker_gate(registry: str, account: str, *, timeout: float = GATE_TIMEOUT_SECONDS) -> Iterator[None]:
    """Serialize one account's startup check, journal, install, and final seal.

    This orchestration lock is taken *before* the registry policy gate; freeze
    never takes it, and installers only take the policy gate inside it. An
    unresolved first outcome is therefore visible before a second worker can
    make any new admission decision.
    """
    with _file_gate(f"apply-worker:{normalize_server_url(registry)}\0{account}", timeout=timeout):
        yield


@contextmanager
def pi_install_lock(registry: str, *, timeout: float = GATE_TIMEOUT_SECONDS) -> Iterator[None]:
    """Serialize Pi writes across registries: their local destinations may overlap.

    Auto-install lock order: registry gate -> Pi install lock -> lockfile.
    Manual pulls and skill installs take only this lock, never the registry gate.
    """
    normalize_server_url(registry)  # Reject an unknown registry identity.
    with _file_gate("pi-install:all-registries", timeout=timeout):
        yield


@contextmanager
def claude_install_lock(registry: str, *, timeout: float = GATE_TIMEOUT_SECONDS) -> Iterator[None]:
    """Serialize manual and guarded Claude Code profile writes across registries."""
    normalize_server_url(registry)
    with _file_gate("claude-install:all-registries", timeout=timeout):
        yield


def record_skip_reason(message: object) -> None:
    """Child installers leave one short, fixed refusal reason for the startup notice.

    Only our own ValueError messages are passed here (never OSError text, paths
    or credentials). The first recorded reason wins; best effort, never raises.
    """
    name = os.environ.get("OBSERVAL_AUTO_UPDATE_REASON_FILE")
    if not name or not isinstance(message, str):
        return
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in message).split())[:200]
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        return
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def read_skip_reason(name: str | Path) -> str | None:
    try:
        path = Path(name)
        if path.is_symlink() or path.stat().st_size > 400:
            return None
        text = path.read_text(encoding="utf-8").strip()
        return text or None
    except (OSError, UnicodeError):
        return None


def project_root(directory: str | Path) -> str:
    """Resolve an explicit root; never grant permission to a parent or child."""
    path = Path(directory).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("The project path must be a directory")
    return str(path)


def _result(registry: str, account: str, data: dict, root: str | None) -> dict:
    section = data["registries"].get(registry, {"accounts": {}})
    preference = section["accounts"].get(account, {"enabled": False, "projects": {}})
    enabled = preference["enabled"]
    grant = root in preference["projects"] if root is not None else enabled
    return {
        "registry": registry,
        "scope": "project" if root is not None else "user",
        "project": root,
        "auto_update": grant,
        "effective": enabled and grant,
    }


def policy_status(registry: str, *, root: str | None = None) -> dict:
    """Return only this authenticated account's consent; fail closed otherwise."""
    registry = normalize_server_url(registry)
    try:
        account = active_account()
    except (AccountUnavailableError, OSError) as exc:
        return {**_result(registry, "", {"registries": {}}, root), "warning": str(exc)}
    try:
        data = _read_policy()
    except (PolicyError, OSError) as exc:
        return {**_result(registry, account, {"registries": {}}, root), "warning": str(exc)}
    return _result(registry, account, data, root)


def set_policy(registry: str, *, enabled: bool, root: str | None = None, timeout: float = GATE_TIMEOUT_SECONDS) -> dict:
    """Commit preference change only after any in-flight auto-install finishes."""
    registry = normalize_server_url(registry)
    with registry_gate(registry, timeout=timeout):
        account = active_account()  # Re-read under the gate, including any account switch.
        try:
            data = _read_policy()
        except LegacyPolicyError:
            # V1 had registry-wide grants. Discard them instead of guessing who
            # consented; an explicit unfreeze here creates a fresh account grant.
            data = {"version": POLICY_VERSION, "registries": {}}
        section = data["registries"].setdefault(registry, {"accounts": {}})
        preference = section["accounts"].setdefault(account, {"enabled": False, "projects": {}})
        if root is None:
            preference["enabled"] = enabled
        elif enabled:
            preference["projects"][root] = True
        else:
            preference["projects"].pop(root, None)
        _write_policy(data)
        return _result(registry, account, data, root)
