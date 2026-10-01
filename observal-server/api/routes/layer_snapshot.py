# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Layer snapshot upload and retrieval endpoint.

Stores full harness layer manifests (with file contents) keyed by hash.
Used for version-aware insights: enables diffing between two layer states.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger as optic
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, require_role
from api.ratelimit import limiter
from models.user import User, UserRole
from observal_shared.migration.constants import DEFAULT_PROJECT_ID

router = APIRouter(prefix="/api/v1/layer-snapshots", tags=["layer-snapshots"])


class LayerFile(BaseModel):
    path: str = Field(..., max_length=500)
    hash: str = Field(..., max_length=100)
    size: int = Field(..., ge=0)
    source: str = Field("user", max_length=20)  # "user" or "observal"
    content: str = Field("", max_length=524288)  # 512KB max per file


_MAX_FILES_PER_SNAPSHOT = 200
_MAX_TOTAL_SIZE = 5 * 1024 * 1024  # 5MB
# One verification per possible MCP pin: 128 agents x 128 components + 512 standalone.
_MAX_MCP_VERIFICATIONS = 128 * 128 + 512


class ComponentPin(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str = Field("", max_length=30)
    id: str = Field("", max_length=100)
    name: str = Field("", max_length=200)
    version: str = Field("", max_length=50)
    scope: str = Field("", max_length=20)
    local_name: str = Field("", max_length=200)
    qualified_name: str = Field("", max_length=300)
    harness: str = Field("", max_length=50)


class AgentPin(ComponentPin):
    components: list[ComponentPin] = Field(default_factory=list, max_length=128)


class PinnedVersions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int | None = Field(None, ge=1, le=2)
    agents: list[AgentPin] = Field(default_factory=list, max_length=128)
    standalone: list[ComponentPin] = Field(default_factory=list, max_length=512)

    @model_validator(mode="after")
    def validate_v2(self):
        if self.schema_version == 2:
            for pin in [*self.agents, *self.standalone, *(c for a in self.agents for c in a.components)]:
                if pin.id:
                    try:
                        UUID(pin.id)
                    except ValueError as error:
                        raise ValueError("v2 pin id must be a UUID or empty") from error
        return self


class LayerSnapshotRequest(BaseModel):
    hash: str = Field(..., min_length=8, max_length=64)
    harnesses: dict[str, list[LayerFile]] = Field(default_factory=dict)  # {harness_name: [files]}
    lockfile_hash: str = Field("", max_length=64)
    pinned_versions: PinnedVersions = Field(default_factory=PinnedVersions)
    drift: dict = Field(default_factory=dict)

    @field_validator("drift")
    @classmethod
    def bound_drift(cls, value: dict) -> dict:
        """Bound the drift fields the extractor reads; the rest is opaque, stored metadata."""
        for key in ("mcp_verifications", "skill_verifications"):
            verifications = value.get(key)
            if verifications is None:
                continue
            if not isinstance(verifications, list) or len(verifications) > _MAX_MCP_VERIFICATIONS:
                raise ValueError(f"drift.{key} must be a bounded list")
            for item in verifications:
                if not isinstance(item, dict) or len(item) > 16:
                    raise ValueError(f"drift.{key} entries must be small objects")
                for field_value in item.values():
                    if not (field_value is None or isinstance(field_value, bool)) and (
                        not isinstance(field_value, str) or len(field_value) > 300
                    ):
                        raise ValueError(f"drift.{key} values must be short strings")
        drifted = value.get("drifted_files")
        if drifted is not None and (
            not isinstance(drifted, list) or len(drifted) > 2 * _MAX_MCP_VERIFICATIONS + 4 * _MAX_FILES_PER_SNAPSHOT
        ):
            raise ValueError("drift.drifted_files must be a bounded list")
        return value


class LayerSnapshotResponse(BaseModel):
    stored: bool
    hash: str
    file_count: int


class LayerSnapshotDetail(BaseModel):
    hash: str
    harness: str
    files: list[dict]
    lockfile_hash: str
    uploaded_at: str
    file_count: int
    total_size: int


class LayerDiffResponse(BaseModel):
    added: list[dict]
    removed: list[dict]
    modified: list[dict]
    unchanged_count: int


class BaselinePinRequest(BaseModel):
    agent_id: str = Field(..., max_length=100)
    layer_hash: str = Field(..., min_length=8, max_length=64)


class BaselinePinResponse(BaseModel):
    agent_id: str
    layer_hash: str
    pinned: bool


async def _ensure_layer_components(project_id: str, user_id: str, layer_hash: str) -> None:
    """Index best-effort once Phase 1.5's extractor is installed; never block uploads."""
    try:
        from services.layer_components.extractor import ensure_layer_components

        await ensure_layer_components(project_id, user_id, layer_hash)
    except ImportError as error:
        optic.debug("layer component extractor unavailable: {}", type(error).__name__)
    except Exception as error:
        # HTTP client exceptions may include credential-bearing URLs in str(error).
        optic.warning("layer component extraction failed for hash={}: {}", layer_hash, type(error).__name__)


@router.post("", response_model=LayerSnapshotResponse)
@limiter.limit("10/minute")
async def upload_layer_snapshot(
    req: LayerSnapshotRequest,
    request: Request,
    current_user: User = Depends(require_role(UserRole.user)),
    db: AsyncSession = Depends(get_db),
):
    """Upload a layer snapshot (full harness config state).

    Called by the CLI when layer_hash changes. Idempotent: uploading
    the same hash again is a no-op.
    """
    optic.trace("user_id={}, hash={}", current_user.id, req.hash)
    project_id = DEFAULT_PROJECT_ID
    user_id = str(current_user.id)

    # Enforce server-side caps (match CLI limits)
    total_files = sum(len(v) for v in req.harnesses.values())
    if total_files > _MAX_FILES_PER_SNAPSHOT:
        raise HTTPException(
            status_code=422,
            detail=f"Snapshot exceeds {_MAX_FILES_PER_SNAPSHOT} file limit ({total_files} files)",
        )
    total_content_size = sum(len(f.content) for files in req.harnesses.values() for f in files)
    if total_content_size > _MAX_TOTAL_SIZE:
        raise HTTPException(
            status_code=422,
            detail=f"Snapshot exceeds {_MAX_TOTAL_SIZE // (1024 * 1024)}MB total content limit",
        )

    from services.clickhouse.client import _query as ch_query
    from services.clickhouse.insert import insert_layer_snapshot
    from services.layer_hash import layer_hash_v2

    # Serialize a user's uploads for one hash across API workers. Without this
    # lock two legacy clients can both see "absent" and overwrite one another
    # under ReplacingMergeTree before the conflict is recorded.
    identity_key = json.dumps([project_id, user_id, req.hash], separators=(",", ":"))
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity_key, 0))"), {"identity_key": identity_key}
    )

    pins = req.pinned_versions.model_dump(exclude_unset=True, exclude_none=True)
    hash_schema_version = 2 if req.hash.startswith("v2_") else 1
    identity_conflict = False
    if hash_schema_version == 2:
        try:
            if not re.fullmatch(r"v2_[0-9a-f]{60}", req.hash) or req.hash != layer_hash_v2(
                {harness: [file.model_dump() for file in files] for harness, files in req.harnesses.items()},
                pins,
            ):
                identity_conflict = True
        except (KeyError, TypeError, ValueError):
            identity_conflict = True

    # Redact secrets from file contents before storage
    from services.secrets_redactor import redact_secrets

    redacted_harnesses: dict[str, list[dict]] = {}
    total_file_count = 0
    total_size = 0
    for harness_name, files in req.harnesses.items():
        redacted_files = []
        for f in files:
            fd = f.model_dump()
            if fd.get("content"):
                fd["content"] = redact_secrets(fd["content"])
            redacted_files.append(fd)
            total_size += f.size
            total_file_count += 1
        redacted_harnesses[harness_name] = redacted_files

    # Serialize the full manifest (with redacted content). Preserve version pins
    # and drift metadata so version-aware insights can distinguish canonical vs
    # dirty installs and compare component/version cohorts.
    content = {
        "harnesses": redacted_harnesses,
        "lockfile_hash": req.lockfile_hash,
        "pinned_versions": pins,
        "drift": req.drift or {},
        "hash_schema_version": hash_schema_version,
    }
    check_sql = """
        SELECT content, uploaded_at
        FROM layer_snapshots
        WHERE project_id = {project_id:String}
          AND user_id = {user_id:String}
          AND hash = {hash:String}
        ORDER BY uploaded_at DESC
        LIMIT 1000
        FORMAT JSON
    """
    existing: list[dict] = []
    try:
        result = await ch_query(
            check_sql,
            {"param_project_id": project_id, "param_user_id": user_id, "param_hash": req.hash},
        )
        result.raise_for_status()
        existing = result.json().get("data", [])
    except Exception as error:
        # HTTP client errors can embed credential-bearing ClickHouse URLs.
        optic.warning("failed to check existing snapshot: {}", type(error).__name__)
        # An unverified retry could overwrite a persisted conflict marker.
        # The client can retry after the scoped existence check recovers.
        raise HTTPException(status_code=503, detail="Layer snapshot identity check unavailable") from error

    if len(existing) >= 1000:
        identity_conflict = True  # Too many revisions to prove a unique identity.
    for record in existing:
        try:
            prior = json.loads(record["content"])
            if prior.get("identity_status") == "identity_conflict" or {
                key: value for key, value in prior.items() if key not in {"identity_status", "hash_schema_version"}
            } != {key: value for key, value in content.items() if key != "hash_schema_version"}:
                identity_conflict = True
        except (KeyError, ValueError, TypeError, AttributeError):
            identity_conflict = True
    if existing and not identity_conflict:
        await _ensure_layer_components(project_id, user_id, req.hash)
        return LayerSnapshotResponse(stored=False, hash=req.hash, file_count=total_file_count)
    if existing and identity_conflict:
        optic.warning("conflicting layer snapshot identity: hash={}", req.hash)
    if identity_conflict:
        content["identity_status"] = "identity_conflict"
    content_json = json.dumps(content)

    row = {
        "hash": req.hash,
        "project_id": project_id,
        "user_id": user_id,
        "harness": ",".join(req.harnesses.keys()),
        "content": content_json,
        "file_count": total_file_count,
        "total_size": total_size,
        "lockfile_hash": req.lockfile_hash,
    }
    if identity_conflict:
        # ReplacingMergeTree uses uploaded_at as its version. Ensure a detected
        # conflict survives a same-millisecond retry of an earlier snapshot.
        marker_time = datetime.now(UTC) + timedelta(milliseconds=1)
        for record in existing:
            try:
                previous = datetime.fromisoformat(str(record["uploaded_at"]).replace(" ", "T"))
                if previous.tzinfo is None:
                    previous = previous.replace(tzinfo=UTC)
                marker_time = max(marker_time, previous + timedelta(milliseconds=1))
            except (KeyError, TypeError, ValueError):
                continue
        row["uploaded_at"] = marker_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    await insert_layer_snapshot(row)
    await _ensure_layer_components(project_id, user_id, req.hash)

    optic.info(
        "layer snapshot stored: hash={}, files={}, size={}",
        req.hash,
        total_file_count,
        total_size,
    )

    return LayerSnapshotResponse(
        stored=True,
        hash=req.hash,
        file_count=sum(len(v) for v in req.harnesses.values()),
    )


@router.get("/{snapshot_hash}", response_model=LayerSnapshotDetail)
@limiter.limit("30/minute")
async def get_layer_snapshot(
    snapshot_hash: str,
    request: Request,
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Retrieve a layer snapshot by hash."""
    optic.trace("user_id={}, hash={}", current_user.id, snapshot_hash)
    project_id = DEFAULT_PROJECT_ID

    from services.clickhouse.client import _query as ch_query

    sql = """
        SELECT hash, harness, content, uploaded_at, file_count, total_size, lockfile_hash
        FROM layer_snapshots
        WHERE project_id = {project_id:String}
          AND user_id = {user_id:String}
          AND hash = {hash:String}
        ORDER BY uploaded_at DESC
        LIMIT 1000
        FORMAT JSON
    """
    result = await ch_query(
        sql,
        {
            "param_project_id": project_id,
            "param_user_id": str(current_user.id),
            "param_hash": snapshot_hash,
        },
    )
    result.raise_for_status()
    rows = result.json().get("data", [])

    if not rows:
        raise HTTPException(status_code=404, detail="Layer snapshot not found")

    import json

    row = rows[0]
    content = json.loads(row["content"])
    if (
        len(rows) >= 1000
        or content.get("identity_status") == "identity_conflict"
        or any(json.loads(other["content"]) != content for other in rows[1:])
    ):
        raise HTTPException(status_code=409, detail="Layer snapshot identity conflict")

    # Flatten harnesses structure into a flat file list for the response
    flat_files = []
    harness_names = []
    for harness_name, files in (content.get("harnesses") or {}).items():
        harness_names.append(harness_name)
        for f in files:
            flat_files.append(f)

    return LayerSnapshotDetail(
        hash=row["hash"],
        harness=",".join(harness_names) if harness_names else row.get("harness", ""),
        files=flat_files,
        lockfile_hash=content.get("lockfile_hash", row.get("lockfile_hash", "")),
        uploaded_at=row.get("uploaded_at", ""),
        file_count=int(row.get("file_count", 0)),
        total_size=int(row.get("total_size", 0)),
    )


@router.get("/{hash_a}/diff/{hash_b}", response_model=LayerDiffResponse)
@limiter.limit("20/minute")
async def diff_layer_snapshots(
    hash_a: str,
    hash_b: str,
    request: Request,
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Diff two layer snapshots to show what changed.

    Returns added, removed, and modified files between snapshot A and B.
    """
    optic.trace("user_id={}, hash_a={}, hash_b={}", current_user.id, hash_a, hash_b)
    project_id = DEFAULT_PROJECT_ID

    import json

    from services.clickhouse.client import _query as ch_query

    sql = """
        SELECT hash, content
        FROM layer_snapshots
        WHERE project_id = {project_id:String}
          AND user_id = {user_id:String}
          AND hash IN ({hash_a:String}, {hash_b:String})
        ORDER BY uploaded_at DESC
        LIMIT 2000
        FORMAT JSON
    """
    result = await ch_query(
        sql,
        {
            "param_project_id": project_id,
            "param_user_id": str(current_user.id),
            "param_hash_a": hash_a,
            "param_hash_b": hash_b,
        },
    )
    result.raise_for_status()
    rows = result.json().get("data", [])

    snapshots: dict[str, dict] = {}
    if len(rows) >= 2000:
        raise HTTPException(status_code=409, detail="Layer snapshot identity conflict")
    for row in rows:
        content = json.loads(row["content"])
        if content.get("identity_status") == "identity_conflict" or (
            row["hash"] in snapshots and snapshots[row["hash"]] != content
        ):
            raise HTTPException(status_code=409, detail="Layer snapshot identity conflict")
        snapshots[row["hash"]] = content

    if hash_a not in snapshots:
        raise HTTPException(status_code=404, detail=f"Snapshot {hash_a} not found")
    if hash_b not in snapshots:
        raise HTTPException(status_code=404, detail=f"Snapshot {hash_b} not found")

    # Flatten {harnesses: {name: [files]}} to {"harness/path": file_dict}
    def _flatten_snapshot(snap: dict) -> dict[str, dict]:
        flat = {}
        for harness_name, files in (snap.get("harnesses") or {}).items():
            for f in files:
                flat[f"{harness_name}/{f['path']}"] = f
        return flat

    files_a = _flatten_snapshot(snapshots[hash_a])
    files_b = _flatten_snapshot(snapshots[hash_b])

    paths_a = set(files_a.keys())
    paths_b = set(files_b.keys())

    added = [files_b[p] for p in paths_b - paths_a]
    removed = [files_a[p] for p in paths_a - paths_b]
    modified = []
    unchanged = 0

    for path in paths_a & paths_b:
        if files_a[path]["hash"] != files_b[path]["hash"]:
            modified.append(
                {
                    "path": path,
                    "before": files_a[path],
                    "after": files_b[path],
                }
            )
        else:
            unchanged += 1

    return LayerDiffResponse(
        added=added,
        removed=removed,
        modified=modified,
        unchanged_count=unchanged,
    )


@router.post("/baseline", response_model=BaselinePinResponse)
@limiter.limit("10/minute")
async def pin_baseline(
    req: BaselinePinRequest,
    request: Request,
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Pin a layer_hash as the baseline for an agent.

    Used for long-term comparison: all future layer states are
    measured against this baseline in insights.
    """
    optic.trace("user_id={}, agent_id={}, hash={}", current_user.id, req.agent_id, req.layer_hash)
    project_id = DEFAULT_PROJECT_ID
    user_id = str(current_user.id)

    import json

    from services.clickhouse.client import _query as ch_query

    # Store/update baseline pin (use a dedicated table or settings)
    # For now, store in layer_snapshots with a special marker
    sql = """
        INSERT INTO layer_snapshots (hash, project_id, user_id, harness, content, file_count, total_size, lockfile_hash)
        VALUES (
            {hash:String},
            {project_id:String},
            {user_id:String},
            'baseline',
            {content:String},
            0, 0, ''
        )
    """
    content = json.dumps({"agent_id": req.agent_id, "baseline": True, "pinned_hash": req.layer_hash})

    try:
        await ch_query(
            sql,
            {
                "param_hash": f"baseline:{req.agent_id}",
                "param_project_id": project_id,
                "param_user_id": user_id,
                "param_content": content,
            },
        )
    except Exception as e:
        optic.error("failed to pin baseline: {}", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to pin baseline")

    return BaselinePinResponse(
        agent_id=req.agent_id,
        layer_hash=req.layer_hash,
        pinned=True,
    )
