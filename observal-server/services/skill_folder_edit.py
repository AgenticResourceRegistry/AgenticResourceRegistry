# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Apply bounded logical file edits to one complete direct skill snapshot.

No version field is mutated until the entire result validates. Legacy scripts
are migrated to explicit extras when touched; untouched scripts retain their
historical suffix-derived executable mode.
"""

from dataclasses import dataclass

from pydantic import ValidationError

from schemas.skill_resources import (
    SkillFileDelete,
    SkillFileOperations,
    SkillFilePut,
    SkillFileRename,
    SkillFolderSnapshot,
    SkillResource,
)
from services.skill_bundle import (
    SkillBundleFile,
    declared_skill_folder_name,
    validate_bundle_path,
    validate_skill_bundle,
)
from services.skill_validator import SkillValidationError, validate_skill_md_content_frontmatter


@dataclass(frozen=True)
class SkillFolderEdit:
    skill_md_content: str
    extra_files: list[dict]
    script_filename: str | None
    script_content: str | None
    files: tuple[SkillBundleFile, ...]


def _validate_new_md(content: str, *, authored: bool = False) -> str | None:
    """Preserve historical forks; validate newly authored folder bytes more strictly."""
    analysis = validate_skill_md_content_frontmatter(content)
    fm = analysis.frontmatter
    if (
        not isinstance(fm.get("name"), str)
        or not fm["name"].strip()
        or not isinstance(fm.get("description"), str)
        or not fm["description"].strip()
    ):
        raise SkillValidationError("SKILL.md must have nonempty name and description frontmatter")
    if authored:
        declared_skill_folder_name(content)
        if len(fm["description"]) > 1024:
            raise SkillValidationError("SKILL.md description exceeds 1024 characters")
        compatibility = fm.get("compatibility")
        if "compatibility" in fm and (
            not isinstance(compatibility, str) or not compatibility.strip() or len(compatibility) > 500
        ):
            raise SkillValidationError("SKILL.md compatibility must be 1-500 characters")
        metadata = fm.get("metadata")
        if "metadata" in fm and (
            not isinstance(metadata, dict)
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in metadata.items())
        ):
            raise SkillValidationError("SKILL.md metadata must map strings to strings")
        for field in ("license", "allowed-tools"):
            if field in fm and not isinstance(fm[field], str):
                raise SkillValidationError(f"SKILL.md {field} must be a string")
        # Keep nonstandard client extensions such as Observal's `command` intact.
    return analysis.slash_command


def replace_folder(snapshot: SkillFolderSnapshot, *, authored: bool = True) -> SkillFolderEdit:
    _validate_new_md(snapshot.skill_md_content, authored=authored)
    files = validate_skill_bundle(
        delivery_mode="registry_direct",
        skill_md_content=snapshot.skill_md_content,
        extra_files=snapshot.extra_files,
    )
    return SkillFolderEdit(
        skill_md_content=snapshot.skill_md_content,
        extra_files=[item.model_dump() for item in snapshot.extra_files],
        script_filename=None,
        script_content=None,
        files=files,
    )


def apply_file_operations(version, patch: SkillFileOperations) -> SkillFolderEdit:
    if version.delivery_mode != "registry_direct":
        raise SkillValidationError("File operations require registry_direct delivery")
    current = validate_skill_bundle(
        delivery_mode="registry_direct",
        skill_md_content=version.skill_md_content,
        script_filename=version.script_filename,
        script_content=version.script_content,
        extra_files=version.extra_files or [],
        enforce_limits=False,
    )
    md = version.skill_md_content
    legacy_path = f"scripts/{version.script_filename}" if version.script_filename is not None else None
    legacy_content = version.script_content
    legacy_filename = version.script_filename
    resources = {item.path: item for item in (SkillResource.model_validate(raw) for raw in (version.extra_files or []))}

    def remove(path: str) -> SkillResource:
        nonlocal legacy_content, legacy_filename
        validate_bundle_path(path)
        if path == "SKILL.md":
            raise SkillValidationError("SKILL.md cannot be removed or renamed")
        if path == legacy_path and legacy_filename is not None:
            original = next(file for file in current if file.path == legacy_path)
            try:
                resource = SkillResource(
                    path=path, content=legacy_content, encoding="utf-8", executable=original.executable
                )
            except ValidationError as exc:
                raise SkillValidationError("Legacy script exceeds per-file authoring limit") from exc
            legacy_content = legacy_filename = None
            return resource
        if path not in resources:
            raise SkillValidationError("File does not exist in this skill version")
        return resources.pop(path)

    for operation in patch.operations:
        if isinstance(operation, SkillFilePut):
            item = operation.file
            validate_bundle_path(item.path)
            if item.path == "SKILL.md":
                if item.executable or item.encoding != "utf-8":
                    raise SkillValidationError("SKILL.md must be UTF-8 and non-executable")
                _validate_new_md(item.content, authored=True)
                md = item.content
            else:
                if item.path == legacy_path and legacy_filename is not None:
                    legacy_filename = legacy_content = None
                resources[item.path] = item
        elif isinstance(operation, SkillFileDelete):
            remove(operation.path)
        elif isinstance(operation, SkillFileRename):
            validate_bundle_path(operation.new_path)
            if operation.new_path == "SKILL.md":
                raise SkillValidationError("SKILL.md cannot be removed or renamed")
            if operation.new_path == operation.path:
                raise SkillValidationError("Rename requires a different destination")
            if operation.new_path in resources or (operation.new_path == legacy_path and legacy_filename is not None):
                raise SkillValidationError("Rename destination already exists")
            item = remove(operation.path)
            resources[operation.new_path] = item.model_copy(update={"path": operation.new_path})
        else:
            raise SkillValidationError("Unknown file operation")

    extras = [resource.model_dump() for resource in resources.values()]
    files = validate_skill_bundle(
        delivery_mode="registry_direct",
        skill_md_content=md,
        script_filename=legacy_filename,
        script_content=legacy_content,
        extra_files=extras,
    )
    return SkillFolderEdit(md, extras, legacy_filename, legacy_content, files)
