# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OTLP forwarding destinations.

Destinations live in one encrypted dynamic setting,
``telemetry.otlp_forward_destinations``, as a JSON list::

    [{"id": "collector", "url": "https://otel.example.com", "signals": ["logs"],
      "protocol": "http/protobuf", "headers": {"Authorization": "Bearer ..."},
      "include_content": false, "enabled": true, "start_from": "now"}]

``url`` is the OTLP/HTTP base URL; the signal path (``/v1/logs``) is appended.
``added_at`` is stamped when a destination is first saved and decides which
sessions it receives: only sessions that start after it was added.

Validation errors name the destination and the field, never a header value.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlparse

from loguru import logger as optic
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

SETTING_KEY = "telemetry.otlp_forward_destinations"

Signal = Literal["logs", "traces"]
# Traces are forwarded once span projection is wired into the forwarder.
SUPPORTED_SIGNALS: frozenset[str] = frozenset({"logs"})

_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class DestinationError(ValueError):
    """An invalid destination list. The message is safe to show and log."""


class Destination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    url: str
    signals: tuple[Signal, ...] = ("logs",)
    protocol: Literal["http/protobuf", "http/json"] = "http/protobuf"
    headers: dict[str, str] = Field(default_factory=dict)
    include_content: bool = False
    enabled: bool = True
    start_from: Literal["now"] = "now"
    added_at: datetime | None = None

    @field_validator("url")
    @classmethod
    def url_is_https_base(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme != "https":
            raise ValueError("must be an https URL")
        if not parsed.hostname:
            raise ValueError("must include a host")
        if parsed.username or parsed.password:
            raise ValueError("must not carry credentials; put them in headers")
        if parsed.query or parsed.fragment:
            raise ValueError("must not have a query string or fragment")
        return value.strip().rstrip("/")

    @field_validator("signals")
    @classmethod
    def signals_are_supported(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("must name at least one signal")
        if len(set(value)) != len(value):
            raise ValueError("must not repeat a signal")
        for signal in value:
            if signal not in SUPPORTED_SIGNALS:
                raise ValueError(f"signal '{signal}' is not supported yet")
        return value

    @field_validator("headers")
    @classmethod
    def headers_are_well_formed(cls, value: dict[str, str]) -> dict[str, str]:
        for name, header_value in value.items():
            if not _HEADER_NAME_RE.match(name):
                raise ValueError(f"header name '{name}' is not valid")
            if "\r" in header_value or "\n" in header_value:
                raise ValueError(f"header '{name}' must not contain line breaks")
        return value

    @field_validator("added_at")
    @classmethod
    def added_at_is_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    def endpoint(self, signal: str) -> str:
        """The OTLP/HTTP URL for ``signal``, e.g. ``<url>/v1/logs``."""
        return f"{self.url}/v1/{signal}"


def _describe(exc: ValidationError) -> str:
    # include_input=False keeps header values (and any other input) out of the message.
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'value'}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
    )


def parse_destinations(raw: str | None) -> list[Destination]:
    """Parse and validate a stored or submitted destination list."""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        raise DestinationError("destinations must be a JSON list") from None
    if not isinstance(data, list):
        raise DestinationError("destinations must be a JSON list")

    destinations: list[Destination] = []
    for index, item in enumerate(data):
        label = item.get("id") if isinstance(item, dict) and isinstance(item.get("id"), str) else f"#{index + 1}"
        try:
            destinations.append(Destination.model_validate(item))
        except ValidationError as exc:
            raise DestinationError(f"destination {label}: {_describe(exc)}") from None

    seen: set[str] = set()
    for destination in destinations:
        if destination.id in seen:
            raise DestinationError(f"destination {destination.id}: id is used more than once")
        seen.add(destination.id)
    return destinations


def prepare_setting_value(raw: str, *, previous: list[Destination], now: datetime) -> tuple[str, list[Destination]]:
    """Validate an admin's new destination list and return the value to store.

    A destination keeps the ``added_at`` it was first saved with, so re-saving
    the list (the stored value is never shown back) does not move its start
    point. New destinations are stamped with ``now``. Resolving each host for
    the SSRF check does DNS, so call this off the event loop.
    """
    from services.ssrf_guard import is_private_url

    destinations = parse_destinations(raw)
    first_added = {d.id: d.added_at for d in previous if d.added_at is not None}
    stamped: list[Destination] = []
    for destination in destinations:
        if is_private_url(destination.url):
            raise DestinationError(f"destination {destination.id}: url resolves to a private or internal address")
        added_at = first_added.get(destination.id) or destination.added_at or now
        stamped.append(destination.model_copy(update={"added_at": added_at}))
    return json.dumps([d.model_dump(mode="json") for d in stamped]), stamped


async def load_destinations() -> list[Destination]:
    """The configured destinations. An invalid stored value disables forwarding."""
    import services.dynamic_settings as ds

    raw = await ds.get(SETTING_KEY)
    try:
        return parse_destinations(raw)
    except DestinationError as exc:
        optic.warning("otlp forwarding is off: stored destinations are invalid: {}", exc)
        return []
