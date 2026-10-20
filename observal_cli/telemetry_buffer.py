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
import re
import sqlite3
import tempfile
import time
import uuid
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
    retry_after: float = 0.0
    payload_snapshot: str = ""


def _path(db_path: Path | None) -> Path:
    return db_path or DB_PATH


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
                UNIQUE(destination, user_id, harness, session_id, start_line, end_line)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_outbox_pending ON session_outbox(destination, user_id, id)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS session_outbox_backoff (item_id INTEGER PRIMARY KEY, retry_after REAL NOT NULL)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS session_outbox_rejected (
                item_id INTEGER PRIMARY KEY, destination TEXT NOT NULL, user_id TEXT NOT NULL,
                harness TEXT NOT NULL, session_id TEXT NOT NULL, recovered INTEGER NOT NULL DEFAULT 0,
                restored_id INTEGER, archive_ref TEXT
            )"""
        )
        if "archive_ref" not in {row[1] for row in conn.execute("PRAGMA table_info(session_outbox_rejected)")}:
            conn.execute("ALTER TABLE session_outbox_rejected ADD COLUMN archive_ref TEXT")
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
        _import_legacy_rejections(conn, db_path)
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
                    start_line, end_line, end_offset, final, payload, records_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(destination, user_id, harness, session_id, start_line, end_line)
                DO UPDATE SET
                    checkpoint_key = excluded.checkpoint_key,
                    end_offset = max(session_outbox.end_offset, excluded.end_offset),
                    final = max(session_outbox.final, excluded.final),
                    payload = CASE
                        WHEN excluded.final = 1 OR (excluded.end_line < excluded.start_line AND session_outbox.final = 0)
                        THEN excluded.payload ELSE session_outbox.payload
                    END
                WHERE session_outbox.records_hash = excluded.records_hash
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
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0] == 0:
                raise OutboxConflictError(
                    f"different records already queued for {harness}/{session_id} lines {start_line}-{end_line}"
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
    exclude_sessions: set[tuple[str, str]] | None = None,
    skip_backoff: bool = False,
    db_path: Path | None = None,
) -> list[OutboxItem]:
    """Return source-order batches by session, excluding rejected sessions and optional backoff."""
    conn = _connect(db_path)
    try:
        exclusions = sorted(exclude_sessions or ())
        predicate = "".join(" AND NOT (harness = ? AND session_id = ?)" for _ in exclusions)
        backoff_predicate = (
            "AND NOT EXISTS (SELECT 1 FROM session_outbox_backoff b JOIN session_outbox s "
            "ON s.id = b.item_id WHERE s.destination = session_outbox.destination "
            "AND s.user_id = session_outbox.user_id AND s.harness = session_outbox.harness "
            "AND s.session_id = session_outbox.session_id AND b.retry_after > ?) "
            if skip_backoff
            else ""
        )
        rows = conn.execute(
            "SELECT id, destination, user_id, harness, session_id, checkpoint_key, "
            "start_line, end_line, end_offset, final, payload, attempts, "
            "(SELECT retry_after FROM session_outbox_backoff WHERE item_id = session_outbox.id) "
            "FROM session_outbox WHERE destination = ? AND user_id = ? "
            "AND NOT EXISTS (SELECT 1 FROM session_outbox_rejected r WHERE "
            "r.destination = session_outbox.destination AND r.user_id = session_outbox.user_id "
            "AND r.harness = session_outbox.harness AND r.session_id = session_outbox.session_id AND r.recovered = 0) "
            + backoff_predicate
            + predicate
            + " ORDER BY (SELECT min(s.id) FROM session_outbox s WHERE "
            "s.destination = session_outbox.destination AND s.user_id = session_outbox.user_id "
            "AND s.harness = session_outbox.harness AND s.session_id = session_outbox.session_id), "
            "(end_line < start_line), start_line, id LIMIT ?",
            (
                destination.rstrip("/"),
                user_id,
                *((time.time(),) if skip_backoff else ()),
                *(part for pair in exclusions for part in pair),
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
                retry_after=float(row[12] or 0),
                payload_snapshot=row[10],
            )
            for row in rows
        ]
    finally:
        conn.close()


def has_rejections(*, destination: str, user_id: str, db_path: Path | None = None) -> bool:
    """Report unresolved, persistently blocked sessions for a destination."""
    conn = _connect(db_path)
    try:
        return (
            conn.execute(
                "SELECT 1 FROM session_outbox_rejected WHERE destination = ? AND user_id = ? AND recovered = 0 LIMIT 1",
                (destination.rstrip("/"), user_id),
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def is_rejected(*, destination: str, user_id: str, harness: str, session_id: str, db_path: Path | None = None) -> bool:
    """Whether this destination's session has a quarantined source gap."""
    conn = _connect(db_path)
    try:
        return (
            conn.execute(
                "SELECT 1 FROM session_outbox_rejected WHERE destination = ? AND user_id = ? "
                "AND harness = ? AND session_id = ? AND recovered = 0 LIMIT 1",
                (destination.rstrip("/"), user_id, harness, session_id),
            ).fetchone()
            is not None
        )
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
        rows = list(
            conn.execute(
                "SELECT start_line, end_line, end_offset FROM session_outbox "
                "WHERE destination = ? AND user_id = ? AND harness = ? AND session_id = ? AND checkpoint_key = ? "
                "ORDER BY start_line, end_line",
                (destination.rstrip("/"), user_id, harness, session_id, checkpoint_key),
            ).fetchall()
        )
        rejected_refs = {
            int(row[0]): row[1]
            for row in conn.execute(
                "SELECT item_id, archive_ref FROM session_outbox_rejected WHERE destination = ? AND user_id = ? "
                "AND harness = ? AND session_id = ? AND recovered = 0",
                (destination.rstrip("/"), user_id, harness, session_id),
            )
        }
    finally:
        conn.close()

    # A rejected source batch is removed from the active queue, but its archived
    # checkpoint still protects appended records from being spooled twice.
    if rejected_refs:
        try:
            for item_id, ref in rejected_refs.items():
                record = _selected_archive(
                    item_id, ref, (destination.rstrip("/"), user_id, harness, session_id), db_path
                )
                if record.get("checkpoint_key") != checkpoint_key:
                    continue
                payload = record["payload"]
                start = int(payload["start_offset"])
                lines = payload.get("lines") or []
                offsets = payload.get("end_byte_offsets") or []
                rows.append((start, start + len(lines) - 1, int(offsets[-1]) if offsets else 0))
        except (OSError, ValueError, KeyError) as exc:
            raise OutboxError("cannot read archived checkpoint for rejected session") from exc
    rows.sort(key=lambda row: (row[0], row[1]))
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


def record_attempt(item_id: int, *, db_path: Path | None = None, backoff: bool = False) -> None:
    """Record a failed attempt without discarding or disabling the batch."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE session_outbox SET attempts = attempts + 1, last_attempt = datetime('now') WHERE id = ?",
            (item_id,),
        )
        if backoff:
            attempts = conn.execute("SELECT attempts FROM session_outbox WHERE id = ?", (item_id,)).fetchone()
            if attempts:
                delay = min(60, 2 ** min(int(attempts[0]), 6))
                conn.execute(
                    "INSERT INTO session_outbox_backoff VALUES (?, ?) "
                    "ON CONFLICT(item_id) DO UPDATE SET retry_after = excluded.retry_after",
                    (item_id, time.time() + delay),
                )
        conn.commit()
    finally:
        conn.close()


def _sync_directory(path: Path) -> None:
    """Persist a new directory entry where supported (Windows lacks directory fsync)."""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _import_legacy_rejections(conn: sqlite3.Connection, db_path: Path | None) -> None:
    """Index old append-only archives without rewriting the evidence or active queue.

    Negative IDs are derived from the original byte position (zero maps to -1),
    so repeated opens and concurrent SQLite imports name the same rejected batch.
    """
    try:
        for record in _legacy_records(db_path):
            item_id = record.get("item_id")
            if type(item_id) is not int or item_id >= 0:
                continue
            identity = tuple(record.get(key) for key in ("destination", "user_id", "harness", "session_id"))
            payload = record.get("payload")
            if (
                any(not isinstance(value, str) or not value for value in identity)
                or not isinstance(record.get("checkpoint_key"), str)
                or not record["checkpoint_key"]
                or not isinstance(payload, dict)
                or payload.get("harness") != identity[2]
                or payload.get("session_id") != identity[3]
                or type(record.get("start_line")) is not int
                or type(record.get("end_line")) is not int
                or type(payload.get("start_offset")) is not int
                or payload["start_offset"] != record["start_line"]
                or not isinstance(payload.get("lines"), list)
                or record["end_line"] != record["start_line"] + len(payload["lines"]) - 1
            ):
                continue  # A damaged row must not hide other intact rows.
            existing = conn.execute(
                "SELECT destination, user_id, harness, session_id FROM session_outbox_rejected WHERE item_id = ?",
                (item_id,),
            ).fetchone()
            if existing is not None and tuple(existing) != identity:
                raise OutboxError(f"legacy quarantine ID collision at {item_id}")
            conn.execute(
                "INSERT OR IGNORE INTO session_outbox_rejected "
                "(item_id, destination, user_id, harness, session_id, archive_ref) VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, *identity, record["_archive_ref"]),
            )
            conn.execute(
                "UPDATE session_outbox_rejected SET archive_ref = ? WHERE item_id = ? AND archive_ref IS NULL",
                (record["_archive_ref"], item_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise


def _legacy_records(db_path: Path | None):
    legacy = _path(db_path).with_suffix(".rejected.jsonl")
    if not legacy.exists():
        return
    try:
        with legacy.open("rb") as archive:
            offset = 0
            for line in archive:
                row_offset = offset
                offset += len(line)
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        continue
                except (ValueError, UnicodeDecodeError):
                    continue
                if "item_id" not in record:
                    record["item_id"] = -(row_offset + 1)
                record["_archive_ref"] = f"legacy:{row_offset}"
                yield record
    except OSError as exc:
        raise OutboxError("cannot read legacy quarantine archive") from exc


def _selected_archive(item_id: int, ref: str | None, identity: tuple, db_path: Path | None) -> dict:
    """Read only the archive selected by the successful SQLite CAS, never an orphan."""
    if not ref:
        raise OutboxError(f"archive identity missing for quarantined batch {item_id}")
    try:
        if re.fullmatch(r"legacy:(0|[1-9][0-9]*)", ref):
            offset = int(ref.split(":", 1)[1])
            with _path(db_path).with_suffix(".rejected.jsonl").open("rb") as archive:
                archive.seek(offset)
                entry = json.loads(archive.readline())
                if isinstance(entry, dict) and "item_id" not in entry:
                    entry["item_id"] = -(offset + 1)
        elif re.fullmatch(r"[0-9]{20}-[0-9a-f]{32}\.json", ref):
            entry = json.loads((_path(db_path).with_suffix(".rejected.d") / ref).read_bytes())
        else:
            raise OutboxError(f"invalid archive reference for quarantined batch {item_id}")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise OutboxError(f"archive missing or damaged for quarantined batch {item_id}") from exc
    if (
        not isinstance(entry, dict)
        or entry.get("item_id") != item_id
        or tuple(entry.get(key) for key in ("destination", "user_id", "harness", "session_id")) != identity
    ):
        raise OutboxError(f"archive identity mismatch for quarantined batch {item_id}")
    return entry


def _archive_entry(record: dict, db_path: Path | None) -> Path:
    """Write a complete owner-only entry before the SQLite deletion transaction."""
    directory = _path(db_path).with_suffix(".rejected.d")
    if not directory.exists():
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _sync_directory(directory.parent)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as archive:
            archive.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
            archive.flush()
            os.fsync(archive.fileno())
        target = directory / f"{time.time_ns():020d}-{uuid.uuid4().hex}.json"
        os.replace(temporary, target)
        _sync_directory(directory)
        return target
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def quarantine(item: OutboxItem, reason: str, *, db_path: Path | None = None) -> Path | None:
    """Preserve a permanently rejected batch outside the delivery queue."""
    record = {
        "item_id": item.id,
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
    path = _archive_entry(record, db_path)
    # Archive first: failed CAS publications are orphans, never recovery candidates.
    conn = _connect(db_path)
    try:
        # Free the payload pages within the same transaction before allocating
        # block state, so a full active outbox can still quarantine the batch.
        deleted = conn.execute(
            "DELETE FROM session_outbox WHERE id = ? AND payload = ? AND end_offset = ? AND final = ?",
            (item.id, item.payload_snapshot, item.end_offset, int(item.final)),
        ).rowcount
        if deleted:
            conn.execute("DELETE FROM session_outbox_backoff WHERE item_id = ?", (item.id,))
            conn.execute(
                "INSERT OR IGNORE INTO session_outbox_rejected "
                "(item_id, destination, user_id, harness, session_id, archive_ref) VALUES (?, ?, ?, ?, ?, ?)",
                (item.id, item.destination, item.user_id, item.harness, item.session_id, path.name),
            )
        conn.commit()
    finally:
        conn.close()
    return path if deleted else None


def recover_quarantined(item_id: int, *, db_path: Path | None = None, payload: dict | None = None) -> int:
    """Explicitly replay a quarantined batch (optionally corrected), then unblock its session.

    The original payload remains in the owner-only archive for audit and manual recovery.
    A failed enqueue leaves the persistent block intact; repeating a completed recovery
    does not replay an already acknowledged batch.
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT destination, user_id, harness, session_id, recovered, archive_ref "
            "FROM session_outbox_rejected WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise OutboxError(f"no quarantined batch {item_id}")
        if row[4]:
            raise OutboxError(f"quarantined batch {item_id} already recovered")
        record = _selected_archive(item_id, row[5], tuple(row[:4]), db_path)
        candidate = payload if payload is not None else record["payload"]
        original = record["payload"]
        if (
            candidate.get("harness"),
            candidate.get("session_id"),
            candidate.get("start_offset"),
            len(candidate.get("lines") or []),
        ) != (row[2], row[3], original.get("start_offset"), len(original.get("lines") or [])):
            raise OutboxConflictError("recovery must retain the original session and source range")
        restored = enqueue(
            candidate, destination=row[0], user_id=row[1], checkpoint_key=record["checkpoint_key"], db_path=db_path
        )
        conn.execute(
            "UPDATE session_outbox_rejected SET recovered = 1, restored_id = ? WHERE item_id = ?",
            (restored, item_id),
        )
        conn.commit()
        return restored
    finally:
        conn.close()


def accept_item(item: OutboxItem, *, db_path: Path | None = None) -> bool:
    """Remove one posted batch when an audit rewinds the contiguous checkpoint."""
    conn = _connect(db_path)
    try:
        deleted = conn.execute(
            "DELETE FROM session_outbox WHERE id = ? AND payload = ? AND end_offset = ? AND final = ?",
            (item.id, item.payload_snapshot, item.end_offset, int(item.final)),
        ).rowcount
        if deleted:
            conn.execute("DELETE FROM session_outbox_backoff WHERE item_id = ?", (item.id,))
            conn.execute(
                "DELETE FROM session_outbox_rejected WHERE item_id > 0 AND recovered = 1 AND restored_id = ?",
                (item.id,),
            )
        conn.commit()
        return bool(deleted)
    finally:
        conn.close()


def acknowledge(
    *,
    destination: str,
    user_id: str,
    harness: str,
    session_id: str,
    acknowledged_line: int,
    posted_metadata_id: int | None = None,
    posted_item_id: int | None = None,
    posted_item: OutboxItem | None = None,
    db_path: Path | None = None,
) -> int:
    """Delete acknowledged source batches and only metadata that was itself posted."""
    if posted_item is not None:
        posted_item_id = posted_item.id
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "DELETE FROM session_outbox WHERE destination = ? AND user_id = ? AND harness = ? "
            "AND session_id = ? AND ((? IS NOT NULL AND id = ? AND payload = ? AND end_offset = ? AND final = ?) OR "
            "(? IS NULL AND ((end_line <= ? AND end_line >= start_line) OR id = ?)))",
            (
                destination.rstrip("/"),
                user_id,
                harness,
                session_id,
                posted_item_id,
                posted_item_id,
                posted_item.payload_snapshot if posted_item else None,
                posted_item.end_offset if posted_item else None,
                int(posted_item.final) if posted_item else None,
                posted_item_id,
                acknowledged_line,
                posted_metadata_id,
            ),
        )
        if cursor.rowcount:
            conn.execute("DELETE FROM session_outbox_backoff WHERE item_id NOT IN (SELECT id FROM session_outbox)")
            conn.execute(
                "DELETE FROM session_outbox_rejected WHERE item_id > 0 AND recovered = 1 AND "
                "restored_id NOT IN (SELECT id FROM session_outbox)"
            )
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
        failed_count = int(
            conn.execute("SELECT COUNT(*) FROM session_outbox_rejected WHERE recovered = 0").fetchone()[0]
        )
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
