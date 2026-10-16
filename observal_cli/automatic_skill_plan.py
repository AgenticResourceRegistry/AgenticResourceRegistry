# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Exact owned-file plan for a registry-direct, user-scoped Pi skill.

The normal skill installer remains the only writer. Git trees and unknown
file sets have no plan; an existing single registry script can be owned too.
"""

from __future__ import annotations

from pathlib import Path

from observal_cli import install_baseline, lockfile
from observal_cli.shared.utils import sanitize_name


class SkillPlanError(ValueError):
    """This skill needs a manual install."""


def identity(component_id: str) -> str:
    # Baseline keys predate component ownership; namespace standalone skills
    # separately from agents with the same registry UUID.
    return f"skill:{component_id}"


def release_key(version: str, digest: str | None, version_id: str | None) -> str:
    return f"skill:{version}:{digest or ''}:{version_id or ''}"


SKILL_ROOTS = {"pi": (".pi", "agent", "skills"), "claude-code": (".claude", "skills")}


def destination(name: str, harness: str = "pi") -> Path:
    return Path.home().joinpath(*SKILL_ROOTS[harness]) / sanitize_name(name)


def owned_files(root: Path) -> list[Path]:
    """Only SKILL.md and, optionally, one existing direct registry script."""
    if not root.is_absolute() or any(part.is_symlink() for part in (root, *root.parents)) or not root.is_dir():
        raise SkillPlanError("The Pi skill destination is missing or crosses a link; update manually.")
    file = root / "SKILL.md"
    contents = {p.name for p in root.iterdir()}
    if not file.is_file() or file.is_symlink() or contents not in ({"SKILL.md"}, {"SKILL.md", "scripts"}):
        raise SkillPlanError("The Pi skill has extra or missing files; update manually.")
    paths = [file]
    if "scripts" in contents:
        scripts = root / "scripts"
        if scripts.is_symlink() or not scripts.is_dir():
            raise SkillPlanError("The skill script directory is unsafe; update manually.")
        entries = list(scripts.iterdir())
        if len(entries) != 1 or not entries[0].is_file() or entries[0].is_symlink():
            raise SkillPlanError("The Pi skill does not own exactly one script; update manually.")
        paths.extend(entries)
    return paths


def unshared(registry: str, component_id: str, name: str, harness: str = "pi") -> None:
    """The lockfile may contain another owner even without its own baseline."""
    from observal_cli.lockfile import normalize_server_url

    target_name = sanitize_name(name)
    try:
        data = lockfile.read_lockfile()
        for url, section in data.get("registries", {}).items():
            for entry in section.get("harnesses", {}).get(harness, {}).get("standalone", []):
                if entry.get("type") != "skill" or entry.get("scope") != "user":
                    continue
                # The normal installer writes using the registry-provided
                # local_name, which can differ from the lockfile display name.
                # Older entries may have only the display name. Neither entry
                # needs its own baseline to claim the destination.
                local_name = entry.get("local_name")
                display_name = entry.get("name")
                if (
                    (isinstance(local_name, str) and sanitize_name(local_name) == target_name)
                    or (isinstance(display_name, str) and sanitize_name(display_name) == target_name)
                ) and (normalize_server_url(url) != normalize_server_url(registry) or entry.get("id") != component_id):
                    raise SkillPlanError("Another installed skill uses this Pi path; update manually.")
    except (OSError, RuntimeError, TypeError, KeyError) as error:
        raise SkillPlanError("Skill ownership cannot be checked; update manually.") from error


def verified_path(item: dict, *, registry: str) -> Path:
    if item.get("type") != "skill" or item.get("harness") not in SKILL_ROOTS or item.get("scope") != "user":
        raise SkillPlanError("Only user-scoped Pi or Claude Code skills can be updated automatically.")
    if item.get("pin_known") is not True or item.get("requested_version"):
        raise SkillPlanError("Skill pin intent is unknown or explicitly pinned; update manually.")
    name = item.get("name")
    local_name = item.get("local_name")
    if not isinstance(name, str) or not isinstance(local_name, str) or sanitize_name(name) != local_name:
        raise SkillPlanError("The installed skill destination is ambiguous; update manually.")
    harness = item["harness"]
    unshared(registry, item["id"], name, harness)
    root = destination(name, harness)
    owned = owned_files(root)
    files, paths = install_baseline.verified_manifest(
        registry=registry,
        harness=harness,
        agent_id=identity(item["id"]),
        scope="user",
        root=str(root),
        version=item["current_version"],
        lock_digest=release_key(item["current_version"], item.get("digest"), item.get("version_id")),
    )
    if paths != sorted(map(str, owned)) or set(files) != set(map(str, owned)):
        raise SkillPlanError("The skill ownership plan contains other files; update manually.")
    return root / "SKILL.md"


def target(item: dict, skill: dict, file: Path) -> tuple[dict[Path, bytes], dict[Path, int]]:
    """Check the normal writer's complete existing file set and target modes."""
    content = skill.get("skill_md_content")
    name = skill.get("name")
    if (
        skill.get("delivery_mode") != "registry_direct"
        or not isinstance(content, str)
        or not content
        or not isinstance(name, str)
        or sanitize_name(name) != item.get("local_name")
        or file != destination(name, item.get("harness", "pi")) / "SKILL.md"
        or skill.get("git_url")
    ):
        raise SkillPlanError("The release changes the skill path or source; update manually.")
    planned = {file: content.encode("utf-8")}
    scripts = skill.get("script_content")
    filename = skill.get("script_filename")
    if scripts is not None or filename is not None:
        if (
            not isinstance(scripts, str)
            or not scripts
            or not isinstance(filename, str)
            or filename in {"", ".", ".."}
            or Path(filename).name != filename
        ):
            raise SkillPlanError("The release has an incomplete or unsafe script; update manually.")
        script = file.parent / "scripts" / filename
        planned[script] = scripts.encode("utf-8")
    owned = owned_files(file.parent)
    if set(planned) != set(owned):
        raise SkillPlanError("The skill release changes its owned file set; update manually.")
    if sum(path.stat().st_size + len(raw) for path, raw in planned.items()) > 2 * 1024 * 1024:
        raise SkillPlanError("The skill exceeds the recovery size limit; update manually.")
    modes = {
        path: (0o755 if path != file and path.suffix in {".sh", ".bash", ".py", ".rb"} else path.stat().st_mode & 0o777)
        for path in planned
    }
    return planned, modes
