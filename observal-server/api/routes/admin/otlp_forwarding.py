# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Admin status for OTLP forwarding: lag, pauses and the last delivery per destination."""

from fastapi import Depends

from api.deps import require_role
from models.user import User, UserRole
from schemas.otlp_forwarding import (
    OtlpDestinationStatus,
    OtlpForwardingStatus,
    OtlpLastDelivery,
    OtlpSignalStatus,
)

from ._router import router


@router.get("/otlp-forwarding", response_model=OtlpForwardingStatus)
async def otlp_forwarding_status(current_user: User = Depends(require_role(UserRole.admin))):
    """How far each destination is behind. Never returns a destination's URL or headers."""
    from services.clickhouse import query_forward_lag, query_last_forward_delivery
    from services.otel.destinations import load_destinations
    from services.otel.forwarder import ForwardStore

    store = ForwardStore()
    statuses: list[OtlpDestinationStatus] = []
    for destination in await load_destinations():
        signals: list[OtlpSignalStatus] = []
        for signal in destination.signals:
            lag = await query_forward_lag(destination.id, signal, destination.added_at)
            last = await query_last_forward_delivery(destination.id, signal)
            signals.append(
                OtlpSignalStatus(
                    signal=signal,
                    sessions_behind=lag["sessions_behind"],
                    lines_behind=lag["lines_behind"],
                    last_delivery=OtlpLastDelivery(
                        status=str(last["status"]),
                        status_code=int(last["status_code"]) if last.get("status_code") is not None else None,
                        rejected=int(last.get("rejected") or 0),
                        error=last.get("error"),
                        timestamp=str(last["timestamp"]),
                    )
                    if last
                    else None,
                )
            )
        statuses.append(
            OtlpDestinationStatus(
                id=destination.id,
                enabled=destination.enabled,
                protocol=destination.protocol,
                include_content=destination.include_content,
                added_at=destination.added_at,
                paused=await store.is_paused(destination.id),
                signals=signals,
            )
        )
    return OtlpForwardingStatus(destinations=statuses)
