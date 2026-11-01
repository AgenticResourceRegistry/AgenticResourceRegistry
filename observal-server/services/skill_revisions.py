# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Version-scoped review revision over decoded files and reviewable metadata.

Download counters, row edit locks, status and timestamps do not change the
reviewed bytes. This is not the v2 installation pin digest: review binds the
listing identity and metadata too, whereas the install pin is release-local.
"""

import hashlib
import json

from pydantic import ValidationError

from services.skill_bundle import needs_bundle_delivery, validate_skill_bundle
from services.skill_validator import SkillValidationError


def skill_content_revision(listing, version) -> str:
    files = validate_skill_bundle(
        delivery_mode=version.delivery_mode,
        skill_md_content=version.skill_md_content,
        script_content=version.script_content,
        script_filename=version.script_filename,
        extra_files=version.extra_files or [],
        enforce_limits=False,  # Existing immutable rows can predate current author caps.
    )
    try:
        declarations = [file.declaration.model_dump() for file in sorted(files, key=lambda file: file.path)]
    except ValidationError as exc:
        raise SkillValidationError("Stored skill folder exceeds supported manifest size") from exc
    payload = {
        "listing": {
            "id": str(listing.id),
            "name": listing.name,
            "namespace": listing.namespace,
            "slug": listing.slug,
            "owner": listing.owner,
            "team_id": str(listing.team_id) if listing.team_id else None,
            "is_private": listing.is_private,
        },
        "version": {
            "id": str(version.id),
            "version": version.version,
            "description": version.description,
            "changelog": version.changelog,
            "supported_harnesses": sorted(version.supported_harnesses or []),
            "target_agents": sorted(version.target_agents or []),
            "task_type": version.task_type,
            "skill_path": version.skill_path,
            "slash_command": version.slash_command,
            "git_url": version.git_url,
            "git_ref": version.git_ref,
            "delivery_mode": version.delivery_mode,
            "validated": version.validated,
            "script_filename": version.script_filename,
            "script_content": version.script_content if version.delivery_mode == "git_fetch" else None,
            "skill_md_content": version.skill_md_content if version.delivery_mode == "git_fetch" else None,
        },
        "files": declarations,
    }
    # Old rows already persist revisions computed without this field. Only a
    # withdrawn row changes the payload; default generation zero stays binary
    # compatible with the pre-migration review revision.
    epoch = getattr(version, "review_epoch", 0) or 0
    if epoch:
        payload["version"]["review_epoch"] = epoch
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def verified_skill_revision(listing, version) -> str:
    """Refuse a changed or unbound reviewed folder before exposing its bytes.

    Older resource-less releases predate stored observations and retain their
    legacy behavior. A saved draft or a resource-bearing release cannot silently
    acquire an approval for files that were never bound to a revision.
    """
    revision = skill_content_revision(listing, version)
    bound = getattr(version, "content_revision", None)
    if bound is None and (
        needs_bundle_delivery(version)
        or getattr(version, "base_version_id", None) is not None
        or getattr(version, "base_revision", None) is not None
        or getattr(version, "review_epoch", 0)
    ):
        raise SkillValidationError("Stored skill folder has no bound review revision")
    if bound is not None and bound != revision:
        raise SkillValidationError("Stored skill folder differs from its reviewed revision")
    return revision
