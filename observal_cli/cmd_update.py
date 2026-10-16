# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Guarded Pi and Claude Code startup runs that use the normal installers.

Startup requires consent, verified owned files, exact release pins, and a
durable outcome journal. The installers invoked here are the ordinary
`agent pull` and `registry <type> install` commands; nothing writes a second way.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from observal_cli import (
    auto_update_policy,
    install_baseline,
    install_recovery,
    installed_updates,
    lockfile,
    update_preflight,
)
from observal_cli.errors import CliError


def _entries(harness: str | None) -> list[dict]:
    return [
        installed_updates.prepare_entry(entry, str(lockfile.LOCKFILE_PATH))
        for entry in lockfile.get_all_entries(harness=harness)
    ]


def _plan(item: dict) -> tuple[list[str] | None, str | None, Path | None]:
    """Only local item identity and verified version become argv; no shell or secrets.

    The startup installers only act on user-scope agents and skills, so any other
    scope or type has no installer here.
    """
    if item["scope"] != "user":
        return None, "The installation is outside this exact update scope.", None
    if not item.get("release_verified") or not item.get("latest_version"):
        return None, item.get("reason") or "The exact approved release could not be verified.", None
    if item.get("requested_version"):
        return None, "This version was explicitly pinned; use the manual install command to change the pin.", None
    argv = [sys.executable, "-m", "observal_cli"]
    if item["type"] == "agent":
        root = Path(item["directory"]).resolve() if item.get("directory") else None
        if root is None or not root.is_dir():
            return None, "The original agent installation directory is unavailable.", None
        argv += [
            "agent",
            "pull",
            item["id"],
            "--harness",
            item["harness"],
            "--scope",
            "user",
            "--dir",
            str(root),
            "--version",
            item["latest_version"],
            "--strict",
            "--no-prompt",
            "--output",
            "json",
        ]
    elif item["type"] == "skill":
        root = Path.cwd()
        argv += [
            "registry",
            "skill",
            "install",
            item["id"],
            "--harness",
            item["harness"],
            "--version",
            item["latest_version"],
            "--output",
            "json",
            "--scope",
            item["scope"],
        ]
    else:
        return None, "This item type has no managed install command.", None
    return argv, None, root


def _same_install(before: dict, after: dict) -> bool:
    return all(
        after.get(key) == before.get(key) for key in ("id", "type", "harness", "scope", "directory", "current_version")
    )


def _verify(item: dict) -> bool:
    """Exit zero alone is not installation evidence; read a fresh lock snapshot."""
    expected = {**item, "current_version": item["latest_version"]}
    matches = [entry for entry in _entries(item["harness"]) if _same_install(expected, entry)]
    if len(matches) != 1:
        return False
    if matches[0].get("requested_version") is not None:
        return False
    if item["type"] != "agent":
        return True
    installed = matches[0]
    if installed.get("lock_status") != "locked" or not installed.get("lock_digest"):
        return False
    try:
        release_components = item["release"]["components"]
        installed_components = installed["components"]
        update_preflight._identities(release_components, installed=False)
        update_preflight._identities(installed_components, installed=True)
        wanted = {
            (component["component_type"], component["component_id"]): component["resolved_version"]
            for component in release_components
        }
        observed = {(component["type"], component["id"]): component["version"] for component in installed_components}
    except (KeyError, TypeError, ValueError):
        return False
    return wanted == observed


def apply_startup_pi_agent(
    item: dict,
    *,
    registry: str,
    account: str,
    deadline: float,
    shutdown_requested: object,
    marker: Path,
    harness: str = "pi",
) -> dict:
    """Use the normal agent installer under the existing startup worker's journal.

    The caller owns the per-session pending record; an unsuccessful child may
    have partially written files and must leave that record unresolved.
    """
    import time

    if not callable(shutdown_requested) or harness not in {"pi", "claude-code"}:
        raise ValueError("A supported harness and shutdown check are required")
    install_lock = auto_update_policy.pi_install_lock if harness == "pi" else auto_update_policy.claude_install_lock
    preflight = (
        update_preflight.pi_user_agent_candidate if harness == "pi" else update_preflight.claude_user_agent_candidate
    )
    with auto_update_policy.registry_gate(registry, timeout=max(0, min(2, deadline - time.monotonic()))):
        if (
            shutdown_requested()
            or time.monotonic() + 15 >= deadline
            or auto_update_policy.active_registry() != registry
            or auto_update_policy.active_account() != account
            or not auto_update_policy.policy_status(registry)["effective"]
        ):
            return {"status": "skipped", "reason": "Session closed, consent changed, or the install window expired."}
        try:
            current = [
                row
                for row in _entries(harness)
                if row["type"] == "agent"
                and row["scope"] == "user"
                and row["id"] == item["id"]
                and row["directory"] == item["directory"]
                and row["current_version"] == item["current_version"]
                and row["lock_digest"] == item["lock_digest"]
            ]
        except (CliError, OSError, ValueError, TypeError):
            return {"status": "skipped", "reason": "The installed state could not be checked; no installer started."}
        if len(current) != 1:
            return {"status": "skipped", "reason": "The managed agent changed during the check."}
        try:
            verified = installed_updates.compare(current, verify_releases=True)[0]
        except (CliError, OSError, ValueError, TypeError):
            return {"status": "skipped", "reason": "The approved release could not be checked; no installer started."}
        if verified.get("latest_version") != item["latest_version"]:
            return {"status": "skipped", "reason": "The approved target changed during the check."}
        argv, reason, root = _plan(verified)
        if reason or not argv or root is None:
            return {"status": "skipped", "reason": reason or "No applicable agent installer."}
        try:
            preflight(verified, registry=registry)
        except (update_preflight.PreflightSkipError, auto_update_policy.PolicyError) as error:
            return {"status": "skipped", "reason": str(error)}
        backup = install_recovery.path_for(marker)
        env = os.environ.copy()
        env.update(
            {
                "OBSERVAL_AUTO_UPDATE_RECOVERY_DIR": str(backup),
                "OBSERVAL_AUTO_UPDATE_INSTALL": "1",
                "OBSERVAL_AUTO_UPDATE_EXPECTED_VERSION": item["current_version"],
                "OBSERVAL_AUTO_UPDATE_NETWORK_CUTOFF": str(deadline - 15),
                "OBSERVAL_AUTO_UPDATE_SHUTDOWN_MARKER": str(marker),
            }
        )
        # Never kill a child once admitted: it may be inside a file write.
        try:
            completed = subprocess.run(
                argv,
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, RuntimeError, ValueError):
            return {"status": "failed", "reason": "The installer may have changed files; inspect them before retrying."}

        def restore_originals() -> bool:
            try:
                # The child is finished. Serialize with *manual* Pi pulls too;
                # never restore across an unrecognized edit or changed lock.
                with install_lock(registry, timeout=2):
                    if not install_recovery.restore_if_safe(backup):
                        return False
                    install_baseline.verified_files(
                        registry=registry,
                        harness=harness,
                        agent_id=item["id"],
                        scope="user",
                        root=item["directory"],
                        version=item["current_version"],
                        lock_digest=item["lock_digest"],
                    )
                    install_recovery.discard(backup)
                    return True
            except (auto_update_policy.GateBusyError, install_baseline.BaselineError, OSError, ValueError):
                return False  # Retain backup and pending notice for manual inspection.

        if completed.returncode != 0:
            # An installer that stopped before touching owned files is a skip,
            # not an unresolved partial write. Only the original baseline AND
            # original installed record together prove that conclusion.
            try:
                unchanged = [
                    row
                    for row in _entries(harness)
                    if _same_install(verified, row) and row.get("lock_digest") == item["lock_digest"]
                ]
                if len(unchanged) == 1:
                    install_baseline.verified_files(
                        registry=registry,
                        harness=harness,
                        agent_id=item["id"],
                        scope="user",
                        root=item["directory"],
                        version=item["current_version"],
                        lock_digest=item["lock_digest"],
                    )
                    if backup.exists():
                        install_recovery.discard(backup)
                    if unchanged[0].get("requested_version"):
                        return {
                            "status": "skipped",
                            "reason": "A manual version pin was set while the startup installer waited; it was preserved.",
                        }
                    return {
                        "status": "skipped",
                        "reason": "The installer stopped before changing managed files; update manually.",
                    }
            except (CliError, install_baseline.BaselineError, OSError, ValueError, KeyError, TypeError):
                pass
            if restore_originals():
                return {
                    "status": "skipped",
                    "reason": "The installer failed; verified original managed files were restored. Update manually.",
                }
        if completed.returncode == 0 and _verify(verified) and install_recovery.planned_matches(backup):
            installed = [
                row
                for row in _entries(harness)
                if row["type"] == "agent"
                and row["id"] == item["id"]
                and row["scope"] == "user"
                and row["directory"] == item["directory"]
                and row["current_version"] == verified["latest_version"]
            ]
            if len(installed) == 1:
                try:
                    install_baseline.verified_files(
                        registry=registry,
                        harness=harness,
                        agent_id=item["id"],
                        scope="user",
                        root=item["directory"],
                        version=verified["latest_version"],
                        lock_digest=installed[0]["lock_digest"],
                    )
                except (install_baseline.BaselineError, OSError, KeyError, TypeError):
                    pass
                else:
                    try:
                        install_recovery.discard(backup)
                    except OSError:
                        pass  # A leftover private backup does not negate verified success.
                    return {
                        "status": "updated",
                        "reason": (
                            "Saved Pi profile updated; re-select it with `/agent` and reload."
                            if harness == "pi"
                            else "Saved Claude Code profile updated; start a new session and select this agent to load it."
                        ),
                    }
        if completed.returncode == 0 and restore_originals():
            return {
                "status": "skipped",
                "reason": "The installed result could not be verified; verified original files were restored. Update manually.",
            }
        return {
            "status": "failed",
            "reason": "The installer failed or its file/lock result could not be verified; inspect files and the private backup.",
        }


def apply_startup_pi_mcp(
    item: dict,
    *,
    registry: str,
    account: str,
    deadline: float,
    shutdown_requested: object,
    marker: Path,
) -> dict:
    """Use the normal managed Pi MCP installer with a whole-file recovery plan."""
    import time

    from observal_cli import automatic_mcp_plan as mcp

    if not callable(shutdown_requested):
        raise ValueError("A shutdown check is required")
    with auto_update_policy.registry_gate(registry, timeout=max(0, min(2, deadline - time.monotonic()))):
        if (
            shutdown_requested()
            or time.monotonic() + 15 >= deadline
            or auto_update_policy.active_registry() != registry
            or auto_update_policy.active_account() != account
            or not auto_update_policy.policy_status(registry)["effective"]
        ):
            return {"status": "skipped", "reason": "Session closed, consent changed, or the install window expired."}
        try:
            rows = mcp.entries(registry)
            current = [row for row in _entries("pi") if _same_install(item, row)]
            if (
                item.get("type") != "mcp"
                or item.get("scope") != "user"
                or len(current) != 1
                or current[0].get("pin_known") is not True
                or current[0].get("requested_version")
                or any(current[0].get(key) != item.get(key) for key in ("digest", "version_id", "local_name"))
            ):
                raise mcp.McpPlanError("The managed MCP pin or identity changed.")
            old_file, old_config = mcp.verified(registry, rows)
            if current[0]["local_name"] not in old_config["mcpServers"]:
                raise mcp.McpPlanError("The MCP reference has no owned entry.")
            verified = installed_updates.compare(current, verify_releases=True)[0]
            if (
                verified.get("status") != "outdated"
                or not verified.get("release_verified")
                or verified.get("latest_version") != item.get("latest_version")
            ):
                raise mcp.McpPlanError("The target is not an accessible approved release.")
        except (CliError, OSError, ValueError, TypeError, KeyError) as error:
            return {"status": "skipped", "reason": str(error) or "Inspect the managed MCP manually."}
        backup = install_recovery.path_for(marker)
        env = os.environ.copy()
        env.update(
            {
                "OBSERVAL_AUTO_UPDATE_RECOVERY_DIR": str(backup),
                "OBSERVAL_AUTO_UPDATE_INSTALL": "1",
                "OBSERVAL_UPDATE_EXACT_TARGET": "1",
                "OBSERVAL_AUTO_UPDATE_EXPECTED_VERSION": item["current_version"],
                "OBSERVAL_AUTO_UPDATE_NETWORK_CUTOFF": str(deadline - 15),
                "OBSERVAL_AUTO_UPDATE_SHUTDOWN_MARKER": str(marker),
            }
        )
        argv = [
            sys.executable,
            "-m",
            "observal_cli",
            "registry",
            "mcp",
            "install",
            item["id"],
            "--harness",
            "pi",
            "--managed",
            "--version",
            item["latest_version"],
            "--no-prompt",
            "--output",
            "json",
        ]
        try:
            completed = subprocess.run(
                argv,
                cwd=Path.cwd(),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, RuntimeError, ValueError):
            return {"status": "failed", "reason": "The installer outcome is uncertain; inspect managed files."}

        def originals() -> bool:
            try:
                if mcp.digest(mcp.entries(registry)) != mcp.digest(rows):
                    return False
                mcp.verified(registry, rows)
                return True
            except (OSError, ValueError, TypeError, KeyError):
                return False

        def restore() -> bool:
            try:
                with auto_update_policy.pi_install_lock(registry, timeout=2):
                    if not install_recovery.restore_if_safe(backup) or not originals():
                        return False
                    install_recovery.discard(backup)
                    return True
            except (auto_update_policy.GateBusyError, OSError, ValueError, TypeError, KeyError):
                return False

        if completed.returncode != 0:
            if originals():
                if backup.exists():
                    install_recovery.discard(backup)
                return {"status": "skipped", "reason": "Managed MCP installer stopped without changing owned files."}
            if restore():
                return {"status": "skipped", "reason": "Verified original MCP config restored; update manually."}
        if completed.returncode == 0:
            try:
                changed = mcp.entries(registry)
                updated = [row for row in changed if row.get("id") == item["id"]]
                file, _ = mcp.verified(registry, changed)
                if (
                    len(updated) == 1
                    and updated[0].get("version") == item["latest_version"]
                    and updated[0].get("requested_version") is None
                    and updated[0].get("pin_known") is True
                    and file == old_file
                    and install_recovery.planned_matches(backup)
                ):
                    install_recovery.discard(backup)
                    return {"status": "updated", "reason": "Saved Pi MCP reference updated; reload Pi to use it."}
            except (OSError, ValueError, TypeError, KeyError):
                pass
            if restore():
                return {"status": "skipped", "reason": "Unverified result; original MCP config restored."}
        return {
            "status": "failed",
            "reason": "MCP outcome is uncertain; inspect the private backup and managed config.",
        }


def apply_startup_claude_mcp(
    item: dict,
    *,
    registry: str,
    account: str,
    deadline: float,
    shutdown_requested: object,
    marker: Path,
    harness: str = "claude-code",
) -> dict:
    """Run the normal managed Claude MCP installer; verify by entry, restore by re-adding."""
    import time

    from observal_cli import automatic_claude_mcp as claude_mcp

    if not callable(shutdown_requested) or harness != "claude-code":
        raise ValueError("A shutdown check is required")
    with auto_update_policy.registry_gate(registry, timeout=max(0, min(2, deadline - time.monotonic()))):
        if (
            shutdown_requested()
            or time.monotonic() + 15 >= deadline
            or auto_update_policy.active_registry() != registry
            or auto_update_policy.active_account() != account
            or not auto_update_policy.policy_status(registry)["effective"]
        ):
            return {"status": "skipped", "reason": "Session closed, consent changed, or the install window expired."}
        try:
            current = [row for row in _entries("claude-code") if _same_install(item, row)]
            record = claude_mcp.load_record(registry, item["id"])
            if (
                item.get("type") != "mcp"
                or item.get("scope") != "user"
                or len(current) != 1
                or current[0].get("pin_known") is not True
                or current[0].get("requested_version")
                or any(current[0].get(key) != item.get(key) for key in ("digest", "version_id", "local_name"))
                or record is None
                or record["name"] != current[0].get("local_name")
            ):
                raise claude_mcp.ClaudeMcpError("The managed MCP pin, identity or ownership record changed.")
            if claude_mcp.read_entry(record["name"]) != record["entry"]:
                raise claude_mcp.ClaudeMcpError(
                    "The Claude Code MCP entry was edited or removed since Observal installed it."
                )
            verified = installed_updates.compare(current, verify_releases=True)[0]
            if (
                verified.get("status") != "outdated"
                or not verified.get("release_verified")
                or verified.get("latest_version") != item.get("latest_version")
            ):
                raise claude_mcp.ClaudeMcpError("The target is not an accessible approved release.")
        except (CliError, OSError, ValueError, TypeError, KeyError) as error:
            return {"status": "skipped", "reason": str(error) or "Inspect the managed MCP manually."}
        backup = install_recovery.path_for(marker)
        env = os.environ.copy()
        env.update(
            {
                "OBSERVAL_AUTO_UPDATE_RECOVERY_DIR": str(backup),
                "OBSERVAL_AUTO_UPDATE_INSTALL": "1",
                "OBSERVAL_UPDATE_EXACT_TARGET": "1",
                "OBSERVAL_AUTO_UPDATE_EXPECTED_VERSION": item["current_version"],
                "OBSERVAL_AUTO_UPDATE_NETWORK_CUTOFF": str(deadline - 15),
                "OBSERVAL_AUTO_UPDATE_SHUTDOWN_MARKER": str(marker),
            }
        )
        argv = [
            sys.executable,
            "-m",
            "observal_cli",
            "registry",
            "mcp",
            "install",
            item["id"],
            "--harness",
            "claude-code",
            "--managed",
            "--version",
            item["latest_version"],
            "--no-prompt",
            "--output",
            "json",
        ]
        try:
            completed = subprocess.run(
                argv,
                cwd=Path.cwd(),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, RuntimeError, ValueError):
            return {"status": "failed", "reason": "The installer outcome is uncertain; inspect the MCP entry."}

        def restore() -> bool:
            try:
                with auto_update_policy.claude_install_lock(registry, timeout=2):
                    if not claude_mcp.restore_if_safe(backup):
                        return False
                    install_recovery.discard(backup)
                    return True
            except (auto_update_policy.GateBusyError, OSError, ValueError, TypeError, KeyError):
                return False

        state = claude_mcp.recovery_state(backup)
        if completed.returncode == 0 and state is not None:
            # Success is the saved plan, exactly: the live entry is the planned one, and our ownership
            # record and installed-lock row are the planned values (not merely "a" newer version).
            record = state[1]
            try:
                if (record["old"] == record["new"] or state[0] == "new") and claude_mcp.bookkeeping_is_planned(record):
                    install_recovery.discard(backup)
                    return {"status": "updated", "reason": "Saved Claude Code MCP entry updated; start a new session."}
            except (OSError, ValueError, TypeError, KeyError):
                pass
        if state is None and completed.returncode != 0 and not backup.exists():
            return {"status": "skipped", "reason": "The installer refused the update without changing the MCP entry."}
        if state is not None and completed.returncode != 0 and claude_mcp.fully_original(backup):
            install_recovery.discard(backup)
            return {"status": "skipped", "reason": "The installer stopped without changing the MCP entry."}
        if state is not None and state[0] != "foreign" and restore():
            return {"status": "skipped", "reason": "Verified original MCP entry restored; update manually."}
        return {
            "status": "failed",
            "reason": "MCP outcome is uncertain or the entry was edited; inspect it and the private backup.",
        }


def apply_startup_pi_skill(
    item: dict,
    *,
    registry: str,
    account: str,
    deadline: float,
    shutdown_requested: object,
    marker: Path,
    harness: str = "pi",
) -> dict:
    """Launch the guarded normal skill installer; verify disk and metadata after it stops."""
    import time

    from observal_cli import automatic_skill_plan

    if not callable(shutdown_requested) or harness not in {"pi", "claude-code"}:
        raise ValueError("A supported harness and shutdown check are required")
    install_lock = auto_update_policy.pi_install_lock if harness == "pi" else auto_update_policy.claude_install_lock
    with auto_update_policy.registry_gate(registry, timeout=max(0, min(2, deadline - time.monotonic()))):
        if (
            shutdown_requested()
            or time.monotonic() + 15 >= deadline
            or auto_update_policy.active_registry() != registry
            or auto_update_policy.active_account() != account
            or not auto_update_policy.policy_status(registry)["effective"]
        ):
            return {"status": "skipped", "reason": "Session closed, consent changed, or the install window expired."}
        try:
            current = [row for row in _entries(harness) if _same_install(item, row)]
            if len(current) != 1 or any(
                current[0].get(key) != item.get(key)
                for key in ("digest", "version_id", "local_name", "requested_version", "pin_known")
            ):
                raise automatic_skill_plan.SkillPlanError("The installed skill record changed.")
            verified = installed_updates.compare(current, verify_releases=True)[0]
            if verified.get("latest_version") != item["latest_version"]:
                raise automatic_skill_plan.SkillPlanError("The approved target changed.")
            file = automatic_skill_plan.verified_path(verified, registry=registry)
            argv, reason, root = _plan(verified)
            if reason or not argv or root is None or not verified.get("release_verified"):
                raise automatic_skill_plan.SkillPlanError(reason or "The release could not be verified.")
        except (CliError, OSError, ValueError, TypeError, KeyError) as error:
            return {"status": "skipped", "reason": str(error) or "The skill needs a manual update."}
        backup = install_recovery.path_for(marker)
        env = os.environ.copy()
        env.update(
            {
                "OBSERVAL_AUTO_UPDATE_RECOVERY_DIR": str(backup),
                "OBSERVAL_AUTO_UPDATE_INSTALL": "1",
                "OBSERVAL_UPDATE_EXACT_TARGET": "1",
                "OBSERVAL_AUTO_UPDATE_EXPECTED_VERSION": item["current_version"],
                "OBSERVAL_AUTO_UPDATE_NETWORK_CUTOFF": str(deadline - 15),
                "OBSERVAL_AUTO_UPDATE_SHUTDOWN_MARKER": str(marker),
            }
        )
        try:
            # Never kill a child that may be inside a file write.
            completed = subprocess.run(
                argv,
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, RuntimeError, ValueError):
            return {"status": "failed", "reason": "The installer outcome is uncertain; inspect managed files."}

        def old_files_verified() -> bool:
            rows = [row for row in _entries(harness) if _same_install(item, row)]
            if len(rows) != 1 or any(
                rows[0].get(key) != item.get(key)
                for key in ("digest", "version_id", "local_name", "requested_version", "pin_known")
            ):
                return False
            automatic_skill_plan.verified_path(item, registry=registry)
            return True

        def restore_originals() -> bool:
            try:
                with install_lock(registry, timeout=2):
                    if not install_recovery.restore_if_safe(backup) or not old_files_verified():
                        return False
                    install_recovery.discard(backup)
                    return True
            except (auto_update_policy.GateBusyError, OSError, ValueError, TypeError, KeyError):
                return False

        if completed.returncode != 0:
            try:
                if old_files_verified():
                    if backup.exists():
                        install_recovery.discard(backup)
                    return {
                        "status": "skipped",
                        "reason": "Installer stopped without changing the skill; update manually.",
                    }
            except (OSError, ValueError, TypeError, KeyError):
                pass
            if restore_originals():
                return {"status": "skipped", "reason": "Verified original skill restored; update manually."}
        if completed.returncode == 0:
            try:
                installed = [
                    row
                    for row in _entries(harness)
                    if _same_install({**item, "current_version": verified["latest_version"]}, row)
                ]
                if (
                    len(installed) == 1
                    and installed[0].get("requested_version") is None
                    and installed[0].get("pin_known") is True
                    and installed[0].get("local_name") == item.get("local_name")
                    and automatic_skill_plan.verified_path(installed[0], registry=registry) == file
                    and install_recovery.planned_matches(backup)
                ):
                    if backup.exists():
                        install_recovery.discard(backup)
                    host = "Pi" if harness == "pi" else "Claude Code"
                    return {
                        "status": "updated",
                        "reason": f"Saved {host} skill updated and verified; reload to use it.",
                    }
            except (OSError, ValueError, TypeError, KeyError):
                pass
            if restore_originals():
                return {
                    "status": "skipped",
                    "reason": "Unverified result; verified original skill restored. Update manually.",
                }
        return {
            "status": "failed",
            "reason": "The skill file or installed record could not be verified; inspect it and the private backup.",
        }
