# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Admin status for OTLP forwarding. Carries IDs, flags and counts; never URLs or headers."""

from datetime import datetime

from pydantic import BaseModel


class OtlpLastDelivery(BaseModel):
    status: str
    status_code: int | None = None
    rejected: int = 0
    error: str | None = None
    timestamp: str


class OtlpSignalStatus(BaseModel):
    signal: str
    sessions_behind: int
    lines_behind: int
    last_delivery: OtlpLastDelivery | None = None


class OtlpDestinationStatus(BaseModel):
    id: str
    enabled: bool
    protocol: str
    include_content: bool
    added_at: datetime | None = None
    paused: bool
    signals: list[OtlpSignalStatus]


class OtlpForwardingStatus(BaseModel):
    destinations: list[OtlpDestinationStatus]
