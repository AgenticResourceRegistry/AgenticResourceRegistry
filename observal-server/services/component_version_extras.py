# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Per-type field validation for component version publishing."""

from __future__ import annotations

import re

from fastapi import HTTPException
from loguru import logger as optic
from pydantic import TypeAdapter, ValidationError

from schemas.skill_commands import normalize_slash_command
from schemas.skill_resources import SkillResource
from services.skill_validator import SkillValidationError, validate_skill_md_content_frontmatter

_OCI_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,499}$")

# Fields allowed in extra dict per component type
HOOK_FIELDS = {
    "event",
    "execution_mode",
    "priority",
    "handler_type",
    "handler_config",
    "scope",
    "tool_filter",
    "source_url",
    "source_ref",
    "source_path",
    "resolved_sha",
    "script_content",
    "script_filename",
    "requirements",
    "file_pattern",
}

SKILL_FIELDS = {
    "skill_path",
    "git_url",
    "git_ref",
    "skill_md_content",
    "target_agents",
    "task_type",
    "slash_command",
    "has_scripts",
    "delivery_mode",
    "script_content",
    "script_filename",
    "extra_files",
}

PROMPT_FIELDS = {
    "category",
    "template",
    "variables",
    "model_hints",
    "tags",
}

MCP_FIELDS = {
    "source_url",
    "source_ref",
    "resolved_sha",
    "transport",
    "framework",
    "docker_image",
    "command",
    "args",
    "url",
    "headers",
    "auto_approve",
    "environment_variables",
    "setup_instructions",
}

SANDBOX_FIELDS = {
    "runtime_type",
    "image",
    "resource_limits",
    "network_policy",
    "entrypoint",
    "runtime_config",
    "source_url",
    "source_ref",
    "resolved_sha",
    "sandbox_path",
}

REQUIRED_FIELDS: dict[str, set[str]] = {
    "hook": {"event", "handler_type"},
    "skill": {"task_type"},
    "prompt": {"category", "template"},
    "mcp": set(),
    "sandbox": set(),
}

ALLOWED_FIELDS: dict[str, set[str]] = {
    "hook": HOOK_FIELDS,
    "skill": SKILL_FIELDS,
    "prompt": PROMPT_FIELDS,
    "mcp": MCP_FIELDS,
    "sandbox": SANDBOX_FIELDS,
}

# Expected types for each field. Fields not listed accept any type.
FIELD_TYPES: dict[str, type | tuple[type, ...]] = {
    # str fields
    "event": str,
    "execution_mode": str,
    "handler_type": str,
    "scope": str,
    "skill_path": str,
    "git_url": str,
    "git_ref": str,
    "skill_md_content": str,
    "delivery_mode": str,
    "script_content": str,
    "script_filename": str,
    "extra_files": list,
    "task_type": str,
    "slash_command": str,
    "category": str,
    "template": str,
    "source_url": str,
    "source_ref": str,
    "resolved_sha": str,
    "transport": str,
    "framework": str,
    "docker_image": str,
    "command": str,
    "url": str,
    "setup_instructions": str,
    "runtime_type": str,
    "image": str,
    "network_policy": str,
    "entrypoint": str,
    "sandbox_path": str,
    # int fields
    "priority": int,
    # bool fields - must come before int since bool is a subclass of int
    "has_scripts": bool,
    "has_templates": bool,
    "is_power": bool,
    # dict fields
    "handler_config": dict,
    "input_schema": dict,
    "output_schema": dict,
    "mcp_server_config": dict,
    "model_hints": dict,
    "resource_limits": dict,
    "runtime_config": dict,
    # list fields
    "tool_filter": list,
    "file_pattern": list,
    "target_agents": list,
    "triggers": list,
    "activation_keywords": list,
    "tags": list,
    "variables": list,
    "args": list,
    "headers": list,
    "auto_approve": list,
    "environment_variables": list,
}


def validate_and_extract(component_type: str, extra: dict | None) -> dict:
    """Validate extra fields for a component type and return clean field dict.

    Returns a dict of field_name -> value to set on the version model.
    Raises HTTPException(422) on validation errors.
    """
    optic.trace("extracting version extras for {} component", component_type)
    allowed = ALLOWED_FIELDS.get(component_type)
    if allowed is None:
        raise HTTPException(status_code=422, detail=f"Unknown component type: {component_type!r}")

    required = REQUIRED_FIELDS.get(component_type, set())

    if not extra:
        if required:
            raise HTTPException(
                status_code=422,
                detail=f"Missing required fields for {component_type}: {', '.join(sorted(required))}",
            )
        return {}

    # Check for unknown fields
    unknown = set(extra.keys()) - allowed
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown fields for {component_type}: {', '.join(sorted(unknown))}",
        )

    # Check required fields are present
    missing = required - set(extra.keys())
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required fields for {component_type}: {', '.join(sorted(missing))}",
        )

    # Check required fields have non-empty/non-null values
    for field in required:
        value = extra.get(field)
        if value is None or value == "":
            raise HTTPException(
                status_code=422,
                detail=f"Required field {field!r} cannot be empty",
            )

    # Check field types
    for field, value in extra.items():
        if value is None:
            continue
        expected = FIELD_TYPES.get(field)
        if expected is None:
            continue
        # bool check must happen before int check (bool is subclass of int)
        if expected is int and isinstance(value, bool):
            raise HTTPException(
                status_code=422,
                detail=f"Field {field!r} must be an integer, got {type(value).__name__}",
            )
        if not isinstance(value, expected):
            _type_names = {int: "integer", str: "str", bool: "bool", dict: "dict", list: "list"}
            expected_name = _type_names.get(
                expected, expected.__name__ if isinstance(expected, type) else str(expected)
            )
            raise HTTPException(
                status_code=422,
                detail=f"Field {field!r} must be a {expected_name}, got {type(value).__name__}",
            )

    clean = {k: v for k, v in extra.items() if k in allowed}
    if component_type == "skill":
        if "extra_files" in clean:
            if clean["extra_files"] is None:
                raise HTTPException(status_code=422, detail="extra_files cannot be null; use [] to clear")
            try:
                clean["extra_files"] = [
                    item.model_dump() for item in TypeAdapter(list[SkillResource]).validate_python(clean["extra_files"])
                ]
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail="Invalid extra_files entries") from exc
        try:
            if "slash_command" in clean:
                clean["slash_command"] = normalize_slash_command(clean["slash_command"])
            if "skill_md_content" in clean:
                analysis = validate_skill_md_content_frontmatter(
                    clean.get("skill_md_content"),
                    slash_command=clean.get("slash_command") if "slash_command" in clean else None,
                )
                if analysis.slash_command is not None:
                    clean["slash_command"] = analysis.slash_command
        except (SkillValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid skill metadata: {exc}") from exc

    if component_type == "sandbox":
        _validate_sandbox_extra(clean)

    return clean


def _validate_sandbox_extra(clean: dict) -> None:
    runtime_type = clean.get("runtime_type")
    image = clean.get("image")
    runtime_config = clean.get("runtime_config") or {}
    if runtime_type is not None:
        from schemas.constants import VALID_SANDBOX_RUNTIME_TYPES

        if runtime_type not in VALID_SANDBOX_RUNTIME_TYPES:
            raise HTTPException(status_code=422, detail=f"Invalid runtime_type {runtime_type!r}")
    if clean.get("network_policy") is not None:
        from schemas.constants import VALID_SANDBOX_NETWORK_POLICIES

        if clean["network_policy"] not in VALID_SANDBOX_NETWORK_POLICIES:
            raise HTTPException(status_code=422, detail=f"Invalid network_policy {clean['network_policy']!r}")
    if runtime_type == "docker" and image and ("://" in image or not _OCI_REF_RE.match(image)):
        raise HTTPException(status_code=422, detail="Docker image must be an OCI/Docker image reference")
    if runtime_type == "firecracker" and not (
        runtime_config.get("config_path")
        or (runtime_config.get("kernel_image_path") and runtime_config.get("rootfs_path"))
    ):
        raise HTTPException(
            status_code=422,
            detail="Firecracker requires runtime_config.config_path or kernel_image_path/rootfs_path",
        )
    if runtime_type == "wasm" and not (image or runtime_config.get("module")):
        raise HTTPException(status_code=422, detail="WASM requires image or runtime_config.module")
