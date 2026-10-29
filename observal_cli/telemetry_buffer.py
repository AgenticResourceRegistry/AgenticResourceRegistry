# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Durable SQLite outbox for acknowledged session delivery.

Observed session records are stored before network delivery and deleted only
after the server acknowledges a contiguous checkpoint.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DB_PATH = Path.home() / ".observal" / "telemetry_buffer.db"
MAX_OUTBOX_BYTES = 256 * 1024 * 1024
BATCH_SIZE = 50


class OutboxError(RuntimeError):
    """Base error for durable outbox failures."""


class OutboxFullError(OutboxError):
    """Raised instead of dropping telemetry when the durable outbox is full."""


class OutboxConflictError(OutboxError):
    """Raised when one source range is queued with different records."""


@dataclass(frozen=True)
class OutboxItem:
    id: int
    destination: str
    user_id: str
    harness: str
    session_id: str
    checkpoint_key: str
    start_line: int
    end_line: int
    end_offset: int
    final: bool
    payload: dict
    attempts: int


def _path(db_path: Path | None) -> Path:
    return db_path or DB_PATH


def delivery_scope(destination: str, user_id: str) -> str:
    """Stable, opaque lock identity for one normalized destination and user."""
    identity = json.dumps([destination.rstrip("/"), user_id], separators=(",", ":"))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@contextmanager
def delivery_lock(*, destination: str, user_id: str, db_path: Path | None = None):
    """Serialize drains for one account across hooks, finalizers and workers."""
    path = _path(db_path).with_suffix(f".delivery-{delivery_scope(destination, user_id)}.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
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


def _connect(db_path: Path | None = None, max_bytes: int = MAX_OUTBOX_BYTES) -> sqlite3.Connection:
    path = _path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                destination TEXT NOT NULL,
                user_id TEXT NOT NULL,
                harness TEXT NOT NULL,
                session_id TEXT NOT NULL,
                checkpoint_key TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                final INTEGER NOT NULL DEFAULT 0,
                payload TEXT NOT NULL,
                records_hash TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt TEXT,
                live INTEGER NOT NULL DEFAULT 0,
                UNIQUE(destination, user_id, harness, session_id, start_line, end_line)
            )
            """
        )
        if not any(row[1] == "live" for row in conn.execute("PRAGMA table_info(session_outbox)")):
            try:
                conn.execute("ALTER TABLE session_outbox ADD COLUMN live INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                # A concurrent hook may have migrated the same existing database.
                if not any(row[1] == "live" for row in conn.execute("PRAGMA table_info(session_outbox)")):
                    raise
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_outbox_pending ON session_outbox(destination, user_id, id)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS session_outbox_pause ("
            "destination TEXT NOT NULL, user_id TEXT NOT NULL, until_epoch REAL NOT NULL, "
            "PRIMARY KEY(destination, user_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS session_recovery_pace ("
            "destination TEXT NOT NULL, user_id TEXT NOT NULL, next_epoch REAL NOT NULL, "
            "PRIMARY KEY(destination, user_id))"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_outbox_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_sync TEXT
            )
            """
        )
        conn.commit()
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        conn.execute(f"PRAGMA max_page_count = {max(1, max_bytes // page_size)}")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return conn
    except sqlite3.Error as exc:
        raise OutboxError(f"cannot open durable telemetry outbox: {exc}") from exc


def _used_bytes(conn: sqlite3.Connection) -> int:
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    free_pages = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    return (page_count - free_pages) * page_size


def _records_hash(payload: dict) -> str:
    lines = payload.get("lines") or []
    return hashlib.sha256("\n".join(str(line) for line in lines).encode()).hexdigest()


def enqueue(
    payload: dict,
    *,
    destination: str,
    user_id: str,
    checkpoint_key: str | None = None,
    db_path: Path | None = None,
    max_bytes: int = MAX_OUTBOX_BYTES,
    live: bool = False,
) -> int:
    """Persist one source batch and return its durable row ID.

    Re-queuing the same source range is idempotent. Different content for an
    existing range is rejected instead of overwriting unacknowledged records.
    """
    lines = payload.get("lines") or []
    metadata_only = not lines
    if metadata_only and payload.get("total_credits") is None and not payload.get("final"):
        raise ValueError("empty session outbox payload must contain durable metadata")
    harness = str(payload.get("harness") or "")
    session_id = str(payload.get("session_id") or "")
    if not destination or not user_id or not harness or not session_id:
        raise ValueError("destination, user_id, harness, and session_id are required")

    start_line = int(payload.get("start_offset", 0))
    end_line = start_line + len(lines) - 1
    end_offsets = payload.get("end_byte_offsets") or []
    end_offset = int(end_offsets[-1]) if end_offsets else int(payload.get("total_offset") or 0)
    final = bool(payload.get("final"))
    serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    records_hash = _records_hash(payload)
    key = (destination.rstrip("/"), user_id, harness, session_id, start_line, end_line)

    conn = _connect(db_path, max_bytes=max_bytes)
    try:
        existing = conn.execute(
            "SELECT id, records_hash FROM session_outbox "
            "WHERE destination = ? AND user_id = ? AND harness = ? AND session_id = ? "
            "AND start_line = ? AND end_line = ?",
            key,
        ).fetchone()
        if existing and existing[1] != records_hash:
            raise OutboxConflictError(
                f"different records already queued for {harness}/{session_id} lines {start_line}-{end_line}"
            )
        if not existing and _used_bytes(conn) + len(serialized.encode()) + 4096 > max_bytes:
            raise OutboxFullError(f"durable telemetry outbox reached its {max_bytes}-byte capacity")

        try:
            conn.execute(
                """
                INSERT INTO session_outbox (
                    destination, user_id, harness, session_id, checkpoint_key,
                    start_line, end_line, end_offset, final, payload, records_hash, live
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(destination, user_id, harness, session_id, start_line, end_line)
                DO UPDATE SET
                    checkpoint_key = excluded.checkpoint_key,
                    end_offset = max(session_outbox.end_offset, excluded.end_offset),
                    final = max(session_outbox.final, excluded.final),
                    live = max(session_outbox.live, excluded.live),
                    payload = CASE
                        WHEN excluded.final = 1 OR excluded.end_line < excluded.start_line THEN excluded.payload
                        ELSE session_outbox.payload
                    END
                """,
                (
                    *key[:4],
                    checkpoint_key or session_id,
                    start_line,
                    end_line,
                    end_offset,
                    int(final),
                    serialized,
                    records_hash,
                    int(live),
                ),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            if "full" in str(exc).lower():
                raise OutboxFullError("durable telemetry outbox or disk is full") from exc
            raise OutboxError(f"cannot persist session records: {exc}") from exc

        row = conn.execute(
            "SELECT id FROM session_outbox WHERE destination = ? AND user_id = ? AND harness = ? "
            "AND session_id = ? AND start_line = ? AND end_line = ?",
            key,
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def pending(
    *,
    destination: str,
    user_id: str,
    limit: int = BATCH_SIZE,
    db_path: Path | None = None,
    preferred_session: tuple[str, str] | None = None,
    prefer_live: bool = False,
) -> list[OutboxItem]:
    """Return durable batches in session line order, with optional live priority.

    Historical and live sessions can be alternated by the drainer. A hook can
    select its own session ahead of the backlog. A checkpoint rewind may spool
    earlier lines after later ones, so row ID alone cannot order a session.
    Attempts never make a row terminal.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            # Group once for each session's first row; a correlated MIN() per row rescans the whole
            # destination for every row, which takes seconds on a few thousand queued batches.
            "WITH firsts AS (SELECT harness, session_id, MIN(id) AS first_id, MAX(live) AS live FROM session_outbox "
            "WHERE destination = ? AND user_id = ? GROUP BY harness, session_id) "
            "SELECT item.id, item.destination, item.user_id, item.harness, item.session_id, "
            "item.checkpoint_key, item.start_line, item.end_line, item.end_offset, item.final, "
            "item.payload, item.attempts "
            "FROM session_outbox AS item JOIN firsts "
            "ON firsts.harness = item.harness AND firsts.session_id = item.session_id "
            "WHERE item.destination = ? AND item.user_id = ? "
            "ORDER BY (item.harness = ? AND item.session_id = ?) DESC, "
            "(CASE WHEN ? = 1 THEN firsts.live ELSE 1 - firsts.live END) DESC, "
            "(item.end_line < item.start_line), firsts.first_id, item.start_line, item.id LIMIT ?",
            (
                destination.rstrip("/"),
                user_id,
                destination.rstrip("/"),
                user_id,
                *(preferred_session or ("", "")),
                int(prefer_live),
                limit,
            ),
        ).fetchall()
        return [
            OutboxItem(
                id=int(row[0]),
                destination=row[1],
                user_id=row[2],
                harness=row[3],
                session_id=row[4],
                checkpoint_key=row[5],
                start_line=int(row[6]),
                end_line=int(row[7]),
                end_offset=int(row[8]),
                final=bool(row[9]),
                payload=json.loads(row[10]),
                attempts=int(row[11]),
            )
            for row in rows
        ]
    finally:
        conn.close()


def spooled_checkpoint(
    *,
    destination: str,
    user_id: str,
    harness: str,
    session_id: str,
    checkpoint_key: str,
    line_count: int,
    byte_offset: int,
    db_path: Path | None = None,
) -> tuple[int, int]:
    """Return the contiguous local checkpoint including durable pending batches."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT start_line, end_line, end_offset FROM session_outbox "
            "WHERE destination = ? AND user_id = ? AND harness = ? AND session_id = ? AND checkpoint_key = ? "
            "ORDER BY start_line, end_line",
            (destination.rstrip("/"), user_id, harness, session_id, checkpoint_key),
        ).fetchall()
    finally:
        conn.close()

    expected = line_count
    offset = byte_offset
    for start_line, end_line, end_offset in rows:
        start_line = int(start_line)
        end_line = int(end_line)
        if start_line > expected:
            break
        if end_line < expected:
            continue
        expected = end_line + 1
        offset = max(offset, int(end_offset))
    return offset, expected


def holds_line(
    *,
    destination: str,
    user_id: str,
    harness: str,
    session_id: str,
    line: int,
    db_path: Path | None = None,
) -> bool:
    """Whether a pending source batch for the session still contains ``line``."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM session_outbox WHERE destination = ? AND user_id = ? AND harness = ? "
            "AND session_id = ? AND start_line <= ? AND end_line >= ? LIMIT 1",
            (destination.rstrip("/"), user_id, harness, session_id, line, line),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def recovery_slot_remaining(destination: str, user_id: str, *, db_path: Path | None = None) -> float:
    """Read shared pacing delay without claiming a future request slot."""
    import time

    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT next_epoch FROM session_recovery_pace WHERE destination = ? AND user_id = ?",
            (destination.rstrip("/"), user_id),
        ).fetchone()
        return max(0.0, float(row[0]) - time.time()) if row else 0.0
    finally:
        conn.close()


def reserve_recovery_slot(
    destination: str, user_id: str, *, db_path: Path | None = None, allow_wait: bool = True
) -> float | None:
    """Reserve a shared request slot; live hooks decline slots that require waiting."""
    import time

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = time.time()
        row = conn.execute(
            "SELECT next_epoch FROM session_recovery_pace WHERE destination = ? AND user_id = ?",
            (destination.rstrip("/"), user_id),
        ).fetchone()
        send_at = max(now, float(row[0])) if row else now
        if not allow_wait and send_at > now:
            conn.rollback()
            return None
        conn.execute(
            "INSERT INTO session_recovery_pace (destination, user_id, next_epoch) VALUES (?, ?, ?) "
            "ON CONFLICT(destination, user_id) DO UPDATE SET next_epoch = excluded.next_epoch",
            (destination.rstrip("/"), user_id, send_at + 1.1),
        )
        conn.commit()
        return send_at - now
    finally:
        conn.close()


def pause_delivery(destination: str, user_id: str, seconds: float, *, db_path: Path | None = None) -> None:
    """Persist a rate-limit cooldown across hook and recovery processes."""
    import time

    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO session_outbox_pause (destination, user_id, until_epoch) VALUES (?, ?, ?) "
            "ON CONFLICT(destination, user_id) DO UPDATE SET until_epoch = max(until_epoch, excluded.until_epoch)",
            (destination.rstrip("/"), user_id, time.time() + seconds),
        )
        conn.commit()
    finally:
        conn.close()


def delivery_pause_remaining(destination: str, user_id: str, *, db_path: Path | None = None) -> float:
    """Remaining rate-limit cooldown shared by all delivery processes."""
    import time

    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT until_epoch FROM session_outbox_pause WHERE destination = ? AND user_id = ?",
            (destination.rstrip("/"), user_id),
        ).fetchone()
        return max(0.0, float(row[0]) - time.time()) if row else 0.0
    finally:
        conn.close()


def delivery_paused(destination: str, user_id: str, *, db_path: Path | None = None) -> bool:
    """Do not retry a known 429 on every subsequent hook invocation."""
    return delivery_pause_remaining(destination, user_id, db_path=db_path) > 0


def record_attempt(item_id: int, *, db_path: Path | None = None) -> None:
    """Record a failed attempt without discarding or disabling the batch."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE session_outbox SET attempts = attempts + 1, last_attempt = datetime('now') WHERE id = ?",
            (item_id,),
        )
        conn.commit()
    finally:
        conn.close()


def quarantine(item: OutboxItem, reason: str, *, db_path: Path | None = None) -> Path:
    """Preserve a permanently rejected batch outside the delivery queue."""
    path = _path(db_path).with_suffix(".rejected.jsonl")
    record = {
        "quarantined_at": datetime.now(UTC).isoformat(),
        "reason": reason,
        "destination": item.destination,
        "user_id": item.user_id,
        "harness": item.harness,
        "session_id": item.session_id,
        "checkpoint_key": item.checkpoint_key,
        "start_line": item.start_line,
        "end_line": item.end_line,
        "payload": item.payload,
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "ab") as file:
        file.write((json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode())
        file.flush()
        os.fsync(file.fileno())
    accept_item(item.id, db_path=db_path)
    return path


def accept_item(item_id: int, *, db_path: Path | None = None) -> None:
    """Remove one posted batch when an audit rewinds the contiguous checkpoint."""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM session_outbox WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()


def acknowledge(
    *,
    destination: str,
    user_id: str,
    harness: str,
    session_id: str,
    acknowledged_line: int,
    include_metadata: bool = False,
    db_path: Path | None = None,
) -> int:
    """Delete acknowledged source batches and only metadata that was itself posted."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "DELETE FROM session_outbox WHERE destination = ? AND user_id = ? AND harness = ? "
            "AND session_id = ? AND end_line <= ? "
            "AND (end_line >= start_line OR ? = 1)",
            (destination.rstrip("/"), user_id, harness, session_id, acknowledged_line, int(include_metadata)),
        )
        if cursor.rowcount:
            conn.execute(
                "INSERT INTO session_outbox_state (id, last_sync) VALUES (1, datetime('now')) "
                "ON CONFLICT(id) DO UPDATE SET last_sync = excluded.last_sync"
            )
        conn.commit()
        return int(cursor.rowcount)
    finally:
        conn.close()


def stats(*, db_path: Path | None = None) -> dict:
    """Return durable outbox statistics for CLI status commands."""
    conn = _connect(db_path)
    try:
        pending_count = int(conn.execute("SELECT COUNT(*) FROM session_outbox").fetchone()[0])
        oldest = conn.execute("SELECT created_at FROM session_outbox ORDER BY id LIMIT 1").fetchone()
        last_sync = conn.execute("SELECT last_sync FROM session_outbox_state WHERE id = 1").fetchone()
        quarantine_path = _path(db_path).with_suffix(".rejected.jsonl")
        try:
            with quarantine_path.open("rb") as file:
                failed_count = sum(1 for _line in file)
        except FileNotFoundError:
            failed_count = 0
        return {
            "pending": pending_count,
            "failed": failed_count,
            "sent": 0,
            "total": pending_count + failed_count,
            "oldest_pending": oldest[0] if oldest else None,
            "last_sync": last_sync[0] if last_sync else None,
            "bytes": _used_bytes(conn),
        }
    finally:
        conn.close()
