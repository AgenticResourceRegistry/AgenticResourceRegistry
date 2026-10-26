# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Generic Python harness hook entry point for durable session delivery."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as optic

from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.sessions.base import (
    drain_outbox,
    drain_session_source,
    load_config,
    log_error,
    read_cursor_state,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_FINAL_DELAY_SECONDS = 2.0
_STABLE_INTERVAL_SECONDS = 0.25
_STABLE_CHECKS = 2
_RECOVERY_MIN_AGE_SECONDS = 120
_OUTBOX_LOCK_WAIT_SECONDS = 5.0
_OUTBOX_LOCK_RETRY_SECONDS = 0.1


def main(home: Path | None = None, harness: str = "claude-code") -> None:
    """Read one hook payload from stdin without ever breaking the harness."""
    try:
        raw = sys.stdin.read()
        event = json.loads(raw)
        if isinstance(event, dict):
            _run_hook(event, harness=harness, home=home)
    except Exception as exc:
        optic.error("session push crashed (swallowed to protect harness): {}", exc)


def _run_hook(event: dict, *, harness: str, home: Path | None = None) -> None:
    ensure_loaded()
    adapter = get_adapter(harness)
    source = adapter.resolve_session_source(event, home=home)
    if source is None:
        optic.debug("{} hook did not resolve a session source", harness)
        return
    config = load_config(home=home)
    if config is None:
        optic.warning("no Observal config found - session source remains local")
        return
    config["_hook_delivery"] = True

    hook_event = str(event.get("hook_event_name") or event.get("hookEventName") or event.get("event") or "")
    is_final = adapter.is_session_final(event)
    deferred = adapter.defer_session_delivery()
    # Scope is decided per source. Related transcripts are handled separately
    # so delegated work is not lost when it lives outside the parent JSONL.
    delivered = True
    if adapter.should_capture_session(source, home=home):
        delivered = drain_session_source(
            source,
            config,
            hook_event=hook_event,
            final=False,
            extra_fields=adapter.session_extra_fields(source, event, is_final, home=home),
            extra_records=adapter.session_extra_records(source, event, is_final, home=home),
            spool_only=deferred,
            home=home,
        )
    else:
        optic.trace("{} session {} is out of scope; not capturing", harness, source.session_id)
    for related in adapter.related_session_sources(source, home=home):
        if not adapter.should_capture_session(related, home=home):
            continue
        delivered = (
            drain_session_source(
                related,
                config,
                hook_event=hook_event,
                final=False,
                extra_fields=adapter.session_extra_fields(related, event, is_final, home=home),
                extra_records=adapter.session_extra_records(related, event, is_final, home=home),
                spool_only=deferred,
                home=home,
            )
            and delivered
        )
    if not delivered:
        log_error(f"session_push: durable records pending for {harness} session {source.session_id}", home=home)
    if deferred or not delivered:
        _spawn_worker("--drain-outbox", harness=harness)

    if is_final:
        _spawn_worker(
            "--finalize-session",
            source.session_id,
            "--cwd",
            source.cwd,
            harness=harness,
        )
    else:
        _spawn_worker("--recover", "--exclude-session", source.session_id, harness=harness)


def _spawn_worker(*args: str, harness: str) -> None:
    try:
        from observal_cli.shared.launcher import module_subprocess

        argv, env = module_subprocess("observal_cli.hooks.session_push", "--harness", harness, *args)
        env.update(get_adapter(harness).session_worker_environment())
        subprocess.Popen(
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        optic.trace("could not spawn {} session worker: {}", harness, exc)


def _wait_until_stable(path: Path) -> None:
    """Wait briefly for post-hook records to finish appending."""
    time.sleep(_FINAL_DELAY_SECONDS)
    stable = 0
    previous_size = -1
    while stable < _STABLE_CHECKS:
        try:
            size = path.stat().st_size
        except OSError:
            return
        stable = stable + 1 if size == previous_size else 0
        previous_size = size
        if stable < _STABLE_CHECKS:
            time.sleep(_STABLE_INTERVAL_SECONDS)


def _finalize_session(harness: str, session_id: str, cwd: str, home: Path | None = None) -> None:
    ensure_loaded()
    adapter = get_adapter(harness)
    source = adapter.resolve_session_source(
        {"session_id": session_id, "cwd": cwd, "_observal_lookup_only": True},
        home=home,
    )
    config = load_config(home=home)
    if source is None or config is None or source.path is None:
        return
    event = {"session_id": session_id, "cwd": cwd, "hook_event_name": "Stop"}
    delivered = True
    if adapter.should_capture_session(source, home=home):
        _wait_until_stable(source.path)
        delivered = drain_session_source(
            source,
            config,
            hook_event="Stop",
            final=True,
            extra_fields=adapter.session_extra_fields(source, event, True, home=home),
            recover_from_server=True,
            home=home,
        )
    for related in adapter.related_session_sources(source, home=home):
        if not adapter.should_capture_session(related, home=home):
            continue
        if related.path is not None:
            _wait_until_stable(related.path)
        delivered = (
            drain_session_source(
                related,
                config,
                hook_event="Stop",
                final=True,
                extra_fields=adapter.session_extra_fields(related, event, True, home=home),
                extra_records=adapter.session_extra_records(related, event, True, home=home),
                recover_from_server=True,
                home=home,
            )
            and delivered
        )
    if not delivered:
        _spawn_worker("--drain-outbox", harness=harness)


def _drain_pending(home: Path | None = None) -> None:
    from observal_cli import telemetry_buffer

    config = load_config(home=home)
    if config is None:
        return
    scope = telemetry_buffer.delivery_scope(str(config.get("server_url") or ""), str(config.get("user_id") or ""))
    outbox_lock = f"outbox-{scope}"
    waiter_lock = f"outbox-waiter-{scope}"
    with ExitStack() as held_lock:
        with ExitStack() as attempt:
            acquired = attempt.enter_context(_recovery_worker_lock(outbox_lock, home))
            if acquired:
                held_lock.enter_context(attempt.pop_all())
        if not acquired:
            with _recovery_worker_lock(waiter_lock, home) as waiting:
                if not waiting:
                    return
                deadline = time.monotonic() + _OUTBOX_LOCK_WAIT_SECONDS
                while time.monotonic() < deadline:
                    time.sleep(min(_OUTBOX_LOCK_RETRY_SECONDS, deadline - time.monotonic()))
                    with ExitStack() as attempt:
                        acquired = attempt.enter_context(_recovery_worker_lock(outbox_lock, home))
                        if acquired:
                            held_lock.enter_context(attempt.pop_all())
                            break
            if not acquired:
                return
        failures = 0
        while True:
            remaining = telemetry_buffer.delivery_pause_remaining(
                str(config.get("server_url") or ""), str(config.get("user_id") or "")
            )
            if remaining > 0:
                time.sleep(remaining)
            if drain_outbox(config, home=home):
                return
            if telemetry_buffer.delivery_paused(str(config.get("server_url") or ""), str(config.get("user_id") or "")):
                continue  # Retry after every 429, including on a final hook.
            failures += 1
            if failures >= 3:
                return  # Offline or another drainer owns the queue; records stay durable.
            time.sleep(1.1)


@contextmanager
def _recovery_worker_lock(harness: str, home: Path | None = None) -> Iterator[bool]:
    """Only one backfill per harness runs at once; other harnesses still recover."""
    import re

    directory = (home or Path.home()) / ".observal"
    directory.mkdir(parents=True, exist_ok=True)
    safe_harness = re.sub(r"[^a-z0-9-]", "_", harness.lower())[:80]
    fd = os.open(
        directory / f"session_recovery_{safe_harness}.lock",
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                locked = True
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
            except OSError:
                pass
        else:
            import fcntl

            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError:
                pass
        yield locked
    finally:
        if locked:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _recover_sessions(harness: str, exclude_session: str = "", home: Path | None = None) -> None:
    with _recovery_worker_lock(harness, home) as acquired:
        if acquired:
            _recover_sessions_locked(harness, exclude_session, home)


def _recovery_pacer(config: dict):
    """Space historical batches across all harness workers for this account."""
    from observal_cli import telemetry_buffer

    def pace_delivery() -> float:
        return telemetry_buffer.recovery_slot_remaining(str(config["server_url"]), str(config["user_id"]))

    return pace_delivery


def _recover_sessions_locked(harness: str, exclude_session: str, home: Path | None) -> None:
    from observal_cli import telemetry_buffer

    ensure_loaded()
    adapter = get_adapter(harness)
    config = load_config(home=home)
    if config is None:
        return
    config["_pace_recovery_delivery"] = _recovery_pacer(config)

    def drain_after_pause(*, initial: bool = False) -> None:
        # Keep the background worker alive through a rate-limit window; a user
        # should not need another prompt just to resume an existing backlog.
        while True:
            remaining = telemetry_buffer.delivery_pause_remaining(
                str(config.get("server_url") or ""), str(config.get("user_id") or "")
            )
            if remaining <= 0 and not initial:
                return
            if remaining > 0:
                time.sleep(remaining)
            drain_outbox(config, home=home)
            initial = False

    # Capture eligible sources before a large paced outbox takes minutes to drain:
    # harness discovery can exclude transcripts once they cross an age cutoff.
    now = time.time()
    sources = tuple(adapter.discover_session_sources(home=home))
    recovery_final = adapter.aged_recovery_final()
    drain_outbox(config, home=home)
    for source in sources:
        if source.session_id == exclude_session or source.path is None:
            continue
        try:
            stat = source.path.stat()
            if now - stat.st_mtime < _RECOVERY_MIN_AGE_SECONDS:
                continue
        except OSError:
            continue
        offset, _line_count, finalized = read_cursor_state(source.checkpoint_key, home=home)
        if offset >= stat.st_size and (finalized or not recovery_final):
            continue
        event = {"session_id": source.session_id, "hook_event_name": "Stop"}
        # Scope is checked after the cheap cursor and age filters: deciding it
        # reads the transcript, and this scan walks every session on disk.
        if adapter.should_capture_session(source, home=home):
            drain_session_source(
                source,
                config,
                hook_event="CrashRecovery",
                final=recovery_final,
                extra_fields=adapter.session_extra_fields(source, event, recovery_final, home=home),
                recover_from_server=True,
                spool_only=telemetry_buffer.delivery_paused(
                    str(config.get("server_url") or ""), str(config.get("user_id") or "")
                ),
                home=home,
            )
        # Recover separately stored delegated work alongside the parent.
        for related in adapter.related_session_sources(source, home=home):
            if not adapter.should_capture_session(related, home=home):
                continue
            drain_session_source(
                related,
                config,
                hook_event="CrashRecovery",
                final=recovery_final,
                extra_fields=adapter.session_extra_fields(related, event, recovery_final, home=home),
                extra_records=adapter.session_extra_records(related, event, recovery_final, home=home),
                recover_from_server=True,
                spool_only=telemetry_buffer.delivery_paused(
                    str(config.get("server_url") or ""), str(config.get("user_id") or "")
                ),
                home=home,
            )
    drain_after_pause(initial=True)


def cli_main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--harness", default="claude-code")
    parser.add_argument("--finalize-session", default="")
    parser.add_argument("--cwd", default="")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--drain-outbox", action="store_true")
    parser.add_argument("--json-response", action="store_true")
    parser.add_argument("--exclude-session", default="")
    args = parser.parse_args()
    try:
        if args.finalize_session:
            _finalize_session(args.harness, args.finalize_session, args.cwd)
        elif args.recover:
            _recover_sessions(args.harness, args.exclude_session)
        elif args.drain_outbox:
            _drain_pending()
        else:
            main(harness=args.harness)
    except Exception as exc:
        optic.error("session worker crashed: {}", exc)
    finally:
        if args.json_response:
            sys.stdout.write('{"continue":true}\n')
            sys.stdout.flush()


if __name__ == "__main__":
    cli_main()
