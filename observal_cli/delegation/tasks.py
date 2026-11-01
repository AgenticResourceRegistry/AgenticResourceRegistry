# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A2A task shapes and the local task store.

Tasks follow the A2A v1.0 JSON serialization (``TASK_STATE_*`` states,
``ROLE_USER``/``ROLE_AGENT``, parts as ``{"text": ...}``). Observal's own
bookkeeping lives under ``metadata.observal``.

Each task is one JSON file in ``~/.observal/delegations/``; the detached
worker writes it and every reader just reads it, so a task survives the MCP
server or CLI process that started it. Cancellation is a marker file the
worker polls. Nothing here is secret: identifiers, text and paths only.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

from observal_cli import lockfile as _lockfile

if TYPE_CHECKING:
    from pathlib import Path

# Tests point this somewhere temporary.
STORE_DIR: Path | None = None

STATE_SUBMITTED = "TASK_STATE_SUBMITTED"
STATE_WORKING = "TASK_STATE_WORKING"
STATE_INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
STATE_AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"
STATE_COMPLETED = "TASK_STATE_COMPLETED"
STATE_FAILED = "TASK_STATE_FAILED"
STATE_CANCELED = "TASK_STATE_CANCELED"
STATE_REJECTED = "TASK_STATE_REJECTED"

TERMINAL_STATES = frozenset({STATE_COMPLETED, STATE_FAILED, STATE_CANCELED, STATE_REJECTED})
INTERRUPTED_STATES = frozenset({STATE_INPUT_REQUIRED, STATE_AUTH_REQUIRED})

# A2A v0.3 serialised states in kebab case; normalise them on the way in.
_LEGACY_STATES = {
    "submitted": STATE_SUBMITTED,
    "working": STATE_WORKING,
    "input-required": STATE_INPUT_REQUIRED,
    "auth-required": STATE_AUTH_REQUIRED,
    "completed": STATE_COMPLETED,
    "failed": STATE_FAILED,
    "canceled": STATE_CANCELED,
    "cancelled": STATE_CANCELED,
    "rejected": STATE_REJECTED,
}

MAX_ARTIFACT_TEXT = 200_000
# Session ids and workspace names become index file names; anything else is ignored.
_INDEX_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def normalize_state(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("TASK_STATE_"):
        return text
    return _LEGACY_STATES.get(text.lower(), STATE_FAILED if text else STATE_SUBMITTED)


def is_final(task: dict) -> bool:
    """Terminal, or waiting on the caller: either way the worker has nothing left to do."""
    state = (task.get("status") or {}).get("state")
    return state in TERMINAL_STATES or state in INTERRUPTED_STATES


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def text_message(role: str, text: str, *, task_id: str | None = None, context_id: str | None = None) -> dict:
    message: dict[str, Any] = {"messageId": str(uuid.uuid4()), "role": role, "parts": [{"text": text}]}
    if task_id:
        message["taskId"] = task_id
    if context_id:
        message["contextId"] = context_id
    return message


def text_artifact(name: str, text: str, *, media_type: str | None = None) -> dict:
    part: dict[str, Any] = {"text": text[:MAX_ARTIFACT_TEXT]}
    if media_type:
        part["mediaType"] = media_type
    artifact: dict[str, Any] = {"artifactId": name, "name": name, "parts": [part]}
    if len(text) > MAX_ARTIFACT_TEXT:
        artifact["metadata"] = {"truncated": True, "length": len(text)}
    return artifact


def message_text(message: dict | None) -> str:
    """Concatenate the text parts of a message or artifact."""
    parts = (message or {}).get("parts") or []
    out: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if isinstance(part.get("text"), str):
            out.append(part["text"])
        elif part.get("data") is not None:
            out.append(json.dumps(part["data"], ensure_ascii=False, indent=2))
        elif part.get("url") or part.get("file"):
            ref = part.get("url") or (part.get("file") or {}).get("uri") or ""
            out.append(f"[file] {ref}".strip())
    return "\n".join(out)


def new_task(*, message: str, observal: dict) -> dict:
    task_id = str(uuid.uuid4())
    context_id = str(uuid.uuid4())
    return {
        "id": task_id,
        "contextId": context_id,
        "status": {"state": STATE_SUBMITTED, "timestamp": now_iso()},
        "history": [text_message("ROLE_USER", message, task_id=task_id, context_id=context_id)],
        "artifacts": [],
        "metadata": {"observal": {**observal, "createdAt": now_iso()}},
    }


def set_status(task: dict, state: str, text: str | None = None) -> dict:
    status: dict[str, Any] = {"state": state, "timestamp": now_iso()}
    if text:
        status["message"] = text_message("ROLE_AGENT", text, task_id=task["id"], context_id=task.get("contextId"))
    task["status"] = status
    return task


def meta(task: dict) -> dict:
    return task.setdefault("metadata", {}).setdefault("observal", {})


# ── Store ────────────────────────────────────────────────────────────────


def store_dir() -> Path:
    return STORE_DIR or (_lockfile.CONFIG_DIR / "delegations")


def task_path(task_id: str) -> Path:
    # Task ids are UUIDs we minted; refuse anything else so a caller-supplied
    # id can never name a path outside the store.
    return store_dir() / f"{uuid.UUID(str(task_id))}.json"


def task_dir(task_id: str) -> Path:
    path = store_dir() / str(uuid.UUID(str(task_id)))
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(task: dict) -> dict:
    path = task_path(task["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(task, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return task


def load(task_id: str) -> dict | None:
    try:
        path = task_path(task_id)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_tasks(limit: int = 50) -> list[dict]:
    root = store_dir()
    if not root.is_dir():
        return []
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out: list[dict] = []
    for path in files:
        task = load(path.stem)
        if task:
            out.append(task)
    return out


def _write_index(kind: str, key: str, task_id: str) -> None:
    path = store_dir() / kind / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(task_id, encoding="utf-8")


def _indexed_task(kind: str, key: str) -> dict:
    if not _INDEX_KEY.fullmatch(key):
        return {}
    try:
        task_id = (store_dir() / kind / key).read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    task = load(task_id) or {}
    return {**meta(task), "taskId": task.get("id")} if task else {}


def record_workspace(task: dict, root: Path) -> None:
    """Remember the workspace a task runs in: for session attribution, and for cleanup if its worker dies."""
    meta(task)["workspaceRoot"] = str(root)
    _write_index("workspaces", root.name, task["id"])


def record_child_session(task: dict, session_id: str | None) -> None:
    """Remember the child's session id, for session attribution."""
    if session_id and _INDEX_KEY.fullmatch(session_id):
        meta(task)["childSessionId"] = session_id
        _write_index("sessions", session_id, task["id"])


def delegated_agent(cwd: str = "", session_ids: tuple[str | None, ...] = ()) -> tuple[str, str | None] | None:
    """``(agent_id, version)`` of the delegated agent a session belongs to, if any.

    Matches the session (or its parent, for a child's own subagents) by id, or
    its working directory by the workspace it ran in. Hook deliveries usually
    know the cwd; ``observal reconcile`` often knows only the session id. The
    indexes live in the task store, which outlives the workspace, so late
    deliveries and recovery after a crash stay attributed, and the user's
    lockfile is never touched.
    """
    for session_id in filter(None, session_ids):
        m = _indexed_task("sessions", session_id)
        if m.get("agentId") and m.get("childSessionId") == session_id:
            return str(m["agentId"]), m.get("version")
    m = delegation_at(cwd) if cwd else None
    return (str(m["agentId"]), m.get("version")) if m and m.get("agentId") else None


def delegation_at(cwd: str) -> dict | None:
    """``metadata.observal`` (plus ``taskId``) of the delegated task whose workspace contains ``cwd``."""
    from observal_cli.delegation.workspace import ROOT_PREFIX

    parts = PurePath(cwd).parts
    index = next((i for i, part in enumerate(parts) if part.startswith(ROOT_PREFIX)), None)
    if index is None:
        return None
    m = _indexed_task("workspaces", parts[index])
    recorded = m.get("workspaceRoot")
    root = str(PurePath(*parts[: index + 1]))
    # realpath resolves symlinked directories (macOS /var) even after the workspace is gone.
    if not recorded or os.path.normcase(os.path.realpath(recorded)) != os.path.normcase(os.path.realpath(root)):
        return None
    return m


def count_children(parent_id: str) -> int:
    """How many tasks a delegated task started itself (children record it as ``parentTaskId``)."""
    root = store_dir()
    if not root.is_dir():
        return 0
    return sum(1 for path in root.glob("*.json") if meta(load(path.stem) or {}).get("parentTaskId") == parent_id)


def request_cancel(task_id: str) -> None:
    task_dir(task_id).joinpath("cancel").touch()


def cancel_requested(task_id: str) -> bool:
    return (store_dir() / str(task_id) / "cancel").exists()


def clear_cancel(task_id: str) -> None:
    with contextlib.suppress(OSError):
        (store_dir() / str(task_id) / "cancel").unlink()
