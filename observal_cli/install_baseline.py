# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Exact local file evidence for future automatic agent updates.

An installed registry version or layer snapshot alone is not evidence that
harness files are owned or unchanged. Missing/legacy baselines fail closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

from observal_cli import config
from observal_cli.lockfile import normalize_server_url

BASELINE_VERSION = 3
BASELINE_DIR = config.CONFIG_DIR / "install-baselines"
_MAX_FILES = 500


class BaselineError(ValueError):
    """Cannot prove the installed files are exactly as Observal left them."""


def _key(registry: str, harness: str, agent_id: str, scope: str, root: str) -> str:
    identity = [normalize_server_url(registry), harness, agent_id, scope, str(Path(root).resolve())]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def _path(registry: str, harness: str, agent_id: str, scope: str, root: str) -> Path:
    return BASELINE_DIR / f"{_key(registry, harness, agent_id, scope, root)}.json"


def _file_evidence(written_paths: list[str]) -> tuple[dict[str, str], dict[str, int]]:
    """Hash owned bytes and record modes from the same file traversal."""
    paths: dict[str, str] = {}
    modes: dict[str, int] = {}
    for name in written_paths:
        if not Path(name).is_absolute():
            raise BaselineError("An installed path is not absolute")
        target = Path(name)
        if not target.exists():
            raise BaselineError("An installed file is missing")
        # Reject symlinked parents, not just symlinked final paths.
        if any(part.is_symlink() for part in (target, *target.parents)):
            raise BaselineError("An installed file crosses a symbolic link")
        if target.is_file():
            candidates = [target]
        elif target.is_dir():
            candidates = list(target.rglob("*"))
            if not candidates or any(part.is_symlink() for part in candidates):
                raise BaselineError("An installed directory is empty or contains links")
        else:
            raise BaselineError("An installed path has an unsupported type")
        for file in candidates:
            if file.is_dir():
                continue
            if not file.is_file() or len(paths) >= _MAX_FILES:
                raise BaselineError("The installation has unsupported or too many files")
            key = str(file)
            if key in paths:
                continue
            mode = stat.S_IMODE(file.lstat().st_mode)
            if mode & ~0o777:
                raise BaselineError("An installed file has unsupported permission bits")
            paths[key] = hashlib.sha256(file.read_bytes()).hexdigest()
            modes[key] = mode
    if not paths:
        raise BaselineError("The installation has no verifiable files")
    return dict(sorted(paths.items())), dict(sorted(modes.items()))


def _files(written_paths: list[str]) -> dict[str, str]:
    """Return only hashes for callers that compare the complete owned path set."""
    return _file_evidence(written_paths)[0]


def _reject_shared_ownership(files: dict[str, str], own_path: Path) -> None:
    """A shared merged config cannot be safely attributed to two installs."""
    if not BASELINE_DIR.exists():
        return
    for other in BASELINE_DIR.glob("*.json"):
        if other == own_path:
            continue
        try:
            data = json.loads(other.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BaselineError("An adjacent ownership baseline cannot be inspected") from exc
        if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
            raise BaselineError("An adjacent ownership baseline is malformed")
        if files.keys() & data["files"].keys():
            raise BaselineError("A file is shared by more than one managed installation")


def capture(
    *,
    registry: str,
    harness: str,
    agent_id: str,
    scope: str,
    root: str,
    version: str,
    lock_digest: str,
    written_paths: list[str],
) -> None:
    """Record evidence after a completed manual pull or verified guarded update.

    Never call this to adopt a legacy or dirty installation. The caller must
    hold the Pi install lock through capture when updating managed files.
    """
    if not lock_digest:
        raise BaselineError("A complete agent lock digest is required for ownership evidence")
    files, modes = _file_evidence(written_paths)
    target = _path(registry, harness, agent_id, scope, root)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    _reject_shared_ownership(files, target)
    body = (
        json.dumps(
            {
                "schema": BASELINE_VERSION,
                "registry": normalize_server_url(registry),
                "harness": harness,
                "agent_id": agent_id,
                "scope": scope,
                "root": str(Path(root).resolve()),
                "version": version,
                "lock_digest": lock_digest,
                "paths": sorted(set(written_paths)),
                "files": files,
                "modes": modes,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".baseline-", dir=target.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
        if os.name != "nt":
            fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def verified_manifest(
    *, registry: str, harness: str, agent_id: str, scope: str, root: str, version: str, lock_digest: str
) -> tuple[dict[str, str], list[str]]:
    """Return verified file hashes and original paths from the same manifest read."""
    try:
        raw = json.loads(_path(registry, harness, agent_id, scope, root).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaselineError("No valid ownership baseline; manually re-pull this version first") from exc
    if isinstance(raw, dict) and raw.get("schema") == 2:
        raise BaselineError("The ownership baseline has no mode evidence; manually re-pull this version first")
    if not isinstance(raw, dict) or any(
        raw.get(key) != value
        for key, value in {
            "schema": BASELINE_VERSION,
            "registry": normalize_server_url(registry),
            "harness": harness,
            "agent_id": agent_id,
            "scope": scope,
            "root": str(Path(root).resolve()),
            "version": version,
            "lock_digest": lock_digest,
        }.items()
    ):
        raise BaselineError("The ownership baseline does not match the installed version")
    files = raw.get("files")
    modes = raw.get("modes")
    paths = raw.get("paths")
    if not isinstance(paths, list) or not paths or any(not isinstance(name, str) for name in paths):
        raise BaselineError("The ownership baseline has no valid paths")
    if (
        not isinstance(files, dict)
        or not files
        or len(files) > _MAX_FILES
        or any(
            not isinstance(name, str) or not isinstance(digest, str) or len(digest) != 64
            for name, digest in files.items()
        )
    ):
        raise BaselineError("The ownership baseline is malformed")
    if (
        not isinstance(modes, dict)
        or modes.keys() != files.keys()
        or any(type(mode) is not int or mode < 0 or mode & ~0o777 for mode in modes.values())
    ):
        raise BaselineError("The ownership baseline has no valid file modes; manually re-pull this version first")
    try:
        _reject_shared_ownership(files, _path(registry, harness, agent_id, scope, root))
        # Also catches edits to unrelated keys in a shared JSON/TOML config.
        current_files, current_modes = _file_evidence(paths)
    except OSError as error:
        raise BaselineError("An installed file or ownership manifest could not be read; update manually") from error
    if current_files != files:
        raise BaselineError("An installed file has changed since the last manual pull")
    if current_modes != modes:
        raise BaselineError("An installed file mode has changed since the last manual pull")
    return files, paths


def verified_files(
    *, registry: str, harness: str, agent_id: str, scope: str, root: str, version: str, lock_digest: str
) -> dict[str, str]:
    """Return verified file hashes or refuse missing, dirty, or legacy installs."""
    files, _paths = verified_manifest(
        registry=registry,
        harness=harness,
        agent_id=agent_id,
        scope=scope,
        root=root,
        version=version,
        lock_digest=lock_digest,
    )
    return files


def reject_foreign_creation(paths: list[Path], own_path: Path) -> None:
    """A file an update would create must not exist or belong to another install."""
    for path in paths:
        if path.exists() or path.is_symlink() or any(part.is_symlink() for part in path.parents):
            raise BaselineError("A file this update would create already exists or crosses a link")
    _reject_shared_ownership({str(path): "" for path in paths}, own_path)
