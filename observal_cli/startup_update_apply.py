# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Guarded Pi and Claude Code startup apply workers.

A registry/account worker lock covers comparison through durable outcome
sealing. The 90-second budget limits *admission*, not the duration of an
existing installer subprocess: killing it mid-write may leave partial files.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
from typing import TYPE_CHECKING

from observal_cli import auto_update_policy, client, cmd_update, install_recovery, installed_updates
from observal_cli import startup_update_check as check
from observal_cli.errors import CliError

if TYPE_CHECKING:
    from pathlib import Path

SHUTDOWN_DIR = check.config.CONFIG_DIR / "update-shutdown"
APPLY_SECONDS = 90
RECOVERY_RESERVE_SECONDS = 15


def expected_notice_key(registry: str, account: str, session_id: str) -> str:
    """Same UTF-8, NUL-separated identity used by the Pi extension."""
    return hashlib.sha256(f"{registry}\0{account}\0{session_id}".encode()).hexdigest()


def shutdown_marker(notice_key: str) -> Path:
    if len(notice_key) != 64 or any(ch not in "0123456789abcdef" for ch in notice_key):
        raise ValueError("Invalid startup notice key")
    return SHUTDOWN_DIR / f"{notice_key}.json"


def _reserve_pending(path: Path, payload: dict) -> None:
    """Create a durable, non-replaceable write-ahead record before any install.

    Linking a fully fsynced temp file avoids ever exposing a half-written
    record, and refuses to overwrite an unresolved record from an earlier run.
    """
    body = json.dumps(payload, ensure_ascii=False).encode()
    if len(body) > check.MAX_NOTICE_BYTES:
        raise ValueError("Pending update record exceeds the notice limit")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory = path.parent.lstat()
    if not stat.S_ISDIR(directory.st_mode) or directory.st_mode & 0o077 or not directory.st_mode & stat.S_IWUSR:
        raise OSError("Update notice directory is not private and writable")
    fd, temporary = tempfile.mkstemp(prefix=".update-pending-", dir=path.parent)
    linked = False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)  # Atomic no-clobber reservation.
        linked = True
        check._sync_directory(path.parent)
    except BaseException:
        if linked:
            # A failed directory sync cannot certify this reservation or
            # completion seal. Best-effort remove the newly linked entry.
            try:
                path.unlink()
                check._sync_directory(path.parent)
            except OSError:
                pass
        raise
    finally:
        os.unlink(temporary)


def _unresolved_pending(registry: str, account: str) -> bool:
    """Do not accumulate uncertain installs for the same local identity."""
    for path in check.NOTICE_DIR.glob("*.pending"):
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > check.MAX_NOTICE_BYTES:
                return True
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or record.get("state") != "pending":
                return True
            if record.get("registry") == registry and record.get("account_id") == account:
                return True
        except (OSError, ValueError, UnicodeError):
            return True
    return False


def _pending_payload(
    registry: str, account: str, session_id: str, msg: dict, completed: list[dict], *, notice_key: str | None = None
) -> dict:
    return {
        "schema": 1,
        "state": "pending",
        "registry": registry,
        "account_id": account,
        "session_id": session_id,
        "checked_at": int(time.time()),
        "item": {key: msg.get(key) for key in ("name", "current_version", "latest_version")},
        "backup_dir": str(
            install_recovery.path_for(shutdown_marker(notice_key or expected_notice_key(registry, account, session_id)))
        ),
        "completed": [{key: item.get(key) for key in ("name", "status")} for item in completed],
    }


def _ended(key: str) -> bool:
    """Unreadable marker storage is not permission to start a mutation."""
    marker = shutdown_marker(key)
    try:
        return marker.exists() or marker.is_symlink()
    except OSError:
        return True


def apply_pi(cwd: str, session_id: str, notice_key: str) -> None:
    """Bounded admission and durable result; the CLI caller must not kill this worker."""
    shutdown_marker(notice_key)  # Reject malformed keys before accessing local files.
    if not session_id or len(session_id) > 256:
        raise ValueError("Invalid session identifier")
    registry = auto_update_policy.active_registry()
    account = auto_update_policy.active_account()
    if notice_key != expected_notice_key(registry, account, session_id):
        raise ValueError("Startup notice key does not match the authenticated session")
    deadline = time.monotonic() + APPLY_SECONDS
    # Hold a separate cross-process gate until the final seal or failure. The
    # inner installer takes the registry gate; neither freeze nor manual pulls
    # ever wait for this outer worker gate while holding their own locks.
    with (
        auto_update_policy.apply_worker_gate(registry, account, timeout=max(0, deadline - time.monotonic())),
        client.bounded_requests(deadline - RECOVERY_RESERVE_SECONDS),
    ):
        _apply_serialized(cwd, session_id, notice_key, registry=registry, account=account, deadline=deadline)


def apply_claude(cwd: str, session_id: str, notice_key: str) -> None:
    """Use the same journal and bounded admission; only the owned-file shape differs."""
    from observal_cli.hooks.claude_updates import notice_key as expected_key

    shutdown_marker(notice_key)
    if not session_id or len(session_id) > 256:
        raise ValueError("Invalid session identifier")
    registry = auto_update_policy.active_registry()
    account = auto_update_policy.active_account()
    if notice_key != expected_key(registry, account, session_id):
        raise ValueError("Claude Code notice key does not match the authenticated session")
    deadline = time.monotonic() + APPLY_SECONDS
    with (
        auto_update_policy.apply_worker_gate(registry, account, timeout=max(0, deadline - time.monotonic())),
        client.bounded_requests(deadline - RECOVERY_RESERVE_SECONDS),
    ):
        _apply_serialized(
            cwd, session_id, notice_key, registry=registry, account=account, deadline=deadline, harness="claude-code"
        )
    _refresh_claude_hooks(registry)


def _refresh_claude_hooks(registry: str) -> None:
    """Keep Observal's own settings hooks current; consent-gated, reported once, never silent.

    The settings write is atomic and abandoned if Claude changed the file, so a
    failure leaves the file as it was. An unexpected error is reported as a
    manual step instead of being swallowed.
    """
    import hashlib
    import json
    import os

    from observal_cli import settings_reconciler
    from observal_cli.harness_specs.claude_code_hooks_spec import HOOKS_SPEC_VERSION
    from observal_cli.hooks import claude_updates

    try:
        with auto_update_policy.registry_gate(registry, timeout=2):
            if not auto_update_policy.policy_status(registry)["effective"]:
                return
            try:
                status, reason = settings_reconciler.refresh_unedited()
            except Exception:
                status, reason = (
                    "manual",
                    "Updating Observal's Claude Code hooks failed unexpectedly; run "
                    "`observal doctor patch --harness claude-code`.",
                )
        if status == "current":
            return
        # Say each outcome once per spec version, not at every session start.
        seen = claude_updates.HOOKS_NOTICE.with_suffix(".seen")
        digest = hashlib.sha256(f"{status}\0{reason}\0{HOOKS_SPEC_VERSION}".encode()).hexdigest()
        if seen.exists() and seen.read_text() == digest:
            return
        target = claude_updates.HOOKS_NOTICE
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = target.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema": 1, "status": status, "reason": reason[:200] if status == "manual" else ""}, handle)
        os.replace(temporary, target)
        seen.write_text(digest)  # Only after the message is safely queued.
    except (auto_update_policy.GateBusyError, OSError, ValueError):
        pass  # Could not reach the notice store; the next session tries again.


def _apply_serialized(
    cwd: str, session_id: str, notice_key: str, *, registry: str, account: str, deadline: float, harness: str = "pi"
) -> None:
    marker = shutdown_marker(notice_key)
    pending_path = check.NOTICE_DIR / f"{notice_key}.pending"
    complete_path = check.NOTICE_DIR / f"{notice_key}.complete"
    journal_active = False
    unresolved_pending = False
    uncertain = False
    payload: dict = {
        "schema": 1,
        "registry": registry,
        "account_id": account,
        "session_id": session_id,
        "harness": harness,
        "checked_at": int(time.time()),
        "items": [],
        "warning": None,
        "effective_in_current_session": "no",
    }
    try:
        # Never silently treat an inaccessible marker directory as a live session.
        marker.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory_stat = marker.parent.lstat()
        if not stat.S_ISDIR(directory_stat.st_mode) or directory_stat.st_mode & 0o077:
            raise OSError("Shutdown marker storage is unsafe")
        # An earlier attempt with this identity may have changed files but
        # failed to record its outcome. Do not overwrite its evidence or retry.
        unresolved_pending = _unresolved_pending(registry, account) or complete_path.exists()
        if unresolved_pending:
            payload["warning"] = "An earlier installation outcome is unresolved; inspect managed files before retrying."
        installed = installed_updates.inventory_for_context(harness, cwd)
        if installed:
            policy = auto_update_policy.policy_status(registry)
            enabled = policy["effective"] and not policy.get("warning")
            # The 24-hour cache is for notice-only startups. An opted-in apply
            # must discover newly approved releases even if the previous Pi
            # session cached an up-to-date result before they were published.
            # The normal installer still re-fetches and verifies the exact
            # target under its lock before writing any managed files.
            findings = (
                installed_updates.compare(installed, verify_releases=True)
                if enabled
                else check._cached_or_compare(registry, account, cwd, installed)
            )
            if policy.get("warning"):
                payload["warning"] = "Auto-update policy is unreadable; automatic installs are disabled."
            for item in findings[: check.MAX_ITEMS]:
                msg = check._message(item, enabled=enabled)
                if msg is None:
                    continue
                if not enabled:
                    msg["reason"] = "Automatic updates are frozen or unavailable; run `observal unfreeze` to opt in."
                elif item.get("reason") is None:
                    msg["reason"] = "This item requires a manual update in the startup pilot."
                if (
                    enabled
                    and item.get("type") in {"agent", "skill", "mcp"}
                    and item.get("scope") == "user"
                    and item.get("release_verified")
                ):
                    current = [
                        entry
                        for entry in installed
                        if entry.get("id") == item.get("id")
                        and entry.get("type") == item.get("type")
                        and entry.get("scope") == "user"
                        and entry.get("directory") == item.get("directory")
                        and entry.get("current_version") == item.get("current_version")
                    ]
                    if len(current) != 1:
                        msg["status"] = "skipped"
                        msg["reason"] = "The installed item changed or is ambiguous; update manually."
                    elif unresolved_pending or uncertain:
                        msg["status"] = "skipped"
                        msg["reason"] = "An earlier update outcome is unresolved; inspect local managed files."
                    elif _ended(notice_key) or time.monotonic() + RECOVERY_RESERVE_SECONDS >= deadline:
                        msg["status"] = "skipped"
                        msg["reason"] = "Session closed or the install admission window expired; update manually."
                    else:
                        # Persist this exact candidate and prior outcomes before
                        # allowing an installer to mutate any owned bytes.
                        pending = _pending_payload(
                            registry, account, session_id, msg, payload["items"], notice_key=notice_key
                        )
                        try:
                            if journal_active:
                                check._write_json(pending_path, pending, check.MAX_NOTICE_BYTES)
                            else:
                                _reserve_pending(pending_path, pending)
                                journal_active = True
                        except (OSError, ValueError):
                            # A failed directory sync can leave a visible but
                            # not yet durable reservation. Never claim it
                            # resolves an older pending outcome.
                            if not journal_active and (pending_path.exists() or pending_path.is_symlink()):
                                unresolved_pending = True
                            msg["status"] = "skipped"
                            msg["reason"] = "Cannot persist an update outcome; no installation was started."
                            payload["warning"] = (
                                "Update notice storage is unavailable; automatic installs are disabled."
                            )
                            payload["items"].append(msg)
                            break
                        try:
                            runner = {
                                "agent": cmd_update.apply_startup_pi_agent,
                                "skill": cmd_update.apply_startup_pi_skill,
                                "mcp": cmd_update.apply_startup_pi_mcp
                                if harness == "pi"
                                else cmd_update.apply_startup_claude_mcp,
                            }[item["type"]]
                            kwargs = {"harness": harness} if harness == "claude-code" else {}
                            reason_file = marker.with_suffix(".reason")
                            reason_file.unlink(missing_ok=True)
                            os.environ["OBSERVAL_AUTO_UPDATE_REASON_FILE"] = str(reason_file)
                            result = runner(
                                {**current[0], "latest_version": item["latest_version"]},
                                registry=registry,
                                account=account,
                                deadline=deadline,
                                shutdown_requested=lambda: _ended(notice_key),
                                marker=marker,
                                **kwargs,
                            )
                            msg["status"] = result["status"]
                            msg["reason"] = result["reason"]
                            specific = auto_update_policy.read_skip_reason(reason_file)
                            if specific and msg["status"] == "skipped":
                                # Keep the recovery fact: the cause alone must not hide that files were put back.
                                restored = (
                                    " The original was restored." if "restored" in result["reason"].lower() else ""
                                )
                                msg["reason"] = f"{specific}{restored} (update manually)"
                            reason_file.unlink(missing_ok=True)
                            if msg["status"] == "updated":
                                msg["reason"] = (
                                    "Saved Pi profile updated and verified; the current session and any copied "
                                    "active profile are unchanged. Re-select the agent with `/agent` and reload "
                                    "to activate it."
                                    if item["type"] == "agent" and harness == "pi"
                                    else "Saved Claude Code MCP entry updated and verified. Start a new session to use it."
                                    if harness == "claude-code" and item["type"] == "mcp"
                                    else "Saved Claude Code skill updated and verified. Start a new session to load it."
                                    if harness == "claude-code" and item["type"] == "skill"
                                    else "Saved Pi skill updated and verified; reload Pi to use the new version."
                                    if harness == "pi" and item["type"] == "skill"
                                    else "Saved Pi MCP reference updated and verified; reload Pi to use the new version."
                                    if harness == "pi"
                                    else "Saved Claude Code profile updated and verified. Start a new session and select the agent to load it."
                                )
                                msg["manual_command"] = None
                            elif msg["status"] == "failed":
                                # A normal installer is not transactional. Its
                                # failed child may have written a subset of files;
                                # retain the pending record for manual review.
                                uncertain = True
                        except auto_update_policy.GateBusyError:
                            msg["status"] = "skipped"
                            msg["reason"] = (
                                "Another install or policy change holds the update gate; retry next startup."
                            )
                        except Exception:
                            from loguru import logger as optic

                            optic.exception("Startup update worker failed before confirming the installer outcome")
                            uncertain = True
                            msg["status"] = "failed"
                            msg["reason"] = "Installation outcome is uncertain; inspect managed files before retrying."
                payload["items"].append(msg)
    except (CliError, OSError, ValueError, TypeError):
        uncertain = uncertain or journal_active
        payload["warning"] = (
            "Update worker could not complete; inspect managed installs and run `observal outdated`."
            if journal_active or unresolved_pending
            else "automatic update skipped: the registry check could not complete; run `observal outdated` later."
        )
    finally:
        # Even if a mutation succeeded before an unexpected exception, never
        # forge a success: installed-state and file-baseline verification decide.
        # Only this worker's completed result can resolve its own journal.
        payload["outcome_final"] = not unresolved_pending and not uncertain
        payload["journaled"] = journal_active
        notice = check.NOTICE_DIR / f"{notice_key}.json"
        # Never replace a previous unresolved outcome for this same session.
        if journal_active or not (pending_path.exists() or complete_path.exists()):
            try:
                check._write_json(notice, payload, check.MAX_NOTICE_BYTES)
            except ValueError:
                # Limit UI notices, but never drop a failed/uncertain outcome.
                compact = {
                    **payload,
                    "warning": "Update details exceeded the notice limit; inspect local managed files.",
                }
                compact["items"] = [
                    {
                        "name": str(item.get("name") or "item")[:100],
                        "type": item.get("type"),
                        "scope": item.get("scope"),
                        "status": item.get("status"),
                        "current_version": item.get("current_version"),
                        "latest_version": item.get("latest_version"),
                        "reason": str(item.get("reason") or "")[:120],
                    }
                    for item in payload["items"][: check.MAX_ITEMS]
                ]
                try:
                    check._write_json(notice, compact, check.MAX_NOTICE_BYTES)
                except ValueError:
                    compact["items"] = [
                        {"status": item["status"], "name": item["name"]} for item in compact["items"][:5]
                    ]
                    check._write_json(notice, compact, check.MAX_NOTICE_BYTES)
            if journal_active and not uncertain:
                # A durable completion seal proves the final notice reached disk.
                # Without it, an os.replace followed by a failed directory fsync
                # must not let the Pi bridge erase the pending record.
                _reserve_pending(
                    complete_path,
                    {
                        "schema": 1,
                        "state": "complete",
                        "registry": registry,
                        "account_id": account,
                        "session_id": session_id,
                    },
                )
                pending_path.unlink(missing_ok=True)
                check._sync_directory(check.NOTICE_DIR)
            check._prune_directory(check.NOTICE_DIR, check.MAX_OUTSTANDING)
