# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# SPDX-License-Identifier: Apache-2.0
"""Disposable filesystem tests for managed folder ownership and recovery."""

import base64
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from observal_cli import lockfile, managed_skill
from observal_cli.skill_folder import validate_bundle


def bundle(version, content=b"skill", script=b"\x00\xff", script_mode="0755"):
    def file(path, data, mode):
        return {
            "path": path,
            "content": base64.b64encode(data).decode(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "mode": mode,
            "version_id": version,
        }

    return validate_bundle(
        {
            "listing_id": "listing-id",
            "version_id": version,
            "digest": f"sha256:{version}",
            "skill_file_path": ".pi/skills/example/SKILL.md",
            "files": [
                file("SKILL.md", content, "0644"),
                file("scripts/run", script, script_mode),
                file("empty", b"", "0644"),
            ],
        }
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setattr(lockfile, "CONFIG_DIR", config)
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", config / "lockfile.json")
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", config / "lockfile.lock")
    return tmp_path / "skills" / "example", tmp_path / "backups"


def install(b, target, root, *, check=False, old=None, fail=False):
    proof = managed_skill.receipt(b, target, "pi", "project", registry_url="https://example.org")

    def record(data):
        if fail:
            raise OSError("injected lock failure")
        entries = (
            data.setdefault("registries", {})
            .setdefault("https://example.org", {"harnesses": {}})["harnesses"]
            .setdefault("pi", {"standalone": []})["standalone"]
        )
        if any(
            item.get("id") == b.listing_id
            and Path(item.get("folder_receipt", {}).get("target", "")).parent == target.parent
            and item.get("folder_receipt", {}).get("target") != str(target)
            for item in entries
        ):
            raise managed_skill.ManagedSkillError("Listing already owns another folder in this root")
        entry = next((item for item in entries if item.get("folder_receipt", {}).get("target") == str(target)), None)
        if entry is None:
            entry = {"id": b.listing_id, "type": "skill"}
            entries.append(entry)
        entry.update({"folder_receipt": proof, "version_id": b.version_id, "digest": b.digest})

    return managed_skill.transact(b, target, proof, record=record, old_bundle=old, backup_root=root, check=check)


def test_git_free_backup_root_preserves_worktree_safety(store, monkeypatch, tmp_path):
    target, root = store
    real_run = managed_skill.subprocess.run

    def missing_git(args, **kwargs):
        if args[0] == "git":
            raise FileNotFoundError("git unavailable")
        return real_run(args, **kwargs)

    monkeypatch.setattr(managed_skill.subprocess, "run", missing_git)
    assert install(bundle("v1"), target, root, check=True)["action"] == "install"
    assert install(bundle("v1"), target, root)["action"] == "install"
    assert (target / "SKILL.md").read_bytes() == b"skill"

    # Even without the executable, known Git metadata must not silently allow
    # backups to be committed with the project.
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    with pytest.raises(managed_skill.ManagedSkillError, match="Git-ignored"):
        install(bundle("v2"), project / "skills" / "example", project / "backups", check=True)


@pytest.mark.parametrize("git_call", ["rev-parse", "check-ignore"])
def test_backup_verification_timeouts_fail_closed(store, monkeypatch, tmp_path, git_call):
    target, _root = store
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    root = project / "backups"

    def timed_out(args, **kwargs):
        if git_call in args:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return subprocess.CompletedProcess(args, 0, stdout=str(project))

    monkeypatch.setattr(managed_skill.subprocess, "run", timed_out)
    with pytest.raises(managed_skill.ManagedSkillError, match=r"Git-tracked|Git-ignored"):
        install(bundle("v1"), target, root, check=True)


def test_upgrade_preview_does_not_create_backup_or_lock_directories(store):
    target, root = store
    first = bundle("v1")
    preview = install(first, target, root, check=True)
    assert preview["action"] == "install"
    assert not root.exists()
    assert not target.parent.exists()
    assert not lockfile.LOCKFILE_PATH.parent.exists()


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("{invalid", "Cannot read"),
        (b"\xff", "Cannot read"),
        ("[]", "Invalid lockfile structure"),
        ('{"lock_version": 2, "registries": []}', "Invalid lockfile structure"),
        ('{"lock_version": 2, "registries": {"https://example.org": null}}', "Invalid machine lockfile structure"),
        (
            '{"lock_version": 2, "registries": {"https://example.org": {"harnesses": {"pi": {"standalone": [null]}}}}}',
            "Invalid machine lockfile structure",
        ),
        ('{"lock_version": 1, "harnesses": {}}', "Migrate the machine lockfile"),
    ],
)
def test_upgrade_preview_refuses_malformed_lockfile_without_writing(store, contents, message):
    target, root = store
    lockfile.LOCKFILE_PATH.parent.mkdir()
    original = contents if isinstance(contents, bytes) else contents.encode()
    lockfile.LOCKFILE_PATH.write_bytes(original)
    with pytest.raises(managed_skill.ManagedSkillError, match=message):
        install(bundle("v1"), target, root, check=True)
    assert lockfile.LOCKFILE_PATH.read_bytes() == original
    assert not lockfile._LOCKFILE_LOCK.exists()
    assert not root.exists()
    assert not target.parent.exists()


def test_initial_upgrade_preview_restore_and_prune(store):
    target, root = store
    one, two = bundle("v1"), bundle("v2", content=b"changed")
    assert install(one, target, root)["action"] == "install"
    assert oct(os.stat(target / "scripts/run").st_mode & 0o777) == "0o755"
    assert (target / "empty").read_bytes() == b""
    assert install(one, target, root)["action"] == "unchanged"
    preview = install(two, target, root, check=True)
    assert preview["from_version_id"] == "v1"
    assert (target / "SKILL.md").read_bytes() == b"skill"
    done = install(two, target, root)
    assert (target / "SKILL.md").read_bytes() == b"changed"
    assert managed_skill.backups_list(backup_root=root)[0]["id"] == done["backup_id"]
    restored = managed_skill.restore_backup(done["backup_id"], backup_root=root)
    assert (target / "SKILL.md").read_bytes() == b"skill"
    assert managed_skill.restore_backup(restored["current_backup"], backup_root=root, prune=True)


def test_upgrade_refuses_tree_changed_between_verification_and_rename(store, monkeypatch):
    target, root = store
    first, second = bundle("v1"), bundle("v2", content=b"changed")
    install(first, target, root)
    original_verify = managed_skill.verify_tree
    checks = 0

    def verify_then_replace(path, proof):
        nonlocal checks
        original_verify(path, proof)
        if path == target and proof["version_id"] == first.version_id:
            checks += 1
            if checks == 2:
                (target / "SKILL.md").write_bytes(b"local replacement")

    monkeypatch.setattr(managed_skill, "verify_tree", verify_then_replace)
    with pytest.raises(managed_skill.ManagedSkillError, match="manual recovery"):
        install(second, target, root)
    assert (
        lockfile.read_lockfile()["registries"]["https://example.org"]["harnesses"]["pi"]["standalone"][0]["version_id"]
        == "v1"
    )
    assert next(root.glob("*/old/SKILL.md")).read_bytes() == b"local replacement"
    assert list(root.glob("*/marker.json")), "retain marker for human recovery"


def test_interrupted_restore_preserves_both_versions_and_prior_receipt(store):
    target, root = store
    first, second = bundle("v1"), bundle("v2", content=b"changed")
    install(first, target, root)
    upgraded = install(second, target, root)
    original = root / upgraded["backup_id"] / "old"
    replacement = root / f"{hashlib.sha256(str(target).encode()).hexdigest()}-restore-crash"
    replacement.mkdir()
    old_proof = managed_skill.receipt(second, target, "pi", "project", registry_url="https://example.org")
    new_proof = managed_skill.receipt(first, target, "pi", "project", registry_url="https://example.org")
    (replacement / "marker.json").write_text(
        json.dumps(
            {
                "target": str(target),
                "old": old_proof,
                "new": new_proof,
                "restore_from": str(original),
            }
        )
    )
    target.rename(replacement / "old")
    original.rename(target)
    managed_skill.recover(target, backup_root=root)
    assert (target / "SKILL.md").read_bytes() == b"changed"
    assert (original / "SKILL.md").read_bytes() == b"skill"
    assert not (replacement / "marker.json").exists()


def test_restore_lock_failure_preserves_active_and_backup(store, monkeypatch):
    target, root = store
    install(bundle("v1"), target, root)
    done = install(bundle("v2", content=b"changed"), target, root)
    monkeypatch.setattr(lockfile, "update_lockfile", lambda _record: (_ for _ in ()).throw(OSError("read only")))
    with pytest.raises(managed_skill.ManagedSkillError, match="active installation preserved"):
        managed_skill.restore_backup(done["backup_id"], backup_root=root)
    assert (target / "SKILL.md").read_bytes() == b"changed"
    assert (root / done["backup_id"] / "old" / "SKILL.md").read_bytes() == b"skill"


def test_modified_tree_and_unowned_folder_refused(store):
    target, root = store
    one = bundle("v1")
    target.mkdir(parents=True)
    (target / "SKILL.md").write_bytes(b"skill")
    with pytest.raises(managed_skill.ManagedSkillError, match="no receipt"):
        install(one, target, root)
    import shutil

    shutil.rmtree(target)
    install(one, target, root)
    (target / "extra").write_bytes(b"unexpected")
    with pytest.raises(managed_skill.ManagedSkillError, match="Modified or unexpected"):
        install(bundle("v2"), target, root)


def test_restore_refuses_changed_active_tree(store):
    target, root = store
    install(bundle("v1"), target, root)
    done = install(bundle("v2"), target, root)
    (target / "extra").write_bytes(b"user change")
    with pytest.raises(managed_skill.ManagedSkillError, match="Modified or unexpected"):
        managed_skill.restore_backup(done["backup_id"], backup_root=root)
    assert (root / done["backup_id"] / "old" / "SKILL.md").read_bytes() == b"skill"


def test_lockfile_failure_after_atomic_replace_retains_complete_new_release(store, monkeypatch):
    target, root = store
    install(bundle("v1"), target, root)
    saved = lockfile._write_locked

    def fail_after_commit(data):
        saved(data)
        raise OSError("injected post-replace failure")

    monkeypatch.setattr(lockfile, "_write_locked", fail_after_commit)
    with pytest.raises(managed_skill.ManagedSkillError, match="New skill is complete and recorded"):
        install(bundle("v2", content=b"changed"), target, root)
    assert (target / "SKILL.md").read_bytes() == b"changed"
    entry = lockfile.read_lockfile()["registries"]["https://example.org"]["harnesses"]["pi"]["standalone"][0]
    assert entry["version_id"] == "v2"
    monkeypatch.setattr(lockfile, "_write_locked", saved)
    managed_skill.recover(target, backup_root=root)
    assert (target / "SKILL.md").read_bytes() == b"changed"
    assert managed_skill.backups_list(backup_root=root)


def test_initial_and_upgrade_record_failure_restore(store):
    target, root = store
    one = bundle("v1")
    with pytest.raises(managed_skill.ManagedSkillError, match="original installation preserved"):
        install(one, target, root, fail=True)
    assert not target.exists()
    install(one, target, root)
    with pytest.raises(managed_skill.ManagedSkillError, match="original installation preserved"):
        install(bundle("v2"), target, root, fail=True)
    assert (target / "SKILL.md").read_bytes() == b"skill"
    assert (
        lockfile.read_lockfile()["registries"]["https://example.org"]["harnesses"]["pi"]["standalone"][0]["version_id"]
        == "v1"
    )


def test_recover_interrupted_rename_and_refuse_changed_target(store):
    target, root = store
    one = bundle("v1")
    install(one, target, root)
    key = hashlib.sha256(str(target).encode()).hexdigest()
    folder = root / f"{key}-123"
    folder.mkdir()
    proof = managed_skill.receipt(one, target, "pi", "project", registry_url="https://example.org")
    (folder / "marker.json").write_text(json.dumps({"target": str(target), "old": proof, "new": proof}))
    target.rename(folder / "old")
    managed_skill.recover(target, backup_root=root)
    assert (target / "SKILL.md").read_bytes() == b"skill"
    assert not (folder / "marker.json").exists()
    (target / "extra").write_text("edited")
    with pytest.raises(managed_skill.ManagedSkillError):
        managed_skill.restore_backup("bad-id", backup_root=root)


def test_recover_unrecorded_initial_swap_without_claiming_managed_ownership(store):
    target, root = store
    first = bundle("v1")
    target.parent.mkdir(parents=True)
    root.mkdir()
    key = hashlib.sha256(str(target).encode()).hexdigest()
    folder = root / f"{key}-initial"
    folder.mkdir()
    proof = managed_skill.receipt(first, target, "pi", "project", registry_url="https://example.org")
    (folder / "marker.json").write_text(json.dumps({"target": str(target), "old": None, "new": proof}))
    from observal_cli.skill_folder import install_folder_bundle

    install_folder_bundle(first, folder / "staged")
    (folder / "staged").rename(target)
    managed_skill.recover(target, backup_root=root)
    assert not target.exists()
    assert not (folder / "marker.json").exists()
    assert lockfile.read_lockfile()["registries"] == {}


def test_recover_crash_after_new_tree_before_receipt_keeps_old_release(store):
    target, root = store
    old, new = bundle("v1"), bundle("v2", content=b"changed")
    install(old, target, root)
    old_proof = managed_skill.receipt(old, target, "pi", "project", registry_url="https://example.org")
    new_proof = managed_skill.receipt(new, target, "pi", "project", registry_url="https://example.org")
    key = hashlib.sha256(str(target).encode()).hexdigest()
    folder = root / f"{key}-crash"
    folder.mkdir()
    (folder / "marker.json").write_text(json.dumps({"target": str(target), "old": old_proof, "new": new_proof}))
    from observal_cli.skill_folder import install_folder_bundle

    install_folder_bundle(new, folder / "staged")
    target.rename(folder / "old")
    (folder / "staged").rename(target)
    managed_skill.recover(target, backup_root=root)
    assert (target / "SKILL.md").read_bytes() == b"skill"
    assert not (folder / "marker.json").exists()
    assert (
        lockfile.read_lockfile()["registries"]["https://example.org"]["harnesses"]["pi"]["standalone"][0]["version_id"]
        == "v1"
    )


def test_symlink_replacement_during_staging_cannot_redirect_swap(store, tmp_path, monkeypatch):
    target, root = store
    target.parent.mkdir(parents=True)
    foreign = tmp_path / "foreign"
    (foreign / target.name).mkdir(parents=True)
    sentinel = foreign / target.name / "SKILL.md"
    sentinel.write_text("not owned")
    moved = target.parent.with_name("old-skill-root")
    real_install = managed_skill.install_folder_bundle

    def replace_parent(bundle_value, stage):
        result = real_install(bundle_value, stage)
        target.parent.rename(moved)
        target.parent.symlink_to(foreign, target_is_directory=True)
        return result

    monkeypatch.setattr(managed_skill, "install_folder_bundle", replace_parent)
    with pytest.raises(managed_skill.ManagedSkillError):
        install(bundle("v1"), target, root)
    assert sentinel.read_text() == "not owned"
    assert not (moved / target.name).exists()
    assert not lockfile.LOCKFILE_PATH.exists()


def test_backup_root_in_another_project_skill_tree_is_refused(store, tmp_path):
    target, _root = store
    discovery = tmp_path / "other-project" / ".pi" / "skills" / "backups"
    proof = managed_skill.receipt(bundle("v1"), target, "pi", "project", registry_url="https://example.org")
    with pytest.raises(managed_skill.ManagedSkillError, match="discovery root"):
        managed_skill.transact(bundle("v1"), target, proof, record=lambda _data: None, backup_root=discovery)
    assert not discovery.exists()
    assert not target.exists()


def test_renamed_successor_refuses_second_live_folder_and_keeps_prior_receipt(store):
    target, root = store
    one = bundle("v1")
    install(one, target, root)
    renamed = replace(bundle("v2"), folder_name="renamed", skill_file_path=".pi/skills/renamed/SKILL.md")
    other = target.parent / "renamed"
    proof = managed_skill.receipt(renamed, other, "pi", "project", registry_url="https://example.org")
    with pytest.raises(managed_skill.ManagedSkillError, match="different folder name"):
        managed_skill.transact(renamed, other, proof, record=lambda _data: None, backup_root=root)
    assert (target / "SKILL.md").read_bytes() == b"skill"
    assert not other.exists()


def test_symlink_target_refused(store):
    target, root = store
    target.parent.mkdir(parents=True)
    target.symlink_to(target.parent)
    with pytest.raises(managed_skill.ManagedSkillError, match="Symlink"):
        install(bundle("v1"), target, root)


def test_old_reader_v2_ignores_optional_receipt(store):
    target, root = store
    install(bundle("v1"), target, root)
    data = lockfile.read_lockfile()
    assert data["lock_version"] == 2
    entry = data["registries"]["https://example.org"]["harnesses"]["pi"]["standalone"][0]
    assert "folder_receipt" in entry
    # Legacy queries only consume known entry fields and do not rewrite receipts.
    assert entry["id"] == "listing-id"
    assert lockfile.read_lockfile() == data


def test_wide_existing_lockfile_is_narrowed(store):
    target, root = store
    install(bundle("v1"), target, root)
    os.chmod(lockfile.LOCKFILE_PATH, 0o666)
    install(bundle("v2"), target, root)
    assert lockfile.LOCKFILE_PATH.stat().st_mode & 0o777 == 0o600


def test_different_targets_cannot_orphan_listing_receipt(store):
    target, root = store
    second = target.parent / "second"
    b = bundle("v1")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(install, b, place, root) for place in (target, second)]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except managed_skill.ManagedSkillError as error:
                results.append(error)
    # Per-target locks do not serialize different names. The lockfile callback
    # must refuse the second destination and transactionally remove its stage.
    assert sum(isinstance(result, managed_skill.ManagedSkillError) for result in results) == 1
    entries = lockfile.read_lockfile()["registries"]["https://example.org"]["harnesses"]["pi"]["standalone"]
    assert len(entries) == 1
    owned = entries[0]["folder_receipt"]["target"]
    assert owned in {str(target), str(second)}
    assert {str(path) for path in (target, second) if path.exists()} == {owned}


def test_restore_lockfile_failure_after_atomic_replace_keeps_tree_and_lock_consistent(store, monkeypatch):
    target, root = store
    install(bundle("v1"), target, root)
    done = install(bundle("v2", content=b"changed"), target, root)
    saved = lockfile._write_locked

    def fail_after_commit(data):
        saved(data)  # the receipt now names v1, then e.g. the directory fsync fails
        raise OSError("injected post-replace failure")

    monkeypatch.setattr(lockfile, "_write_locked", fail_after_commit)
    with pytest.raises(managed_skill.ManagedSkillError, match="Restored skill is complete and recorded"):
        managed_skill.restore_backup(done["backup_id"], backup_root=root)
    monkeypatch.setattr(lockfile, "_write_locked", saved)
    entry = lockfile.read_lockfile()["registries"]["https://example.org"]["harnesses"]["pi"]["standalone"][0]
    assert entry["version_id"] == "v1"
    assert (target / "SKILL.md").read_bytes() == b"skill"  # never roll the files back under an advanced lock
    managed_skill.recover(target, backup_root=root)  # the retained marker must finalize cleanly
    assert (target / "SKILL.md").read_bytes() == b"skill"
    managed_skill.verify_tree(target, entry["folder_receipt"])
    assert not list(root.glob("*/marker.json"))


def test_restore_lockfile_unreadable_after_swap_refuses_rollback(store, monkeypatch):
    target, root = store
    install(bundle("v1"), target, root)
    done = install(bundle("v2", content=b"changed"), target, root)

    saved_read = lockfile.read_lockfile
    failed = []

    def fail_write(_record):
        failed.append(True)
        raise OSError("boom")

    def unreadable_after_failure():
        if failed:
            raise RuntimeError("lock unreadable")
        return saved_read()

    monkeypatch.setattr(lockfile, "update_lockfile", fail_write)
    monkeypatch.setattr(lockfile, "read_lockfile", unreadable_after_failure)
    with pytest.raises(managed_skill.ManagedSkillError, match="Cannot read lock after restore"):
        managed_skill.restore_backup(done["backup_id"], backup_root=root)
    assert list(root.glob("*/marker.json"))  # recovery evidence is retained, not silently deleted


def test_ancestor_symlink_swapped_during_staging_is_refused(tmp_path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setattr(lockfile, "CONFIG_DIR", config)
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", config / "lockfile.json")
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", config / "lockfile.lock")
    project = tmp_path / "project"
    target = project / ".pi" / "skills" / "example"
    outside = tmp_path / "outside"
    (outside / "skills").mkdir(parents=True)
    real_install = managed_skill.install_folder_bundle

    swapped = []

    def swap_ancestor(b, stage):
        real_install(b, stage)
        (project / ".pi").rename(project / ".pi-original")
        (project / ".pi").symlink_to(outside, target_is_directory=True)
        swapped.append(True)

    monkeypatch.setattr(managed_skill, "install_folder_bundle", swap_ancestor)
    with pytest.raises(managed_skill.ManagedSkillError, match="changed"):
        install(bundle("v1"), target, tmp_path / "backups")
    assert swapped  # the refusal came from the detected substitution, not an unrelated early failure
    assert not (outside / "skills" / "example").exists()
    registries = lockfile.read_lockfile().get("registries", {})
    assert not registries.get("https://example.org", {}).get("harnesses", {}).get("pi", {}).get("standalone")


def _isolated_machine(tmp_path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setattr(lockfile, "CONFIG_DIR", config)
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", config / "lockfile.json")
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", config / "lockfile.lock")


def test_backup_ancestor_swap_never_deletes_redirected_staging_directory(tmp_path, monkeypatch):
    _isolated_machine(tmp_path, monkeypatch)
    target = tmp_path / "project" / ".pi" / "skills" / "example"
    backups = tmp_path / "bk" / "root"
    outside = tmp_path / "outside"
    real_install = managed_skill.install_folder_bundle
    sentinel = []

    def swap_backup_ancestor(b, stage):
        real_install(b, stage)
        (tmp_path / "bk").rename(tmp_path / "bk-original")
        redirected = outside / "root" / stage.parent.name / "staged"
        redirected.mkdir(parents=True)
        (redirected / "unrelated.txt").write_text("not ours")
        sentinel.append(redirected / "unrelated.txt")
        (tmp_path / "bk").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(managed_skill, "install_folder_bundle", swap_backup_ancestor)
    with pytest.raises(managed_skill.ManagedSkillError):
        install(bundle("v1"), target, backups)
    assert sentinel and sentinel[0].read_text() == "not ours"  # cleanup must not follow the substituted ancestor
    assert not target.exists()


def test_restore_refuses_when_destination_ancestor_is_swapped_after_pinning(tmp_path, monkeypatch):
    _isolated_machine(tmp_path, monkeypatch)
    project = tmp_path / "project"
    target = project / ".pi" / "skills" / "example"
    backups = tmp_path / "backups"
    install(bundle("v1"), target, backups)
    done = install(bundle("v2", content=b"changed"), target, backups)
    outside = tmp_path / "outside"
    (outside / "skills" / "example").mkdir(parents=True)
    (outside / "skills" / "example" / "unrelated.txt").write_text("not ours")
    real_pin = managed_skill._pin_dir
    calls = []

    def pin_then_swap(path):
        fd = real_pin(path)
        calls.append(path)
        if len(calls) == 3:  # destination parent, backup folder and replacement are all pinned
            (project / ".pi").rename(project / ".pi-original")
            (project / ".pi").symlink_to(outside, target_is_directory=True)
        return fd

    monkeypatch.setattr(managed_skill, "_pin_dir", pin_then_swap)
    with pytest.raises(managed_skill.ManagedSkillError, match="changed"):
        managed_skill.restore_backup(done["backup_id"], backup_root=backups)
    assert calls and (outside / "skills" / "example" / "unrelated.txt").read_text() == "not ours"
    assert not (outside / "skills" / "example" / "SKILL.md").exists()
    assert (project / ".pi-original" / "skills" / "example" / "SKILL.md").read_bytes() == b"changed"


def test_first_install_does_not_need_a_same_filesystem_backup_root(store, monkeypatch):
    target, root = store
    # Model a backup root on another device: only a replacement renames across it.
    monkeypatch.setattr(managed_skill, "_same_device", lambda _root, _target: False)
    assert install(bundle("v1"), target, root, check=True)["action"] == "install"
    done = install(bundle("v1"), target, root)
    assert done["action"] == "install" and (target / "SKILL.md").read_bytes() == b"skill"
    assert [p.name for p in target.parent.iterdir()] == [target.name]  # nothing but the skill where harnesses scan
    assert not list(target.parent.parent.glob(".example.*"))  # and no staging leftovers one level up
    managed_skill.verify_tree(
        target, managed_skill.receipt(bundle("v1"), target, "pi", "project", registry_url="https://example.org")
    )


def test_replacement_on_another_filesystem_names_the_backup_root_fix(store, monkeypatch):
    target, root = store
    install(bundle("v1"), target, root)
    monkeypatch.setattr(managed_skill, "_same_device", lambda _root, _target: False)
    with pytest.raises(managed_skill.ManagedSkillError, match="another filesystem") as exc:
        install(bundle("v2", content=b"changed"), target, root)
    assert "--backup-root" in exc.value.remediation
    assert (target / "SKILL.md").read_bytes() == b"skill"


def test_upgrade_preview_reports_modified_files_and_mode_changes(store):
    target, root = store
    install(bundle("v1"), target, root)
    preview = install(bundle("v2", content=b"changed", script_mode="0644"), target, root, check=True)
    assert preview["modified"] == ["SKILL.md"]
    assert preview["mode_changed"] == ["scripts/run"]
    assert preview["added"] == [] and preview["removed"] == []
    identical = install(bundle("v1"), target, root, check=True)
    assert identical["action"] == "unchanged"


def test_ancestor_swapped_after_record_is_never_reported_as_success(tmp_path, monkeypatch):
    _isolated_machine(tmp_path, monkeypatch)
    project = tmp_path / "project"
    target = project / ".pi" / "skills" / "example"
    backups = tmp_path / "backups"
    outside = tmp_path / "outside"
    (outside / "skills").mkdir(parents=True)
    real_update = lockfile.update_lockfile

    def update_then_swap(mutate):
        result = real_update(mutate)  # the receipt is durable, then the destination ancestor is substituted
        (project / ".pi").rename(project / ".pi-original")
        (project / ".pi").symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(lockfile, "update_lockfile", update_then_swap)
    with pytest.raises(managed_skill.ManagedSkillCommittedError, match="recorded, but") as exc:
        install(bundle("v1"), target, backups)
    assert exc.value.remediation
    assert exc.value.outcome["action"] == "install"  # callers learn the lock advanced, so they must not roll back
    assert list(backups.glob("*/marker.json"))  # evidence retained for reconciliation
    assert not (outside / "skills" / "example").exists()
    monkeypatch.setattr(lockfile, "update_lockfile", real_update)
    with pytest.raises(managed_skill.ManagedSkillError):  # fails closed until the path is repaired
        install(bundle("v2", content=b"changed"), target, backups)
    assert not (outside / "skills" / "example").exists()


def test_crash_while_staging_beside_the_destination_is_cleaned_up_by_recovery(store, monkeypatch):
    target, root = store
    monkeypatch.setattr(managed_skill, "_same_device", lambda _root, _target: False)
    real_install = managed_skill.install_folder_bundle
    stages = []

    def crash_after_staging(b, stage):
        real_install(b, stage)
        inner = stage.parent / f".{stage.name}.stage.leftover"  # install_folder_bundle's own temporary directory
        inner.mkdir()
        (inner / "SKILL.md").write_text("orphan")
        stages.append((stage, inner))
        raise KeyboardInterrupt  # stands in for a kill: nothing after this point gets to clean up

    monkeypatch.setattr(managed_skill, "install_folder_bundle", crash_after_staging)
    real_discard = managed_skill._discard_stage
    monkeypatch.setattr(managed_skill, "_discard_stage", lambda _stage: None)
    with pytest.raises(KeyboardInterrupt):
        install(bundle("v1"), target, root)
    (stage, inner) = stages[0]
    assert stage.is_dir() and inner.is_dir() and list(root.glob("*/marker.json"))
    assert stage.parent == target.parent.parent  # never inside the directory a harness scans for skills
    assert not list(target.parent.glob("*"))  # nothing discoverable was left where harnesses scan

    monkeypatch.setattr(managed_skill, "_discard_stage", real_discard)
    managed_skill.recover(target, backup_root=root, require_same_device=False)
    assert not stage.exists() and not inner.exists()
    assert not list(root.glob("*/marker.json"))


def test_recovery_refuses_a_marker_that_names_a_stage_elsewhere(store):
    target, root = store
    root.mkdir(parents=True)
    key = hashlib.sha256(str(target).encode()).hexdigest()
    folder = root / f"{key}-deadbeef"
    folder.mkdir()
    victim = target.parent.parent / "precious"
    victim.mkdir(parents=True)
    (folder / "marker.json").write_text(
        json.dumps({"target": str(target), "old": None, "new": {}, "stage": str(victim)})
    )
    with pytest.raises(managed_skill.ManagedSkillError, match="Invalid recovery marker stage"):
        managed_skill.recover(target, backup_root=root)
    assert victim.is_dir()


def test_unchanged_reinstall_after_a_cross_filesystem_first_install_is_not_refused(store, monkeypatch):
    target, root = store
    monkeypatch.setattr(managed_skill, "_same_device", lambda _root, _target: False)
    assert install(bundle("v1"), target, root)["action"] == "install"
    # Nothing is replaced, so no rename crosses the backup root and the device must not matter.
    assert install(bundle("v1"), target, root)["action"] == "unchanged"
    assert install(bundle("v1"), target, root, check=True)["action"] == "unchanged"


def test_stage_falls_back_to_the_skills_directory_when_the_parent_is_not_usable(store, monkeypatch):
    target, root = store
    monkeypatch.setattr(managed_skill, "_same_device", lambda _root, _target: False)
    real_access = os.access
    monkeypatch.setattr(
        managed_skill.os,
        "access",
        lambda path, mode: False if Path(path) == target.parent.parent else real_access(path, mode),
    )
    real_install = managed_skill.install_folder_bundle
    seen = []
    monkeypatch.setattr(
        managed_skill, "install_folder_bundle", lambda b, stage: seen.append(stage) or real_install(b, stage)
    )
    assert install(bundle("v1"), target, root)["action"] == "install"
    assert seen[0].parent == target.parent  # not writable one level up, so it stages beside the destination
    assert [p.name for p in target.parent.iterdir()] == [target.name]  # and cleans up after itself


def test_recovery_removes_a_stage_recorded_inside_the_skills_directory(store):
    target, root = store
    root.mkdir(parents=True)
    key = hashlib.sha256(str(target).encode()).hexdigest()
    folder = root / f"{key}-cafe"
    folder.mkdir()
    stage = target.parent / f".{target.name}.observal-stage-abc123"
    stage.mkdir(parents=True)
    (stage / "SKILL.md").write_text("orphan")
    (folder / "marker.json").write_text(
        json.dumps({"target": str(target), "old": None, "new": {}, "stage": str(stage)})
    )
    managed_skill.recover(target, backup_root=root, require_same_device=False)
    assert not stage.exists() and not list(root.glob("*/marker.json"))


def test_read_failure_while_verifying_after_the_lock_advanced_is_still_a_committed_install(tmp_path, monkeypatch):
    _isolated_machine(tmp_path, monkeypatch)
    target = tmp_path / "project" / ".pi" / "skills" / "example"
    real_update, real_verify = lockfile.update_lockfile, managed_skill.verify_tree
    recorded = []

    def update(mutate):
        result = real_update(mutate)
        recorded.append(True)
        return result

    def verify(path, proof):
        if recorded:
            raise PermissionError("cannot read installed tree")
        return real_verify(path, proof)

    monkeypatch.setattr(lockfile, "update_lockfile", update)
    monkeypatch.setattr(managed_skill, "verify_tree", verify)
    with pytest.raises(managed_skill.ManagedSkillCommittedError, match="could not be verified") as exc:
        install(bundle("v1"), target, tmp_path / "backups")
    assert exc.value.outcome["action"] == "install"  # callers must keep the recorded install, not roll it back
    assert list((tmp_path / "backups").glob("*/marker.json"))
