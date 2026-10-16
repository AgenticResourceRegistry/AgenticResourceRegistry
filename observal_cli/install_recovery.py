# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Private, bounded backups for *planned* automatic writes by normal installers.

This is not a second installer. Call ``save`` under the item's install lock
immediately before its first write, after verifying ownership and the exact
file plan. A failed child can restore only when metadata is still old and
*every* managed file is either the old bytes/mode or its planned bytes/mode.
Otherwise leave the backup for manual inspection; never overwrite unknown edits.
Any harness/component installer can use this contract once it can enumerate
its complete owned file plan. No plan means no automatic install.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

from observal_cli import config

BACKUP_DIR = config.CONFIG_DIR / "update-backups"
MAX_BYTES = 2 * 1024 * 1024


class RecoveryError(ValueError):
    """Backup or restore could not be proved safe."""


def path_for(marker: Path) -> Path:
    """One private directory per Pi session; other bridges may use their own key."""
    key = marker.stem
    if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
        key = hashlib.sha256(marker.name.encode()).hexdigest()
    return BACKUP_DIR / key


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _regular(path: Path) -> None:
    if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
        raise RecoveryError("An owned path crosses a symbolic link")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise RecoveryError("An owned path is no longer a regular file")


def _sync(directory: Path) -> None:
    if os.name != "nt":
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_text_mode(directory: Path) -> int:
    """Measure the mode that tempfile-based text replacement will produce.

    The normal pull writer uses NamedTemporaryFile (mkstemp) in the target
    directory; sampling there also accounts for a restrictive process umask.
    """
    if any(part.is_symlink() for part in (directory, *directory.parents)) or not directory.is_dir():
        raise RecoveryError("The planned file's directory is unsafe")
    fd, temporary = tempfile.mkstemp(prefix=".update-mode-", dir=directory)
    try:
        return stat.S_IMODE(os.fstat(fd).st_mode)
    finally:
        os.close(fd)
        Path(temporary).unlink(missing_ok=True)


def _write(path: Path, body: bytes, mode: int = 0o600) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".update-recovery-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)  # os.fchmod is unavailable on Windows before Python 3.13
        os.replace(temporary, path)
        _sync(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def created_mode(directory: Path) -> int:
    """Mode a plain write_text creates in this directory (umask-aware)."""
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def _unlinked_ancestors_ok(path: Path) -> bool:
    return not any(part.is_symlink() for part in (path, *path.parents))


def save(
    root: Path,
    planned: dict[Path, bytes],
    old_hashes: dict[str, str],
    metadata: list[Path],
    *,
    expected_modes: dict[Path, int | None],
    deleted: tuple[Path, ...] | list[Path] = (),
) -> None:
    """Durably save pre-write bytes and the exact planned bytes/modes.

    ``planned`` paths absent from ``old_hashes`` are *created* (they must not
    exist); ``deleted`` paths are existing owned files the installer removes.
    Metadata (installed lock and ownership manifest) is *not* restored here:
    if either changed, restoring only files could make the state dishonest.
    """
    if root.parent != BACKUP_DIR or len(root.name) != 64 or any(c not in "0123456789abcdef" for c in root.name):
        raise RecoveryError("Invalid recovery directory")
    removed = {str(path) for path in deleted}
    if (
        (not planned and not removed)
        or set(planned) != set(expected_modes)
        or not metadata
        or set(old_hashes) != (set(old_hashes) & {str(p) for p in planned}) | removed
        or removed & {str(p) for p in planned}
        or not removed <= set(old_hashes)
    ):
        raise RecoveryError("Automatic installation has no complete owned file plan")
    if root.exists() or root.is_symlink():
        raise RecoveryError("An earlier recovery record already exists")
    if BACKUP_DIR.exists() and (BACKUP_DIR.is_symlink() or BACKUP_DIR.stat().st_mode & 0o077):
        raise RecoveryError("Recovery storage is not private")
    BACKUP_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    rows: list[dict] = []
    originals: list[bytes | None] = []
    total = 0

    def mode_ok(value: object) -> bool:
        return type(value) is int and 0 <= value <= 0o777

    for path in sorted(planned):
        target_mode = expected_modes[path]
        if str(path) not in old_hashes:
            if path.exists() or path.is_symlink() or not _unlinked_ancestors_ok(path) or not mode_ok(target_mode):
                raise RecoveryError("A file to create already exists or has no valid mode")
            total += len(planned[path])
            rows.append(
                {"path": str(path), "kind": "create", "target": _hash(planned[path]), "target_mode": target_mode}
            )
            originals.append(None)
            continue
        _regular(path)
        raw = path.read_bytes()
        if _hash(raw) != old_hashes[str(path)]:
            raise RecoveryError("A managed file changed before its backup")
        if not isinstance(planned[path], bytes):
            raise RecoveryError("An automatic target has no exact bytes")
        if target_mode is not None and not mode_ok(target_mode):
            raise RecoveryError("An automatic target has no valid expected file mode")
        old_mode = stat.S_IMODE(path.lstat().st_mode)
        if old_mode & ~0o777:
            raise RecoveryError("An owned file has unsupported permission bits")
        total += len(raw) + len(planned[path])
        rows.append(
            {
                "path": str(path),
                "old": _hash(raw),
                "target": _hash(planned[path]),
                "mode": old_mode,
                "target_mode": target_mode,
            }
        )
        originals.append(raw)
    for path in sorted(deleted):
        _regular(path)
        raw = path.read_bytes()
        if _hash(raw) != old_hashes[str(path)]:
            raise RecoveryError("A managed file changed before its backup")
        old_mode = stat.S_IMODE(path.lstat().st_mode)
        if old_mode & ~0o777:
            raise RecoveryError("An owned file has unsupported permission bits")
        total += len(raw)
        rows.append({"path": str(path), "kind": "delete", "old": _hash(raw), "mode": old_mode})
        originals.append(raw)
    if total > MAX_BYTES:
        raise RecoveryError("Automatic update exceeds the backup size limit")
    records = {}
    for path in metadata:
        _regular(path)
        metadata_mode = stat.S_IMODE(path.lstat().st_mode)
        if metadata_mode & ~0o777:
            raise RecoveryError("Installed metadata has unsupported permission bits")
        records[str(path)] = {"hash": _hash(path.read_bytes()), "mode": metadata_mode}
    try:
        root.mkdir(mode=0o700)
        for index, raw in enumerate(originals):
            if raw is not None:
                _write(root / str(index), raw)
        _write(root / "manifest.json", json.dumps({"schema": 1, "files": rows, "metadata": records}).encode())
        _sync(BACKUP_DIR)
    except BaseException:
        # If deletion fails, the leftover directory blocks another automatic
        # attempt rather than allowing an install without durable backups.
        try:
            shutil.rmtree(root)
        except OSError:
            pass
        raise


def restore_if_safe(root: Path) -> bool:
    """Restore after a stopped installer, or retain backups on *any* doubt.

    Caller must hold the same installation lock as the normal installer. A
    missing/incomplete record, altered metadata, foreign edit or unsafe path
    is never a reason to overwrite a file.
    """
    try:
        if root.parent != BACKUP_DIR or root.is_symlink() or root.stat().st_mode & 0o077:
            return False
        manifest = root / "manifest.json"
        _regular(manifest)
        if manifest.stat().st_size > MAX_BYTES:
            return False
        record = json.loads(manifest.read_text())
        files = record["files"]
        metadata = record["metadata"]
        if (
            record.get("schema") != 1
            or not isinstance(files, list)
            or not files
            or not isinstance(metadata, dict)
            or not metadata
        ):
            return False
        for name, evidence in metadata.items():
            path = Path(name)
            _regular(path)
            if (
                not isinstance(evidence, dict)
                or _hash(path.read_bytes()) != evidence["hash"]
                or stat.S_IMODE(path.lstat().st_mode) != evidence["mode"]
            ):
                return False
        replacements: list[tuple[Path, bytes, int]] = []
        removals: list[Path] = []
        total = 0
        seen = set()
        for index, row in enumerate(files):
            path = Path(row["path"])
            kind = row.get("kind", "modify")
            if str(path) in seen or kind not in {"modify", "create", "delete"} or not _unlinked_ancestors_ok(path):
                return False
            seen.add(str(path))
            if kind == "create":
                target_mode = row["target_mode"]
                if not path.exists() and not path.is_symlink():
                    continue
                _regular(path)
                if (_hash(path.read_bytes()), stat.S_IMODE(path.lstat().st_mode)) != (row["target"], target_mode):
                    return False  # A foreign file now occupies the created path.
                removals.append(path)
                continue
            old_mode = row["mode"]
            if type(old_mode) is not int or old_mode < 0 or old_mode & ~0o777:
                return False
            backup = root / str(index)
            _regular(backup)
            old = backup.read_bytes()
            if _hash(old) != row["old"]:
                return False
            total += len(old)
            if total > MAX_BYTES:
                return False
            if kind == "delete":
                if not path.exists() and not path.is_symlink():
                    replacements.append((path, old, old_mode))
                    continue
                _regular(path)
                if (_hash(path.read_bytes()), stat.S_IMODE(path.lstat().st_mode)) != (row["old"], old_mode):
                    return False
                continue
            target_mode = row["target_mode"]
            if target_mode is not None and (type(target_mode) is not int or target_mode < 0 or target_mode & ~0o777):
                return False
            _regular(path)
            actual = _hash(path.read_bytes())
            actual_mode = stat.S_IMODE(path.lstat().st_mode)
            # Check bytes AND mode as a pair. The writer may rewrite identical
            # bytes with a different mode; an unknown target mode cannot prove
            # such a change belongs to this installer.
            if (actual, actual_mode) != (row["old"], old_mode) and (
                target_mode is None or (actual, actual_mode) != (row["target"], target_mode)
            ):
                return False
            if (actual, actual_mode) != (row["old"], old_mode):
                replacements.append((path, old, old_mode))
        for path in removals:
            path.unlink()
            _sync(path.parent)
        for path, old, mode in replacements:
            path.parent.mkdir(parents=True, exist_ok=True)
            _write(path, old, mode)
        for row in files:
            path = Path(row["path"])
            if row.get("kind") == "create":
                if path.exists() or path.is_symlink():
                    return False
            elif _hash(path.read_bytes()) != row["old"] or stat.S_IMODE(path.lstat().st_mode) != row["mode"]:
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return False


def discard(root: Path) -> None:
    """Remove a verified-success or verified-restored backup; never prune pending ones."""
    if root.parent == BACKUP_DIR and root.is_dir() and not root.is_symlink():
        shutil.rmtree(root)
        _sync(BACKUP_DIR)


def planned_matches(root: Path) -> bool:
    """Confirm a completed normal installer produced the saved exact plan.

    Unlike restore_if_safe, installed metadata is expected to have advanced.
    Never use the newly captured baseline as evidence for target bytes: an
    external edit between write and capture could otherwise be adopted.
    """
    try:
        if root.parent != BACKUP_DIR or root.is_symlink() or root.stat().st_mode & 0o077:
            return False
        manifest = root / "manifest.json"
        _regular(manifest)
        if manifest.stat().st_size > MAX_BYTES:
            return False
        record = json.loads(manifest.read_text())
        rows = record["files"]
        if record.get("schema") != 1 or not isinstance(rows, list) or not rows:
            return False
        seen = set()
        for row in rows:
            path = Path(row["path"])
            if row.get("kind") == "delete":
                if str(path) in seen or path.exists() or path.is_symlink() or not _unlinked_ancestors_ok(path):
                    return False
                seen.add(str(path))
                continue
            mode = row["target_mode"]
            if (
                str(path) in seen
                or type(mode) is not int
                or mode < 0
                or mode & ~0o777
                or not isinstance(row["target"], str)
                or len(row["target"]) != 64
            ):
                return False
            seen.add(str(path))
            _regular(path)
            if path.stat().st_size > MAX_BYTES:
                return False
            if _hash(path.read_bytes()) != row["target"] or stat.S_IMODE(path.lstat().st_mode) != mode:
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return False
