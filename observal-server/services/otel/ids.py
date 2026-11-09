# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic OTLP IDs and timestamp parsing.

Trace and span IDs are hashes of the session ID and a stable per-span key, so
projecting the same rows again always yields the same IDs.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

_FRACTION_RE = re.compile(r"(\.\d{6})\d+")
_EPOCH_SENTINEL = "1970-01-01"


def trace_id_for(session_id: str) -> str:
    """32-hex-char trace ID for a (root) session."""
    return hashlib.sha256(f"observal-trace:{session_id}".encode()).hexdigest()[:32]


def span_id(session_id: str, key: str) -> str:
    """16-hex-char span ID for ``key`` within a session."""
    return hashlib.sha256(f"observal-span:{session_id}:{key}".encode()).hexdigest()[:16]


def to_unix_nanos(value: object) -> int | None:
    """Parse a stored or transcript timestamp into Unix nanoseconds (UTC)."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float) or (isinstance(value, str) and value.strip().isdigit()):
        number = float(value)
        if number <= 0:
            return None
        # Millisecond epochs are 13 digits; second epochs are 10.
        return int(number * 1_000_000) if number > 1e11 else int(number * 1_000_000_000)
    text = str(value).strip()
    if not text or _EPOCH_SENTINEL in text:
        return None
    text = text.replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]
    text = _FRACTION_RE.sub(r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delta = parsed - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000
