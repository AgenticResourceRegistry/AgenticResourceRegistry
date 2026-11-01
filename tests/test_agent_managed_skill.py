# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# SPDX-License-Identifier: Apache-2.0
"""Pinned Agent folder receipts remain separate from disposable delegation writes."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from observal_cli import cmd_pull, lockfile, managed_skill


def _bundle(version: str = "version-1", body: bytes = b"hello") -> dict:
    files = []
    for path, data, mode in [("SKILL.md", b"---\nname: demo\n---\n", "0644"), ("run.sh", body, "0755")]:
        files.append(
            {
                "path": path,
                "content": base64.b64encode(data).decode(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "mode": mode,
                "version_id": version,
            }
        )
    return {
        "listing_id": "listing-1",
        "version_id": version,
        "digest": "sha256:digest-" + version,
        "skill_file_path": ".pi/skills/demo/SKILL.md",
        "files": files,
    }


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "machine"
    home.mkdir()
    monkeypatch.setattr(lockfile, "CONFIG_DIR", home)
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", home / "lockfile.json")
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", home / "lockfile.lock")
    monkeypatch.setattr(lockfile, "current_registry_url", lambda: "https://registry.example")
    return home


def _plan(
    project: Path, raw: dict, *, agent: str = "agent-1", agent_version: str = "1.0.0", allow_change: bool = False
):
    pin = {
        "id": raw["listing_id"],
        "type": "skill",
        "version_id": raw["version_id"],
        "digest": raw["digest"],
        "version": "1.0.0",
    }
    snippet = {
        "skill_components": [{"name": "demo", "path": raw["skill_file_path"], "bundle_version_id": raw["version_id"]}]
    }
    plan = cmd_pull._managed_agent_folders(
        snippet,
        [raw],
        {"status": "locked", "components": [pin]},
        harness="pi",
        target_dir=project,
        is_user_scope=False,
        agent_id=agent,
        agent_version=agent_version,
        registry_url="https://registry.example/",
        allow_agent_pin_change=allow_change,
    )
    return plan, pin


def test_initial_agent_receipt_preserves_binary_mode_and_is_idempotent(machine: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    raw = _bundle(body=b"\x00\xff")
    plan, pin = _plan(project, raw)
    bundle, target, proof, _ = plan[0]

    def record(data):
        cmd_pull._record_managed_agent_component(
            data,
            harness="pi",
            scope="project",
            directory=project,
            agent_id="agent-1",
            agent_version="1.0.0",
            component=pin,
            proof=proof,
        )

    outcome = managed_skill.transact(bundle, target, proof, record=record)
    assert outcome["action"] == "install"
    assert (target / "run.sh").read_bytes() == b"\x00\xff"
    assert (target / "run.sh").stat().st_mode & 0o777 == 0o755
    assert _plan(project, raw)[0][0][2] == proof
    assert managed_skill.transact(bundle, target, proof, record=record)["action"] == "unchanged"
    entry = lockfile.installed_agent("pi", "agent-1", scope="project", directory=str(project))
    assert entry["components"][0]["folder_receipt"] == proof
    assert "content" not in str(proof)


def test_foreign_agent_and_local_edit_refuse_before_any_config_write(machine: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    raw = _bundle()
    plan, pin = _plan(project, raw)
    bundle, target, proof, _ = plan[0]
    managed_skill.transact(
        bundle,
        target,
        proof,
        record=lambda data: cmd_pull._record_managed_agent_component(
            data,
            harness="pi",
            scope="project",
            directory=project,
            agent_id="agent-1",
            agent_version="1.0.0",
            component=pin,
            proof=proof,
        ),
    )
    with pytest.raises(managed_skill.ManagedSkillError, match=r"owner|provenance"):
        _plan(project, raw, agent="agent-2")
    (target / "extra").write_text("local")
    with pytest.raises(managed_skill.ManagedSkillError, match=r"Modified|unexpected"):
        _plan(project, raw)
    assert not (project / "agent.md").exists()


def test_same_agent_pin_can_upgrade_verified_folder_and_keep_backup(machine: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    first = _bundle()
    first_plan, pin = _plan(project, first)
    bundle, target, proof, _ = first_plan[0]

    def record_first(data):
        cmd_pull._record_managed_agent_component(
            data,
            harness="pi",
            scope="project",
            directory=project,
            agent_id="agent-1",
            agent_version="1.0.0",
            component=pin,
            proof=proof,
        )

    managed_skill.transact(bundle, target, proof, record=record_first)
    newer = _bundle("version-2", b"updated")
    new_bundle, _, new_proof, new_pin = _plan(project, newer)[0][0]

    def record_new(data):
        cmd_pull._record_managed_agent_component(
            data,
            harness="pi",
            scope="project",
            directory=project,
            agent_id="agent-1",
            agent_version="1.0.0",
            component=new_pin,
            proof=new_proof,
        )

    result = managed_skill.transact(new_bundle, target, new_proof, record=record_new)
    assert result["action"] == "upgrade"
    assert (target / "run.sh").read_bytes() == b"updated"
    assert (Path(result["backup"]) / "run.sh").read_bytes() == b"hello"
    assert (
        lockfile.installed_agent("pi", "agent-1", scope="project", directory=str(project))["components"][0][
            "folder_receipt"
        ]
        == new_proof
    )


def test_new_agent_pin_cannot_relabel_previous_receipt(machine: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    raw = _bundle()
    plan, pin = _plan(project, raw)
    bundle, target, proof, _ = plan[0]
    managed_skill.transact(
        bundle,
        target,
        proof,
        record=lambda data: cmd_pull._record_managed_agent_component(
            data,
            harness="pi",
            scope="project",
            directory=project,
            agent_id="agent-1",
            agent_version="1.0.0",
            component=pin,
            proof=proof,
        ),
    )
    with pytest.raises(managed_skill.ManagedSkillError, match="provenance"):
        _plan(project, _bundle(version="version-2", body=b"new"), agent_version="2.0.0")
    assert (target / "run.sh").read_bytes() == b"hello"
    assert lockfile.installed_agent("pi", "agent-1", scope="project", directory=str(project))["version"] == "1.0.0"


def test_explicit_agent_version_change_keeps_prior_folder_and_pin_recoverable(machine: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    original = _bundle()
    ((old_bundle, target, old_proof, old_pin),) = _plan(project, original)[0]

    def record_old(data):
        cmd_pull._record_managed_agent_component(
            data,
            harness="pi",
            scope="project",
            directory=project,
            agent_id="agent-1",
            agent_version="1.0.0",
            component=old_pin,
            proof=old_proof,
        )

    managed_skill.transact(old_bundle, target, old_proof, record=record_old)
    updated = _bundle("version-2", b"pinned update")
    ((new_bundle, _, new_proof, new_pin),) = _plan(project, updated, agent_version="2.0.0", allow_change=True)[0]

    def record_new(data):
        cmd_pull._record_managed_agent_component(
            data,
            harness="pi",
            scope="project",
            directory=project,
            agent_id="agent-1",
            agent_version="2.0.0",
            component=new_pin,
            proof=new_proof,
            allow_agent_pin_change=True,
        )

    result = managed_skill.transact(new_bundle, target, new_proof, record=record_new, allow_agent_pin_change=True)
    assert result["action"] == "upgrade"
    assert (target / "run.sh").read_bytes() == b"pinned update"
    assert (Path(result["backup"]) / "run.sh").read_bytes() == b"hello"
    assert (
        lockfile.installed_agent("pi", "agent-1", scope="project", directory=str(project))["components"][0][
            "folder_receipt"
        ]
        == new_proof
    )


def test_failed_project_pin_write_restores_old_agent_entry_and_folder(machine: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    original = _bundle()
    ((first, target, old_proof, old_pin),) = _plan(project, original)[0]
    managed_skill.transact(
        first,
        target,
        old_proof,
        record=lambda data: cmd_pull._record_managed_agent_component(
            data,
            harness="pi",
            scope="project",
            directory=project,
            agent_id="agent-1",
            agent_version="1.0.0",
            component=old_pin,
            proof=old_proof,
        ),
    )
    from copy import deepcopy

    previous = deepcopy(lockfile.installed_agent("pi", "agent-1", scope="project", directory=str(project)))
    ((second, _, new_proof, new_pin),) = _plan(
        project,
        _bundle("version-2", b"new"),
        agent_version="2.0.0",
        allow_change=True,
    )[0]
    outcome = managed_skill.transact(
        second,
        target,
        new_proof,
        record=lambda data: cmd_pull._record_managed_agent_component(
            data,
            harness="pi",
            scope="project",
            directory=project,
            agent_id="agent-1",
            agent_version="2.0.0",
            component=new_pin,
            proof=new_proof,
            allow_agent_pin_change=True,
        ),
        allow_agent_pin_change=True,
    )
    cmd_pull._sync_managed_agent_lock(
        harness="pi",
        scope="project",
        directory=project,
        agent_id="agent-1",
        agent_version="2.0.0",
        metadata={"id": "agent-1", "version": "2.0.0", "scope": "project", "directory": str(project)},
        components=[new_pin],
        proofs=[new_proof],
    )
    assert not cmd_pull._rollback_managed_agent_folders([(new_proof, outcome)])
    cmd_pull._restore_prior_managed_agent_entry(
        harness="pi",
        scope="project",
        directory=project,
        agent_id="agent-1",
        previous=previous,
        expected_version="2.0.0",
    )
    assert (target / "run.sh").read_bytes() == b"hello"
    assert lockfile.installed_agent("pi", "agent-1", scope="project", directory=str(project)) == previous


@pytest.mark.parametrize("change", ["removed", "relocated"])
def test_agent_upgrade_cannot_orphan_managed_skill_receipt(machine: Path, tmp_path: Path, change: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    proofs = [
        {
            "listing_id": listing_id,
            "target": str(project / ".pi" / "skills" / folder),
            "registry_url": lockfile.current_registry_url(),
        }
        for listing_id, folder in (("listing-1", "demo"), ("listing-2", "other"))
    ]
    for proof in proofs:
        lockfile.update_lockfile(
            lambda data, proof=proof: cmd_pull._record_managed_agent_component(
                data,
                harness="pi",
                scope="project",
                directory=project,
                agent_id="agent-1",
                agent_version="1.0.0",
                component={"id": proof["listing_id"], "type": "skill"},
                proof=proof,
            )
        )
    before = lockfile.installed_agent("pi", "agent-1", scope="project", directory=str(project))
    incoming = (
        [proofs[0]]
        if change == "removed"
        else [
            proofs[0],
            {**proofs[1], "target": str(project / "another-root" / "other")},
        ]
    )
    with pytest.raises(RuntimeError, match="without its ownership receipt"):
        cmd_pull._sync_managed_agent_lock(
            harness="pi",
            scope="project",
            directory=project,
            agent_id="agent-1",
            agent_version="2.0.0",
            metadata={"id": "agent-1", "version": "2.0.0", "scope": "project", "directory": str(project)},
            components=[{"id": proof["listing_id"], "type": "skill"} for proof in incoming],
            proofs=incoming,
        )
    assert lockfile.installed_agent("pi", "agent-1", scope="project", directory=str(project)) == before


def test_failed_initial_pull_removes_agent_tracking_as_well_as_folder(machine: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    ((bundle, target, proof, pin),) = _plan(project, _bundle())[0]
    outcome = managed_skill.transact(
        bundle,
        target,
        proof,
        record=lambda data: cmd_pull._record_managed_agent_component(
            data,
            harness="pi",
            scope="project",
            directory=project,
            agent_id="agent-1",
            agent_version="1.0.0",
            component=pin,
            proof=proof,
        ),
    )
    assert not cmd_pull._rollback_managed_agent_state(
        [(proof, outcome)],
        harness="pi",
        scope="project",
        directory=project,
        agent_id="agent-1",
        agent_version="1.0.0",
        previous=None,
    )
    assert not target.exists()
    assert lockfile.installed_agent("pi", "agent-1", scope="project", directory=str(project)) is None


def test_failed_upgrade_restores_old_agent_metadata_without_project_pin_write(machine: Path, tmp_path: Path) -> None:
    from copy import deepcopy

    project = tmp_path / "project"
    project.mkdir()
    ((first, target, old_proof, old_pin),) = _plan(project, _bundle())[0]
    managed_skill.transact(
        first,
        target,
        old_proof,
        record=lambda data: cmd_pull._record_managed_agent_component(
            data,
            harness="pi",
            scope="project",
            directory=project,
            agent_id="agent-1",
            agent_version="1.0.0",
            component=old_pin,
            proof=old_proof,
        ),
    )
    previous = deepcopy(lockfile.installed_agent("pi", "agent-1", scope="project", directory=str(project)))
    ((second, _, proof, pin),) = _plan(
        project,
        _bundle("version-2", b"new"),
        agent_version="2.0.0",
        allow_change=True,
    )[0]
    outcome = managed_skill.transact(
        second,
        target,
        proof,
        record=lambda data: cmd_pull._record_managed_agent_component(
            data,
            harness="pi",
            scope="project",
            directory=project,
            agent_id="agent-1",
            agent_version="2.0.0",
            component=pin,
            proof=proof,
            allow_agent_pin_change=True,
        ),
        allow_agent_pin_change=True,
    )
    assert not cmd_pull._rollback_managed_agent_state(
        [(proof, outcome)],
        harness="pi",
        scope="project",
        directory=project,
        agent_id="agent-1",
        agent_version="2.0.0",
        previous=previous,
    )
    assert (target / "run.sh").read_bytes() == b"hello"
    assert lockfile.installed_agent("pi", "agent-1", scope="project", directory=str(project)) == previous


@pytest.mark.parametrize("overlap", ["skill", "config"])
def test_legacy_agent_writers_refuse_any_managed_folder_owner(machine: Path, tmp_path: Path, overlap: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    ((bundle, target, proof, pin),) = _plan(project, _bundle())[0]
    managed_skill.transact(
        bundle,
        target,
        proof,
        record=lambda data: cmd_pull._record_managed_agent_component(
            data,
            harness="pi",
            scope="project",
            directory=project,
            agent_id="other-agent",
            agent_version="1.0.0",
            component=pin,
            proof=proof,
        ),
    )
    snippet = (
        {"skill_components": [{"name": "demo", "path": ".pi/skills/demo/SKILL.md"}]}
        if overlap == "skill"
        else {"mcp_config": {"path": ".pi/skills/demo/settings.json", "content": {}}}
    )
    with pytest.raises(managed_skill.ManagedSkillError, match="verified folder"):
        cmd_pull._preflight_existing_managed_skill_paths(
            snippet,
            harness="pi",
            target_dir=project,
            is_user_scope=False,
        )
    assert (target / "run.sh").read_bytes() == b"hello"


def test_unowned_existing_folder_and_duplicate_destinations_refuse(machine: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    raw = _bundle()
    target = project / ".pi/skills/demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("---\nname: demo\n---\n")
    with pytest.raises(managed_skill.ManagedSkillError, match="no receipt"):
        _plan(project, raw)
    target.joinpath("SKILL.md").unlink()
    target.rmdir()
    snippet = {"skill_components": [{"path": raw["skill_file_path"], "bundle_version_id": raw["version_id"]}] * 2}
    pin = {"id": raw["listing_id"], "type": "skill", "version_id": raw["version_id"], "digest": raw["digest"]}
    with pytest.raises(managed_skill.ManagedSkillError, match=r"duplicate|Duplicate"):
        cmd_pull._managed_agent_folders(
            snippet,
            [raw],
            {"status": "locked", "components": [pin]},
            harness="pi",
            target_dir=project,
            is_user_scope=False,
            agent_id="agent-1",
            agent_version="1.0.0",
            registry_url="https://registry.example",
        )


def test_config_overlap_and_symlinked_parent_fail_preflight(machine: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    raw = _bundle()
    pin = {"id": raw["listing_id"], "type": "skill", "version_id": raw["version_id"], "digest": raw["digest"]}
    snippet = {
        "skill_components": [{"path": raw["skill_file_path"], "bundle_version_id": raw["version_id"]}],
        "mcp_config": {"path": ".pi/skills/demo/settings.json", "content": {}},
    }
    with pytest.raises(managed_skill.ManagedSkillError, match="overlaps"):
        cmd_pull._managed_agent_folders(
            snippet,
            [raw],
            {"status": "locked", "components": [pin]},
            harness="pi",
            target_dir=project,
            is_user_scope=False,
            agent_id="agent-1",
            agent_version="1.0.0",
            registry_url="https://registry.example",
        )
    assert not (project / ".pi/skills/demo/SKILL.md").exists()
    linked_project = tmp_path / "linked-project"
    linked_project.mkdir()
    (linked_project / ".pi").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(managed_skill.ManagedSkillError, match="Symlink"):
        _plan(linked_project, raw)


def test_delegation_snippet_does_not_persist_machine_receipt(machine: Path, tmp_path: Path) -> None:
    project = tmp_path / "throwaway"
    project.mkdir()
    raw = _bundle()
    pin = {"id": raw["listing_id"], "type": "skill", "version_id": raw["version_id"], "digest": raw["digest"]}

    class Adapter:
        pass

    written, failed = cmd_pull.write_install_snippet(
        {
            "skill_components": [
                {"name": "demo", "path": raw["skill_file_path"], "bundle_version_id": raw["version_id"]}
            ]
        },
        harness="pi",
        adapter=Adapter(),
        target_dir=project,
        agent_id="temporary-agent",
        is_user_scope=False,
        skill_bundles=[raw],
        lock={"status": "locked", "components": [pin]},
        quiet=True,
    )
    assert not failed and written
    assert not lockfile.LOCKFILE_PATH.exists()
    assert (project / ".pi/skills/demo/run.sh").read_bytes() == b"hello"
