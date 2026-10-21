# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Session JSONL ingest service.

Receives raw JSONL transcript lines from harness sessions, classifies each
line into an event type, and batch-inserts into the ``session_events`` table.

Classification dispatches strictly by harness via
``services.session_parsers.ingest_classify.get_classifier`` -- there is no
default fallback.  Passing an unknown ``harness`` value raises ``KeyError``.
"""

import hashlib
import uuid as _uuid
from collections.abc import Callable
from dataclasses import dataclass

import orjson
import xxhash
from loguru import logger as optic

from services.clickhouse import (
    insert_session_checkpoint,
    insert_session_events,
    query_existing_for_dedup,
    query_session_checkpoint,
    query_session_source_manifest,
    query_source_records_after,
    refresh_session_summary,
)
from services.secrets_redactor import redact_secrets
from services.session_parsers.base import token_count, uncached_input_tokens
from services.session_parsers.ingest_classify import extract_timestamp, get_classifier, get_extra_rows
from services.session_parsers.kiro import extract_usage as _usage_kiro

# ---------------------------------------------------------------------------
# Per-harness token usage extraction (dispatch pattern)
# ---------------------------------------------------------------------------


def _usage_claude_code(parsed: dict) -> dict:
    """Claude Code / Cursor: usage.input_tokens, usage.output_tokens, etc."""
    msg = parsed.get("message", {})
    usage = msg.get("usage") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "cache_write_tokens": int(usage.get("cache_creation_input_tokens") or 0),
        "model": str(msg.get("model") or parsed.get("model") or ""),
    }


def _usage_pi(parsed: dict) -> dict:
    """Pi: usage.input, usage.output, usage.cacheRead, usage.cacheWrite."""
    msg = parsed.get("message", {})
    usage = msg.get("usage") or {}
    return {
        "input_tokens": int(usage.get("input") or 0),
        "output_tokens": int(usage.get("output") or 0),
        "cache_read_tokens": int(usage.get("cacheRead") or 0),
        "cache_write_tokens": int(usage.get("cacheWrite") or 0),
        "model": str(msg.get("model") or ""),
    }


def _usage_antigravity(parsed: dict) -> dict:
    """Antigravity: no token counts in transcript format."""
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "model": "",
    }


def _usage_goose(parsed: dict) -> dict:
    """Goose: per-message counts live in metadata.usage (camelCase)."""
    metadata = parsed.get("metadata")
    usage = metadata.get("usage") if isinstance(metadata, dict) else None
    inference = metadata.get("inference") if isinstance(metadata, dict) else None
    if not isinstance(usage, dict):
        usage = {}
    if not isinstance(inference, dict):
        inference = {}

    cache_read = token_count(usage, "cacheReadTokens")
    cache_write = token_count(usage, "cacheWriteTokens")
    return {
        # Goose counts cache reads and writes inside inputTokens.
        "input_tokens": uncached_input_tokens(token_count(usage, "inputTokens"), cache_read, cache_write),
        "output_tokens": token_count(usage, "outputTokens"),
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "model": str(inference.get("resolvedModel") or inference.get("requestedModel") or ""),
    }


def _usage_codex(parsed: dict) -> dict:
    """Codex: event_msg/token_count has payload.info.last_token_usage.

    Codex follows the OpenAI convention where input_tokens includes
    cached_input_tokens, so the cached part is subtracted.
    """
    payload = parsed.get("payload", {})
    if not isinstance(payload, dict):
        return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "model": ""}
    info = payload.get("info", {})
    if not isinstance(info, dict):
        info = {}
    usage = info.get("last_token_usage")
    if not isinstance(usage, dict) or not usage:
        usage = info.get("total_token_usage")
    if not isinstance(usage, dict):
        usage = {}
    cached = token_count(usage, "cached_input_tokens")
    return {
        "input_tokens": uncached_input_tokens(token_count(usage, "input_tokens"), cached),
        "output_tokens": token_count(usage, "output_tokens"),
        "cache_read_tokens": cached,
        "cache_write_tokens": 0,
        "model": "",
    }


def _usage_copilot_cli(parsed: dict) -> dict:
    """Copilot CLI: tokens are in assistant.message data or assistant.usage events.

    Copilot CLI v1.0.59+ uses flat format:
      {"type": "assistant.message", "data": {"outputTokens": N, "model": "..."}, ...}
    Older/SDK format uses envelope:
      {"agentId": "...", "ts": "...", "event": {"type": "assistant.usage", "data": {...}}}
    """
    # Try flat format first (Copilot CLI v1.0.59+)
    data = parsed.get("data", {})
    if isinstance(data, dict) and (data.get("outputTokens") or data.get("inputTokens")):
        return _copilot_usage(data)

    # Try envelope format (older/SDK)
    event = parsed.get("event", {})
    if isinstance(event, dict):
        edata = event.get("data", {})
        if isinstance(edata, dict) and (edata.get("outputTokens") or edata.get("inputTokens")):
            return _copilot_usage(edata)

    return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "model": ""}


def _copilot_usage(data: dict) -> dict:
    """Copilot usage counts cache reads and writes inside inputTokens."""
    cache_read = token_count(data, "cacheReadTokens")
    cache_write = token_count(data, "cacheWriteTokens")
    return {
        "input_tokens": uncached_input_tokens(token_count(data, "inputTokens"), cache_read, cache_write),
        "output_tokens": token_count(data, "outputTokens"),
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "model": str(data.get("model") or ""),
    }


_UsageFn = Callable[[dict], dict]

_USAGE_EXTRACTORS: dict[str, _UsageFn] = {
    "claude-code": _usage_claude_code,
    "codex": _usage_codex,
    "kiro": _usage_kiro,
    "cursor": _usage_claude_code,
    "goose": _usage_goose,
    "pi": _usage_pi,
    "copilot-cli": _usage_copilot_cli,
    "copilot": _usage_copilot_cli,
    "opencode": _usage_claude_code,
    "antigravity": _usage_antigravity,
}


# ---------------------------------------------------------------------------
# Per-harness UUID extraction (dispatch pattern)
# ---------------------------------------------------------------------------


def _uuid_default(parsed: dict) -> tuple[str | None, str | None]:
    """Claude Code / Cursor: uuid, parentUuid."""
    return parsed.get("uuid"), parsed.get("parentUuid")


def _uuid_kiro(parsed: dict) -> tuple[str | None, str | None]:
    """Kiro: CLI records carry uuid/parentUuid, IDE records a flat ``id``."""
    uuid = parsed.get("uuid")
    if uuid:
        return uuid, parsed.get("parentUuid")
    if isinstance(parsed.get("payload"), dict):
        return parsed.get("id"), None
    return None, None


def _uuid_pi(parsed: dict) -> tuple[str | None, str | None]:
    """Pi: id, parentId (at entry level)."""
    return parsed.get("id"), parsed.get("parentId")


def _uuid_antigravity(parsed: dict) -> tuple[str | None, str | None]:
    """Antigravity: use step_index as a pseudo-UUID (no native UUIDs)."""
    step = parsed.get("step_index")
    if step is not None:
        return str(step), None
    return None, None


def _uuid_goose(parsed: dict) -> tuple[str | None, str | None]:
    """Goose: message_id identifies a row; boundaries fall back to the session id."""
    return parsed.get("message_id") or parsed.get("session_id"), parsed.get("parent_session_id")


_UuidFn = Callable[[dict], "tuple[str | None, str | None]"]

_UUID_EXTRACTORS: dict[str, _UuidFn] = {
    "claude-code": _uuid_default,
    "kiro": _uuid_kiro,
    "cursor": _uuid_default,
    "goose": _uuid_goose,
    "opencode": _uuid_default,
    "pi": _uuid_pi,
    "antigravity": _uuid_antigravity,
}


def _extract_usage_tokens(parsed: dict, harness: str = "claude-code") -> dict:
    """Extract input/output/cache token counts and model from a parsed JSONL line.

    Dispatches to per-harness extractor. Falls back to Claude Code format.
    """
    extractor = _USAGE_EXTRACTORS.get(harness, _usage_claude_code)
    return extractor(parsed)


def _extract_uuid(parsed: dict, harness: str = "claude-code") -> tuple[str | None, str | None]:
    """Extract (uuid, parent_uuid) from a parsed JSONL line.

    Dispatches to per-harness extractor. Falls back to Claude Code format.
    """
    extractor = _UUID_EXTRACTORS.get(harness, _uuid_default)
    return extractor(parsed)


# ---------------------------------------------------------------------------
# Agent attribution resolution
# ---------------------------------------------------------------------------


def _is_uuid(value: str) -> bool:
    """Return True if *value* is a valid UUID string."""
    try:
        _uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


async def _resolve_agent_id(agent_id: str | None) -> str | None:
    """Resolve an agent name to its UUID if it isn't one already.

    Uses a Redis cache (5min TTL) to avoid hitting Postgres on every
    ingest push. At 500 active sessions with named agents, this saves
    ~500 Postgres queries/sec.

    Returns the original value unchanged if it is already a UUID, None,
    or if the name matches no live agent or more than one.
    """
    if not agent_id:
        return agent_id
    if _is_uuid(agent_id):
        return agent_id

    # Check Redis cache first
    from services.redis import get_redis

    cache_key = f"agent_name_resolve:{agent_id}"
    redis = None
    try:
        redis = get_redis()
        cached = await redis.get(cache_key)
        if cached is not None:
            return cached if cached != "__none__" else agent_id
    except Exception as e:
        optic.trace("Redis cache read failed for agent resolution: {}", e)

    # Import lazily to avoid circular deps at module level
    from sqlalchemy import select

    from database import async_session
    from models.agent import Agent

    resolved = None
    try:
        async with async_session() as db:
            # Names are not unique across namespaces: only an unambiguous live match is safe to attribute.
            stmt = select(Agent.id).where(Agent.name == agent_id, Agent.deleted_at.is_(None)).limit(2)
            result = await db.execute(stmt)
            rows = result.scalars().all()
            if len(rows) == 1:
                resolved = str(rows[0])
                optic.debug("resolved agent name '{}' to UUID {}", agent_id, resolved)
            elif rows:
                optic.warning("agent name '{}' matches several agents; leaving the session unattributed", agent_id)
    except Exception as e:
        optic.warning("agent_id resolution failed: {}", e)

    # Cache result in Redis (5 min TTL)
    if redis:
        try:
            await redis.setex(cache_key, 300, resolved or "__none__")
        except Exception as e:
            optic.trace("Redis cache write failed for agent resolution: {}", e)

    return resolved if resolved else agent_id


async def _resolve_agent_version(agent_id: str | None, agent_version: str | None) -> str | None:
    """Resolve the mutable latest alias to the agent's current version."""
    if agent_version != "latest" or not agent_id or not _is_uuid(agent_id):
        return agent_version

    from services.redis import get_redis

    cache_key = f"agent_version_resolve:{agent_id}:latest"
    redis = None
    try:
        redis = get_redis()
        cached = await redis.get(cache_key)
        if cached is not None:
            return cached if cached != "__none__" else agent_version
    except Exception as e:
        optic.trace("Redis cache read failed for agent version resolution: {}", e)

    from sqlalchemy import select

    from database import async_session
    from models.agent import Agent, AgentVersion

    resolved = None
    try:
        async with async_session() as db:
            stmt = (
                select(AgentVersion.version)
                .join(Agent, Agent.latest_version_id == AgentVersion.id)
                .where(Agent.id == _uuid.UUID(agent_id))
                .limit(1)
            )
            result = await db.execute(stmt)
            resolved = result.scalar_one_or_none()
    except Exception as e:
        optic.warning("agent_version resolution failed: {}", e)

    if redis:
        try:
            await redis.setex(cache_key, 300, resolved or "__none__")
        except Exception as e:
            optic.trace("Redis cache write failed for agent version resolution: {}", e)

    return resolved if resolved else agent_version


class SessionRecordConflictError(ValueError):
    """Raised when a source line index is retried with different content."""

    def __init__(self, offsets: list[int]):
        self.offsets = offsets
        super().__init__(f"session source content changed at line(s): {', '.join(map(str, offsets))}")


@dataclass
class IngestResult:
    ingested: int
    skipped: int
    errors: int


@dataclass
class IntegrityResult:
    ok: bool
    acknowledged_line: int
    acknowledged_offset: int
    expected_line: int
    expected_offset: int
    server_hash: str | None = None
    repair_from_line: int | None = None
    repair_offset: int = 0


async def ingest_session_lines(
    session_id: str,
    project_id: str,
    user_id: str,
    agent_id: str | None,
    agent_version: str | None,
    layer_hash: str | None = None,
    harness: str = "claude-code",
    lines: list[str] | None = None,
    start_offset: int = 0,
    end_byte_offsets: list[int] | None = None,
    total_credits: float | None = None,
    parent_session_id: str | None = None,
) -> IngestResult:
    """Parse, classify, and batch-insert JSONL transcript lines.

    Each string in *lines* is one raw JSONL line from an harness session.
    Lines that fail to parse are counted as errors and skipped.
    ``continuation`` lines (empty API signals) are counted as skipped.

    Args:
        session_id:      Unique session identifier (UUID).
        project_id:      Project the session belongs to.
        user_id:         User who owns the session.
        agent_id:        Optional agent identifier.
        agent_version:   Optional agent version string.
        harness:             harness name (e.g. ``"claude-code"``, ``"kiro"``).
        lines:           Raw JSONL strings to ingest.
        start_offset:    Line offset of the first item in *lines* within the
                         full session transcript.

    Returns:
        An :class:`IngestResult` with ``ingested``, ``skipped``, and
        ``errors`` counts.
    """
    optic.trace("ingesting session lines for session {}", session_id)

    # Normalize agent_id and agent_version so downstream queries match canonical versions.
    agent_id = await _resolve_agent_id(agent_id)
    agent_version = await _resolve_agent_version(agent_id, agent_version)

    ingested = 0
    skipped = 0
    errors = 0

    if end_byte_offsets is not None and len(end_byte_offsets) != len(lines or []):
        raise ValueError("end_byte_offsets must contain one value per source line")

    if not lines:
        extra = get_extra_rows(harness, session_id, project_id, user_id, agent_id, agent_version, total_credits)
        for row in extra:
            row["is_source_record"] = 0
            row["rendered"] = 1
        if extra:
            await insert_session_events(extra)
            await refresh_session_summary(session_id, project_id, user_id, harness)
        optic.debug("no lines to ingest for session {}", session_id)
        return IngestResult(ingested=0, skipped=0, errors=0)

    max_batch_offset = start_offset + len(lines) - 1
    existing = await query_existing_for_dedup(
        session_id,
        project_id,
        user_id,
        harness,
        start_offset,
        max_batch_offset,
    )
    line_hashes = [xxhash.xxh128(line.encode("utf-8", errors="replace")).hexdigest() for line in lines]
    conflicts = [
        start_offset + i for i, digest in enumerate(line_hashes) if existing.get(start_offset + i, digest) != digest
    ]
    repair_offsets: set[int] = set()
    if existing:
        checkpoint_line, _checkpoint_offset = await query_session_checkpoint(session_id, project_id, user_id, harness)
        blocked = [offset for offset in conflicts if offset <= checkpoint_line]
        if blocked:
            raise SessionRecordConflictError(blocked)
        repair_offsets = {offset for offset in existing if offset > checkpoint_line}

    byte_offsets = end_byte_offsets or [0] * len(lines)
    rows: list[dict] = []
    classify_fn, preview_fn, tool_info_fn = get_classifier(harness)
    last_real_ts: str | None = None

    from datetime import UTC, datetime

    for i, (raw_line, line_hash) in enumerate(zip(lines, line_hashes, strict=True)):
        line_offset = start_offset + i
        if line_offset in existing and line_offset not in repair_offsets:
            skipped += 1
            continue

        parsed: dict = {}
        rendered = 1
        try:
            candidate = orjson.loads(raw_line)
            if not isinstance(candidate, dict):
                raise ValueError("session record must be a JSON object")
            parsed = candidate
            event_type = classify_fn(parsed)
            if event_type is None:
                event_type = "_ignored"
                rendered = 0
                skipped += 1
        except (orjson.JSONDecodeError, ValueError) as exc:
            optic.warning(
                "session_ingest_parse_error: session={}, offset={}, error={}, line_preview={}",
                session_id,
                line_offset,
                str(exc),
                repr(raw_line[:200]),
            )
            event_type = "_parse_error"
            rendered = 0
            errors += 1

        if parsed:
            ts = extract_timestamp(harness, parsed)
            if ts is not None:
                last_real_ts = ts
            timestamp = ts or last_real_ts or datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        else:
            timestamp = last_real_ts or datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        preview = redact_secrets(preview_fn(parsed, event_type)) if rendered else ""
        tool_name, tool_id = tool_info_fn(parsed) if rendered else (None, None)
        uuid, parent_uuid = _extract_uuid(parsed, harness) if parsed else (None, None)
        usage = (
            _extract_usage_tokens(parsed, harness)
            if parsed
            else {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "model": "",
            }
        )
        rows.append(
            {
                "session_id": session_id,
                "project_id": project_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "agent_version": agent_version,
                "layer_hash": layer_hash,
                "harness": harness,
                "line_offset": line_offset,
                "source_end_offset": byte_offsets[i],
                "line_hash": line_hash,
                "source_sha256": hashlib.sha256(raw_line.encode("utf-8", errors="replace")).hexdigest(),
                "is_source_record": 1,
                "rendered": rendered,
                "event_type": event_type,
                "timestamp": timestamp,
                "uuid": uuid,
                "parent_uuid": parent_uuid,
                "tool_name": tool_name,
                "tool_id": tool_id,
                "content_preview": preview,
                "content_length": len(raw_line.encode("utf-8", errors="replace")),
                "raw_line": redact_secrets(raw_line.encode("utf-8", errors="replace").decode("utf-8")),
                "credits": 0.0,
                "parent_session_id": parent_session_id,
                **usage,
            }
        )
        ingested += rendered

    if rows:
        await insert_session_events(rows)
        optic.debug("inserted {} canonical rows for session={}", len(rows), session_id)

    extra = get_extra_rows(harness, session_id, project_id, user_id, agent_id, agent_version, total_credits)
    for row in extra:
        row["is_source_record"] = 0
        row["rendered"] = 1
    if extra:
        await insert_session_events(extra)

    if rows or extra:
        await refresh_session_summary(session_id, project_id, user_id, harness)

    return IngestResult(ingested=ingested, skipped=skipped, errors=errors)


async def advance_session_checkpoint(
    session_id: str,
    project_id: str,
    user_id: str,
    harness: str,
) -> tuple[int, int]:
    """Advance and return the highest contiguous canonical source position."""
    acknowledged_line, acknowledged_offset = await query_session_checkpoint(session_id, project_id, user_id, harness)
    while True:
        records = await query_source_records_after(
            session_id,
            project_id,
            user_id,
            harness,
            acknowledged_line,
        )
        if not records:
            break
        expected = acknowledged_line + 1
        advanced = False
        for line_offset, end_offset in records:
            if line_offset < expected:
                continue
            if line_offset != expected:
                break
            acknowledged_line = line_offset
            acknowledged_offset = end_offset
            expected += 1
            advanced = True
        if not advanced or len(records) < 5000 or records[-1][0] != acknowledged_line:
            break

    await insert_session_checkpoint(
        session_id,
        project_id,
        user_id,
        harness,
        acknowledged_line,
        acknowledged_offset,
    )
    return acknowledged_line, acknowledged_offset


async def check_session_integrity(
    session_id: str,
    project_id: str,
    user_id: str,
    harness: str,
    expected_line_count: int,
    expected_offset: int,
    acknowledged_line: int | None = None,
    acknowledged_offset: int | None = None,
    expected_hash: str | None = None,
    hashed_line_count: int | None = None,
) -> IntegrityResult:
    """Audit final source continuity and hash only on finalization."""
    if acknowledged_line is None or acknowledged_offset is None:
        acknowledged_line, acknowledged_offset = await query_session_checkpoint(
            session_id, project_id, user_id, harness
        )
    expected_line = expected_line_count - 1
    offset_ok = expected_offset == 0 or acknowledged_offset == 0 or acknowledged_offset == expected_offset
    server_hash = None
    repair_from_line = acknowledged_line + 1 if acknowledged_line < expected_line else None
    manifest: list[tuple[int, int, str]] = []

    if expected_hash is not None:
        manifest = await query_session_source_manifest(session_id, project_id, user_id, harness)
        hash_count = expected_line_count if hashed_line_count is None else hashed_line_count
        hasher = hashlib.sha256()
        next_offset = 0
        missing_offset = None
        for line_offset, _end_offset, source_hash in manifest:
            if line_offset >= hash_count:
                continue
            hasher.update(source_hash.encode())
            hasher.update(b"\n")
            if missing_offset is None and line_offset != next_offset:
                missing_offset = next_offset
            next_offset += 1
        server_hash = hasher.hexdigest()
        if missing_offset is None and next_offset < hash_count:
            missing_offset = next_offset
        if missing_offset is not None:
            repair_from_line = missing_offset if repair_from_line is None else min(repair_from_line, missing_offset)
        elif server_hash != expected_hash:
            repair_from_line = 0 if repair_from_line is None else min(repair_from_line, 0)

    repair_offset = 0
    if repair_from_line and manifest:
        repair_offset = next(
            (end_offset for line_offset, end_offset, _raw in manifest if line_offset == repair_from_line - 1),
            0,
        )

    return IntegrityResult(
        ok=acknowledged_line == expected_line and offset_ok and repair_from_line is None,
        acknowledged_line=acknowledged_line,
        acknowledged_offset=acknowledged_offset,
        expected_line=expected_line,
        expected_offset=expected_offset,
        server_hash=server_hash,
        repair_from_line=repair_from_line,
        repair_offset=repair_offset,
    )
