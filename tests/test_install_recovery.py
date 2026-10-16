# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""A shared backup can restore only attributed writes, never foreign edits."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest

from observal_cli import install_recovery as recovery

if TYPE_CHECKING:
    from pathlib import Path


def _saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, profile_target_mode: int | None = 0o600
) -> tuple[Path, Path, Path, Path]:
    monkeypatch.setattr(recovery, "BACKUP_DIR", tmp_path / "backups")
    profile = tmp_path / "AGENTS.md"
    profile.write_text("old profile")
    profile.chmod(0o644)
    mcp = tmp_path / "mcp.json"
    mcp.write_text('{"mcpServers": {}}')
    mcp.chmod(0o644)
    installed = tmp_path / "lockfile.json"
    installed.write_text('{"version": "1.0"}')
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"files": 2}')
    root = recovery.BACKUP_DIR / ("a" * 64)
    recovery.save(
        root,
        {profile: b"new profile", mcp: mcp.read_bytes()},
        {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (profile, mcp)},
        [installed, baseline],
        expected_modes={profile: profile_target_mode, mcp: 0o644},
    )
    return root, profile, mcp, installed


def test_attributed_write_restores_from_durable_private_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, profile, mcp, _lock = _saved(tmp_path, monkeypatch)
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "0").stat().st_mode & 0o777 == 0o600
    assert (
        json.loads((root / "manifest.json").read_text())["files"][0]["target"]
        == hashlib.sha256(b"new profile").hexdigest()
    )
    profile.write_text("new profile")
    profile.chmod(0o600)  # The normal Pi profile writer replaces with a mkstemp file.
    assert recovery.restore_if_safe(root)
    assert profile.read_text() == "old profile" and profile.stat().st_mode & 0o777 == 0o644
    assert mcp.read_text() == '{"mcpServers": {}}'
    recovery.discard(root)
    assert not root.exists()


def test_unknown_edits_or_advanced_metadata_leave_backup_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, profile, _mcp, installed = _saved(tmp_path, monkeypatch)
    profile.write_text("foreign edit")
    assert not recovery.restore_if_safe(root)
    assert profile.read_text() == "foreign edit" and root.exists()
    profile.write_text("new profile")
    profile.chmod(0o600)
    installed.write_text('{"version": "2.0"}')
    assert not recovery.restore_if_safe(root)
    assert profile.read_text() == "new profile" and root.exists()


def test_measured_mode_matches_the_normal_pi_profile_writer(tmp_path: Path) -> None:
    from observal_cli.cmd_pull import _atomic_write_text

    target = tmp_path / "AGENTS.md"
    target.write_text("old")
    target.chmod(0o644)
    expected = recovery.atomic_text_mode(tmp_path)
    _atomic_write_text(target, "new")
    assert target.stat().st_mode & 0o777 == expected


def test_chmod_after_planned_write_is_foreign_and_keeps_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, profile, _mcp, installed = _saved(tmp_path, monkeypatch)
    profile.write_text("new profile")
    profile.chmod(0o777)
    assert not recovery.restore_if_safe(root)
    assert profile.read_text() == "new profile" and profile.stat().st_mode & 0o777 == 0o777
    assert (root / "manifest.json").exists()
    # An altered metadata mode is also an unrecognized local change.
    profile.chmod(0o600)
    installed.chmod(0o777)
    assert not recovery.restore_if_safe(root)
    assert profile.read_text() == "new profile" and root.exists()


def test_unknown_post_write_mode_cannot_authorize_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, profile, _mcp, _installed = _saved(tmp_path, monkeypatch, profile_target_mode=None)
    profile.write_text("new profile")
    profile.chmod(0o600)
    assert not recovery.restore_if_safe(root)
    assert profile.read_text() == "new profile" and root.exists()


@pytest.mark.parametrize("relative", [".pi/skills/review/SKILL.md", ".kiro/hooks/review.json", ".codex/config.toml"])
def test_shared_recovery_is_not_tied_to_pi_profile_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    monkeypatch.setattr(recovery, "BACKUP_DIR", tmp_path / "backups")
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text("old owned bytes")
    installed = tmp_path / "lockfile.json"
    installed.write_text("original installed state")
    root = recovery.BACKUP_DIR / ("b" * 64)
    recovery.save(
        root,
        {target: b"planned bytes"},
        {str(target): hashlib.sha256(target.read_bytes()).hexdigest()},
        [installed],
        expected_modes={target: target.stat().st_mode & 0o777},
    )
    target.write_text("planned bytes")
    assert recovery.restore_if_safe(root)
    assert target.read_text() == "old owned bytes"


def test_missing_or_corrupt_record_cannot_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, profile, _mcp, _installed = _saved(tmp_path, monkeypatch)
    profile.write_text("new profile")
    profile.chmod(0o600)
    (root / "0").write_text("corrupt backup")
    assert not recovery.restore_if_safe(root)
    assert profile.read_text() == "new profile"
    (root / "manifest.json").unlink()
    assert not recovery.restore_if_safe(root)


def test_backup_fails_closed_on_dirty_owned_file_or_existing_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, profile, mcp, installed = _saved(tmp_path, monkeypatch)
    with pytest.raises(recovery.RecoveryError, match="earlier recovery"):
        recovery.save(
            root, {profile: b"new profile"}, {str(profile): "0" * 64}, [installed], expected_modes={profile: 0o600}
        )
    recovery.discard(root)
    profile.write_text("foreign edit")
    with pytest.raises(recovery.RecoveryError, match="changed before"):
        recovery.save(
            root,
            {profile: b"new profile", mcp: mcp.read_bytes()},
            {
                str(profile): hashlib.sha256(b"old profile").hexdigest(),
                str(mcp): hashlib.sha256(mcp.read_bytes()).hexdigest(),
            },
            [installed],
            expected_modes={profile: 0o600, mcp: 0o644},
        )
    assert not root.exists()


def _create_delete_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(recovery, "BACKUP_DIR", tmp_path / "backups")
    keep = tmp_path / "keep.md"
    keep.write_text("old")
    keep.chmod(0o644)
    gone = tmp_path / "gone.sh"
    gone.write_text("old script")
    gone.chmod(0o755)
    fresh = tmp_path / "skills" / "new" / "SKILL.md"  # parent does not exist yet
    metadata = tmp_path / "lockfile.json"
    metadata.write_text("{}")
    root = recovery.BACKUP_DIR / ("b" * 64)
    old = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (keep, gone)}
    recovery.save(
        root,
        {keep: b"new", fresh: b"fresh"},
        old,
        [metadata],
        expected_modes={keep: 0o644, fresh: 0o644},
        deleted=[gone],
    )
    return root, keep, gone, fresh


def _simulate_install(keep: Path, gone: Path, fresh: Path) -> None:
    keep.write_text("new")
    gone.unlink()
    fresh.parent.mkdir(parents=True)
    fresh.write_text("fresh")
    fresh.chmod(0o644)


def test_created_and_deleted_files_are_reversed_when_unedited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, keep, gone, fresh = _create_delete_plan(tmp_path, monkeypatch)
    _simulate_install(keep, gone, fresh)
    assert recovery.planned_matches(root) is True
    assert recovery.restore_if_safe(root) is True
    assert keep.read_text() == "old" and not fresh.exists()
    assert gone.read_text() == "old script" and gone.stat().st_mode & 0o777 == 0o755


def test_stopped_before_any_write_is_a_noop_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, keep, gone, fresh = _create_delete_plan(tmp_path, monkeypatch)
    assert recovery.restore_if_safe(root) is True
    assert keep.read_text() == "old" and gone.exists() and not fresh.exists()


@pytest.mark.parametrize("which", ["created", "recreated"])
def test_foreign_content_on_created_or_deleted_path_blocks_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    root, keep, gone, fresh = _create_delete_plan(tmp_path, monkeypatch)
    _simulate_install(keep, gone, fresh)
    (fresh if which == "created" else gone).write_text("someone else's work")
    assert recovery.restore_if_safe(root) is False
    assert (fresh if which == "created" else gone).read_text() == "someone else's work"


def test_save_refuses_to_create_over_an_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "BACKUP_DIR", tmp_path / "backups")
    meta = tmp_path / "lock.json"
    meta.write_text("{}")
    existing = tmp_path / "taken.md"
    existing.write_text("mine")
    with pytest.raises(recovery.RecoveryError):
        recovery.save(recovery.BACKUP_DIR / ("c" * 64), {existing: b"x"}, {}, [meta], expected_modes={existing: 0o644})
