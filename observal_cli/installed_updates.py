# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Shared installed-state comparison for `outdated` and future startup notices.

No install is performed here. `verify_releases=True` fetches the exact approved
release before treating a candidate as a startup-notice target. Ordinary
`outdated` keeps its existing wire and JSON contract by not requesting details.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from packaging.version import InvalidVersion, Version

from observal_cli import client
from observal_cli.constants import VALID_HARNESSES
from observal_cli.errors import CliError, ErrorCategory, fail

_OPERATION = "Check installed versions"
_COMPONENT_TYPES = {"mcp", "skill", "hook"}
_PUBLIC_FIELDS = (
    "id",
    "qualified_name",
    "name",
    "namespace",
    "slug",
    "type",
    "harness",
    "current_version",
    "latest_version",
    "status",
    "outdated",
    "error",
    "upgrade_command",
)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def prepare_entry(entry: object, lockfile_path: str) -> dict:
    if not isinstance(entry, dict):
        fail(
            ErrorCategory.VALIDATION,
            "The installed-state lockfile contains an invalid item entry.",
            operation=_OPERATION,
            resource=lockfile_path,
            remediation="Reinstall the affected item to rebuild its lockfile entry.",
        )
    entry_type = _text(entry.get("entry_type"))
    component_type = _text(entry.get("type"))
    item_type = "agent" if entry_type == "agent" else component_type if entry_type == "standalone" else None
    if item_type != "agent" and item_type not in _COMPONENT_TYPES:
        fail(
            ErrorCategory.VALIDATION,
            "The installed-state lockfile contains an unsupported item type.",
            operation=_OPERATION,
            resource=lockfile_path,
            remediation="Reinstall the affected item to rebuild its lockfile entry.",
        )

    item_id = _text(entry.get("id"))
    current_version = _text(entry.get("version"))
    item_harness = _text(entry.get("harness"))
    if not item_id or not item_harness:
        fail(
            ErrorCategory.VALIDATION,
            "An installed-state lockfile entry is missing its ID or harness.",
            operation=_OPERATION,
            resource=lockfile_path,
            remediation="Reinstall the affected item to rebuild its lockfile entry.",
        )
    if item_harness not in VALID_HARNESSES:
        fail(
            ErrorCategory.VALIDATION,
            "An installed-state lockfile entry uses an unsupported harness.",
            operation=_OPERATION,
            resource=lockfile_path,
            remediation="Reinstall the affected item for a currently supported harness.",
        )
    try:
        if current_version is not None:
            Version(current_version)
    except InvalidVersion as error:
        fail(
            ErrorCategory.VALIDATION,
            "An installed-state lockfile entry has an invalid version.",
            operation=_OPERATION,
            resource=lockfile_path,
            remediation="Reinstall the affected item to rebuild its lockfile entry.",
            detail=repr(error),
        )

    name = _text(entry.get("name")) or item_id[:8]
    namespace = _text(entry.get("namespace"))
    slug = _text(entry.get("slug"))
    qualified_name = _text(entry.get("qualified_name"))
    if not qualified_name:
        qualified_name = f"{namespace}/{slug}" if namespace and slug else item_id

    return {
        "id": item_id,
        "qualified_name": qualified_name,
        "name": name,
        "namespace": namespace,
        "slug": slug,
        "type": item_type,
        "harness": item_harness,
        "current_version": current_version,
        # Installation context is intentionally private to the shared service;
        # outdated's public JSON retains its existing fields and semantics.
        "scope": _text(entry.get("scope")),
        "directory": _text(entry.get("directory")),
        "local_name": _text(entry.get("local_name")),
        "requested_version": _text(entry.get("requested_version")),
        "pin_known": entry.get("pin_known") is True,
        "version_id": _text(entry.get("version_id")),
        "digest": _text(entry.get("digest")),
        "lock_digest": _text(entry.get("lock_digest")),
        "lock_status": _text(entry.get("lock_status")),
        "components": entry.get("components"),
        # A managed complete-folder receipt (on the item or any agent component)
        # means only the reviewed --upgrade flow may replace it.
        "folder_managed": isinstance(entry.get("folder_receipt"), dict)
        or any(
            isinstance(c, dict) and isinstance(c.get("folder_receipt"), dict) for c in entry.get("components") or []
        ),
    }


def inventory_for_context(harness: str, cwd: str) -> list[dict]:
    """Only installations relevant to this harness and exact project root.

    Unknown legacy scopes stay visible in explicit `outdated` but must not be
    silently treated as user installations at startup.
    """
    from observal_cli.lockfile import LOCKFILE_PATH, get_all_entries

    if harness not in VALID_HARNESSES:
        raise ValueError(f"Unsupported harness: {harness}")
    root = Path(cwd).resolve()
    entries = [prepare_entry(raw, str(LOCKFILE_PATH)) for raw in get_all_entries(harness=harness)]
    return [
        item
        for item in entries
        if item["scope"] == "user"
        or (item["scope"] == "project" and item["directory"] and Path(item["directory"]).resolve() == root)
    ]


def public_result(item: dict) -> dict:
    """Do not leak local roots, pins or source metadata into outdated JSON."""
    return {field: item[field] for field in _PUBLIC_FIELDS}


def upgrade_command(item: dict) -> str:
    """Legacy hint for `outdated`; not a verified safe automatic install plan."""
    target = shlex.quote(item["qualified_name"])
    harness = shlex.quote(item["harness"])
    if item["type"] == "agent":
        return f"observal agent pull {target} --harness {harness} --no-prompt --upgrade"
    prompt_flag = " --no-prompt" if item["type"] == "mcp" else ""
    return f"observal registry {item['type']} install {target} --harness {harness}{prompt_flag}"


def version_newer(latest: str, current: str) -> bool:
    return Version(latest) > Version(current)


def error_payload(error: CliError) -> dict:
    return {
        "category": error.category.value,
        "message": error.message,
        "operation": error.operation,
        "resource": error.resource,
        "remediation": error.remediation,
        "request_id": error.request_id,
        "http_status": error.http_status,
        "exit_code": error.contract_exit_code,
    }


def _registry_path(item_type: str, item_id: str) -> str:
    return f"/api/v1/agents/{item_id}" if item_type == "agent" else f"/api/v1/{item_type}s/{item_id}"


def _latest_version(item_type: str, data: dict, *, verified: bool) -> object:
    if item_type == "agent":
        # Never mistake a draft/pending latest agent version for an approved one.
        return (
            data.get("latest_approved_version")
            if verified
            else data.get("latest_approved_version") or data.get("version")
        )
    return data.get("version")


def _release(item: dict, version: str) -> tuple[dict | None, str | None]:
    """Verify exact target release; 404/private/malformed releases are notice-only."""
    path = f"{_registry_path(item['type'], item['id'])}/versions/{version}"
    try:
        detail = client.get(path, operation=_OPERATION, resource=f"{item['type']} {item['qualified_name']}@{version}")
    except CliError as error:
        if error.category in {ErrorCategory.NOT_FOUND, ErrorCategory.PERMISSION}:
            return None, "The exact release is not accessible; update manually after review."
        raise
    if not isinstance(detail, dict) or detail.get("version") != version or detail.get("status") != "approved":
        return None, "The exact release is not confirmed approved; update manually after review."
    harnesses = detail.get("supported_harnesses")
    if not isinstance(harnesses, list) or item["harness"] not in harnesses:
        return None, "The exact release does not declare support for this harness."
    description = _text(detail.get("description"))
    changelog = _text(detail.get("changelog")) if item["type"] != "agent" else None
    return {
        "description": description,
        "changelog": changelog,
        "components": detail.get("components") if item["type"] == "agent" else None,
    }, None


def compare(entries: list[dict], *, verify_releases: bool = False) -> list[dict]:
    """Compare prepared local entries with the active registry, preserving scope.

    `verify_releases` is mandatory before a startup notice or future auto-update.
    No cached or unverified listing response authorizes an install.
    """
    results: list[dict] = []
    for item in entries:
        try:
            data = client.get(
                _registry_path(item["type"], item["id"]),
                operation=_OPERATION,
                resource=f"{item['type']} {item['qualified_name']}",
            )
        except CliError as error:
            if error.category is not ErrorCategory.NOT_FOUND:
                raise
            results.append(
                {
                    **item,
                    "latest_version": None,
                    "status": "missing",
                    "outdated": False,
                    "error": error_payload(error),
                    "upgrade_command": None,
                }
            )
            continue
        if not isinstance(data, dict):
            fail(
                ErrorCategory.UNAVAILABLE,
                "The registry returned an invalid item response.",
                operation=_OPERATION,
                resource=f"{item['type']} {item['qualified_name']}",
                remediation="Check server health and version compatibility, then retry.",
            )
        latest = _latest_version(item["type"], data, verified=verify_releases)
        if verify_releases and (not latest or data.get("status") == "archived"):
            results.append(
                {
                    **item,
                    "latest_version": None,
                    "status": "missing",
                    "outdated": False,
                    "error": None,
                    "upgrade_command": None,
                    "reason": "No visible approved release.",
                }
            )
            continue
        if not isinstance(latest, str) or not latest.strip():
            fail(
                ErrorCategory.UNAVAILABLE,
                "The registry response does not contain a valid latest version.",
                operation=_OPERATION,
                resource=f"{item['type']} {item['qualified_name']}",
                remediation="Check server health and version compatibility, then retry.",
            )
        try:
            Version(latest)
            is_outdated = item["current_version"] is not None and version_newer(latest, item["current_version"])
        except InvalidVersion as error:
            fail(
                ErrorCategory.UNAVAILABLE,
                "The registry returned an invalid latest version.",
                operation=_OPERATION,
                resource=f"{item['type']} {item['qualified_name']}",
                remediation="Correct the registry version and retry.",
                detail=repr(error),
            )
        namespace = _text(data.get("namespace")) or item["namespace"]
        slug = _text(data.get("slug")) or item["slug"]
        qualified_name = f"{namespace}/{slug}" if namespace and slug else item["qualified_name"]
        result = {
            **item,
            "qualified_name": qualified_name,
            "namespace": namespace,
            "slug": slug,
            "latest_version": latest,
            "status": "unknown" if item["current_version"] is None else "outdated" if is_outdated else "current",
            "outdated": is_outdated,
            "error": None,
            "upgrade_command": None,
        }
        if result["status"] != "current":
            result["upgrade_command"] = upgrade_command(result)
        if verify_releases and is_outdated:
            release, reason = _release(result, latest)
            result["release"] = release
            result["release_verified"] = release is not None
            # `outdated` is version availability, not install eligibility.
            # Keep it true for a pinned or unverified target so startup can
            # still display the appropriate notice/diagnostic.
            if reason:
                result["status"] = "skipped"
                result["reason"] = reason
            elif item["requested_version"]:
                result["status"] = "skipped"
                result["reason"] = "This standalone version was explicitly pinned; update manually."
        results.append(result)
    return results
