# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cross-process delivery coordination and acknowledgement-aware hook receipts."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from uuid import uuid4

from observal_cli.lockfile import _exclusive_lock

if TYPE_CHECKING:
    from collections.abc import Callable

    from observal_cli.telemetry_buffer import OutboxItem


@dataclass
class DeliveryAttempt:
    """A duplicate may skip capture; only successful work can create a receipt."""

    proceed: bool = True
    delivered: bool = False
    reason: str = ""
    finalization_generation: str | None = None


@dataclass
class OutboxDelivery:
    """A transport attempt may acknowledge records without retaining final authority."""

    item: OutboxItem | None
    final_allowed: Callable[[], bool] = lambda: True


_kiro_context = ContextVar("kiro_final_delivery_context", default=None)


def _outbox_lock_path(db_path=None):
    """All lifecycle and transport owners acquire outbox before session locks."""
    from observal_cli import telemetry_buffer

    return Path(db_path or telemetry_buffer.DB_PATH).with_suffix(".delivery.lock")


@contextmanager
def _transport_claim(path, *, db_path=None, wait=False):
    """Acquire a group under the outbox lock, but never hold that lock during HTTP.

    Nonblocking group acquisition prevents lock-order cycles when a lifecycle owner
    drains another group's rows. Workers may retry busy rows; hooks retain Stop intent.
    """
    while True:
        stack = ExitStack()
        with _exclusive_lock(_outbox_lock_path(db_path)):
            acquired = stack.enter_context(_exclusive_lock(path, blocking=False))
        if acquired:
            break
        stack.close()
        if not wait:
            yield False
            return
        time.sleep(0.01)
    try:
        yield True
    finally:
        stack.close()


@contextmanager
def ordinary_outbox_guard(item, config, *, home=None, db_path=None):
    """Claim one ordinary session and refresh its candidate before transport."""
    from observal_cli import telemetry_buffer

    identity = [item.destination, item.user_id, item.harness, item.session_id]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    path = _outbox_lock_path(db_path).with_suffix(".sessions") / (key + ".lock")
    with _transport_claim(path, db_path=db_path) as acquired:
        if not acquired:
            yield OutboxDelivery(None)
            return
        yield OutboxDelivery(telemetry_buffer.get_item(item.id, db_path=db_path))


def _session_directory(source, config: dict, home: Path) -> Path:
    """Use an opaque, destination/user-scoped session identity for intent and receipts."""
    identity = [config.get("server_url", "").rstrip("/"), config.get("user_id", ""), source.checkpoint_key]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    return home / ".observal" / "session-delivery" / key


def _prompt_fingerprint(source):
    """Hash source prompt boundaries, never store prompt content in Stop intent."""
    if source.path is None:
        return None
    digest = hashlib.sha256()
    try:
        with source.path.open("rb") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                payload = record.get("payload")
                if record.get("kind") == "Prompt" or (isinstance(payload, dict) and payload.get("type") == "user"):
                    digest.update(line.strip())
                    digest.update(b"\n")
    except (OSError, UnicodeError):
        return None
    return digest.hexdigest()


def kiro_finalization_pending(source, config: dict, home=None) -> bool:
    """Only an explicit Stop matching the current prompt sequence permits final recovery."""
    path = _session_directory(source, config, home or Path.home()) / "finalization.pending"
    try:
        intent = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    anchor = _prompt_fingerprint(source)
    return isinstance(intent, dict) and anchor is not None and intent.get("prompt_fingerprint") == anchor


@contextmanager
def kiro_delivery_guard(
    source,
    event: dict,
    config: dict,
    *,
    related=(),
    final=False,
    final_requested=False,
    cancel_final_requested=False,
    require_final_intent=False,
    expected_generation=None,
    finalizable=(),
    home=None,
):
    """Serialize a Kiro session group and skip only an unchanged, acknowledged attempt.

    Receipt identities include destination and user, and contain hashes rather than
    prompts or credentials. File signatures permit new turns, delayed credits and
    related transcripts to run again. Failed work never marks a receipt, so an
    immediately following duplicate can retry the durable outbox.
    """
    home = home or Path.home()
    directory = _session_directory(source, config, home)
    root, key = directory.parent, directory.name
    phase = (
        "final"
        if final
        else str(event.get("hook_event_name") or event.get("hookEventName") or event.get("event") or "hook").lower()
    )
    phase_key = hashlib.sha256(phase.encode()).hexdigest()
    receipt = root / key / (phase_key + ".json")
    # Lock files stay in place: unlinking an actively held lock would split ownership.
    with _transport_claim(root / (key + ".lock"), wait=True):
        pending = directory / "finalization.pending"
        if cancel_final_requested:
            pending.unlink(missing_ok=True)
        if (
            final
            and require_final_intent
            and (
                not kiro_finalization_pending(source, config, home=home)
                or (
                    expected_generation is not None
                    and expected_generation != kiro_finalization_generation(source, config, home)
                )
            )
        ):
            yield DeliveryAttempt(proceed=False, reason="finalization intent superseded")
            return
        if final_requested and finalizable:
            directory.mkdir(parents=True, exist_ok=True)
            temporary = pending.with_suffix(".tmp")
            anchor = _prompt_fingerprint(source)
            try:
                intent = json.loads(pending.read_text())
            except (OSError, ValueError):
                intent = {}
            if (
                not isinstance(intent, dict)
                or intent.get("prompt_fingerprint") != anchor
                or not intent.get("generation")
            ):
                intent = {"prompt_fingerprint": anchor, "generation": uuid4().hex}
            temporary.write_text(json.dumps(intent))
            temporary.chmod(0o600)
            temporary.replace(pending)
        paths = set()
        for item in [source, *related]:
            if item.path is not None:
                paths.update([item.path, item.path.with_suffix(".json"), item.path.parent / "session.json"])
        signatures = []
        for path in sorted(paths):
            try:
                stat = path.stat()
                signatures.append([str(path), stat.st_size, stat.st_mtime_ns, stat.st_ino])
            except OSError:
                signatures.append([str(path), None])
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    {key: value for key, value in event.items() if key != "_observal_finalization_generation"},
                    signatures,
                    final,
                ],
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        try:
            previous = json.loads(receipt.read_text())
        except (OSError, ValueError):
            previous = None
        from observal_cli.sessions.base import read_cursor_state, write_cursor

        def reset_resumed_cursor():
            """Clear finality when prompt boundaries changed since the last acknowledged capture."""
            for item in [source, *related]:
                offset, lines, finalized = read_cursor_state(item.checkpoint_key, home=home)
                if finalized:
                    write_cursor(item.checkpoint_key, offset, lines, home=home, preserve_finalized=False)

        superseded = cancel_final_requested or (
            pending.exists() and not kiro_finalization_pending(source, config, home)
        )
        if superseded:
            pending.unlink(missing_ok=True)
            reset_resumed_cursor()

        def cursor_signature(item):
            """Fingerprint parent and child cursors for acknowledgement-aware duplicate detection."""
            cursor = read_cursor_state(item.checkpoint_key, home=home)
            # Finalization is separate progress; its flag must not invalidate an
            # otherwise acknowledged capture of the same bytes and metadata.
            return list(cursor if final else cursor[:2])

        state = {"fingerprint": fingerprint, "cursors": [cursor_signature(item) for item in [source, *related]]}
        attempt = DeliveryAttempt(
            proceed=previous != state,
            finalization_generation=kiro_finalization_generation(source, config, home) if final_requested else None,
        )
        context = None
        if final and kiro_finalization_pending(source, config, home):
            intent = json.loads(pending.read_text())
            context = {
                **intent,
                "checkpoint_key": source.checkpoint_key,
                "source_path": str(source.path),
                "checkpoints": [item.checkpoint_key for item in [source, *related]],
            }
        token = _kiro_context.set(context)
        try:
            yield attempt
        finally:
            _kiro_context.reset(token)
        if final and require_final_intent and not kiro_finalization_pending(source, config, home):
            pending.unlink(missing_ok=True)
            reset_resumed_cursor()
        if superseded:
            reset_resumed_cursor()
        completed = not final or all(read_cursor_state(item.checkpoint_key, home=home)[2] for item in finalizable)
        if attempt.proceed and attempt.delivered and completed:
            receipt.parent.mkdir(parents=True, exist_ok=True)
            # The session lock makes this atomic to other readers; replace also
            # prevents a killed writer leaving a falsely successful partial receipt.
            temporary = receipt.with_suffix(".tmp")
            state["cursors"] = [cursor_signature(item) for item in [source, *related]]
            temporary.write_text(json.dumps(state))
            temporary.chmod(0o600)
            temporary.replace(receipt)
        # Capture acknowledgements never clear finalization intent. A worker
        # must finish all in-scope cursors; launch success is not completion.
        if (
            final
            and (attempt.delivered or not attempt.proceed)
            and finalizable
            and all(read_cursor_state(item.checkpoint_key, home=home)[2] for item in finalizable)
        ):
            pending.unlink(missing_ok=True)


def kiro_outbox_context(source):
    """Capture the active guarded parent generation for a final parent or child batch."""
    context = _kiro_context.get()
    return dict(context) if context and source.checkpoint_key in context["checkpoints"] else None


@contextmanager
def kiro_outbox_guard(item, config, *, home=None, db_path=None):
    """Serialize queued sends/ACKs with resume and irreversibly downgrade stale finals.

    The local envelope survives source loss and binds a batch to its particular Stop,
    not just to a future Stop with identical prompt bytes. It never reaches the API.
    Source prompts are checked before sending and again at ACK because the harness
    can append a prompt before its hook acquires this lock.
    """
    from observal_cli import telemetry_buffer
    from observal_cli.sessions.base import read_cursor_state, write_cursor

    home = home or Path.home()
    context = item.delivery_context
    scoped = isinstance(context, dict)
    source = SimpleNamespace(
        checkpoint_key=context.get("checkpoint_key", item.checkpoint_key) if scoped else item.checkpoint_key,
        path=Path(context["source_path"]) if scoped and isinstance(context.get("source_path"), str) else None,
    )
    directory = _session_directory(source, config, home)
    pending = directory / "finalization.pending"
    requires_authority = item.final or scoped

    def final_allowed():
        """Revalidate the queued Stop generation and revoke superseded cursor finality."""
        if not requires_authority:
            return True
        try:
            intent = json.loads(pending.read_text())
        except (OSError, ValueError):
            intent = None
        valid = (
            scoped
            and isinstance(intent, dict)
            and isinstance(context.get("generation"), str)
            and context["generation"] == intent.get("generation")
            and context.get("prompt_fingerprint") == intent.get("prompt_fingerprint")
            and kiro_finalization_pending(source, config, home)
        )
        if not valid:
            if scoped and isinstance(intent, dict) and intent.get("generation") == context.get("generation"):
                pending.unlink(missing_ok=True)
            keys = set(context.get("checkpoints", ())) if scoped else set()
            keys.update([source.checkpoint_key, item.checkpoint_key])
            for key in keys:
                offset, lines, finalized = read_cursor_state(key, home=home)
                if finalized:
                    write_cursor(key, offset, lines, home=home, preserve_finalized=False)
        return bool(valid)

    # Retain the parent's reentrant claim through ACK, not the outbox-wide lock.
    with _transport_claim(directory.parent / (directory.name + ".lock"), db_path=db_path) as acquired:
        if not acquired:
            yield OutboxDelivery(None)
            return
        item = telemetry_buffer.get_item(item.id, db_path=db_path)
        if item is None:
            yield OutboxDelivery(None)
            return
        context = item.delivery_context
        scoped = isinstance(context, dict)
        requires_authority = item.final or scoped
        if not final_allowed() and (item.final or item.payload.get("final")):
            item = telemetry_buffer.supersede_final(item, db_path=db_path)
        yield OutboxDelivery(item, final_allowed)


def kiro_finalization_generation(source, config, home=None):
    """Snapshot the scheduled Stop identity; an absent identity cannot adopt a later Stop."""
    if not kiro_finalization_pending(source, config, home):
        return ""
    pending = _session_directory(source, config, home or Path.home()) / "finalization.pending"
    try:
        return str(json.loads(pending.read_text()).get("generation") or "")
    except (OSError, ValueError):
        return ""
