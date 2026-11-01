# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""File batch edits preserve untouched bytes and migrate touched legacy scripts."""

from types import SimpleNamespace

import pytest

from schemas.skill_resources import SkillFileOperations, SkillFolderSnapshot
from services.skill_bundle import MAX_FILE_BYTES
from services.skill_folder_edit import _validate_new_md, apply_file_operations, replace_folder
from services.skill_validator import SkillValidationError

MD = "---\nname: example\ndescription: Example skill\n---\n# Example\n"


def version(**changes):
    fields = {
        "delivery_mode": "registry_direct",
        "skill_md_content": MD,
        "script_filename": "old.sh",
        "script_content": "echo old\n",
        "extra_files": [{"path": "templates/one.txt", "content": "old text"}],
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


def patch(*operations):
    return SkillFileOperations.model_validate({"observed_revision": "a" * 64, "operations": list(operations)})


def test_atomic_edit_migrates_renamed_legacy_script_without_dropping_other_files():
    original = version()
    edit = apply_file_operations(
        original,
        patch(
            {"action": "rename", "path": "scripts/old.sh", "new_path": "scripts/renamed.sh"},
            {"action": "put", "file": {"path": "templates/one.txt", "content": "new text"}},
            {"action": "put", "file": {"path": "templates/empty.txt", "content": ""}},
        ),
    )
    assert original.script_filename == "old.sh"  # Validate before mutating the ORM row.
    assert edit.script_filename is None and edit.script_content is None
    assert [(file.path, file.content, file.executable) for file in edit.files] == [
        ("SKILL.md", MD.encode(), False),
        ("templates/one.txt", b"new text", False),
        ("scripts/renamed.sh", b"echo old\n", True),
        ("templates/empty.txt", b"", False),
    ]


def test_oversized_stored_legacy_script_rename_is_a_handled_validation_error():
    original = version(script_content="x" * (MAX_FILE_BYTES + 800_000))
    with pytest.raises(SkillValidationError, match="Legacy script exceeds"):
        apply_file_operations(
            original, patch({"action": "rename", "path": "scripts/old.sh", "new_path": "scripts/new.sh"})
        )
    assert original.script_filename == "old.sh"


def test_file_replacement_clears_git_and_script_slots_at_call_site():
    edit = replace_folder(
        SkillFolderSnapshot.model_validate(
            {"skill_md_content": MD, "extra_files": [{"path": "scripts/tool.sh", "content": "", "executable": True}]}
        )
    )
    assert edit.script_filename is None and edit.script_content is None
    assert edit.files[-1].declaration.mode == "0755"
    assert edit.files[-1].declaration.size == 0


@pytest.mark.parametrize(
    "frontmatter",
    [
        "name: UPPER\ndescription: Example",
        "name: bad--name\ndescription: Example",
        "name: example\ndescription: " + "x" * 1025,
        "name: example\ndescription: Example\ncompatibility: " + "x" * 501,
        "name: example\ndescription: Example\nmetadata: not-a-map",
        "name: example\ndescription: Example\nmetadata: {count: 3}",
        "name: example\ndescription: Example\nallowed-tools: [bash]",
    ],
)
def test_new_folder_rejects_invalid_standard_metadata_without_rewriting_legacy(frontmatter):
    content = f"---\n{frontmatter}\n---\n# Body\n"
    with pytest.raises(SkillValidationError):
        replace_folder(SkillFolderSnapshot(skill_md_content=content, extra_files=[]))
    assert _validate_new_md(content) is None  # An unchanged historical draft can still be forked.


def test_new_folder_preserves_observal_command_extension_and_standard_fields():
    content = (
        "---\nname: café\ndescription: When handling documents, use this skill.\n"
        "license: Apache-2.0\ncompatibility: Requires Python 3.\n"
        "metadata: {team: docs}\nallowed-tools: Bash(git)\ncommand: docs\n---\n# Body\n"
    )
    edit = replace_folder(SkillFolderSnapshot(skill_md_content=content, extra_files=[]))
    assert edit.skill_md_content == content
    assert _validate_new_md(content, authored=True) == "docs"


@pytest.mark.parametrize(
    "operations",
    [
        [{"action": "delete", "path": "SKILL.md"}],
        [{"action": "rename", "path": "SKILL.md", "new_path": "renamed.md"}],
        [{"action": "delete", "path": "missing"}],
        [{"action": "rename", "path": "templates/one.txt", "new_path": "scripts/old.sh"}],
        [{"action": "put", "file": {"path": "SKILL.md", "content": MD, "executable": True}}],
        [{"action": "put", "file": {"path": "SKILL.md", "content": "# no frontmatter"}}],
        [
            {"action": "put", "file": {"path": "templates/other.txt", "content": "x"}},
            {"action": "delete", "path": "SKILL.md"},
        ],
    ],
)
def test_invalid_file_batch_cannot_mutate_original_snapshot(operations):
    original = version()
    with pytest.raises(SkillValidationError):
        apply_file_operations(original, patch(*operations))
    assert original.script_filename == "old.sh"
    assert original.extra_files == [{"path": "templates/one.txt", "content": "old text"}]
