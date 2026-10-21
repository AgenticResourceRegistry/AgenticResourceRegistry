# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared IO primitives for all harness session push scripts.

These functions are harness-agnostic and handle config loading, offset
tracking, line reading, durable spooling, acknowledged delivery, and error
logging. Hooks, background recovery, and public reconcile all use this engine.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as optic

if TYPE_CHECKING:
    from collections.abc import Callable

    from observal_cli.harness import SessionSource
    from observal_cli.telemetry_buffer import OutboxItem

# ---------------------------------------------------------------------------
# Offset / cursor state
# ---------------------------------------------------------------------------


def read_cursor_status(session_id: str, home: Path | None = None) -> tuple[int, int, bool, bool]:
    """Return byte offset, line count, finality, and whether local state is valid."""
    if home is None:
        home = Path.home()
    state_file = home / ".observal" / "sync_state.json"
    if state_file.exists():
        try:
            entry = json.loads(state_file.read_text()).get(session_id)
            if not isinstance(entry, dict):
                return 0, 0, False, False
            offset = entry.get("offset")
            line_count = entry.get("line_count")
            if not isinstance(offset, int) or not isinstance(line_count, int) or offset < 0 or line_count < 0:
                return 0, 0, False, False
            return offset, line_count, bool(entry.get("finalized")), True
        except Exception:
            pass
    return 0, 0, False, False


def read_cursor_state(session_id: str, home: Path | None = None) -> tuple[int, int, bool]:
    """Return byte offset, line count, and finality for one local source."""
    offset, line_count, finalized, _valid = read_cursor_status(session_id, home=home)
    return offset, line_count, finalized


def read_cursor(session_id: str, home: Path | None = None) -> tuple[int, int]:
    """Return (byte_offset, line_count) for *session_id* from sync_state.json."""
    offset, line_count, _finalized = read_cursor_state(session_id, home=home)
    return offset, line_count


def write_cursor(
    session_id: str,
    offset: int,
    line_count: int,
    finalized: bool = False,
    home: Path | None = None,
    preserve_finalized: bool = True,
) -> None:
    """Persist updated byte offset and line count for *session_id*.

    ``finalized=True`` marks that the Stop hook completed (or crash recovery
    ran) so the scanner will skip this session.
    """
    if home is None:
        home = Path.home()
    sync_dir = home / ".observal"
    sync_dir.mkdir(parents=True, exist_ok=True)
    state_file = sync_dir / "sync_state.json"

    data: dict = {}
    if state_file.exists():
        try:
            loaded = json.loads(state_file.read_text())
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            pass

    entry: dict = {"offset": offset, "line_count": line_count}
    previous = data.get(session_id)
    if finalized or (preserve_finalized and isinstance(previous, dict) and previous.get("finalized")):
        entry["finalized"] = True
    data[session_id] = entry
    with tempfile.NamedTemporaryFile("w", dir=sync_dir, delete=False) as temporary:
        temporary.write(json.dumps(data))
    Path(temporary.name).replace(state_file)


# ---------------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------------


# The server rejects a session line longer than this (ingest MAX_TEXT_LENGTH).
MAX_LINE_CHARS = 1_048_576


def _omit_long_strings(value, limit: int):
    if isinstance(value, str):
        return value if len(value) <= limit else f"[observal: {len(value)} characters omitted]"
    if isinstance(value, list):
        return [_omit_long_strings(item, limit) for item in value]
    if isinstance(value, dict):
        return {key: _omit_long_strings(item, limit) for key, item in value.items()}
    return value


def fit_line(line: str) -> str:
    """Return *line* short enough for the server to accept.

    A pasted image or a long thinking signature can push one transcript line past
    the server's limit, which rejects the whole batch for good. Long string values
    are replaced with a marker, longest first, so the line stays valid JSON with
    its structure intact. Records are always read through this, so the session
    hash covers exactly the lines that are sent.
    """
    if len(line) <= MAX_LINE_CHARS:
        return line
    try:
        record = json.loads(line)
    except ValueError:
        record = None
    if isinstance(record, (dict, list)):
        limit = 65_536
        while limit >= 64:
            fitted = json.dumps(_omit_long_strings(record, limit), ensure_ascii=False, separators=(",", ":"))
            if len(fitted) <= MAX_LINE_CHARS:
                return fitted
            limit //= 4
    return json.dumps({"observal_omitted_characters": len(line)})


def read_new_records(jsonl_path: Path, offset: int) -> tuple[list[str], list[int], int]:
    """Read complete non-empty records, fitted to the server's line limit, and their absolute end-byte offsets."""
    with open(jsonl_path, "rb") as file:
        file.seek(offset)
        raw = file.read()
    if not raw:
        return [], [], 0

    complete_bytes = len(raw) if raw.endswith(b"\n") else raw.rfind(b"\n") + 1
    if complete_bytes <= 0:
        return [], [], 0

    lines: list[str] = []
    end_offsets: list[int] = []
    absolute_offset = offset
    for encoded_line in raw[:complete_bytes].splitlines(keepends=True):
        absolute_offset += len(encoded_line)
        line = encoded_line.rstrip(b"\r\n").decode("utf-8", errors="replace")
        if line.strip():
            lines.append(fit_line(line))
            end_offsets.append(absolute_offset)
    return lines, end_offsets, complete_bytes


def hash_session_source(jsonl_path: Path) -> tuple[str, int]:
    """Hash complete non-empty source records for final/audit delivery."""
    lines, _offsets, _bytes_read = read_new_records(jsonl_path, 0)
    hasher = hashlib.sha256()
    for line in lines:
        source_hash = hashlib.sha256(line.encode("utf-8", errors="replace")).hexdigest()
        hasher.update(source_hash.encode())
        hasher.update(b"\n")
    return hasher.hexdigest(), len(lines)


def read_new_lines(jsonl_path: Path, offset: int) -> tuple[list[str], int]:
    """Read complete non-empty lines from a byte offset."""
    lines, _end_offsets, bytes_read = read_new_records(jsonl_path, offset)
    return lines, bytes_read


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config(home: Path | None = None) -> dict | None:
    """Read server_url and access_token from ~/.observal/config.json.

    Token priority: api_key (30-day) > access_token (1-hour).
    Returns None when the file is missing or required fields are absent.
    """
    if home is None:
        home = Path.home()
    cfg_file = home / ".observal" / "config.json"
    if not cfg_file.exists():
        return None
    try:
        data = json.loads(cfg_file.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    server_url = data.get("server_url", "").strip()
    access_token = data.get("api_key", "").strip() or data.get("access_token", "").strip()
    if not server_url or not access_token:
        return None
    return {
        "server_url": server_url,
        "access_token": access_token,
        "refresh_token": data.get("refresh_token", "").strip(),
        "user_id": str(data.get("user_id", "")).strip(),
        "_config_path": str(cfg_file),
    }


# ---------------------------------------------------------------------------
# HTTP posting
# ---------------------------------------------------------------------------


# Written next to config.json when the server rejects the stored refresh token
# (revoked by a logout or password change, or expired). Holds a fingerprint of
# that token, so hooks stop retrying it and a new login clears the state.
AUTH_EXPIRED_FILE = "auth_expired"


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def login_expired(home: Path | None = None) -> bool:
    """Whether hooks found the current login revoked or expired."""
    config = load_config(home)
    if not config or not config["refresh_token"]:
        return False
    marker = Path(config["_config_path"]).parent / AUTH_EXPIRED_FILE
    try:
        return marker.read_text().strip() == _token_fingerprint(config["refresh_token"])
    except OSError:
        return False


def _refresh_access_token(server_url: str, refresh_token: str, config_path: str) -> str | None:
    """Use refresh_token to obtain a new access_token and persist it."""
    import httpx

    marker = Path(config_path).parent / AUTH_EXPIRED_FILE
    fingerprint = _token_fingerprint(refresh_token)
    try:
        if marker.read_text().strip() == fingerprint:
            # Already rejected: retrying only burns the server's auth rate limit.
            return None
    except OSError:
        pass

    url = f"{server_url.rstrip('/')}/api/v1/auth/token/refresh"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url, json={"refresh_token": refresh_token})
            if resp.status_code == 401:
                optic.warning("refresh token rejected; session delivery paused until `observal auth login`")
                try:
                    marker.write_text(fingerprint)
                except OSError:
                    pass
                return None
            if resp.status_code >= 300:
                return None
            data = resp.json()
            new_token = data.get("access_token", "")
            if not new_token:
                return None
            cfg_path = Path(config_path)
            try:
                cfg = json.loads(cfg_path.read_text())
                cfg["access_token"] = new_token
                if data.get("refresh_token"):
                    cfg["refresh_token"] = data["refresh_token"]
                cfg_path.write_text(json.dumps(cfg, indent=2))
            except Exception:
                pass
            return new_token
    except Exception:
        return None


class PermanentIngestRejectionError(RuntimeError):
    """A payload rejection that retrying cannot resolve."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"server permanently rejected the payload with status {status_code}")


def post_to_server_ack(
    server_url: str,
    access_token: str,
    payload: dict,
    config: dict | None = None,
) -> dict | None:
    """POST a session batch and return its server acknowledgement."""
    import time

    import httpx

    started = time.perf_counter()
    url = f"{server_url.rstrip('/')}/api/v1/ingest/session"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    session_id = str(payload.get("session_id", "?"))[:12]
    line_count = len(payload.get("lines", []))

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload, headers=headers)
            if response.status_code == 401 and config:
                refresh_token = config.get("refresh_token", "")
                config_path = config.get("_config_path", "")
                if refresh_token and config_path:
                    new_token = _refresh_access_token(server_url, refresh_token, config_path)
                    if new_token:
                        config["access_token"] = new_token
                        headers["Authorization"] = f"Bearer {new_token}"
                        response = client.post(url, json=payload, headers=headers)
            if response.status_code >= 300:
                optic.warning(
                    "ingest POST returned {} for session {}: server rejected the payload",
                    response.status_code,
                    session_id,
                )
                if response.status_code in {400, 409, 413, 415, 422}:
                    raise PermanentIngestRejectionError(response.status_code)
                return None
            data = response.json()
            if not isinstance(data.get("acknowledged_line"), int):
                optic.warning("ingest response for session {} had no acknowledgement", session_id)
                return None
            elapsed = (time.perf_counter() - started) * 1000
            optic.debug("uploaded {} lines for session {} ({:.0f}ms)", line_count, session_id, elapsed)
            return data
    except PermanentIngestRejectionError:
        raise
    except Exception as e:
        elapsed = (time.perf_counter() - started) * 1000
        optic.error(
            "ingest POST failed for session {} after {:.0f}ms: {} - durable outbox retained the records",
            session_id,
            elapsed,
            e,
        )
        return None


def get_server_checkpoint(source: SessionSource, config: dict) -> dict | None:
    """Fetch the authenticated contiguous checkpoint for one session source."""
    import httpx

    server_url = str(config.get("server_url") or "").rstrip("/")
    token = str(config.get("access_token") or "")
    if not server_url or not token:
        return None
    url = f"{server_url}/api/v1/ingest/session/checkpoint"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                url,
                params={"session_id": source.session_id, "harness": source.harness},
                headers=headers,
            )
            if response.status_code == 401:
                refresh_token = str(config.get("refresh_token") or "")
                config_path = str(config.get("_config_path") or "")
                if refresh_token and config_path:
                    token = _refresh_access_token(server_url, refresh_token, config_path) or ""
                    if token:
                        config["access_token"] = token
                        headers["Authorization"] = f"Bearer {token}"
                        response = client.get(
                            url,
                            params={"session_id": source.session_id, "harness": source.harness},
                            headers=headers,
                        )
            if response.status_code >= 300:
                return None
            checkpoint = response.json()
            if not isinstance(checkpoint.get("acknowledged_line"), int):
                return None
            return checkpoint
    except Exception as exc:
        optic.debug("could not recover server checkpoint for {}: {}", source.session_id, exc)
        return None


def _checkpoint_byte_offset(path: Path, line_count: int, server_offset: int) -> int | None:
    """Resolve a server source position to a safe local newline boundary."""
    try:
        size = path.stat().st_size
        if server_offset > 0:
            if server_offset > size:
                return None
            with path.open("rb") as file:
                file.seek(server_offset - 1)
                return server_offset if file.read(1) == b"\n" else None
        if line_count == 0:
            return 0
        seen = 0
        offset = 0
        with path.open("rb") as file:
            for encoded_line in file:
                offset += len(encoded_line)
                if encoded_line.rstrip(b"\r\n").strip():
                    seen += 1
                    if seen == line_count:
                        return offset
    except OSError:
        pass
    return None


def recover_cursor_from_server(
    source: SessionSource,
    config: dict,
    *,
    home: Path | None = None,
    fetch: Callable[[SessionSource, dict], dict | None] | None = None,
) -> tuple[int, int] | None:
    """Replace missing, corrupt, or stale local state with the server checkpoint."""
    if source.path is None:
        return None
    checkpoint = (fetch or get_server_checkpoint)(source, config)
    if checkpoint is None:
        return read_cursor(source.checkpoint_key, home=home)
    acknowledged_line = int(checkpoint["acknowledged_line"])
    line_count = acknowledged_line + 1
    server_offset = int(checkpoint.get("acknowledged_offset") or 0)
    byte_offset = _checkpoint_byte_offset(source.path, line_count, server_offset)
    if byte_offset is None:
        log_error(
            f"server checkpoint does not match local source for {source.harness} session {source.session_id}",
            home=home,
        )
        return None
    write_cursor(
        source.checkpoint_key,
        byte_offset,
        line_count,
        home=home,
        preserve_finalized=False,
    )
    return byte_offset, line_count


MAX_CHUNK_SIZE = 500
# Batch bytes stay under the server's default 10 MB request limit (MAX_REQUEST_SIZE_MB).
MAX_BATCH_BYTES = 8 * 1024 * 1024


def batch_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """Split *lines* into ``(start, end)`` batches under both the line and the byte limit."""
    ranges: list[tuple[int, int]] = []
    start = size = 0
    for index, line in enumerate(lines):
        # Each line travels JSON-encoded inside the request body.
        line_bytes = len(json.dumps(line, ensure_ascii=False).encode("utf-8"))
        if index > start and (index - start >= MAX_CHUNK_SIZE or size + line_bytes > MAX_BATCH_BYTES):
            ranges.append((start, index))
            start, size = index, 0
        size += line_bytes
    if lines:
        ranges.append((start, len(lines)))
    return ranges


# ---------------------------------------------------------------------------
# Durable acknowledged delivery
# ---------------------------------------------------------------------------


def drain_outbox(
    config: dict,
    *,
    home: Path | None = None,
    db_path: Path | None = None,
    post: Callable[[dict, dict], dict | None] | None = None,
    repairs: list[tuple[str, str]] | None = None,
    rejections: list[tuple[str, str, int]] | None = None,
) -> bool:
    """Drain durable batches for the configured server/user until blocked."""
    from observal_cli import telemetry_buffer

    destination = str(config.get("server_url") or "").rstrip("/")
    user_id = str(config.get("user_id") or "")
    if not destination or not user_id:
        return False
    post_batch = post or (
        lambda payload, cfg: post_to_server_ack(
            destination,
            str(cfg.get("access_token") or ""),
            payload,
            config=cfg,
        )
    )

    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    while items := telemetry_buffer.pending(
        destination=destination,
        user_id=user_id,
        limit=1,
        db_path=db_path,
    ):
        item = items[0]
        validator = getattr(get_adapter(item.harness), "validate_outbox_provenance", None)
        if validator is not None and not validator(item):
            telemetry_buffer.hold_if_current(item, "unverified session attribution", db_path=db_path)
            continue
        if not telemetry_buffer.revision_is_current(item, db_path=db_path):
            continue
        claim = telemetry_buffer.claim_for_delivery(item, db_path=db_path)
        if claim is None:
            # Selection raced another sender or a review. Re-select, excluding
            # claimed/held sessions, so other sessions can still make progress.
            continue
        try:
            try:
                acknowledgement = post_batch(item.payload, config)
            except PermanentIngestRejectionError as exc:
                quarantine_path = telemetry_buffer.quarantine(item, str(exc), db_path=db_path)
                if quarantine_path is None:
                    return False
                if rejections is not None:
                    rejections.append((item.harness, item.session_id, exc.status_code))
                log_error(
                    f"quarantined rejected {item.harness} session {item.session_id} batch at {quarantine_path}",
                    home=home,
                )
                continue
            if acknowledgement is not None and not telemetry_buffer.revision_is_current(item, db_path=db_path):
                # The server may have received the previous revision; never erase
                # a concurrently corrected payload or advance its local checkpoint.
                return False
            if acknowledgement is None:
                telemetry_buffer.record_attempt(item.id, db_path=db_path)
                return False
            acknowledged_line = int(acknowledgement["acknowledged_line"])
            acknowledged_offset = int(acknowledgement.get("acknowledged_offset") or 0)
            repair_from_line = acknowledgement.get("repair_from_line")
            if isinstance(repair_from_line, int):
                if not telemetry_buffer.accept_item(item.id, db_path=db_path, revision=item.revision):
                    return False
                write_cursor(
                    item.checkpoint_key,
                    acknowledged_offset,
                    repair_from_line,
                    home=home,
                    preserve_finalized=False,
                )
                if repairs is not None:
                    repairs.append((item.harness, item.session_id))
                return False
            if acknowledged_line < item.end_line:
                if _checkpoint_gap_is_permanent(item, acknowledged_line, acknowledgement, db_path=db_path):
                    # The server stored this batch, but a permanently missing range
                    # prevents its checkpoint from reaching it.
                    if not telemetry_buffer.accept_item(item.id, db_path=db_path, revision=item.revision):
                        return False
                    write_cursor(
                        item.checkpoint_key,
                        item.end_offset,
                        item.end_line + 1,
                        finalized=item.final,
                        home=home,
                    )
                    log_error(
                        f"accepted {item.harness} session {item.session_id} lines {item.start_line}-{item.end_line} "
                        f"past a checkpoint gap after line {acknowledged_line}",
                        home=home,
                    )
                    continue
                return False
            if acknowledged_offset <= 0:
                acknowledged_offset = item.end_offset
            deleted = telemetry_buffer.acknowledge(
                destination=destination,
                user_id=user_id,
                harness=item.harness,
                session_id=item.session_id,
                acknowledged_line=acknowledged_line,
                include_metadata=item.end_line < item.start_line,
                db_path=db_path,
                expected_item=item,
            )
            if not deleted:
                return False
            # An ahead-of-us server checkpoint does not establish that other local
            # rows were reviewed or posted. Advance only across this sent range,
            # without rewinding a cursor already ahead of an older posted batch.
            current_offset, current_lines = read_cursor(item.checkpoint_key, home=home)
            write_cursor(
                item.checkpoint_key,
                max(current_offset, min(acknowledged_offset, item.end_offset)),
                max(current_lines, item.end_line + 1 if item.end_line >= item.start_line else item.start_line),
                finalized=item.final,
                home=home,
            )
        finally:
            telemetry_buffer.release_delivery_claim(item, claim, db_path=db_path)
    return not (
        telemetry_buffer.has_holds(destination, user_id, db_path=db_path)
        or telemetry_buffer.has_inflight(destination, user_id, db_path=db_path)
    )


def _checkpoint_gap_is_permanent(
    item: OutboxItem,
    acknowledged_line: int,
    acknowledgement: dict,
    *,
    db_path: Path | None = None,
) -> bool:
    """Whether the server stored a batch that its checkpoint can never reach."""
    from observal_cli import telemetry_buffer

    if item.end_line < item.start_line or acknowledged_line + 1 >= item.start_line:
        return False
    # The server counts lines it already had and ignorable records as skipped; both are stored.
    handled = int(acknowledgement.get("ingested") or 0) + int(acknowledgement.get("skipped") or 0)
    stored_whole_batch = handled == len(item.payload.get("lines") or [])
    if int(acknowledgement.get("errors") or 0) or not stored_whole_batch:
        return False
    return not telemetry_buffer.holds_line(
        destination=item.destination,
        user_id=item.user_id,
        harness=item.harness,
        session_id=item.session_id,
        line=acknowledged_line + 1,
        db_path=db_path,
    )


def drain_session_source(
    source: SessionSource,
    config: dict,
    *,
    hook_event: str,
    final: bool = False,
    extra_fields: dict | None = None,
    extra_records: tuple[str, ...] = (),
    spool_only: bool = False,
    recover_from_server: bool = False,
    home: Path | None = None,
    db_path: Path | None = None,
    post: Callable[[dict, dict], dict | None] | None = None,
    checkpoint_fetch: Callable[[SessionSource, dict], dict | None] | None = None,
    rejections: list[tuple[str, str, int]] | None = None,
    _repair_attempts: int = 0,
    identity_scope: str = "live",
) -> bool:
    """Spool all complete source records, then deliver them through the outbox."""
    from observal_cli import telemetry_buffer

    destination = str(config.get("server_url") or "").rstrip("/")
    user_id = str(config.get("user_id") or "")
    if source.path is None or not destination or not user_id:
        return False
    if telemetry_buffer.is_terminal(destination, user_id, source.harness, source.session_id, db_path=db_path):
        return False
    if telemetry_buffer.is_held(destination, user_id, source.harness, source.session_id, db_path=db_path):
        return False

    # Keep a per-call boundary even when the caller reuses a report list.
    rejections = rejections if rejections is not None else []
    rejection_start = len(rejections)

    def provenance(payload: dict) -> dict | None:
        from observal_cli.harness import ensure_loaded, get_adapter

        ensure_loaded()
        builder = getattr(get_adapter(source.harness), "outbox_provenance", None)
        return builder(source, payload) if builder is not None else None

    def source_was_rejected() -> bool:
        return any(
            harness == source.harness and session_id == source.session_id
            for harness, session_id, _ in rejections[rejection_start:]
        )

    # A transient pre-drain failure never prevents new local records from being spooled.
    if not spool_only:
        drain_outbox(config, home=home, db_path=db_path, post=post, rejections=rejections)
        if source_was_rejected():
            return False

    # The pre-drain may have quarantined this very source. Never recover its
    # cursor from a server checkpoint or re-read its rollout afterward.
    if telemetry_buffer.is_terminal(destination, user_id, source.harness, source.session_id, db_path=db_path):
        return False
    if recover_from_server:
        recovered = recover_cursor_from_server(source, config, home=home, fetch=checkpoint_fetch)
        if recovered is None:
            return False
        byte_offset, line_count = recovered
    else:
        byte_offset, line_count = read_cursor(source.checkpoint_key, home=home)
    byte_offset, line_count = telemetry_buffer.spooled_checkpoint(
        destination=destination,
        user_id=user_id,
        harness=source.harness,
        session_id=source.session_id,
        checkpoint_key=source.checkpoint_key,
        line_count=line_count,
        byte_offset=byte_offset,
        db_path=db_path,
    )
    session_hash, hashed_line_count = hash_session_source(source.path) if final else (None, None)
    lines, end_byte_offsets, bytes_read = read_new_records(source.path, byte_offset)
    # Sources rebuilt from disk (recovery, reconcile) have no cwd. The payload's layer
    # hash and the uploaded snapshot must both describe the project the transcript
    # records; with none, build_payload sends no hash and nothing is uploaded.
    cwd = source.cwd or (_recorded_cwd(lines, source.path) if hook_event in _DISK_EVENTS else "")
    if extra_records:
        lines.extend(extra_records)
        end_byte_offsets.extend([byte_offset + bytes_read] * len(extra_records))
    if not lines:
        if bytes_read:
            byte_offset += bytes_read
        if extra_fields or final:
            payload = build_payload(
                session_id=source.session_id,
                lines=[],
                start_offset=line_count,
                hook_event=hook_event,
                line_count_before=line_count,
                new_offset=byte_offset,
                cwd=cwd,
                parent_session_id=source.parent_session_id,
                session_jsonl=source.path,
                harness=source.harness,
                identity_scope=identity_scope,
            )
            payload["harness"] = source.harness
            if extra_fields:
                payload.update({key: value for key, value in extra_fields.items() if key != "layer_hash"})
            if final:
                payload["final"] = True
                payload["total_line_count"] = line_count
                payload["total_offset"] = byte_offset
                payload["session_hash"] = session_hash
                payload["hashed_line_count"] = hashed_line_count
            if not spool_only and post is None and payload.get("layer_hash") and config.get("access_token"):
                _maybe_upload_layer_snapshot(
                    destination, str(config["access_token"]), payload["layer_hash"], source.harness, cwd, config
                )
            telemetry_buffer.enqueue(
                payload,
                destination=destination,
                user_id=user_id,
                checkpoint_key=source.checkpoint_key,
                db_path=db_path,
                provenance=provenance(payload),
            )
        if spool_only:
            return True
        repairs: list[tuple[str, str]] = []
        delivered = drain_outbox(
            config,
            home=home,
            db_path=db_path,
            post=post,
            repairs=repairs,
            rejections=rejections,
        )
        if final and (source.harness, source.session_id) in repairs and _repair_attempts < 1:
            return drain_session_source(
                source,
                config,
                hook_event=hook_event,
                final=True,
                extra_fields=extra_fields,
                extra_records=extra_records,
                recover_from_server=recover_from_server,
                home=home,
                db_path=db_path,
                post=post,
                checkpoint_fetch=checkpoint_fetch,
                rejections=rejections,
                _repair_attempts=_repair_attempts + 1,
                identity_scope=identity_scope,
            )
        delivered = telemetry_buffer.session_delivered(
            destination, user_id, source.harness, source.session_id, db_path=db_path
        )
        if bytes_read or (final and delivered and not source_was_rejected()):
            write_cursor(
                source.checkpoint_key,
                byte_offset,
                line_count,
                finalized=final and delivered and not source_was_rejected(),
                home=home,
            )
        return delivered and not source_was_rejected()

    # Attribute trailing blank-line bytes to the last real record checkpoint.
    end_byte_offsets[-1] = byte_offset + bytes_read

    for chunk_start, chunk_end in batch_ranges(lines):
        chunk = lines[chunk_start:chunk_end]
        chunk_end_offsets = end_byte_offsets[chunk_start:chunk_end]
        is_last = chunk_end == len(lines)
        payload = build_payload(
            session_id=source.session_id,
            lines=chunk,
            start_offset=line_count + chunk_start,
            hook_event=hook_event,
            line_count_before=line_count + chunk_start,
            new_offset=byte_offset + bytes_read if is_last else chunk_end_offsets[-1],
            cwd=cwd,
            parent_session_id=source.parent_session_id,
            session_jsonl=source.path,
            harness=source.harness,
            identity_scope=identity_scope,
        )
        payload["harness"] = source.harness
        payload["end_byte_offsets"] = chunk_end_offsets
        if final and is_last:
            payload["final"] = True
            payload["total_line_count"] = line_count + len(lines)
            payload["total_offset"] = byte_offset + bytes_read
            payload["session_hash"] = session_hash
            payload["hashed_line_count"] = hashed_line_count
        else:
            payload.pop("final", None)
            payload.pop("total_line_count", None)
            payload.pop("total_offset", None)
        if extra_fields:
            payload.update({key: value for key, value in extra_fields.items() if key != "layer_hash"})
        if not spool_only and post is None and payload.get("layer_hash") and config.get("access_token"):
            _maybe_upload_layer_snapshot(
                destination, str(config["access_token"]), payload["layer_hash"], source.harness, cwd, config
            )
        telemetry_buffer.enqueue(
            payload,
            destination=destination,
            user_id=user_id,
            checkpoint_key=source.checkpoint_key,
            db_path=db_path,
            provenance=provenance(payload),
        )

    if spool_only:
        return True
    repairs = []
    delivered = drain_outbox(
        config,
        home=home,
        db_path=db_path,
        post=post,
        repairs=repairs,
        rejections=rejections,
    )
    if final and (source.harness, source.session_id) in repairs and _repair_attempts < 1:
        return drain_session_source(
            source,
            config,
            hook_event=hook_event,
            final=True,
            extra_fields=extra_fields,
            extra_records=extra_records,
            recover_from_server=recover_from_server,
            home=home,
            db_path=db_path,
            post=post,
            checkpoint_fetch=checkpoint_fetch,
            rejections=rejections,
            _repair_attempts=_repair_attempts + 1,
            identity_scope=identity_scope,
        )
    return (
        telemetry_buffer.session_delivered(destination, user_id, source.harness, source.session_id, db_path=db_path)
        and not source_was_rejected()
    )


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------


# Events whose sources are rebuilt from files on disk, without the live hook's cwd.
_DISK_EVENTS = frozenset({"CrashRecovery", "Reconcile"})


def _recorded_cwd(lines: list[str], session_jsonl: Path | None, scan_lines: int = 200) -> str:
    """The absolute, existing project directory a transcript's records name, else ``""``."""
    import json
    import os

    def first_cwd(raw_lines) -> str:
        for raw in raw_lines:
            try:
                record = json.loads(raw)
            except (TypeError, ValueError):
                continue
            value = record.get("cwd") if isinstance(record, dict) else None
            if isinstance(value, str) and os.path.isabs(value) and os.path.isdir(value):
                return value
        return ""

    found = first_cwd(lines)
    if found or session_jsonl is None:
        return found
    try:
        with open(session_jsonl, encoding="utf-8", errors="replace") as handle:
            return first_cwd(line for _, line in zip(range(scan_lines), handle, strict=False))
    except OSError:
        return ""


def build_payload(
    session_id: str,
    lines: list[str],
    start_offset: int,
    hook_event: str,
    line_count_before: int,
    new_offset: int = 0,
    cwd: str = "",
    parent_session_id: str | None = None,
    session_jsonl: Path | None = None,
    harness: str = "claude-code",
    identity_scope: str = "live",
) -> dict:
    """Construct the JSON body for the ingest endpoint.

    Defaults harness telemetry to ``claude-code``; callers override ``payload["harness"]``
    for other harnesses.
    """
    recovered_without_cwd = hook_event in _DISK_EVENTS and not cwd
    if recovered_without_cwd:
        # Recovery and reconcile rebuild sources from disk with no cwd. Hashing "no
        # project" would describe only user-scope components, so use the project the
        # transcript records, or send no hash (unknown coverage) rather than a wrong one.
        cwd = _recorded_cwd(lines, session_jsonl)
    agent_id, agent_version = _resolve_agent(
        cwd,
        lines,
        session_jsonl,
        harness=harness,
        session_ids=(session_id, parent_session_id),
        identity_scope=identity_scope,
    )
    layer_hash = None if recovered_without_cwd and not cwd else _get_cached_layer_hash(session_id, cwd, harness)
    payload: dict = {
        "session_id": session_id,
        "harness": "claude-code",
        "agent_id": agent_id,
        "agent_version": agent_version,
        "layer_hash": layer_hash,
        "lines": lines,
        "start_offset": start_offset,
        "hook_event": hook_event,
        "parent_session_id": parent_session_id,
    }
    capabilities = _capabilities_for_session(session_id, cwd, harness, session_jsonl)
    if capabilities:
        payload["capabilities_used"] = capabilities
    if hook_event == "Stop":
        payload["final"] = True
        payload["total_line_count"] = line_count_before + len(lines)
        payload["total_offset"] = new_offset
        _evict_layer_hash_cache(session_id, cwd, harness)
    return payload


_TIMESTAMP_KEYS = ("timestamp", "ts", "time", "created_at", "createdAt", "start_time")
_MAX_CAPABILITIES_PER_PUSH = 200


_START_SCAN_LINES = 20


def _first_line_timestamp(session_jsonl: Path):
    """The earliest timestamp among a transcript's first lines, if any.

    Some harnesses open a transcript with a record that carries no timestamp
    (a summary or snapshot line), so only the first line is not enough.
    """
    import json
    from datetime import UTC, datetime

    found = []
    try:
        with session_jsonl.open("r", encoding="utf-8", errors="replace") as handle:
            lines = [handle.readline() for _ in range(_START_SCAN_LINES)]
    except OSError:
        return None
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        for key in _TIMESTAMP_KEYS:
            value = record.get(key)
            if isinstance(value, int | float) and value > 0:
                found.append(datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=UTC))
                break
            if isinstance(value, str) and value:
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    continue
                found.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC))
                break
    return min(found) if found else None


def _session_started_at(session_jsonl: Path | None):
    """When the session began, or ``None`` when that cannot be established.

    Prefer the file's birth time where the platform records one, then the
    transcript's own first timestamp. ``st_ctime`` is deliberately not used:
    on Linux it moves with every write, which would silently drop uses from
    the start of a long session.
    """
    from datetime import UTC, datetime

    if session_jsonl is None:
        return None
    try:
        birth = getattr(session_jsonl.stat(), "st_birthtime", None)
        if birth:
            return datetime.fromtimestamp(birth, tz=UTC)
    except OSError:
        pass
    return _first_line_timestamp(session_jsonl)


def _capabilities_for_session(session_id: str, cwd: str, harness: str, session_jsonl: Path | None) -> list[dict]:
    """Capability-lock uses that belong to this session, shaped for ingest.

    See ``capability_lock.for_session`` for the attribution rules. Best effort:
    attribution is evidence, so a broken lock file never blocks telemetry.
    """
    try:
        from observal_cli import capability_lock

        matched = capability_lock.for_session(
            session_id=session_id,
            harness=harness,
            cwd=cwd or None,
            started_at=_session_started_at(session_jsonl),
            has_transcript=session_jsonl is not None,
        )
        if not matched:
            return []
        confidence = {id(use): level for use, level in matched}
        latest = capability_lock.dedupe_latest(use for use, _ in matched)
        # The ingest contract caps the list; keep the most recent distinct uses.
        latest = sorted(latest, key=lambda u: u.ts, reverse=True)[:_MAX_CAPABILITIES_PER_PUSH]
        return [
            payload | {"confidence": confidence[id(use)]}
            for use, payload in zip(latest, capability_lock.to_payload(latest, confidence="window"), strict=True)
        ]
    except Exception as exc:
        optic.debug("capability attribution skipped for {}: {}", session_id, exc)
        return []


# A detected mid-session transition makes later unattributable intervals unknown.
_layer_hash_cache: dict[tuple[str, str, str], str | None] = {}


def _get_cached_layer_hash(session_id: str, cwd: str, harness: str) -> str | None:
    """Recheck each scoped session; never mix projects or harnesses sharing an ID."""
    key = (session_id, cwd, harness)
    current = _compute_layer_hash_safe(cwd, harness)
    if key not in _layer_hash_cache:
        _layer_hash_cache[key] = current
    elif _layer_hash_cache[key] != current:
        _layer_hash_cache[key] = None  # Sticky ambiguous transition.
    return _layer_hash_cache[key]


def _evict_layer_hash_cache(session_id: str, cwd: str, harness: str) -> None:
    """Remove only the completed scoped session's hash on Stop."""
    _layer_hash_cache.pop((session_id, cwd, harness), None)


def _compute_layer_hash_safe(cwd: str, harness: str) -> str | None:
    """Compute layer_hash without ever blocking the session push.

    Computes across ALL detected harnesses (not just the session's harness).
    Returns None on any failure.
    """
    try:
        from observal_cli.layer import ensure_local_snapshot

        return ensure_local_snapshot(harness=None, project_dir=cwd or None)
    except Exception:
        return None


def _is_layer_canonical() -> bool | None:
    """Check if the current layer state matches lockfile integrity (canonical).

    Returns True if no drift detected, False if files were modified,
    None if unable to determine.
    """
    try:
        from observal_cli.layer import _compute_drift, _detect_active_harnesses, build_layer_manifest
        from observal_cli.lockfile import read_registry_lockfile

        _, lockfile_data = read_registry_lockfile()
        harnesses_section: dict = {}
        for scan_harness in _detect_active_harnesses():
            harnesses_section[scan_harness] = build_layer_manifest(scan_harness, include_content=False)

        drift = _compute_drift(lockfile_data, harnesses_section)
        return drift["is_canonical"]
    except Exception:
        return None


def _maybe_upload_layer_snapshot(
    server_url: str,
    access_token: str,
    layer_hash: str,
    harness: str,
    cwd: str,
    config: dict | None = None,
) -> None:
    """Upload layer snapshot to server if the hash has changed since last upload.

    Fire-and-forget: never blocks session push on failure.
    Saves the snapshot locally as ~/.observal/layer_snapshot.json (mirror of server).
    """
    try:
        from observal_cli.layer import (
            build_upload_payload,
            needs_upload,
            save_local_snapshot,
            set_last_uploaded_hash,
            was_uploaded_for,
        )

        # Build first, then compare: a changed pin cannot reuse the old advertised hash.
        payload = build_upload_payload(project_dir=cwd or None)
        user_id = str((config or {}).get("user_id") or "")
        if payload["hash"] != layer_hash:
            # The configuration changed after the session advertised its hash. The advertised hash cannot be
            # uploaded (its files are gone), so the session has no snapshot; coverage reports it as such.
            optic.warning("layer snapshot not uploaded: the configuration changed since the session started")
            return
        if not needs_upload(layer_hash) and was_uploaded_for(layer_hash, server_url, user_id):
            return

        # POST to server
        import httpx

        from observal_cli.client import _get_cli_version

        url = f"{server_url.rstrip('/')}/api/v1/layer-snapshots"
        # The server's CLI/server parity check answers 426 to authenticated CLI requests
        # without this header; unlike ingest, this path is not exempt.
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Observal-CLI-Version": _get_cli_version(),
        }

        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload, headers=headers)
            if resp.status_code < 300:
                # Save locally: same content as what server now has
                save_local_snapshot(payload)
                set_last_uploaded_hash(payload["hash"], server_url, user_id)
                optic.debug("layer snapshot uploaded and saved locally: hash={}", payload["hash"])
            else:
                optic.warning("layer snapshot upload failed: status={}", resp.status_code)
    except Exception as e:
        optic.debug("layer snapshot upload skipped: {}", e)


def _resolve_agent(
    cwd: str,
    lines: list[str],
    session_jsonl: Path | None,
    harness: str = "claude-code",
    session_ids: tuple[str | None, ...] = (),
    identity_scope: str = "live",
) -> tuple[str | None, str | None]:
    """Resolve agent identity: delegated child first, then harness adapter, environment, and lockfile."""
    import os

    from observal_cli.delegation.tasks import delegated_agent
    from observal_cli.harness import ensure_loaded, get_adapter

    # A Codex rollout may share a workspace with other sessions. Only an
    # indexed child session, never the workspace, identifies its agent.
    delegated = delegated_agent(
        "" if harness == "codex" else cwd, session_ids[:1] if harness == "codex" else session_ids
    )
    if delegated:
        return delegated

    ensure_loaded()
    adapter = get_adapter(harness)
    resolve_identity = getattr(adapter, "resolve_session_agent_identity", None)
    if callable(resolve_identity):
        adapter_identity = resolve_identity(session_jsonl, cwd)
        if adapter_identity is not None:
            return adapter_identity

    # Codex's generated hook marker is static installation state, not rollout identity.
    # Recovery and public reconcile must likewise never inherit the caller's identity.
    allows_ambient = getattr(adapter, "allows_ambient_agent_identity", None)
    if callable(allows_ambient) and not allows_ambient():
        return None, None
    if identity_scope != "live":
        return None, None

    env_agent_id = os.environ.get("OBSERVAL_AGENT_ID", "")
    env_agent_name = os.environ.get("OBSERVAL_AGENT_NAME", "")
    if env_agent_id and env_agent_name:
        id_entry = _lookup_lockfile_agent_by_id(env_agent_id)
        name_entry = _lookup_lockfile_agent(cwd, agent_name=env_agent_name)
        if not id_entry or not name_entry or id_entry.get("id") != name_entry.get("id"):
            return None, None
    if env_agent_id:
        # Prefer a harness-scoped match, but fall back to an unscoped UUID
        # lookup. A UUID is globally unique, and the harness recorded at pull
        # time may differ from the one resolving the session: Copilot CLI is
        # pulled under "copilot" yet reports "copilot-cli", and build_payload
        # defaults harness to "claude-code" when a caller sets payload["harness"]
        # after the fact. Scoping the lookup would leave those sessions
        # unattributed despite a valid OBSERVAL_AGENT_ID.
        lockfile_entry = _lookup_lockfile_agent_by_id(env_agent_id, harness=harness)
        if lockfile_entry is None:
            lockfile_entry = _lookup_lockfile_agent_by_id(env_agent_id)
        if lockfile_entry:
            from observal_cli.lockfile import agent_entry_is_registry_backed

            if not agent_entry_is_registry_backed(lockfile_entry):
                optic.warning(
                    "OBSERVAL_AGENT_ID={} is not registry-backed (status={}); leaving unattributed",
                    env_agent_id,
                    lockfile_entry.get("registry_status"),
                )
                return None, None
            return lockfile_entry.get("id"), lockfile_entry.get("version")
        optic.warning("OBSERVAL_AGENT_ID={} not found in lockfile (harness={})", env_agent_id, harness)
        return None, None

    if adapter.requires_explicit_agent_id():
        optic.debug("{} session has no OBSERVAL_AGENT_ID; leaving unattributed", harness)
        return None, None

    # Resolve agent name from env var or JSONL
    agent_name: str | None = None

    # 1. Env var for legacy/non-Kiro hooks
    env_agent = os.environ.get("OBSERVAL_AGENT_NAME", "")
    if env_agent:
        agent_name = env_agent

    # 2. Parse agent-setting from JSONL lines (Claude Code)
    if not agent_name:
        agent_name = _parse_agent_from_lines(lines)

    # 3. A subagent transcript names the agent definition it ran as. Only a pulled
    # agent counts: built-in subagent types are not registry agents.
    if not agent_name:
        subagent_name = getattr(adapter, "session_subagent_name", lambda _path: None)(session_jsonl)
        if subagent_name:
            entry = _lookup_lockfile_agent(cwd, agent_name=subagent_name)
            if entry is None:
                return None, None
            return entry.get("id") or entry.get("name"), entry.get("version")

    # Look up version from lockfile. Agent name is the reliable signal for per-agent hooks.
    lockfile_entry = _lookup_lockfile_agent(cwd, agent_name=agent_name) if cwd or agent_name else None

    if agent_name:
        # Only use lockfile version when the entry matches this agent
        agent_id = agent_name
        version = None
        if lockfile_entry:
            lf_name = lockfile_entry.get("name", "")
            lf_id = lockfile_entry.get("id", "")
            if lf_name == agent_name or lf_id == agent_name:
                agent_id = lf_id or lf_name or agent_name
                version = lockfile_entry.get("version")
        return agent_id, version

    # 4. No name from env/JSONL: harnesses that cannot name the agent fall back
    # to the agent pulled into the session's directory.
    if lockfile_entry and getattr(adapter, "attributes_by_directory", lambda: True)():
        return lockfile_entry.get("id") or lockfile_entry.get("name"), lockfile_entry.get("version")

    return None, None


def _lookup_lockfile_agent_by_id(agent_id: str, harness: str | None = None) -> dict | None:
    """Find a lockfile agent by UUID."""
    try:
        from observal_cli.lockfile import get_agent_by_id

        return get_agent_by_id(agent_id, harness=harness)
    except Exception as e:
        optic.debug("lockfile agent id lookup failed: {}", e)
    return None


def _lookup_lockfile_agent(cwd: str, agent_name: str | None = None, harness: str | None = None) -> dict | None:
    """Find the lockfile agent for a directory and optional agent name."""
    try:
        from observal_cli.lockfile import read_registry_lockfile
        from observal_shared.harness_registry import get_valid_harnesses

        _, data = read_registry_lockfile()
        name_matches: list[dict] = []
        for candidate_harness in (harness,) if harness else get_valid_harnesses():
            for agent in data.get("harnesses", {}).get(candidate_harness, {}).get("agents", []):
                same_dir = bool(cwd) and agent.get("directory") == cwd
                same_agent = agent_name and (agent.get("name") == agent_name or agent.get("id") == agent_name)
                if agent_name:
                    if same_agent and same_dir:
                        return agent
                    if same_agent:
                        name_matches.append(agent)
                elif same_dir:
                    return agent
        if name_matches:
            for agent in name_matches:
                if agent.get("scope") != "project":
                    return agent
            return name_matches[0]
    except Exception as e:
        optic.debug("lockfile agent lookup failed: {}", e)
    return None


def _parse_agent_from_lines(lines: list[str]) -> str | None:
    """Extract agent name from Claude Code agent-setting JSONL line.

    Claude Code writes a line like:
      {"type": "agent-setting", "agentSetting": "my-agent", ...}
    at the start of a session when an agent is active.
    """
    for raw in lines:
        if "agent-setting" not in raw:
            continue
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("type") == "agent-setting":
            name = entry.get("agentSetting") or entry.get("agentName") or entry.get("name")
            if name:
                return name
    return None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def log_error(message: str, home: Path | None = None) -> None:
    """Append a single-line error entry to ~/.observal/sync.log."""
    if home is None:
        home = Path.home()
    log_dir = home / ".observal"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        import datetime

        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(log_dir / "sync.log", "a") as f:
            f.write(f"{ts} {message}\n")
    except Exception:
        pass
