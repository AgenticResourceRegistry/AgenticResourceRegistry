# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OTLP attribute values and GenAI semantic-convention helpers.

Attributes follow the OpenTelemetry GenAI semantic conventions (``gen_ai.*``)
and nothing vendor specific.  Content is clipped here, when a projector builds
it, so a projected ``Span`` already holds exactly what would be sent.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

MAX_CONTENT_CHARS = 32_000

# Observal token column -> GenAI semconv attribute.
TOKEN_KEYS = {
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
    "cache_read_tokens": "gen_ai.usage.cache_read.input_tokens",
    "cache_creation_tokens": "gen_ai.usage.cache_creation.input_tokens",
    "cache_write_tokens": "gen_ai.usage.cache_creation.input_tokens",
}


def to_int(value: object) -> int:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def clip(text: str) -> str:
    if len(text) <= MAX_CONTENT_CHARS:
        return text
    return text[:MAX_CONTENT_CHARS] + "...[truncated]"


def json_or_text(value: str) -> object:
    try:
        return json.loads(value)
    except ValueError:
        return value


def pairs(values: Mapping[str, object]) -> tuple[tuple[str, object], ...]:
    """Attribute pairs for a ``Span``, dropping empty values and freezing lists."""
    return tuple(
        (key, tuple(value) if isinstance(value, list) else value)
        for key, value in values.items()
        if value is not None and value != ""
    )


def messages(role: str, parts: list[dict], finish_reason: str | None = None) -> str | None:
    """Encode one GenAI semconv message (``gen_ai.*.messages``) as a JSON string.

    Each part's ``content`` is clipped rather than the JSON string, so the
    result is always valid JSON.
    """
    if not parts:
        return None
    clipped = [{**part, "content": clip(part["content"])} if "content" in part else part for part in parts]
    message: dict = {"role": role, "parts": clipped}
    if finish_reason:
        message["finish_reason"] = finish_reason
    return json.dumps([message], ensure_ascii=False)


def semconv_input_tokens(tokens: Mapping[str, int]) -> int:
    """Total input tokens as the GenAI conventions define them.

    Observal stores ``input_tokens`` without cache reads and writes (the
    Anthropic convention its insights also assume).  ``gen_ai.usage.input_tokens``
    includes them, and backends derive uncached input by subtracting the
    ``cache_*`` counts, so they are added back here.
    """
    cache_writes = tokens.get("cache_creation_tokens", 0) or tokens.get("cache_write_tokens", 0)
    return tokens.get("input_tokens", 0) + tokens.get("cache_read_tokens", 0) + cache_writes


def any_value(value: object) -> dict:
    """An OTLP/JSON ``AnyValue``."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, list | tuple):
        return {"arrayValue": {"values": [any_value(item) for item in value]}}
    return {"stringValue": str(value)}


def to_attributes(values: Iterable[tuple[str, object]]) -> list[dict]:
    """OTLP/JSON ``KeyValue`` list, skipping empty values."""
    return [{"key": key, "value": any_value(value)} for key, value in values if value is not None and value != ""]
