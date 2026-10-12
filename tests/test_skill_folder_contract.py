# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Bounded author/reviewer contract; resource-bearing decisions remain gated until rollout."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from api.routes.skill_files import _forkable_direct_base
from schemas.component_version import VersionReviewRequest
from schemas.skill import SkillFolderDraftRequest, SkillInstallRequest
from schemas.skill_resources import (
    SkillFileContents,
    SkillFileOperations,
    SkillFolderSnapshot,
    SkillSnapshotReplace,
    SkillVersionManifest,
)
from services.skill_bundle import MAX_BUNDLE_BYTES, validate_skill_bundle
from services.skill_validator import SkillValidationError, validate_skill_md_content_frontmatter

FIXTURE = json.loads((Path(__file__).parent / "fixtures/skill_folder_contract.json").read_text())


def test_historical_direct_base_needing_authoring_repairs_uses_import_not_fork():
    base = SimpleNamespace(
        delivery_mode="registry_direct",
        skill_md_content=FIXTURE["snapshot"]["skill_md_content"],
        script_filename=None,
        script_content=None,
        extra_files=[],
    )
    assert _forkable_direct_base(base)
    base.skill_md_content += "x" * (2 * 1024 * 1024)
    assert not _forkable_direct_base(base)
    base.skill_md_content = FIXTURE["snapshot"]["skill_md_content"]
    base.extra_files = [{"path": "template.txt", "content": "x" * (2 * 1024 * 1024 + 1)}]
    assert not _forkable_direct_base(base)
    base.extra_files = []
    base.delivery_mode = "git_fetch"
    assert not _forkable_direct_base(base)


def test_full_tree_and_request_examples_bind_to_same_version():
    snapshot = SkillFolderSnapshot.model_validate(FIXTURE["snapshot"])
    analysis = validate_skill_md_content_frontmatter(snapshot.skill_md_content)
    assert analysis.frontmatter["name"] == "example"
    assert analysis.frontmatter["description"] == "Example skill"
    files = validate_skill_bundle(
        delivery_mode="registry_direct", skill_md_content=snapshot.skill_md_content, extra_files=snapshot.extra_files
    )
    assert len(files) == 10  # SKILL.md, 2 scripts, 5 templates, empty script, binary
    assert [f.path for f in files if f.executable] == [
        "scripts/one.sh",
        "scripts/two.py",
        "scripts/empty.sh",
    ]
    assert next(f.content for f in files if f.path == "scripts/empty.sh") == b""
    assert next(f.content for f in files if f.path == "assets/logo.bin") == b"\x00\xff"
    declarations = [f.declaration for f in files]
    manifest = SkillVersionManifest.model_validate_json(
        json.dumps(FIXTURE["manifest_identity"] | {"files": [entry.model_dump() for entry in declarations]})
    )
    assert len(manifest.files) == len(files)
    for file in files:
        if file.path == "assets/logo.bin":
            continue  # Binary previews are raw attachment responses, not SkillFileContents JSON.
        detail = SkillFileContents(
            version_id=manifest.version_id,
            revision=manifest.revision,
            file=file.declaration,
            content=file.content.decode("utf-8"),
            encoding="utf-8",
        )
        assert detail.file.sha256 == file.declaration.sha256
    assert str(manifest.version_id) == FIXTURE["draft_version_id"]
    assert SkillFolderDraftRequest.model_validate(FIXTURE["author_create"]["body"]).version == "1.1.0"
    assert SkillSnapshotReplace.model_validate(FIXTURE["author_replace"]["body"]).observed_revision == manifest.revision
    assert SkillSnapshotReplace.model_validate(FIXTURE["author_replace"]["body"]).extra_files == snapshot.extra_files
    assert len(SkillFileOperations.model_validate(FIXTURE["author_patch"]["body"]).operations) == 3
    assert VersionReviewRequest.model_validate(FIXTURE["review"]["body"]).observed_revision == manifest.revision
    assert FIXTURE["review"]["path"] == (
        f"/api/v1/review/skills/{FIXTURE['listing_id']}/versions/{FIXTURE['draft_version_id']}/decision"
    )
    assert FIXTURE["author_withdraw"]["path"] == (
        f"/api/v1/skills/{FIXTURE['listing_id']}/versions/{FIXTURE['draft_version_id']}/withdraw"
    )
    assert SkillInstallRequest.model_validate(FIXTURE["standalone_request"]).supported_features == [
        "skill_extra_files_v1"
    ]
    assert FIXTURE["agent_request"]["supported_features"] == ["skill_extra_files_v1"]
    assert FIXTURE["failures"]["missing_feature"]["status"] == 409


def test_sixty_file_tree_is_bounded_and_manifest_is_content_free():
    template = FIXTURE["sixty_file_tree"]
    extras = [
        {"path": f"{template['path_prefix']}{i:02}{template['path_suffix']}", "content": f"item-{i:02}"}
        for i in range(template["first_index"], template["last_index"] + 1)
    ]
    assert len(extras) == 60
    snapshot = SkillFolderSnapshot(skill_md_content=template["skill_md_content"], extra_files=extras)
    files = validate_skill_bundle(
        delivery_mode="registry_direct", skill_md_content=snapshot.skill_md_content, extra_files=snapshot.extra_files
    )
    assert len(files) == 61
    assert sum(f.declaration.size for f in files) < MAX_BUNDLE_BYTES
    manifest = SkillVersionManifest.model_validate_json(
        json.dumps(FIXTURE["manifest_identity"] | {"files": [f.declaration.model_dump() for f in files]})
    )
    assert '"content"' not in manifest.model_dump_json()
    assert manifest.files[-1].path == "templates/item-59.txt"


@pytest.mark.parametrize(
    "change",
    [
        {"extra_files": [{"path": "a", "content": "x"}] * 129},
        {"extra_files": [{"path": "a", "content": "x" * 2_796_205}]},
        {"skill_md_content": "x" * (2 * 1024 * 1024 + 1)},
    ],
)
def test_snapshot_schema_rejects_oversized_payloads(change):
    with pytest.raises(ValidationError):
        SkillFolderSnapshot.model_validate(FIXTURE["snapshot"] | change)


def test_revision_and_operations_are_bounded_and_fail_closed():
    base = FIXTURE["author_patch"]["body"]
    for patch in (
        {"observed_revision": "bad"},
        {"operations": []},
        {"operations": base["operations"] * 43},
        {"operations": [{"action": "invalid", "path": "SKILL.md"}]},
        {"operations": [{"action": "put", "file": {"path": "x", "content": "a", "executable": 1}}]},
    ):
        with pytest.raises(ValidationError):
            SkillFileOperations.model_validate(base | patch)
    with pytest.raises(ValidationError):
        VersionReviewRequest(action="approve", observed_revision="bad")
    with pytest.raises(SkillValidationError, match="Duplicate"):
        validate_skill_bundle(
            delivery_mode="registry_direct",
            skill_md_content=FIXTURE["snapshot"]["skill_md_content"],
            extra_files=[{"path": "SKILL.md", "content": "replacement"}],
        )
