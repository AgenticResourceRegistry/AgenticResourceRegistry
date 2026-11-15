# SPDX-FileCopyrightText: 2026 MAOLVLHL <3523624716@qq.com>
# SPDX-License-Identifier: Apache-2.0

"""A sidecar event log for a running delegation.

``stdout.log`` and ``stderr.log`` stay the raw truth: the child writes them and
the worker reads them once, at the end. Nothing publishes them while the child
runs, so a caller in another process learns nothing for ten minutes and then
everything at once. This module tails both from the worker and appends one JSON
object per line to ``progress.jsonl`` next to them, naming the stream each
line came from.

The sidecar is a pure derivation: delete it and the run is unaffected, which is
why a reader may also stop at any byte offset. Events are indexed by ``seq``
(monotonic per task) rather than by timestamp, so a slow clock can never
reorder them, and a reader that restarts resumes with ``after_seq``.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from loguru import logger as optic

from observal_cli.delegation import tasks

if TYPE_CHECKING:
    from pathlib import Path

PROGRESS_FILE_NAME = "progress.jsonl"
# One event line: enough for a tool call or an error, not a dump of a whole file.
MAX_LINE_CHARS = 2_000
# The sidecar is a convenience; past this size the caller should read the task logs.
MAX_FILE_BYTES = 2_000_000
# Redact a bounded prefix only: regexes over a multi-megabyte line are wasted work.
MAX_REDACT_CHARS = 20_000
TAIL_INTERVAL_SECONDS = 0.25
READ_LIMIT = 200
# Pages one drain call will walk before handing control back to the caller.
DRAIN_ROUNDS = 20

# Event names a harness uses for a finished tool call, versus one that started it.
_TOOL_RESULT_HINTS = ("result", "output", "end", "complete", "finish", "response")

# task id -> (byte offset of the next unread line, highest seq consumed at that
# offset), per process. Polling is the common case and re-parsing a whole file
# four times a second is not free; the seq keeps the cache honest, because a
# second reader asking for an earlier position must not be served from it.
_OFFSETS: dict[str, tuple[int, int]] = {}


def progress_path(task_id: str) -> Path | None:
    """Where a task's sidecar lives, or None when the id is not one we minted."""
    try:
        directory = tasks.store_dir() / str(uuid.UUID(str(task_id)))
    except ValueError:
        return None
    return directory / PROGRESS_FILE_NAME


def read(task_id: str, after_seq: int = 0, *, limit: int = READ_LIMIT) -> tuple[list[dict], int]:
    """Events newer than ``after_seq``, and the highest seq read so far.

    The returned seq is the caller's next ``after_seq``; it advances even when
    nothing matches, so a caller cannot get stuck on an empty file.
    """
    path = progress_path(task_id)
    if path is None or not path.is_file():
        return [], after_seq
    cached = _OFFSETS.get(task_id)
    # Only a reader that has already seen everything the cache covers may skip
    # ahead in the file; anyone asking from the start gets the whole backlog.
    offset = cached[0] if cached is not None and after_seq >= cached[1] else 0
    try:
        if path.stat().st_size < offset:  # rewritten or rotated: the offset means nothing now
            offset = 0
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
    except OSError as exc:
        optic.debug("progress read failed task={}: {}", task_id, exc)
        return [], after_seq
    if not chunk:
        _OFFSETS[task_id] = (offset, after_seq)
        return [], after_seq

    lines = chunk.split(b"\n")
    tail = lines.pop()  # everything after the last newline is a half-written record
    complete_end = offset + len(chunk) - len(tail)
    parsed: list[tuple[int, dict]] = []
    position = offset
    highest = after_seq
    for raw in lines:
        position += len(raw) + 1
        record = _parse(raw)
        if record is None:
            continue
        parsed.append((position, record))
        highest = max(highest, int(record["seq"]))

    fresh = [(end, record) for end, record in parsed if int(record["seq"]) > after_seq]
    if len(fresh) > limit:
        # Consume only what we hand over; the rest is read again on the next poll,
        # so the returned seq has to stop at the last page too or the caller will
        # skip everything that did not fit.
        fresh = fresh[:limit]
        highest = int(fresh[-1][1]["seq"])
        _OFFSETS[task_id] = (fresh[-1][0], highest)
    else:
        _OFFSETS[task_id] = (complete_end, highest)
    return [record for _, record in fresh], highest


def drain(
    task_id: str, after_seq: int = 0, *, limit: int = READ_LIMIT, rounds: int | None = DRAIN_ROUNDS
) -> tuple[list[dict], int]:
    """Every event after ``after_seq``, paging until the backlog is empty.

    ``rounds`` bounds one call while the task still runs, so a producer that outpaced
    the reader cannot make it spin: the next poll continues where this one stopped.
    Pass None once the task has settled, when the backlog is fixed and a reader that
    stops early would leave records nobody ever prints.
    """
    events: list[dict] = []
    step = 0
    while rounds is None or step < rounds:
        step += 1
        page, after_seq = read(task_id, after_seq, limit=limit)
        events.extend(page)
        if len(page) < limit:
            break
    return events, after_seq


def recent(task_id: str, *, limit: int = 3) -> list[dict]:
    """The last few events of a task, for a status summary."""
    path = progress_path(task_id)
    if path is None or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    records = [record for line in text.splitlines() if (record := _parse(line.encode("utf-8"))) is not None]
    return records[-limit:]


class _LogTail:
    """One child log, and how far the writer has read into it.

    The read position and the half-written line belong to one file each, so they
    are kept together: sharing one buffer across the two streams would splice one
    child's bytes onto another's line.
    """

    __slots__ = ("buffer", "name", "offset", "path")

    def __init__(self, path: Path, name: str) -> None:
        self.path = path
        self.name = name
        self.offset = 0
        self.buffer = b""


class ProgressWriter:
    """Tail a child's logs into a task's progress sidecar.

    ``start`` returns immediately; a daemon thread does the reading, so a slow
    log can never delay the run. ``stop`` drains what is left, so a child killed
    by a timeout still publishes its last complete lines. Both streams are
    tailed: a child that fails says why on stderr, and a caller watching the run
    needs that as much as it needs stdout.
    """

    def __init__(self, task_id: str, stdout_log: Path, stderr_log: Path | None = None) -> None:
        """Point the writer at a task and at the logs it will tail."""
        self._task_id = task_id
        self._logs = [_LogTail(stdout_log, "stdout")]
        if stderr_log is not None:
            self._logs.append(_LogTail(stderr_log, "stderr"))
        self._path = progress_path(task_id)
        self._seq = 0
        self._written = 0
        self._capped = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> ProgressWriter:
        """Begin tailing in a daemon thread; returns self so it can be used inline."""
        if self._path is None:
            return self
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            optic.warning("progress sidecar unavailable task={}: {}", self._task_id, exc)
            self._path = None
            return self
        self._thread = threading.Thread(target=self._tail, name=f"progress-{self._task_id[:8]}", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop the thread and publish whatever the child wrote before it exited."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        try:
            self._drain(final=True)
        except Exception:
            optic.exception("progress flush failed task={}", self._task_id)

    def _tail(self) -> None:
        """Poll the logs until stop is requested; a failure ends the thread, not the run."""
        while not self._stop.is_set():
            try:
                self._drain(final=False)
            except Exception:
                # A broken sidecar must never take the delegation down with it.
                optic.exception("progress tail failed task={}", self._task_id)
                return
            self._stop.wait(TAIL_INTERVAL_SECONDS)

    def _drain(self, *, final: bool) -> None:
        """Emit every complete line read so far, keeping the half-written tail back.

        With ``final`` set the tail is flushed too, because no further bytes are
        coming: a killed child usually leaves its last line unterminated.
        """
        for log in self._logs:
            chunk = self._read_new_bytes(log)
            if chunk:
                log.buffer += chunk
            lines = log.buffer.split(b"\n")
            log.buffer = lines.pop()
            if final and log.buffer:
                lines.append(log.buffer)
                log.buffer = b""
            # The streams are polled in turn, so two lines written in the same
            # instant can arrive in either order: seq orders records, not wall time.
            for raw in lines:
                self._emit(raw, stream=log.name)

    def _read_new_bytes(self, log: _LogTail) -> bytes:
        """Bytes appended to one log since the last read, or empty when it is gone."""
        try:
            with log.path.open("rb") as handle:
                if handle.seek(0, 2) < log.offset:
                    log.offset = 0  # the log was recreated
                handle.seek(log.offset)
                chunk = handle.read()
        except OSError:
            return b""
        log.offset += len(chunk)
        return chunk

    def _emit(self, raw: bytes, *, stream: str) -> None:
        """Append one line to the sidecar, split into a stable kind and its text."""
        if self._capped or self._path is None:
            return
        line = raw.decode("utf-8", errors="replace").rstrip("\r")
        kind, event = _classify(line)
        text, truncated = _clean(line)
        if not text.strip():
            return
        self._seq += 1
        record = {
            "seq": self._seq,
            "ts": _now(),
            "stream": stream,
            "kind": kind,
            **({"event": event} if event else {}),
            "text": text,
            "truncated": truncated,
        }
        payload = json.dumps(record, ensure_ascii=False) + "\n"
        if self._written + len(payload.encode("utf-8")) > MAX_FILE_BYTES:
            self._capped = True
            payload = (
                json.dumps(
                    {
                        "seq": self._seq,
                        "ts": _now(),
                        "stream": stream,
                        "kind": "truncated",
                        "text": f"Progress stopped after {MAX_FILE_BYTES} bytes; read the task logs for the rest.",
                        "truncated": True,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
        except OSError as exc:
            optic.debug("progress write failed task={}: {}", self._task_id, exc)
            return
        self._written += len(payload.encode("utf-8"))


def _now() -> str:
    """ISO-8601 UTC timestamp with milliseconds, the shape the other logs use."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _classify(text: str) -> tuple[str, str | None]:
    """The kind of one line, in the issue vocabulary, plus the harness event name.

    The four kinds stay stable across harnesses: a reader never has to know
    whether it is watching Codex or Claude Code. The raw event name travels
    alongside them in ``event`` so nothing the harness said is lost.
    """
    stripped = text.strip()
    if not stripped.startswith("{"):
        return "line", None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return "line", None
    if not isinstance(payload, dict):
        return "line", None
    event = None
    for key in ("type", "kind", "event"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            # A harness is free to put anything in its event name, so it goes
            # through the same redactor as the text before it is stored.
            event = _clean(value.strip())[0][:60]
            break
    if event is None:
        return "status", None
    return _kind_of(event), event


def _kind_of(event: str) -> str:
    """Map a harness event name to ``tool_call``, ``tool_result`` or ``status``."""
    name = event.lower()
    if "tool" in name:
        return "tool_result" if any(hint in name for hint in _TOOL_RESULT_HINTS) else "tool_call"
    # Everything else a harness reports about itself is a lifecycle step. An event
    # we do not recognise lands here too: it is still a status update, never a
    # dropped line, and `event` keeps the name the harness gave it.
    return "status"


def _clean(line: str) -> tuple[str, bool]:
    """Redact credentials, then cap the line so one event cannot dwarf the file."""
    from observal_cli.discovery.redact import redact_text

    text = redact_text(line[:MAX_REDACT_CHARS])
    if len(line) > MAX_LINE_CHARS:
        return text[:MAX_LINE_CHARS], True
    return text, False


def _parse(raw: bytes) -> dict | None:
    """One stored record, or None for a line that is not one (a partial write)."""
    try:
        record = json.loads(raw.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("seq"), int):
        return None
    return record
