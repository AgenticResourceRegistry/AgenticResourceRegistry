# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Fire-and-forget audit logging for registry actions."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from loguru import logger as optic

from services.clickhouse import insert_audit_log

# prevent GC of fire-and-forget tasks (same pattern as telemetry.py)
_background_tasks: set[asyncio.Task] = set()


async def _emit(audit: dict):
    """Insert an audit_log entry. Swallows errors."""
    optic.trace("recording registry audit event: {}", audit)
    try:
        await insert_audit_log([audit])
    except Exception:
        optic.error("Registry audit write failed")


def emit_registry_event(
    *,
    action: str,
    user_id: str,
    user_email: str = "",
    user_role: str = "",
    agent_id: str | None = None,
    resource_name: str = "",
    resource_type: str = "agent",
    metadata: dict[str, str] | None = None,
) -> None:
    """Fire-and-forget a registry audit_log entry into ClickHouse."""
    optic.trace("recording registry action: {}", action)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    meta = metadata or {}
    audit = {
        "event_id": str(uuid.uuid4()),
        "timestamp": now,
        "actor_id": user_id,
        "actor_email": user_email,
        "actor_role": user_role,
        "action": action,
        "resource_type": resource_type,
        "resource_id": agent_id or "",
        "resource_name": resource_name,
        "detail": ", ".join(f"{k}={v}" for k, v in meta.items()) if meta else "",
    }

    task = asyncio.create_task(_emit(audit))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
