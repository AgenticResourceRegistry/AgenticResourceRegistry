# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""CLI-side audit event emission.

Best-effort POST to the server. Never blocks CLI UX.
"""

from __future__ import annotations

import threading
import uuid
from contextvars import copy_context
from datetime import UTC, datetime
from http import HTTPStatus

from loguru import logger as optic


def emit_cli_audit(
    action: str,
    *,
    resource_type: str = "",
    resource_id: str = "",
    resource_name: str = "",
    detail: str = "",
    sensitivity: str = "standard",
) -> None:
    """Fire-and-forget audit event to the server."""
    from observal_cli.config import load

    cfg = load()
    if not cfg.get("api_key") and not cfg.get("access_token"):
        return
    server_url = cfg.get("server_url", "").rstrip("/")
    token = cfg.get("api_key") or cfg.get("access_token", "")
    if not server_url or not token:
        return

    event = {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:23],
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "resource_name": resource_name,
        "detail": detail,
        "sensitivity": sensitivity,
        "source": "cli",
    }

    def _send():
        try:
            import httpx

            resp = httpx.post(
                f"{server_url}/api/v1/audit/cli-event",
                json=event,
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )
            if resp.status_code >= HTTPStatus.BAD_REQUEST:
                optic.debug("cli audit POST failed: status={}", resp.status_code)
        except Exception as e:
            from observal_cli.client import _pull_reporting_secrets

            if _pull_reporting_secrets.get() is not None:
                # The exception may contain a reflected request URL or header.
                optic.debug("cli audit POST error: {}", type(e).__name__)
            else:
                optic.debug("cli audit POST error: {}", e)

    # Thread creation happens inside pull's reporting scope, but a new thread
    # otherwise loses the context used by the HTTPX/httpcore log filters.
    context = copy_context()
    threading.Thread(target=lambda: context.run(_send), daemon=True).start()
