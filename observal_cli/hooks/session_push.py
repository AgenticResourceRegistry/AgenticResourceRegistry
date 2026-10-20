# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Generic Python harness hook entry point for durable session delivery."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from loguru import logger as optic

from observal_cli import telemetry_buffer
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.sessions.base import (
    cursor_source_shrunk,
    drain_outbox,
    drain_session_source,
    load_config,
    log_error,
    read_cursor_state,
)

if TYPE_CHECKING:
    from pathlib import Path

_FINAL_DELAY_SECONDS = 2.0
_STABLE_INTERVAL_SECONDS = 0.25
_STABLE_CHECKS = 2
_RECOVERY_MIN_AGE_SECONDS = 120


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
    if deferred:
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
    if adapter.should_capture_session(source, home=home):
        _wait_until_stable(source.path)
        drain_session_source(
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


def _drain_pending(home: Path | None = None) -> None:
    config = load_config(home=home)
    if config is not None:
        drain_outbox(config, home=home)


def _recover_sessions(harness: str, exclude_session: str = "", home: Path | None = None) -> None:
    ensure_loaded()
    adapter = get_adapter(harness)
    config = load_config(home=home)
    if config is None:
        return
    drain_outbox(config, home=home)
    recovery_final = adapter.aged_recovery_final()
    now = time.time()
    for source in adapter.discover_session_sources(home=home):
        if source.session_id == exclude_session or source.path is None:
            continue
        try:
            stat = source.path.stat()
        except OSError:
            continue
        held = (
            telemetry_buffer.replay_held(
                destination=str(config["server_url"]),
                user_id=str(config["user_id"]),
                harness=source.harness,
                session_id=source.session_id,
            )
            if config.get("server_url") and config.get("user_id")
            else False
        )
        shrunk = cursor_source_shrunk(source.checkpoint_key, home=home)
        offset, _line_count, finalized = read_cursor_state(source.checkpoint_key, home=home)
        if not held and not shrunk and offset <= stat.st_size and now - stat.st_mtime < _RECOVERY_MIN_AGE_SECONDS:
            continue
        if offset == stat.st_size and (finalized or not recovery_final) and not held and not shrunk:
            continue
        event = {"session_id": source.session_id, "hook_event_name": "Stop"}
        # Scope is checked after the cheap cursor and age filters: deciding it
        # reads the transcript, and this scan walks every session on disk.
        if adapter.should_capture_session(source, home=home):
            drain_session_source(
                source,
                config,
                hook_event="CrashRecovery",
                final=recovery_final or held,
                extra_fields=adapter.session_extra_fields(source, event, recovery_final or held, home=home),
                recover_from_server=True,
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
                home=home,
            )


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
