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
import secrets
import sqlite3
import stat
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
    provenance: dict | None = None
    revision: int = 0


def _path(db_path: Path | None) -> Path:
    return db_path or DB_PATH


def _assert_archive_identity(path: Path, identity: tuple[int, int]) -> None:
    """Refuse to discard originals if the archive name no longer resolves to our file."""
    try:
        info = path.lstat()
    except OSError as exc:
        raise OutboxError("quarantine archive path disappeared") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (info.st_dev, info.st_ino) != identity:
        raise OutboxError("quarantine archive path changed")


def _validate_archive(fd: int) -> None:
    """Reject any damaged existing JSONL before appending (under SQLite's writer lock)."""
    with os.fdopen(os.dup(fd), "rb") as archive:
        archive.seek(0)
        for line_number, line in enumerate(archive, 1):
            if not line.endswith(b"\n"):
                raise OutboxError(
                    f"quarantine archive has an incomplete line {line_number}; manual repair required: "
                    "back up the archive and database, then inspect and repair the archive before retrying"
                )
            try:
                record = json.loads(line.decode("utf-8"))
                if not isinstance(record, dict):
                    raise ValueError("archive record must be a JSON object")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise OutboxError(
                    f"quarantine archive has malformed JSON on line {line_number}; manual repair required: "
                    "back up the archive and database, then inspect and repair the archive before retrying"
                ) from exc


def _archive_os_name() -> str:
    return os.name


def _secure_archive_flags() -> tuple[int, int]:
    """Refuse archives where no-follow, private mode, or directory durability is unverified."""
    # Windows os.open cannot enforce no-follow or private ACLs, and fsync on a
    # directory descriptor is not a verified durability primitive there. Do not
    # delete the SQLite originals on an unverified best-effort archive.
    if _archive_os_name() == "nt":
        raise OutboxError(
            "terminal quarantine archive is unsupported on Windows: cannot verify no-follow, "
            "private ACLs and directory durability; keep the outbox database and quarantine "
            "on a supported POSIX host, or preserve the database for manual recovery"
        )
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if not isinstance(nofollow, int) or not isinstance(directory, int) or not callable(getattr(os, "fchmod", None)):
        raise OutboxError(
            "terminal quarantine archive requires O_NOFOLLOW, O_DIRECTORY and fchmod; "
            "keep the outbox database and use a supported POSIX host for quarantine"
        )
    return os.O_RDWR | os.O_APPEND | os.O_CREAT | nofollow, os.O_RDONLY | directory


def _archive_rows(path: Path, rows: list[dict]) -> tuple[tuple[int, int], int]:
    """Append originals and keep the inode open until the SQLite change finishes."""
    flags, directory_flags = _secure_archive_flags()
    try:
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OutboxError("unsafe quarantine archive path")
            identity = (info.st_dev, info.st_ino)
            # The caller already holds BEGIN IMMEDIATE. A previous process may
            # have died mid-write without entering our OSError rollback path.
            _validate_archive(fd)
            offset = info.st_size
            try:
                os.fchmod(fd, 0o600)
                for row in rows:
                    data = (json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
                    while data:
                        written = os.write(fd, data)
                        if written == 0:
                            raise OSError("zero-byte quarantine archive write")
                        data = data[written:]
                os.fsync(fd)
                # A newly created archive must survive a crash before SQLite commits.
                directory = os.open(path.parent, directory_flags)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                # Fsync on an open descriptor also succeeds after its name is
                # unlinked. Confirm the destination still names that regular file.
                _assert_archive_identity(path, identity)
            except OSError as exc:
                # The caller holds SQLite's writer lock. Remove an incomplete JSONL
                # suffix before allowing any later archive attempt to append.
                try:
                    os.ftruncate(fd, offset)
                    os.fsync(fd)
                except OSError as rollback_exc:
                    raise OutboxError("cannot restore quarantine archive after failed append") from rollback_exc
                raise OutboxError("cannot durably append quarantine archive") from exc
            # Keep a descriptor alive so unlink/relink cannot recycle this inode
            # between the append and the pre-commit path checks.
            return identity, os.dup(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        raise OutboxError("cannot durably append quarantine archive") from exc


def _connect(db_path: Path | None = None, max_bytes: int = MAX_OUTBOX_BYTES) -> sqlite3.Connection:
    path = _path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    archive_fd: int | None = None
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute("BEGIN IMMEDIATE")
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
        columns = {row[1] for row in conn.execute("PRAGMA table_info(session_outbox)")}
        if "provenance" not in columns:
            conn.execute("ALTER TABLE session_outbox ADD COLUMN provenance TEXT")
        if "revision" not in columns:
            conn.execute("ALTER TABLE session_outbox ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS session_outbox_holds (
                destination TEXT NOT NULL, user_id TEXT NOT NULL, harness TEXT NOT NULL,
                session_id TEXT NOT NULL, reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (destination, user_id, harness, session_id)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS session_outbox_inflight (
                destination TEXT NOT NULL, user_id TEXT NOT NULL, harness TEXT NOT NULL,
                session_id TEXT NOT NULL, claim_token TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (destination, user_id, harness, session_id)
            )"""
        )
        # Upgrade early terminal archives without discarding originals. Concurrent
        # connections serialize this migration through SQLite's write lock.
        terminal_columns = {r[1] for r in conn.execute("PRAGMA table_info(session_outbox_terminal)")}
        legacy: list[dict] = []
        if "payload" in terminal_columns:
            names = [r[1] for r in conn.execute("PRAGMA table_info(session_outbox_terminal)")]
            legacy = [
                dict(zip(names, row, strict=True)) for row in conn.execute("SELECT * FROM session_outbox_terminal")
            ]
            if legacy:
                archive_path = path.with_suffix(".quarantined.jsonl")
                archive_identity, archive_fd = _archive_rows(archive_path, legacy)
                _assert_archive_identity(archive_path, archive_identity)
            conn.execute("DROP TABLE session_outbox_terminal")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS session_outbox_terminal (
                destination TEXT NOT NULL, user_id TEXT NOT NULL, harness TEXT NOT NULL,
                session_id TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
                archived_rows INTEGER NOT NULL,
                quarantined_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (destination, user_id, harness, session_id)
            )"""
        )
        if legacy:
            for row in legacy:
                conn.execute(
                    "INSERT INTO session_outbox_terminal "
                    "(destination, user_id, harness, session_id, status, reason, archived_rows, quarantined_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, ?) "
                    "ON CONFLICT(destination, user_id, harness, session_id) "
                    "DO UPDATE SET archived_rows = archived_rows + 1",
                    (
                        row["destination"],
                        row["user_id"],
                        row["harness"],
                        row["session_id"],
                        row["status"],
                        row["reason"],
                        row["quarantined_at"],
                    ),
                )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_outbox_pending ON session_outbox(destination, user_id, id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_outbox_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_sync TEXT
            )
            """
        )
        if legacy:
            _assert_archive_identity(archive_path, archive_identity)
        conn.commit()
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        conn.execute(f"PRAGMA max_page_count = {max(1, max_bytes // page_size)}")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return conn
    except (sqlite3.Error, OutboxError) as exc:
        if "conn" in locals():
            conn.rollback()
            conn.close()
        if isinstance(exc, OutboxError):
            raise
        raise OutboxError("cannot open durable telemetry outbox") from exc
    finally:
        if archive_fd is not None:
            os.close(archive_fd)


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
    provenance: dict | None = None,
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

    # Every replacement must be independently validated; a previous proof
    # cannot be carried forward to a new payload with different identity.
    if provenance is not None:
        from observal_cli.harness import ensure_loaded, get_adapter

        ensure_loaded()
        validator = getattr(get_adapter(harness), "validate_outbox_provenance", None)
        candidate = OutboxItem(
            0,
            *key[:4],
            checkpoint_key or session_id,
            start_line,
            end_line,
            end_offset,
            final,
            payload,
            0,
            provenance,
        )
        if validator is not None and not validator(candidate):
            raise OutboxConflictError("invalid session provenance")
    conn = _connect(db_path, max_bytes=max_bytes)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _terminal(conn, *key[:4]):
            raise OutboxConflictError(f"session {harness}/{session_id} is terminally quarantined")
        if _held(conn, *key[:4]):
            raise OutboxConflictError(f"session {harness}/{session_id} is held for review")
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
                    start_line, end_line, end_offset, final, payload, records_hash, provenance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(destination, user_id, harness, session_id, start_line, end_line)
                DO UPDATE SET
                    checkpoint_key = CASE
                        WHEN excluded.final = 1 OR (excluded.end_line < excluded.start_line AND session_outbox.final = 0)
                        THEN excluded.checkpoint_key ELSE session_outbox.checkpoint_key END,
                    end_offset = CASE
                        WHEN excluded.final = 1 OR (excluded.end_line < excluded.start_line AND session_outbox.final = 0)
                        THEN excluded.end_offset ELSE session_outbox.end_offset END,
                    final = max(session_outbox.final, excluded.final),
                    payload = CASE
                        WHEN excluded.final = 1 OR (excluded.end_line < excluded.start_line AND session_outbox.final = 0)
                        THEN excluded.payload ELSE session_outbox.payload END,
                    provenance = CASE
                        WHEN excluded.final = 1 OR (excluded.end_line < excluded.start_line AND session_outbox.final = 0)
                        THEN excluded.provenance ELSE session_outbox.provenance END,
                    revision = session_outbox.revision + 1
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
                    json.dumps(provenance) if provenance is not None else None,
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
) -> list[OutboxItem]:
    """Return unheld, unclaimed batches oldest session first, each session in line order.

    Requeued earlier lines must precede later lines even when their row ids are newer.
    Attempts never make a row terminal.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            # Group once per session while excluding held sessions; retain source-line order
            # even if an earlier range was requeued after a later one.
            "WITH firsts AS (SELECT harness, session_id, MIN(id) AS first_id FROM session_outbox "
            "WHERE destination = ? AND user_id = ? GROUP BY harness, session_id) "
            "SELECT item.id, item.destination, item.user_id, item.harness, item.session_id, "
            "item.checkpoint_key, item.start_line, item.end_line, item.end_offset, item.final, "
            "item.payload, item.attempts, item.provenance, item.revision "
            "FROM session_outbox AS item JOIN firsts "
            "ON firsts.harness = item.harness AND firsts.session_id = item.session_id "
            "WHERE item.destination = ? AND item.user_id = ? "
            "AND NOT EXISTS (SELECT 1 FROM session_outbox_holds AS h WHERE "
            "h.destination = item.destination AND h.user_id = item.user_id AND h.harness = item.harness "
            "AND h.session_id = item.session_id) "
            "AND NOT EXISTS (SELECT 1 FROM session_outbox_inflight AS i WHERE "
            "i.destination = item.destination AND i.user_id = item.user_id AND i.harness = item.harness "
            "AND i.session_id = item.session_id) "
            "ORDER BY (item.end_line < item.start_line), firsts.first_id, item.start_line, item.id LIMIT ?",
            (destination.rstrip("/"), user_id, destination.rstrip("/"), user_id, limit),
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
                provenance=json.loads(row[12]) if row[12] else None,
                revision=int(row[13]),
            )
            for row in rows
        ]
    finally:
        conn.close()


def _held(conn: sqlite3.Connection, destination: str, user_id: str, harness: str, session_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM session_outbox_holds WHERE destination = ? AND user_id = ? AND harness = ? AND session_id = ?",
            (destination, user_id, harness, session_id),
        ).fetchone()
        is not None
    )


def _inflight(conn: sqlite3.Connection, destination: str, user_id: str, harness: str, session_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM session_outbox_inflight WHERE destination = ? AND user_id = ? AND harness = ? AND session_id = ?",
            (destination, user_id, harness, session_id),
        ).fetchone()
        is not None
    )


def _terminal(conn: sqlite3.Connection, destination: str, user_id: str, harness: str, session_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM session_outbox_terminal WHERE destination = ? AND user_id = ? AND harness = ? AND session_id = ?",
            (destination, user_id, harness, session_id),
        ).fetchone()
        is not None
    )


def is_terminal(destination: str, user_id: str, harness: str, session_id: str, *, db_path: Path | None = None) -> bool:
    conn = _connect(db_path)
    try:
        return _terminal(conn, destination.rstrip("/"), user_id, harness, session_id)
    finally:
        conn.close()


def is_held(destination: str, user_id: str, harness: str, session_id: str, *, db_path: Path | None = None) -> bool:
    conn = _connect(db_path)
    try:
        return _held(conn, destination.rstrip("/"), user_id, harness, session_id)
    finally:
        conn.close()


def hold_if_current(item: OutboxItem, reason: str, *, db_path: Path | None = None) -> bool:
    """Hold the entire session only if the inspected payload and proof still match."""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT revision FROM session_outbox WHERE id = ?", (item.id,)).fetchone()
        if not current or current[0] != item.revision:
            conn.rollback()
            return False
        conn.execute(
            "INSERT OR IGNORE INTO session_outbox_holds (destination, user_id, harness, session_id, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (item.destination, item.user_id, item.harness, item.session_id, reason),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def claim_for_delivery(item: OutboxItem, *, db_path: Path | None = None) -> str | None:
    """Claim a session across processes before posting; stale claims require manual investigation.

    No automatic expiry: a crashed sender may have sent the request before dying.
    """
    conn = _connect(db_path)
    key = (item.destination, item.user_id, item.harness, item.session_id)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT revision FROM session_outbox WHERE id = ?", (item.id,)).fetchone()
        if not row or row[0] != item.revision or _held(conn, *key) or _terminal(conn, *key):
            return None
        token = secrets.token_hex(16)
        inserted = conn.execute(
            "INSERT OR IGNORE INTO session_outbox_inflight "
            "(destination, user_id, harness, session_id, claim_token) VALUES (?, ?, ?, ?, ?)",
            (*key, token),
        )
        if inserted.rowcount != 1:
            return None
        conn.commit()
        return token
    finally:
        conn.close()


def release_delivery_claim(item: OutboxItem, token: str, *, db_path: Path | None = None) -> None:
    """Only the sender owning the claim can clear it, after posting/ack handling."""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM session_outbox_inflight WHERE destination = ? AND user_id = ? "
            "AND harness = ? AND session_id = ? AND claim_token = ?",
            (item.destination, item.user_id, item.harness, item.session_id, token),
        )
        conn.commit()
    finally:
        conn.close()


def revision_is_current(item: OutboxItem, *, db_path: Path | None = None) -> bool:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT revision FROM session_outbox WHERE id = ?", (item.id,)).fetchone()
        return bool(
            row
            and row[0] == item.revision
            and not _held(conn, item.destination, item.user_id, item.harness, item.session_id)
        )
    finally:
        conn.close()


def has_holds(destination: str, user_id: str, *, db_path: Path | None = None) -> bool:
    conn = _connect(db_path)
    try:
        return (
            conn.execute(
                "SELECT 1 FROM session_outbox_holds WHERE destination = ? AND user_id = ? LIMIT 1",
                (destination.rstrip("/"), user_id),
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def has_inflight(destination: str, user_id: str, *, db_path: Path | None = None) -> bool:
    """A claimed session is not yet known to be delivered, even if no row is selectable."""
    conn = _connect(db_path)
    try:
        return (
            conn.execute(
                "SELECT 1 FROM session_outbox_inflight WHERE destination = ? AND user_id = ? LIMIT 1",
                (destination.rstrip("/"), user_id),
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def session_delivered(
    destination: str, user_id: str, harness: str, session_id: str, *, db_path: Path | None = None
) -> bool:
    """A different held or pending session cannot block this source's completion."""
    conn = _connect(db_path)
    try:
        key = (destination.rstrip("/"), user_id, harness, session_id)
        return (
            not _held(conn, *key)
            and not _terminal(conn, *key)
            and conn.execute(
                "SELECT 1 FROM session_outbox WHERE destination = ? AND user_id = ? "
                "AND harness = ? AND session_id = ? LIMIT 1",
                key,
            ).fetchone()
            is None
        )
    finally:
        conn.close()


def held_sessions(*, db_path: Path | None = None) -> list[tuple[str, str, str, str, str]]:
    """Expose durable holds for explicit operator review."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT destination, user_id, harness, session_id, reason FROM session_outbox_holds ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()


def quarantine_held_session(
    destination: str, user_id: str, harness: str, session_id: str, *, db_path: Path | None = None
) -> int:
    """Atomically preserve all held rows, then remove their hold and queued copies.

    Only an explicit operator action calls this; archived rows are never replayed.
    """
    if harness != "codex" or not session_id or not destination or not user_id:
        raise OutboxConflictError("quarantine requires a Codex session and authenticated destination/user")
    destination = destination.rstrip("/")
    key = (destination, user_id, harness, session_id)
    conn = _connect(db_path)
    archive_fd: int | None = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        hold = conn.execute(
            "SELECT reason FROM session_outbox_holds WHERE destination = ? AND user_id = ? "
            "AND harness = ? AND session_id = ?",
            key,
        ).fetchone()
        if not hold:
            raise OutboxConflictError("session is not held")
        if conn.execute(
            "SELECT 1 FROM session_outbox_inflight WHERE destination = ? AND user_id = ? "
            "AND harness = ? AND session_id = ?",
            key,
        ).fetchone():
            raise OutboxConflictError(
                "session has an in-flight delivery claim; wait for the sender to finish, or if it crashed, "
                "investigate server delivery and preserve the database for manual recovery"
            )
        names = [r[1] for r in conn.execute("PRAGMA table_info(session_outbox)")]
        rows = [
            dict(zip(names, row, strict=True))
            for row in conn.execute(
                "SELECT * FROM session_outbox WHERE destination = ? AND user_id = ? "
                "AND harness = ? AND session_id = ? ORDER BY id",
                key,
            )
        ]
        count = len(rows)
        if not count:
            raise OutboxConflictError("held session has no queued rows")
        archive_path = _path(db_path).with_suffix(".quarantined.jsonl")
        archive_identity, archive_fd = _archive_rows(
            archive_path,
            [{**row, "status": "quarantined", "reason": hold[0]} for row in rows],
        )
        conn.execute(
            "INSERT INTO session_outbox_terminal (destination, user_id, harness, session_id, status, reason, archived_rows) "
            "VALUES (?, ?, ?, ?, 'quarantined', ?, ?)",
            (*key, hold[0], count),
        )
        _assert_archive_identity(archive_path, archive_identity)
        deleted = conn.execute(
            "DELETE FROM session_outbox WHERE destination = ? AND user_id = ? AND harness = ? AND session_id = ?",
            key,
        )
        if deleted.rowcount != count:
            raise OutboxConflictError("session changed during quarantine")
        conn.execute(
            "DELETE FROM session_outbox_holds WHERE destination = ? AND user_id = ? AND harness = ? AND session_id = ?",
            key,
        )
        _assert_archive_identity(archive_path, archive_identity)
        conn.commit()
        return count
    except sqlite3.Error as exc:
        raise OutboxError("cannot durably quarantine held session") from exc
    finally:
        conn.close()
        if archive_fd is not None:
            os.close(archive_fd)


def review_hold(
    destination: str,
    user_id: str,
    harness: str,
    session_id: str,
    corrections: dict[int, tuple[dict, dict]],
    *,
    db_path: Path | None = None,
) -> None:
    """Atomically correct every held range before releasing it for replay.

    An operator must supply independently verified payload and provenance for
    *all* rows. This API does not infer historical identity from a directory.
    """
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    destination = destination.rstrip("/")

    def snapshot(conn: sqlite3.Connection) -> list[tuple]:
        return conn.execute(
            "SELECT id, checkpoint_key, start_line, end_line, end_offset, final, payload, attempts, revision "
            "FROM session_outbox WHERE destination = ? AND user_id = ? AND harness = ? AND session_id = ? ORDER BY id",
            (destination, user_id, harness, session_id),
        ).fetchall()

    conn = _connect(db_path)
    try:
        if not _held(conn, destination, user_id, harness, session_id):
            raise OutboxConflictError("session is not held")
        rows = snapshot(conn)
        if not rows or {row[0] for row in rows} != set(corrections):
            raise OutboxConflictError("review must cover every pending range")
        validator = getattr(get_adapter(harness), "validate_outbox_provenance", None)
        updates = []
        for row in rows:
            payload, proof = corrections[row[0]]
            original = json.loads(row[6])
            if {key: value for key, value in payload.items() if key not in ("agent_id", "agent_version")} != {
                key: value for key, value in original.items() if key not in ("agent_id", "agent_version")
            }:
                raise OutboxConflictError("review may change only agent identity")
            item = OutboxItem(
                row[0],
                destination,
                user_id,
                harness,
                session_id,
                row[1],
                row[2],
                row[3],
                row[4],
                bool(row[5]),
                payload,
                row[7],
                proof,
                row[8],
            )
            if validator is not None and not validator(item):
                raise OutboxConflictError("unverified review provenance")
            updates.append((json.dumps(payload, separators=(",", ":"), ensure_ascii=False), json.dumps(proof), row[0]))
        conn.execute("BEGIN IMMEDIATE")
        key = (destination, user_id, harness, session_id)
        if _inflight(conn, *key):
            raise OutboxConflictError("session has an in-flight delivery claim; wait for the sender to finish")
        if not _held(conn, *key) or snapshot(conn) != rows:
            raise OutboxConflictError("held session changed during review")
        for update in updates:
            conn.execute(
                "UPDATE session_outbox SET payload = ?, provenance = ?, revision = revision + 1 WHERE id = ?",
                update,
            )
        conn.execute(
            "DELETE FROM session_outbox_holds WHERE destination = ? AND user_id = ? AND harness = ? AND session_id = ?",
            (destination, user_id, harness, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def review_unattributed(
    destination: str,
    user_id: str,
    harness: str,
    session_id: str,
    source,
    *,
    db_path: Path | None = None,
) -> None:
    """Explicitly release a held session as unattributed after verifying each source range."""
    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    adapter = get_adapter(harness)
    if harness != "codex" or source.session_id != session_id or source.path is None:
        raise OutboxConflictError("review requires an exact Codex rollout")
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, payload FROM session_outbox WHERE destination = ? AND user_id = ? "
            "AND harness = ? AND session_id = ?",
            (destination.rstrip("/"), user_id, harness, session_id),
        ).fetchall()
    finally:
        conn.close()
    corrections = {}
    for row_id, raw in rows:
        payload = json.loads(raw)
        payload["agent_id"] = payload["agent_version"] = None
        proof = adapter.outbox_provenance(source, payload)
        if proof is None:
            raise OutboxConflictError("cannot verify source attribution")
        corrections[row_id] = payload, proof
    review_hold(destination, user_id, harness, session_id, corrections, db_path=db_path)


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


def quarantine(item: OutboxItem, reason: str, *, db_path: Path | None = None) -> Path | None:
    """Quarantine only the current unheld snapshot, atomically with queue removal."""
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
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT revision, destination, user_id, harness, session_id FROM session_outbox WHERE id = ?", (item.id,)
        ).fetchone()
        if (
            not current
            or current != (item.revision, item.destination, item.user_id, item.harness, item.session_id)
            or _held(conn, item.destination, item.user_id, item.harness, item.session_id)
        ):
            conn.rollback()
            return None
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "ab") as file:
            file.write((json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode())
            file.flush()
            os.fsync(file.fileno())
        conn.execute("DELETE FROM session_outbox WHERE id = ? AND revision = ?", (item.id, item.revision))
        conn.commit()
        return path
    finally:
        conn.close()


def accept_item(item_id: int, *, db_path: Path | None = None, revision: int | None = None) -> bool:
    """Remove a posted snapshot, never a replacement written after it was read."""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "DELETE FROM session_outbox WHERE id = ? AND (? IS NULL OR revision = ?) "
            "AND NOT EXISTS (SELECT 1 FROM session_outbox_holds AS h WHERE "
            "h.destination = session_outbox.destination AND h.user_id = session_outbox.user_id "
            "AND h.harness = session_outbox.harness AND h.session_id = session_outbox.session_id)",
            (item_id, revision, revision),
        )
        conn.commit()
        return bool(cursor.rowcount)
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
    expected_item: OutboxItem,
) -> int:
    """Delete only the posted, validated revision; a high server checkpoint proves nothing about other rows."""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if (
            expected_item.destination != destination.rstrip("/")
            or expected_item.user_id != user_id
            or expected_item.harness != harness
            or expected_item.session_id != session_id
            or expected_item.end_line > acknowledged_line
            or (expected_item.end_line < expected_item.start_line and not include_metadata)
            or _held(conn, destination.rstrip("/"), user_id, harness, session_id)
        ):
            conn.rollback()
            return 0
        cursor = conn.execute(
            "DELETE FROM session_outbox WHERE id = ? AND destination = ? AND user_id = ? AND harness = ? "
            "AND session_id = ? AND revision = ?",
            (expected_item.id, destination.rstrip("/"), user_id, harness, session_id, expected_item.revision),
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
        terminal_count, archived_rows = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(archived_rows), 0) FROM session_outbox_terminal"
        ).fetchone()
        inflight_count = int(conn.execute("SELECT COUNT(*) FROM session_outbox_inflight").fetchone()[0])
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
            "total": pending_count + failed_count + terminal_count,
            "terminal_sessions": terminal_count,
            "inflight_sessions": inflight_count,
            "archived_rows": archived_rows,
            "archive_path": str(_path(db_path).with_suffix(".quarantined.jsonl")) if terminal_count else None,
            "oldest_pending": oldest[0] if oldest else None,
            "last_sync": last_sync[0] if last_sync else None,
            "bytes": _used_bytes(conn),
        }
    finally:
        conn.close()
