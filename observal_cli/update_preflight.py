# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed eligibility checks for normal agent pulls launched at startup.

This module never installs; it is kept separate from the explicit `outdated`
contract, which lists older versions regardless of auto-update eligibility.
"""

from __future__ import annotations

from packaging.version import InvalidVersion, Version

from observal_cli import auto_update_policy, install_baseline


class PreflightSkipError(ValueError):
    """The update remains available for an explicit manual pull only."""


def _identities(components: object, *, installed: bool) -> set[tuple[str, str]]:
    if not isinstance(components, list):
        raise PreflightSkipError("Component identity evidence is missing; update manually.")
    identities: set[tuple[str, str]] = set()
    for component in components:
        if not isinstance(component, dict):
            raise PreflightSkipError("Component identity evidence is malformed; update manually.")
        kind = component.get("type" if installed else "component_type")
        component_id = component.get("id" if installed else "component_id")
        if not isinstance(kind, str) or not kind or not isinstance(component_id, str) or not component_id:
            raise PreflightSkipError("A component has no exact type and registry ID; update manually.")
        version = component.get("version" if installed else "resolved_version")
        try:
            if not isinstance(version, str) or version == "latest":
                raise InvalidVersion(str(version))
            Version(version)
        except InvalidVersion as error:
            raise PreflightSkipError("A component has no exact pinned version; update manually.") from error
        identity = (kind, component_id)
        if identity in identities:
            raise PreflightSkipError("The agent has ambiguous duplicate component identities; update manually.")
        identities.add(identity)
    return identities


def require_generated_release_lock(release: object, lock: object, *, version: str, harness: str) -> None:
    """Reject a server /install lock that differs from the exact approved release.

    Call this inside the normal pull's Pi install lock, before *any* file write.
    The response's fallback/planned component list is not evidence: only the
    actual generated lock can prove the identities and pinned versions.
    """
    if (
        not isinstance(release, dict)
        or release.get("version") != version
        or release.get("status") != "approved"
        or not isinstance(release.get("supported_harnesses"), list)
        or harness not in release["supported_harnesses"]
    ):
        raise PreflightSkipError("The exact agent release is not approved for this harness.")
    if not isinstance(lock, dict) or lock.get("status") != "locked" or not lock.get("digest"):
        raise PreflightSkipError("The generated agent lock is incomplete.")
    expected = release.get("components")
    actual = lock.get("components")
    _identities(expected, installed=False)
    _identities(actual, installed=True)
    wanted = {(row["component_type"], row["component_id"]): row["resolved_version"] for row in expected}
    found = {(row["type"], row["id"]): row["version"] for row in actual}
    if wanted != found:
        raise PreflightSkipError("The generated component pins differ from the approved release.")


def pi_user_agent_candidate(item: dict, *, registry: str) -> dict[str, str]:
    return _user_agent_candidate(item, registry=registry, harness="pi")


def claude_user_agent_candidate(item: dict, *, registry: str) -> dict[str, str]:
    return _user_agent_candidate(item, registry=registry, harness="claude-code")


def _user_agent_candidate(item: dict, *, registry: str, harness: str) -> dict[str, str]:
    """A positive comparison alone is never installation authorization."""
    if item.get("type") != "agent" or item.get("harness") != harness or item.get("scope") != "user":
        raise PreflightSkipError(f"Only managed {harness} user-scope agents are eligible.")
    if (
        item.get("status") != "outdated"
        or not item.get("outdated")
        or not item.get("release")
        or item.get("release_verified") is not True
    ):
        raise PreflightSkipError(item.get("reason") or "No verified approved update is available.")
    if not auto_update_policy.policy_status(registry)["effective"]:
        raise PreflightSkipError("Automatic updates are frozen; run `observal unfreeze` to opt in.")
    if item.get("pin_known") is not True:
        raise PreflightSkipError("The user agent's pin intent is unknown; manually re-pull it before auto-updating.")
    if item.get("requested_version"):
        raise PreflightSkipError("The user explicitly pinned this agent version; update it manually.")
    if item.get("lock_status") != "locked" or not item.get("lock_digest"):
        raise PreflightSkipError("The installed agent does not have a complete component lock; update manually.")
    current = item.get("current_version")
    if not isinstance(current, str) or not current:
        raise PreflightSkipError("The installed version is unknown; update manually.")
    installed = _identities(item.get("components"), installed=True)
    target = _identities(item["release"].get("components"), installed=False)
    changed_types = {kind for kind, _id in installed ^ target}
    if harness == "claude-code" and "mcp" in changed_types:
        raise PreflightSkipError(
            "The release adds or removes an MCP. Claude Code registers an agent's MCPs per project "
            "directory, which is not updated automatically; run `observal agent pull` manually."
        )
    allowed_types = {"skill", "hook", "mcp"} if harness == "pi" else {"skill", "hook"}
    if changed_types - allowed_types:
        raise PreflightSkipError(
            "The release adds or removes a component type that needs manual review (only "
            + ("skills, hooks and credential-free MCPs" if harness == "pi" else "skills and hooks")
            + " can be added or removed automatically)."
        )
    root = item.get("directory")
    if not isinstance(root, str) or not root:
        raise PreflightSkipError("The installation root is unknown; update manually.")
    try:
        files = install_baseline.verified_files(
            registry=registry,
            harness=harness,
            agent_id=item["id"],
            scope="user",
            root=root,
            version=current,
            lock_digest=item["lock_digest"],
        )
    except install_baseline.BaselineError as error:
        raise PreflightSkipError(str(error)) from error
    if harness == "claude-code":
        from observal_cli import automatic_claude_plan

        try:
            automatic_claude_plan.profile(item, files)
        except automatic_claude_plan.ClaudePlanError as error:
            raise PreflightSkipError(str(error)) from error
    return {"current_version": current, "target_version": item["latest_version"], "verified_files": str(len(files))}
