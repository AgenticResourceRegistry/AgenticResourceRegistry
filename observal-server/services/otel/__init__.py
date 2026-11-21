# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OpenTelemetry (OTLP) output for stored sessions.

Sessions are stored as raw harness transcript lines in ``session_events``;
nothing here changes that.  This package derives OTLP from the stored rows:

- ``types``: the ``Span`` model every projector returns.
- ``ids``: deterministic trace and span IDs, timestamp parsing.
- ``attributes``: OTLP attribute values and the GenAI semantic-convention helpers.
- ``encode``: ``Span`` objects to an OTLP request, as protobuf or OTLP/JSON bytes.
- ``projectors``: per-harness ``rows -> spans`` projection, dispatched on the
  registry's ``session_parser`` key.
"""
