# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Claude Code hook bridge for the shared gated startup worker.

SessionStart launches a detached worker; it never waits for registry I/O.
A later prompt or session displays sealed results through a user-facing
systemMessage, not model context. SessionEnd prevents new write admission.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

from observal_cli import auto_update_policy, config
from observal_cli import startup_update_check as check

STARTED_DIR = config.CONFIG_DIR / "claude-update-started"
MAX_AGE = 7 * 24 * 60 * 60


def notice_key(registry: str, account: str, session_id: str) -> str:
    return hashlib.sha256(f"claude-code\0{registry}\0{account}\0{session_id}".encode()).hexdigest()


def _safe_text(value: object, length: int = 120) -> str:
    return re.sub(r"\s+", " ", "".join(ch if ch.isprintable() else " " for ch in str(value or "")))[:length]


def _private_directory(directory: Path) -> bool:
    try:
        mode = directory.lstat().st_mode
        return stat.S_ISDIR(mode) and not mode & 0o077 and not directory.is_symlink()
    except OSError:
        return False


def _notices(registry: str, account: str, *, ack: list[Path] | None = None) -> list[str]:
    """Consume only sealed Claude Code results; leave Pi journals alone."""
    if not _private_directory(check.NOTICE_DIR):
        return []
    messages = []
    try:
        paths = sorted(check.NOTICE_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime)
    except OSError:
        return []
    for path in paths:
        if len(messages) >= 3:  # Never drop notices merely to fit hook output.
            break
        try:
            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_size > check.MAX_NOTICE_BYTES
                or time.time() - info.st_mtime > MAX_AGE
            ):
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(record, dict)
                or record.get("schema") != 1
                or record.get("harness") != "claude-code"
                or record.get("registry") != registry
                or record.get("account_id") != account
                or record.get("effective_in_current_session") not in {"unknown", "no"}
                or not isinstance(record.get("session_id"), str)
                or path.name != f"{notice_key(registry, account, record['session_id'])}.json"
                or not isinstance(record.get("items"), list)
                # A journaled record must say whether its outcome is final;
                # otherwise there is no seal to verify or acknowledge.
                or (record.get("journaled") is True and not isinstance(record.get("outcome_final"), bool))
            ):
                continue
            seal: Path | None = None
            if record.get("journaled") is True and record.get("outcome_final") is True:
                seal = path.with_suffix(".complete")
                seal_info = seal.lstat()
                if not stat.S_ISREG(seal_info.st_mode) or seal_info.st_mode & 0o077:
                    continue
                completed = json.loads(seal.read_text())
                if (
                    any(completed.get(key) != record.get(key) for key in ("registry", "account_id", "session_id"))
                    or completed.get("state") != "complete"
                ):
                    continue
            elif record.get("journaled") is True and record.get("outcome_final") is False:
                # Failure may have partially written files. The private backup
                # and pending journal must remain for manual inspection.
                messages.append(
                    "Observal Claude Code update outcome unresolved; inspect managed files and the private backup before retrying."
                )
                continue
            lines = []
            for item in record["items"][: check.MAX_ITEMS]:
                if not isinstance(item, dict) or item.get("status") not in {
                    "available",
                    "unverified",
                    "skipped",
                    "updated",
                    "failed",
                }:
                    continue
                status = item["status"]
                if status in {"updated", "failed"} and not (
                    record.get("journaled") is True and record.get("outcome_final") is True
                ):
                    continue
                label = (
                    "installed on disk (start a new session and select agent to load)"
                    if status == "updated"
                    else status
                )
                why = (
                    f" Reason: {_safe_text(item.get('reason'), 220)}"
                    if status in {"skipped", "failed"} and item.get("reason")
                    else ""
                )
                lines.append(
                    f"{_safe_text(item.get('name'))}: {_safe_text(item.get('current_version'), 40)} → "
                    f"{_safe_text(item.get('latest_version'), 40)} ({label}).{why}"
                )
            if lines:
                messages.append(
                    "Observal Claude Code update result; saved files alone do not confirm which profile this session loaded. "
                    + " ".join(lines)
                )
            elif record.get("warning"):
                messages.append("Observal update check could not complete; run `observal outdated` later.")
            # The real hook acknowledges only after flushing the user-facing
            # systemMessage. Unit callers without an ack list consume directly.
            paths = [path, seal] if seal is not None else [path]
            if ack is None:
                for delivered in paths:
                    delivered.unlink(missing_ok=True)
            else:
                ack.extend(paths)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            continue
    return messages


HOOKS_NOTICE = config.CONFIG_DIR / "claude-hooks-notice.json"


def _hooks_notice(ack: list[Path] | None) -> list[str]:
    """One sealed message about Observal's own settings hooks, delivered once."""
    try:
        info = HOOKS_NOTICE.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 4096:
            return []
        record = json.loads(HOOKS_NOTICE.read_text(encoding="utf-8"))
        if (
            not isinstance(record, dict)
            or record.get("schema") != 1
            or record.get("status") not in {"updated", "manual"}
            or time.time() - info.st_mtime > MAX_AGE
        ):
            return []
    except (OSError, ValueError, UnicodeError):
        return []
    if ack is None:
        HOOKS_NOTICE.unlink(missing_ok=True)
    else:
        ack.append(HOOKS_NOTICE)
    if record["status"] == "updated":
        return ["Observal updated its Claude Code hooks to the current version. Start a new session to load them."]
    return [
        "Observal could not update its Claude Code hooks automatically. "
        f"Reason: {_safe_text(record.get('reason'), 220)}"
    ]


def _launch(cwd: str, session_id: str, key: str) -> None:
    STARTED_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not _private_directory(STARTED_DIR):
        return
    marker = STARTED_DIR / key
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w") as handle:
        handle.write("startup-worker\n")
    try:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "observal_cli",
                "_startup-apply-claude",
                "--cwd",
                cwd,
                "--session-id",
                session_id,
                "--notice-key",
                key,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        marker.unlink(missing_ok=True)
    # Retain a bounded number of dedupe records, not a permanent session log.
    try:
        for old in sorted(STARTED_DIR.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True)[100:]:
            if old.is_file() and not old.is_symlink():
                old.unlink(missing_ok=True)
    except OSError:
        pass


def _mark_shutdown(key: str) -> None:
    from observal_cli import startup_update_apply

    directory = startup_update_apply.SHUTDOWN_DIR
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not _private_directory(directory):
        return
    marker = startup_update_apply.shutdown_marker(key)
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w") as handle:
        handle.write("ended\n")
        handle.flush()
        os.fsync(handle.fileno())
    check._sync_directory(directory)


def handle(event: object, *, ack: list[Path] | None = None) -> dict | None:
    if not isinstance(event, dict) or event.get("hook_event_name") not in {
        "SessionStart",
        "UserPromptSubmit",
        "SessionEnd",
    }:
        return None
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not 0 < len(session_id) <= 256:
        return None
    # Do not inherit another account's consent through env credentials, and
    # never run a local worker inside a remote/cloud Claude session.
    if os.environ.get("CLAUDE_CODE_REMOTE") == "true" or any(
        os.environ.get(name) or os.environ.get(f"{name}_FILE")
        for name in ("OBSERVAL_ACCESS_TOKEN", "OBSERVAL_API_KEY", "OBSERVAL_TOKEN", "OBSERVAL_SERVER_URL")
    ):
        return None
    registry = auto_update_policy.active_registry()
    account = auto_update_policy.active_account()
    key = notice_key(registry, account, session_id)
    if event["hook_event_name"] == "SessionEnd":
        _mark_shutdown(key)
        return None
    messages = _notices(registry, account, ack=ack) + _hooks_notice(ack)
    if event["hook_event_name"] == "SessionStart":
        cwd = event.get("cwd")
        if isinstance(cwd, str) and cwd and Path(cwd).is_dir():
            _launch(str(Path(cwd).resolve()), session_id, key)
    return {"systemMessage": "\n".join(messages)[:6000]} if messages else None


def main() -> None:
    try:
        raw = sys.stdin.read(65537)
        if len(raw) > 65536:
            return
        ack: list[Path] = []
        output = handle(json.loads(raw), ack=ack)
        if output:
            print(json.dumps(output), flush=True)
        for delivered in ack:
            delivered.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError, RuntimeError):
        pass  # A notice failure must not break session start or block a prompt.


if __name__ == "__main__":
    main()
