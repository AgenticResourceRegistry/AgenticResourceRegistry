# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Conservative path-and-metadata three-way merge for saved direct-skill drafts."""

import base64
from copy import deepcopy

from schemas.skill_resources import SkillFolderSnapshot, SkillResource
from services.skill_bundle import validate_skill_bundle
from services.skill_folder_edit import SkillFolderEdit, replace_folder

# Only author-editable metadata shared by two releases participates. Changelog
# describes this candidate, not inherited state; identity and review fields are
# never merged into a draft.
_REBASE_FIELDS = ("description", "target_agents", "task_type", "supported_harnesses")


def merge_skill_draft(base, draft, current) -> tuple[SkillFolderEdit | None, dict, dict[str, list[str]]]:
    """Return (complete folder, merged metadata, conflicts); never mutate rows.

    Existing legacy script slots are logical files. A successful merge writes
    them as explicit extras with their effective mode, avoiding a duplicate
    script slot and preserving empty bytes. Shared edits to the same path or
    metadata field conflict even if the resulting bytes happen to match: the
    author must explicitly resolve overlapping work rather than guess intent.
    """
    snapshots = {}
    for key, version in (("base", base), ("draft", draft), ("current", current)):
        files = validate_skill_bundle(
            delivery_mode=version.delivery_mode,
            skill_md_content=version.skill_md_content,
            script_content=version.script_content,
            script_filename=version.script_filename,
            extra_files=version.extra_files or [],
            enforce_limits=False,
        )
        snapshots[key] = {file.path: file for file in files}
    before, ours, theirs = (snapshots[key] for key in ("base", "draft", "current"))

    def changed(left, right, path):
        a, b = left.get(path), right.get(path)
        if a is None or b is None:
            return a is not b
        return a.content != b.content or a.executable != b.executable

    conflicts = {"paths": [], "metadata": []}
    chosen = {}
    for path in sorted(before.keys() | ours.keys() | theirs.keys()):
        author_changed = changed(before, ours, path)
        newer_changed = changed(before, theirs, path)
        if author_changed and newer_changed:
            conflicts["paths"].append(path)
            continue
        result = ours.get(path) if author_changed else theirs.get(path)
        if result is not None:
            chosen[path] = result

    metadata = {}
    for name in _REBASE_FIELDS:
        original, authored, newer = (getattr(row, name) for row in (base, draft, current))
        author_changed = authored != original
        newer_changed = newer != original
        if author_changed and newer_changed:
            conflicts["metadata"].append(name)
        else:
            metadata[name] = deepcopy(authored if author_changed else newer)
    if conflicts["paths"] or conflicts["metadata"]:
        return None, {}, conflicts

    resources = [
        SkillResource(
            path=file.path,
            encoding="base64",
            content=base64.b64encode(file.content).decode("ascii"),
            executable=file.executable,
        )
        for path, file in sorted(chosen.items())
        if path != "SKILL.md"
    ]
    edit = replace_folder(
        SkillFolderSnapshot(skill_md_content=chosen["SKILL.md"].content.decode("utf-8"), extra_files=resources),
        authored=chosen["SKILL.md"].content != before["SKILL.md"].content,
        # Inherited historical SKILL.md is not newly authored by this rebase.
    )
    return edit, metadata, conflicts
