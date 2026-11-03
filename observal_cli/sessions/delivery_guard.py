# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cross-process delivery coordination and acknowledgement-aware hook receipts."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

from observal_cli.lockfile import _exclusive_lock


@dataclass
class DeliveryAttempt:
    """A duplicate may skip capture; only successful work can create a receipt."""

    proceed: bool = True
    delivered: bool = False


def serialized_outbox(function):
    """Keep one process in the pending-item/send/acknowledge loop for each outbox."""

    @wraps(function)
    def run(*args, **kwargs):
        from observal_cli import telemetry_buffer

        path = Path(kwargs.get("db_path") or telemetry_buffer.DB_PATH)
        with _exclusive_lock(path.with_suffix(".delivery.lock")):
            return function(*args, **kwargs)

    return run


@contextmanager
def kiro_delivery_guard(source, event: dict, config: dict, *, related=(), final=False, home=None):
    """Serialize a Kiro session group and skip only an unchanged, acknowledged attempt.

    Receipt identities include destination and user, and contain hashes rather than
    prompts or credentials. File signatures permit new turns, delayed credits and
    related transcripts to run again. Failed work never marks a receipt, so an
    immediately following duplicate can retry the durable outbox.
    """
    home = home or Path.home()
    identity = [config.get("server_url", "").rstrip("/"), config.get("user_id", ""), source.checkpoint_key]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    root = home / ".observal" / "session-delivery"
    phase = (
        "final"
        if final
        else str(event.get("hook_event_name") or event.get("hookEventName") or event.get("event") or "hook").lower()
    )
    phase_key = hashlib.sha256(phase.encode()).hexdigest()
    receipt = root / key / (phase_key + ".json")
    # Lock files stay in place: unlinking an actively held lock would split ownership.
    with _exclusive_lock(root / (key + ".lock")):
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
            json.dumps([event, signatures, final], sort_keys=True, default=str).encode()
        ).hexdigest()
        try:
            previous = json.loads(receipt.read_text())
        except (OSError, ValueError):
            previous = None
        from observal_cli.sessions.base import read_cursor_state

        cursors = [read_cursor_state(item.checkpoint_key, home=home) for item in [source, *related]]
        state = {"fingerprint": fingerprint, "cursors": [list(cursor) for cursor in cursors]}
        attempt = DeliveryAttempt(proceed=previous != state)
        yield attempt
        if attempt.proceed and attempt.delivered:
            receipt.parent.mkdir(parents=True, exist_ok=True)
            # The session lock makes this atomic to other readers; replace also
            # prevents a killed writer leaving a falsely successful partial receipt.
            temporary = receipt.with_suffix(".tmp")
            state["cursors"] = [list(read_cursor_state(item.checkpoint_key, home=home)) for item in [source, *related]]
            temporary.write_text(json.dumps(state))
            temporary.chmod(0o600)
            temporary.replace(receipt)
