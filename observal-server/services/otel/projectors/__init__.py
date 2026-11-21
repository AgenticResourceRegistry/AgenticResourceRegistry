# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Per-harness span projectors: stored rows -> complete spans.

Dispatches on the ``session_parser`` key in
``observal_shared.harness_registry.HARNESS_REGISTRY``, the same key
``services.session_parsers`` uses.  A harness without a projector gets
``None``, and the forwarder sends it as logs only, so projectors can be added
one harness at a time.

Contract for every projector:

- ``project_spans`` is a pure function of the rows with deterministic IDs.
- It is **monotonic**: every span in the projection of a prefix of a
  session's rows appears, identical, in the projection of any longer prefix.
  A span is included only once it is complete; ``session_closed=True`` closes
  whatever is still open.
- ``record_span_ids`` maps each row's ``line_offset`` to the span it belongs
  to, including spans that are still open.

When adding a projector, add a module under ``projectors/`` and register it in
``_PROJECTORS`` below under its parser ID.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from services.otel.types import Row, Span


class Projector(Protocol):
    def project_spans(self, rows: Sequence[Row], *, session_closed: bool) -> list[Span]: ...

    def record_span_ids(self, rows: Sequence[Row]) -> Mapping[int, str]: ...  # includes open spans


# Maps session_parser ID -> projector.
_PROJECTORS: dict[str, Projector] = {}


def get_projector(harness: str) -> Projector | None:
    """The span projector for ``harness``, or ``None`` when it has none yet."""
    from observal_shared.harness_registry import HARNESS_REGISTRY

    parser_id = HARNESS_REGISTRY.get(harness, {}).get("session_parser")
    if parser_id is None:
        return None
    return _PROJECTORS.get(parser_id)
