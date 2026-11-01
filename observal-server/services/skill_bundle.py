# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Validate an effective (post-inheritance) direct skill bundle once, before persistence.

Routes must merge omitted fields from the previous version first; an explicitly
empty extra_files list clears inherited resources. Do not use this for git clones.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from schemas.skill_resources import SkillFileDeclaration, SkillInstallFile, SkillInstallFolder, SkillResource
from services.skill_validator import SkillValidationError, validate_skill_md_content_frontmatter

MAX_EXTRA_FILES = 128
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_PATH_BYTES = 240
MAX_SEGMENT_BYTES = 100
MAX_DEPTH = 12
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])", re.IGNORECASE)
_UNSAFE = re.compile(r'[<>:"|?*\\]')


@dataclass(frozen=True)
class SkillBundleFile:
    path: str
    content: bytes
    executable: bool = False

    @property
    def declaration(self) -> SkillFileDeclaration:
        return SkillFileDeclaration(
            path=self.path,
            size=len(self.content),
            sha256=hashlib.sha256(self.content).hexdigest(),
            mode="0755" if self.executable else "0644",
        )


SKILL_EXTRA_FILES_FEATURE = "skill_extra_files_v1"
MAX_INSTALL_RESPONSE_BYTES = 12 * 1024 * 1024


def declared_skill_folder_name(skill_md_content: str | None) -> str:
    """Choose a safe installed directory matching the Agent Skills frontmatter name.

    Registry namespace/slug and user aliases are lookup/display identities, not
    names for a resource-bearing complete folder. Historical resource-less and
    empty-script installs retain legacy names even when clients opt in.
    """
    analysis = validate_skill_md_content_frontmatter(skill_md_content)
    name = analysis.frontmatter.get("name")
    if not isinstance(name, str):
        raise SkillValidationError("Complete skill folder requires a string SKILL.md name")
    canonical = unicodedata.normalize("NFKC", name.strip())
    if (
        canonical != name  # the directory must equal the stored frontmatter name, never a rewritten one
        or not 1 <= len(canonical) <= 64
        or canonical != canonical.lower()
        or canonical.startswith("-")
        or canonical.endswith("-")
        or "--" in canonical
        or not all(char.isalnum() or char == "-" for char in canonical)
    ):
        raise SkillValidationError("Complete skill folder has an invalid Agent Skills name")
    validate_bundle_path(canonical)
    return canonical


def complete_skill_folder(listing_id: Any, version: Any, *, skill_file_path: str) -> SkillInstallFolder:
    """Encode the exact stored tree for an opted-in installer, never inferred paths."""
    from services.agent_lock import content_digest

    files = validate_skill_bundle(
        delivery_mode=version.delivery_mode,
        skill_md_content=version.skill_md_content,
        script_content=version.script_content,
        script_filename=version.script_filename,
        extra_files=version.extra_files or [],
        enforce_limits=False,
    )
    if not files:
        raise SkillValidationError("Direct skill folder has no files")
    total = sum(len(file.content) for file in files)
    if total > MAX_BUNDLE_BYTES or len(files) > MAX_EXTRA_FILES + 2:
        raise SkillValidationError("Stored skill folder exceeds response size limit")
    try:
        return SkillInstallFolder(
            listing_id=listing_id,
            version_id=version.id,
            digest=content_digest("skill", version),
            skill_file_path=skill_file_path,
            files=[
                SkillInstallFile(
                    version_id=version.id,
                    content=base64.b64encode(file.content).decode("ascii"),
                    **file.declaration.model_dump(),
                )
                for file in sorted(files, key=lambda item: item.path)
            ],
        )
    except ValidationError as exc:
        raise SkillValidationError("Stored skill folder exceeds manifest limits") from exc


def prepare_agent_skill_folders(
    skill_listings: dict,
    snippet: dict,
    harness: str,
    *,
    scope: str | None = None,
    folder_names: dict | None = None,
) -> list[SkillInstallFolder]:
    """Preflight each emitted harness destination; remove incomplete duplicate file copies.

    The declared folder remains authoritative. In particular Copilot synthesizes
    its own SKILL.md in `skills`; it must not overwrite the persisted version.
    This runs before recording an agent download.
    """
    from observal_shared.harness_registry import HARNESS_REGISTRY
    from services.harness.helpers import _local_registry_names
    from services.shared.utils import sanitize_name

    bundled = {key: row for key, row in skill_listings.items() if row.delivery_mode == "registry_direct"}
    if not bundled:
        return []
    spec = HARNESS_REGISTRY.get(harness.replace("_", "-"), {})
    if "skills" not in spec.get("capabilities", set()):
        raise SkillValidationError("Harness does not support complete skill folders")
    if scope is not None and snippet.get("scope") is not None and snippet["scope"] != scope:
        raise SkillValidationError("Harness emitted a different Agent scope than requested")
    scope = scope or snippet.get("scope") or spec.get("default_scope", "project")
    paths = spec.get("skills", {})
    if scope not in paths:
        raise SkillValidationError("Harness has no skill destination for this scope")
    components = snippet.get("skill_components", [])
    emitted = snippet.get("skills", [])
    if (
        not isinstance(components, list)
        or not isinstance(emitted, list)
        or any(not isinstance(entry, dict) for entry in (*components, *emitted))
    ):
        raise SkillValidationError("Harness emitted invalid skill config")
    local_names = {key: sanitize_name(value) for key, value in _local_registry_names(skill_listings).items()}
    local_names.update(folder_names or {})
    if len({name.casefold() for name in local_names.values()}) != len(local_names):
        raise SkillValidationError("Skill aliases collide at the harness destination")
    folders = []
    destinations: dict[str, str] = {}
    chosen_paths: set[str] = set()
    duplicates: set[str] = set()
    for listing_id, proxy in bundled.items():
        name = local_names[listing_id]
        matches = [component for component in components if component.get("name") == name]
        expected = paths[scope].format(name=name)
        destination = matches[0].get("path", expected) if matches else expected
        if not isinstance(destination, str) or not destination.endswith("/SKILL.md"):
            raise SkillValidationError("Harness has no usable skill folder destination")
        if listing_id in (folder_names or {}) and unicodedata.normalize("NFKC", destination.split("/")[-2]) != name:
            raise SkillValidationError("Harness renamed a skill folder away from its SKILL.md name")
        identity = destination.casefold()
        if identity in chosen_paths or len(matches) > 1:
            raise SkillValidationError("Two skill components share the same destination")
        chosen_paths.add(identity)
        file_matches = [file for file in emitted if file.get("path") == destination]
        if not matches and len(file_matches) != 1:
            raise SkillValidationError("Harness omitted this direct skill")
        if len(file_matches) > 1:
            raise SkillValidationError("Harness emitted duplicate SKILL.md entries")
        folder = complete_skill_folder(listing_id, proxy.pinned_version, skill_file_path=destination)
        folders.append(folder)
        destinations[name] = destination
        duplicates.add(destination)
    # Do not mutate the config until every component and file has passed.
    if (
        len(json.dumps(snippet, default=str).encode("utf-8"))
        + sum(len(file.content) for folder in folders for file in folder.files)
        > MAX_INSTALL_RESPONSE_BYTES
    ):
        raise SkillValidationError("Agent skill folders exceed aggregate response limit")
    bundled_names = {local_names[id_] for id_ in bundled}
    version_by_name = {local_names[folder.listing_id]: folder.version_id for folder in folders}
    for component in components:
        if component.get("name") in bundled_names:
            for field in ("skill_md_content", "script_content", "script_filename"):
                component.pop(field, None)
            component["bundle_version_id"] = str(version_by_name[component["name"]])
            # Clients require the exact destination; only some adapters emit it themselves.
            component["path"] = destinations[component["name"]]
    # Adapters that emit only SKILL.md file entries (e.g. Antigravity) have no component to carry the bundle id.
    for name in bundled_names - {component.get("name") for component in components}:
        components.append({"name": name, "path": destinations[name], "bundle_version_id": str(version_by_name[name])})
    if components:
        snippet["skill_components"] = components
    snippet["skills"] = [file for file in emitted if file.get("path") not in duplicates]
    if not snippet["skills"]:
        snippet.pop("skills", None)
    return folders


def needs_bundle_delivery(version: Any) -> bool:
    """Current installers omit extras and an empty named legacy script.

    Neither can be reported as a successful install until the complete-file
    contract is emitted and both clients materialize all declared files.
    """
    return version.delivery_mode == "registry_direct" and bool(
        version.extra_files or (version.script_filename is not None and version.script_content == "")
    )


def validate_bundle_path(path: str) -> str:
    """Reject paths unsafe on POSIX, Windows, or case-insensitive filesystems."""
    if not isinstance(path, str) or not path or path.startswith("/") or unicodedata.normalize("NFC", path) != path:
        raise SkillValidationError("Resource path must be nonempty, relative and NFC normalized")
    try:
        raw = path.encode("utf-8")
        windows_length = len(path.encode("utf-16-le")) // 2
    except UnicodeError as exc:
        raise SkillValidationError("Resource path is not valid Unicode") from exc
    # The relative path must fit both the UTF-8 limit and Windows UTF-16
    # destination budget. Installers still check the full harness destination.
    if len(raw) > MAX_PATH_BYTES or windows_length > MAX_PATH_BYTES:
        raise SkillValidationError("Resource path exceeds 240 units")
    parts = path.split("/")
    if len(parts) > MAX_DEPTH:
        raise SkillValidationError("Resource path exceeds depth limit")
    for part in parts:
        if (
            part in {"", ".", ".."}
            or len(part.encode("utf-8")) > MAX_SEGMENT_BYTES
            or len(part.encode("utf-16-le")) // 2 > MAX_SEGMENT_BYTES
            or part.endswith((" ", "."))
            or _UNSAFE.search(part)
            or _RESERVED.fullmatch(part.partition(".")[0].rstrip(" "))
            or part.casefold() == ".git"
            or any(ord(c) < 32 or ord(c) == 127 or unicodedata.category(c) in {"Cc", "Cf"} for c in part)
        ):
            raise SkillValidationError("Unsafe resource path segment")
    return path


def _decode(resource: SkillResource) -> bytes:
    if resource.encoding == "utf-8":
        try:
            return resource.content.encode("utf-8")
        except UnicodeError as exc:
            raise SkillValidationError("Resource content is not valid UTF-8") from exc
    try:
        # Bound encoded input before allocating decoded bytes.
        if len(resource.content) > ((MAX_FILE_BYTES + 2) // 3) * 4 + 4:
            raise SkillValidationError("Resource exceeds per-file limit")
        return base64.b64decode(resource.content.encode("ascii"), validate=True)
    except (UnicodeError, binascii.Error) as exc:
        raise SkillValidationError("Invalid base64 resource content") from exc


def validate_skill_bundle(
    *,
    delivery_mode: Literal["registry_direct", "git_fetch"],
    skill_md_content: str | None,
    script_content: str | None = None,
    script_filename: str | None = None,
    extra_files: list[SkillResource | dict[str, Any]],
    enforce_limits: bool = True,
) -> tuple[SkillBundleFile, ...]:
    """Return the entire decoded file set; raise SkillValidationError on invalid input.

    Pass the effective extra_files list explicitly. An omitted request field
    inherits before calling this function; [] explicitly clears it. Only previously
    stored, unchanged versions may use enforce_limits=False; paths and decoding
    always receive validation. A git_fetch skill has no locally written bundle.
    """
    if not isinstance(extra_files, list):
        raise SkillValidationError("extra_files must be a list")
    if (script_content is None) != (script_filename is None) and not (
        delivery_mode == "git_fetch" and not enforce_limits
    ):
        # Old git rows may contain one orphaned inline script field. The clone
        # determines installed bytes; only unchanged stored rows are grandfathered.
        raise SkillValidationError("script_content and script_filename must both be set or cleared")
    if delivery_mode == "git_fetch":
        if extra_files:
            raise SkillValidationError("git_fetch cannot contain extra_files")
        # Preserve historical git_fetch metadata: the clone, not these inline
        # fields, determines the installed files. They must still be coherent.
        return ()
    if delivery_mode != "registry_direct":
        raise SkillValidationError("Unknown skill delivery mode")
    if not isinstance(skill_md_content, str) or not skill_md_content:
        raise SkillValidationError("registry_direct requires SKILL.md content")
    try:
        md = skill_md_content.encode("utf-8")
        validate_skill_md_content_frontmatter(skill_md_content)
    except UnicodeError as exc:
        raise SkillValidationError("SKILL.md is not valid UTF-8") from exc
    resources = extra_files
    if len(resources) > MAX_EXTRA_FILES:
        raise SkillValidationError("Too many extra_files")
    if len(md) > MAX_BUNDLE_BYTES:
        raise SkillValidationError("Skill bundle exceeds decoded size limit")
    files = [SkillBundleFile("SKILL.md", md)]
    decoded_total = len(md)
    if script_filename is not None:
        if not isinstance(script_filename, str):
            raise SkillValidationError("Invalid script filename")
        validate_bundle_path("scripts/" + script_filename)
        if "/" in script_filename:
            raise SkillValidationError("Legacy script filename must be one segment")
        try:
            content = script_content.encode("utf-8")  # type: ignore[union-attr]
        except (UnicodeError, AttributeError) as exc:
            raise SkillValidationError("Invalid legacy script content") from exc
        # Legacy CLI treats recognized script suffixes as executable.
        decoded_total += len(content)
        if decoded_total > MAX_BUNDLE_BYTES:
            raise SkillValidationError("Skill bundle exceeds decoded size limit")
        files.append(
            SkillBundleFile(
                "scripts/" + script_filename, content, script_filename.endswith((".sh", ".bash", ".py", ".rb"))
            )
        )
    for item in resources:
        try:
            resource = item if isinstance(item, SkillResource) else SkillResource.model_validate(item)
        except (ValidationError, ValueError, TypeError) as exc:
            raise SkillValidationError("Invalid extra_files entry") from exc
        validate_bundle_path(resource.path)
        content = _decode(resource)
        if enforce_limits and len(content) > MAX_FILE_BYTES:
            raise SkillValidationError("Resource exceeds per-file limit")
        decoded_total += len(content)
        if decoded_total > MAX_BUNDLE_BYTES:
            raise SkillValidationError("Skill bundle exceeds decoded size limit")
        files.append(SkillBundleFile(resource.path, content, resource.executable))
    seen: set[str] = set()
    for file in files:
        key = file.path.casefold()
        parts = key.split("/")
        if key in seen or any("/".join(parts[:index]) in seen for index in range(1, len(parts))):
            raise SkillValidationError("Duplicate or overlapping skill file paths")
        seen.add(key)
    if any(other.startswith(path + "/") for path in seen for other in seen):
        raise SkillValidationError("Skill file collides with a directory")
    if enforce_limits and any(len(file.content) > MAX_FILE_BYTES for file in files):
        raise SkillValidationError("Skill file exceeds per-file limit")
    if enforce_limits and sum(len(file.content) for file in files) > MAX_BUNDLE_BYTES:
        raise SkillValidationError("Skill bundle exceeds decoded size limit")
    return tuple(files)
