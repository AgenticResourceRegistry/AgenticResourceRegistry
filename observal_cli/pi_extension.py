# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Install/version-check logic for the bundled Pi telemetry extension.

Observal writes the canonical extension straight to
~/.pi/agent/extensions/observal.ts, tracked by an adjacent
.observal-extension.json manifest recording the CLI version it was installed
from. A file at that path with no matching manifest is either migrated (if it
carries Observal's own header, i.e. it was installed by a CLI predating this
tracking) or treated as unmanaged and never overwritten.

Shared by `observal doctor` (interactive check/patch/cleanup) and the
automatic post-login install in cmd_auth.py.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version

from observal_cli.shared.utils import atomic_write
from observal_cli.version_check import get_current_version

NOT_DETECTED = "not_detected"
NOT_INSTALLED = "not_installed"
CURRENT = "current"
STALE = "stale"
NEWER = "newer"
UNMANAGED = "unmanaged"
MIGRATABLE = "migratable"
DRIFTED = "drifted"

# Present in every observal.ts this project has ever shipped. Used only to
# recognize a pre-manifest install as ours; see _is_observal_authored.
_SIGNATURE = "Observal session telemetry extension for Pi"

# Every action that replaces or deletes an existing file. "install" has no
# prior file and "adopt" has already matched the bundle byte for byte, so only
# those two can skip the copy: a manifest proves which version we wrote, never
# that the bytes are still unedited.
_BACKS_UP = frozenset({"refresh", "restore", "migrate"})


@dataclass(frozen=True)
class PiExtensionResult:
    """Outcome of install_or_refresh: what was done, and where the old file went."""

    changed: bool
    action: str | None = None
    backup: Path | None = None


@dataclass(frozen=True)
class PiExtensionStatus:
    state: str
    message: str | None = None
    action: str | None = None  # install | refresh | restore | adopt | migrate


def pi_agent_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".pi" / "agent"


def extension_path(home: Path | None = None) -> Path:
    return pi_agent_dir(home) / "extensions" / "observal.ts"


def manifest_path(home: Path | None = None) -> Path:
    return pi_agent_dir(home) / "extensions" / ".observal-extension.json"


def _reserve_backup(home: Path | None = None) -> Path:
    """Claim a free backup name by creating it, so two runs cannot pick the same one.

    backup_path() only reports which name is free, which is fine for a message
    or a dry run but races between the look-up and the copy: two processes both
    see observal.ts.bak free, and the second overwrites the first one's copy of
    the original. Creating the file with O_EXCL settles ownership up front.
    """
    base = extension_path(home)
    base.parent.mkdir(parents=True, exist_ok=True)
    candidate = base.with_name(f"{base.name}.bak")
    index = 1
    while True:
        try:
            handle = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            candidate = base.with_name(f"{base.name}.bak.{index}")
            index += 1
            continue
        os.close(handle)
        return candidate


def backup_path(home: Path | None = None) -> Path:
    """First free `observal.ts.bak[.N]` beside the extension.

    Deliberately does not end in `.ts` so Pi's extension loader ignores it.
    Callers that need to report the destination must read it before the copy,
    since this returns a different name once the file exists.
    """
    base = extension_path(home)
    candidate = base.with_name(f"{base.name}.bak")
    index = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}.bak.{index}")
        index += 1
    return candidate


def extension_source() -> str:
    """Read the canonical extension source: bundled wheel copy, or dev source tree."""
    bundled = Path(__file__).parent / "_bundled" / "observal.ts"
    source_tree = Path(__file__).parents[1] / "packages" / "pi-extension" / "extensions" / "observal.ts"
    for path in (bundled, source_tree):
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError("Bundled Pi telemetry extension is missing")


def _unmanaged_message(path: Path) -> str:
    return (
        f"{path} exists but is not managed by Observal. Remove it (or move it aside) and "
        "re-run `observal doctor patch --harness pi` to let Observal manage the Pi extension, "
        "or leave it as-is to keep using it unmanaged."
    )


def _is_observal_authored(content: str) -> bool:
    """Whether an untracked local file is one Observal itself installed.

    CLI versions before install tracking wrote observal.ts with no manifest
    beside it. Those are ours to refresh, but they are indistinguishable from
    a hand-written extension by path alone, so match on the doc header that
    every observal.ts we have shipped carries. A file without it belongs to
    someone else and is never written to.
    """
    return _SIGNATURE in content


def _parse_version(value: str) -> Version | None:
    try:
        return Version(value)
    except InvalidVersion:
        return None


def _read_manifest(home: Path | None = None) -> dict | None:
    path = manifest_path(home)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def check_status(home: Path | None = None) -> PiExtensionStatus:
    """Determine the current install state.

    Raises OSError if the installed extension file exists but can't be read;
    callers decide how to surface that.
    """
    pi_dir = pi_agent_dir(home)
    if not pi_dir.exists():
        return PiExtensionStatus(NOT_DETECTED)

    # Read the bundled source eagerly (not just when we're about to install)
    # so a broken/missing package bundle is surfaced by `doctor check`, not
    # only discovered later when a patch/install is actually attempted.
    expected = extension_source()

    path = extension_path(home)
    if not path.exists():
        return PiExtensionStatus(
            NOT_INSTALLED,
            f"Observal Pi extension is not installed. Doctor can install {path}.",
            action="install",
        )

    try:
        installed = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Ownership is settled by the manifest, not by whether the bytes decode.
        # A tracked install that is no longer UTF-8 is still ours to repair;
        # anything else is somebody else's file and we leave it alone.
        manifest = _read_manifest(home)
        if manifest is not None and manifest.get("managed") is True:
            return PiExtensionStatus(
                DRIFTED,
                f"{path} is no longer valid UTF-8. Doctor can restore it, keeping a copy at {backup_path(home).name}.",
                action="restore",
            )
        return PiExtensionStatus(UNMANAGED, _unmanaged_message(path))
    except OSError as exc:
        raise OSError(f"{path}: {exc}") from exc

    manifest = _read_manifest(home)
    if manifest is not None and manifest.get("managed") is True and isinstance(manifest.get("version"), str):
        manifest_version = _parse_version(manifest["version"])
        current_version = _parse_version(get_current_version())
        if manifest_version is not None and current_version is not None:
            if manifest_version < current_version:
                return PiExtensionStatus(
                    STALE,
                    f"Observal Pi extension is stale ({manifest['version']} < {get_current_version()}). "
                    f"Doctor can refresh {path}.",
                    action="refresh",
                )
            if manifest_version > current_version:
                return PiExtensionStatus(NEWER)
            # Same version but different bytes: an edited working copy, or a
            # bundle that moved without a release. The version alone cannot see
            # that, so compare content before calling the install clean.
            if installed != expected:
                return PiExtensionStatus(
                    DRIFTED,
                    f"{path} no longer matches the extension bundled with Observal "
                    f"{get_current_version()}. Doctor can restore it, keeping a copy at "
                    f"{backup_path(home).name}.",
                    action="restore",
                )
            return PiExtensionStatus(CURRENT)

    # No trustworthy manifest (missing, corrupt, or an unparseable version).
    # Adopt silently if the content already matches what we'd install; migrate
    # it if it carries our own header, which means an older CLI wrote it before
    # install tracking existed; otherwise it is a foreign file we must not touch.
    if installed == expected:
        return PiExtensionStatus(CURRENT, action="adopt")
    if _is_observal_authored(installed):
        return PiExtensionStatus(
            MIGRATABLE,
            f"{path} was installed by an older Observal CLI, before install tracking existed. "
            f"Doctor can refresh it to {get_current_version()}, keeping a copy at "
            f"{backup_path(home).name}.",
            action="migrate",
        )
    return PiExtensionStatus(UNMANAGED, _unmanaged_message(path))


def install_or_refresh(
    *, dry_run: bool, home: Path | None = None, status: PiExtensionStatus | None = None
) -> PiExtensionResult:
    """Perform the action check_status recommends, if any.

    Pass `status` when the caller has already read it, to avoid a second scan
    of the same files. Returns what was done and, for the actions that keep the
    previous file, where that copy went.
    """
    status = status or check_status(home)
    if status.action is None:
        return PiExtensionResult(False)

    if dry_run:
        # Report the name that would be used without creating anything.
        return PiExtensionResult(True, status.action, backup_path(home) if status.action in _BACKS_UP else None)

    backup = _reserve_backup(home) if status.action in _BACKS_UP else None
    if backup is not None:
        shutil.copy2(extension_path(home), backup)
    if status.action != "adopt":
        atomic_write(extension_path(home), extension_source())
    atomic_write(
        manifest_path(home),
        json.dumps({"managed": True, "version": get_current_version()}, indent=2) + "\n",
    )
    return PiExtensionResult(True, status.action, backup)


def remove(*, dry_run: bool, home: Path | None = None) -> bool:
    """Remove an Observal-managed local install. Never touches unmanaged files."""
    status = check_status(home)
    if status.state not in (CURRENT, STALE, NEWER, DRIFTED, MIGRATABLE):
        return False
    # CURRENT is the only state that proves the bytes match what we shipped;
    # every other one may be carrying someone's edits, so keep a copy.
    keep_copy = status.state != CURRENT
    if not dry_run:
        if keep_copy and extension_path(home).is_file():
            shutil.copy2(extension_path(home), backup_path(home))
        extension_path(home).unlink(missing_ok=True)
        manifest_path(home).unlink(missing_ok=True)
    return True
