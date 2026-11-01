# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Session JSONL ingest endpoint."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger as optic
from pydantic import BaseModel, Field, field_validator, model_validator

from api.deps import require_role
from api.ratelimit import limiter
from models.user import User, UserRole
from observal_shared.migration.constants import DEFAULT_PROJECT_ID

MAX_SHORT_STRING_LENGTH = 512
MAX_TEXT_LENGTH = 1_048_576

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

MAX_SESSION_LINES = 1000
MAX_SESSION_TOTAL_LINES = 10_000_000


MAX_CAPABILITIES_PER_PUSH = 200
_CAPABILITY_KINDS = {"agent", "mcp", "skill", "hook", "prompt", "sandbox", "external"}
# delegated: the session handed a task to this agent (ADR 0002)
_CAPABILITY_MODES = {"context", "next-session", "delegated"}
_CAPABILITY_CONFIDENCE = {"exact", "window", "loose"}


class CapabilityUsed(BaseModel):
    """One discovery resource the CLI recorded as used in this session (from the capability lock)."""

    identifier: str | None = Field(None, max_length=MAX_SHORT_STRING_LENGTH)
    kind: str = Field(..., max_length=32)
    component_id: str | None = Field(None, max_length=MAX_SHORT_STRING_LENGTH)
    native_ref: str | None = Field(None, max_length=MAX_SHORT_STRING_LENGTH)
    version: str | None = Field(None, max_length=64)
    digest: str | None = Field(None, max_length=96)
    mode: str = Field("context", max_length=32)
    source: str = Field("unknown", max_length=32)
    used_at: str = Field(..., max_length=64)
    confidence: str = Field("window", max_length=16)

    @field_validator("kind")
    @classmethod
    def kind_is_known(cls, value: str) -> str:
        if value not in _CAPABILITY_KINDS:
            raise ValueError(f"unknown capability kind: {value}")
        return value

    @field_validator("mode")
    @classmethod
    def mode_is_known(cls, value: str) -> str:
        if value not in _CAPABILITY_MODES:
            raise ValueError(f"unknown capability mode: {value}")
        return value

    @field_validator("confidence")
    @classmethod
    def confidence_is_known(cls, value: str) -> str:
        if value not in _CAPABILITY_CONFIDENCE:
            raise ValueError(f"unknown capability confidence: {value}")
        return value

    @model_validator(mode="after")
    def has_some_identity(self):
        if not (self.identifier or self.component_id or self.native_ref):
            raise ValueError("a capability needs an identifier, component_id or native_ref")
        return self


class SessionIngestRequest(BaseModel):
    session_id: str = Field(..., max_length=MAX_SHORT_STRING_LENGTH)
    harness: str = Field("claude-code", max_length=MAX_SHORT_STRING_LENGTH)
    agent_id: str | None = Field(None, max_length=MAX_SHORT_STRING_LENGTH)
    agent_version: str | None = Field(None, max_length=MAX_SHORT_STRING_LENGTH)
    layer_hash: str | None = Field(None, max_length=MAX_SHORT_STRING_LENGTH)
    lines: list[str] = Field(..., max_length=MAX_SESSION_LINES)  # Raw JSONL lines
    start_offset: int = Field(0, ge=0)
    end_byte_offsets: list[int] | None = Field(None, max_length=MAX_SESSION_LINES)
    hook_event: str = Field("UserPromptSubmit", max_length=MAX_SHORT_STRING_LENGTH)
    # Sent on Stop for integrity check
    final: bool = False
    total_line_count: int | None = Field(None, ge=0, le=MAX_SESSION_TOTAL_LINES)
    total_offset: int | None = Field(None, ge=0)
    session_hash: str | None = Field(None, max_length=128)
    hashed_line_count: int | None = Field(None, ge=0, le=MAX_SESSION_TOTAL_LINES)
    # Kiro-specific: total credits consumed this session
    total_credits: float | None = Field(None, ge=0)
    # Claude Code subagent attribution: set when this session is a subagent
    parent_session_id: str | None = Field(None, max_length=MAX_SHORT_STRING_LENGTH)
    # Discovery resources the CLI's capability lock attributes to this session
    capabilities_used: list[CapabilityUsed] | None = Field(None, max_length=MAX_CAPABILITIES_PER_PUSH)

    @field_validator("lines")
    @classmethod
    def lines_are_bounded(cls, value: list[str]) -> list[str]:
        for line in value:
            if len(line) > MAX_TEXT_LENGTH:
                raise ValueError(f"session lines must be at most {MAX_TEXT_LENGTH} characters")
        return value

    @model_validator(mode="after")
    def byte_offsets_match_lines(self):
        if self.end_byte_offsets is not None:
            if len(self.end_byte_offsets) != len(self.lines):
                raise ValueError("end_byte_offsets must contain one value per source line")
            if any(offset < 0 for offset in self.end_byte_offsets):
                raise ValueError("end_byte_offsets cannot contain negative values")
            if self.end_byte_offsets != sorted(self.end_byte_offsets):
                raise ValueError("end_byte_offsets must be ordered")
        if self.session_hash is not None and self.hashed_line_count is None:
            raise ValueError("hashed_line_count is required with session_hash")
        if (
            self.hashed_line_count is not None
            and self.total_line_count is not None
            and self.hashed_line_count > self.total_line_count
        ):
            raise ValueError("hashed_line_count cannot exceed total_line_count")
        return self


class SessionIngestResponse(BaseModel):
    ingested: int
    skipped: int
    errors: int
    acknowledged_line: int
    acknowledged_offset: int
    integrity_ok: bool | None = None  # Only set when final=True
    server_hash: str | None = None
    repair_from_line: int | None = None


class SessionCheckpointResponse(BaseModel):
    session_id: str
    harness: str
    acknowledged_line: int
    acknowledged_offset: int


@router.post("/session", response_model=SessionIngestResponse)
@limiter.limit("60/minute")
async def ingest_session(
    req: SessionIngestRequest,
    request: Request,
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Ingest raw JSONL transcript lines from an harness session.

    Called by the session_push hook on each UserPromptSubmit and Stop event.
    Lines are stored as-is and classified server-side.
    """
    optic.trace("user_id={}", current_user.id)
    from services.session_ingest import (
        SessionRecordConflictError,
        advance_session_checkpoint,
        check_session_integrity,
        ingest_session_lines,
    )

    user_id = str(current_user.id)
    project_id = DEFAULT_PROJECT_ID

    optic.debug(
        "ingest request: session={}, harness={}, lines={}, offset={}, final={}",
        req.session_id,
        req.harness,
        len(req.lines),
        req.start_offset,
        req.final,
    )

    try:
        result = await ingest_session_lines(
            session_id=req.session_id,
            project_id=project_id,
            user_id=user_id,
            agent_id=req.agent_id,
            agent_version=req.agent_version,
            layer_hash=req.layer_hash,
            harness=req.harness,
            lines=req.lines,
            start_offset=req.start_offset,
            end_byte_offsets=req.end_byte_offsets,
            total_credits=req.total_credits,
            parent_session_id=req.parent_session_id,
        )
    except SessionRecordConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "session source changed at an acknowledged line", "offsets": exc.offsets},
        )

    acknowledged_line, acknowledged_offset = await advance_session_checkpoint(
        req.session_id,
        project_id,
        user_id,
        req.harness,
    )

    if req.capabilities_used:
        from services.clickhouse import insert_session_capabilities

        await insert_session_capabilities(
            session_id=req.session_id,
            project_id=project_id,
            user_id=user_id,
            harness=req.harness,
            uses=[c.model_dump() for c in req.capabilities_used],
        )

    integrity_ok = None
    if req.final and req.total_line_count is not None:
        integrity = await check_session_integrity(
            session_id=req.session_id,
            project_id=project_id,
            user_id=user_id,
            harness=req.harness,
            expected_line_count=req.total_line_count,
            expected_offset=req.total_offset or 0,
            acknowledged_line=acknowledged_line,
            acknowledged_offset=acknowledged_offset,
            expected_hash=req.session_hash,
            hashed_line_count=req.hashed_line_count,
        )
        integrity_ok = integrity.ok
        if integrity.repair_from_line is not None:
            from services.clickhouse import insert_session_checkpoint

            acknowledged_line = integrity.repair_from_line - 1
            acknowledged_offset = integrity.repair_offset
            await insert_session_checkpoint(
                req.session_id,
                project_id,
                user_id,
                req.harness,
                acknowledged_line,
                acknowledged_offset,
            )
        if not integrity_ok:
            optic.warning(
                "session integrity check failed: session={}, expected={}, actual offset={}",
                req.session_id,
                req.total_line_count,
                req.total_offset,
            )

    # Only an integrity-verified final push has a complete canonical source.
    # A missing/late layer snapshot is retried by the job, not treated as no use.
    if req.final and integrity_ok is True:
        try:
            from jobs.activity import enqueue_activity_projection

            await enqueue_activity_projection(
                project_id, user_id, req.harness, req.session_id, source_digest=integrity.server_hash or ""
            )
        except Exception as error:
            optic.warning("session activity enqueue failed: {}", type(error).__name__)

    # Notify WebSocket subscribers so the frontend gets instant turn updates.
    # Publish to both a session-specific channel (for detail viewers, O(1) fan-out)
    # and the global channel (for list viewers with debounced refresh).
    # Fire-and-forget so a Redis blip never blocks the HTTP response to the CLI.
    if result.ingested > 0:
        import asyncio

        from services.redis import publish

        _payload = {
            "session_id": req.session_id,
            "event_name": "session_push",
        }
        asyncio.create_task(publish(f"sessions:{req.session_id}:updated", _payload))  # noqa: RUF006
        asyncio.create_task(publish("sessions:updated", _payload))  # noqa: RUF006

    optic.info(
        "session ingested: session={}, ingested={}, skipped={}, errors={}",
        req.session_id,
        result.ingested,
        result.skipped,
        result.errors,
    )

    return SessionIngestResponse(
        ingested=result.ingested,
        skipped=result.skipped,
        errors=result.errors,
        acknowledged_line=acknowledged_line,
        acknowledged_offset=acknowledged_offset,
        integrity_ok=integrity_ok,
        server_hash=integrity.server_hash if req.final and req.total_line_count is not None else None,
        repair_from_line=integrity.repair_from_line if req.final and req.total_line_count is not None else None,
    )


@router.get("/session/checkpoint", response_model=SessionCheckpointResponse)
async def get_session_checkpoint(
    session_id: str = Query(..., max_length=MAX_SHORT_STRING_LENGTH),
    harness: str = Query(..., max_length=MAX_SHORT_STRING_LENGTH),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Return the caller's durable contiguous checkpoint for one session source."""
    from services.clickhouse import query_session_checkpoint

    project_id = DEFAULT_PROJECT_ID
    acknowledged_line, acknowledged_offset = await query_session_checkpoint(
        session_id,
        project_id,
        str(current_user.id),
        harness,
    )
    return SessionCheckpointResponse(
        session_id=session_id,
        harness=harness,
        acknowledged_line=acknowledged_line,
        acknowledged_offset=acknowledged_offset,
    )
