<!-- SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# OpenTelemetry forwarding

Observal can forward every stored session record to one or more OTLP/HTTP
destinations as it arrives: an OpenTelemetry Collector, Grafana, Datadog,
Elastic, or any backend that accepts OTLP logs. Each stored transcript line
becomes one OTLP log record, so nothing is reshaped or dropped on the way.

Forwarding runs in the background worker. Ingest only queues a job, so a slow
or unavailable destination never slows down session delivery, and nothing is
lost while it is down: each destination keeps its own position and catches up
when it comes back.

## What is sent

| OTLP field | Value |
| --- | --- |
| Resource `service.name` | The harness, for example `claude-code` |
| `timeUnixNano` / `observedTimeUnixNano` | When the record happened / when Observal stored it |
| `body` | The stored line, after Observal's secret redaction. Only with `include_content` |
| `traceId` | Derived from the root session ID, the same trace an [OpenTelemetry export](opentelemetry-export.md) uses |
| `session.id`, `observal.line_offset`, `observal.line_hash` | Which record this is. A destination can de-duplicate on these three |
| `observal.event_type`, `observal.uuid`, `observal.parent_uuid` | Observal's classification and the transcript's own links |
| `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.response.model` | Tool and model, when the record has them |
| `observal.usage.*` | Token counts as stored on the record. Some harnesses repeat one response's usage on every line, so don't sum these |
| `user.id`, `gen_ai.agent.id`, `observal.agent.version` | Who ran the session, with which agent |
| `observal.record.synthetic`, `observal.raw_line_truncated`, `observal.forward.replay` | Set only when true; see [Delivery](#delivery) for replays |

Without `include_content`, records still carry structure, timing, tools,
models and token counts, but no prompt, response or tool payload text.

## Add a destination

Destinations are one admin setting, `telemetry.otlp_forward_destinations`, a
JSON list. It is stored encrypted, because headers carry credentials.

```bash
observal admin set telemetry.otlp_forward_destinations '[
  {
    "id": "collector",
    "url": "https://otel-collector.example.com",
    "signals": ["logs"],
    "protocol": "http/protobuf",
    "headers": {"Authorization": "Bearer <token>"},
    "include_content": false,
    "enabled": true
  }
]'
```

| Field | Default | Meaning |
| --- | --- | --- |
| `id` | required | Lowercase letters, digits, `-` and `_`. Keep it stable: the destination's position is stored under it |
| `url` | required | The OTLP/HTTP base URL, `https` only. `/v1/logs` is appended |
| `signals` | `["logs"]` | `logs` today |
| `protocol` | `http/protobuf` | Or `http/json` |
| `headers` | `{}` | Sent with every request |
| `include_content` | `false` | Send the stored line as the record body |
| `enabled` | `true` | Set `false` to stop without losing the destination's position |

The setting holds the whole list, so include every destination each time you
set it. Saving checks every destination and rejects the list with a message
naming the destination and field at fault. A destination's URL must not
resolve to a private or internal address.

A destination receives sessions that start after it was first saved. Re-saving
the list keeps each destination's original start point.

## Delivery

- **Retries.** 5xx, 408, 429 and connection errors are retried up to 5 times,
  with backoff or the server's `Retry-After` (at most 60 s). After that the
  worker's sweep, which runs every minute, tries again.
- **Final failures.** Any other status, such as 401 or 404, pauses that
  destination for an hour, since retrying cannot fix it. Saving the
  destinations again lifts the pause at once. Other destinations keep flowing.
- **Ordering and duplicates.** Records are sent in order, and a destination's
  position moves only after a 2xx, so delivery is at least once. A retry after
  a crash can repeat a chunk; de-duplicate on `session.id`,
  `observal.line_offset` and `observal.line_hash` if that matters.
- **Replays.** If a client's integrity check finds a session's stored lines
  are wrong, Observal re-reads them from the client. Records sent before the
  repair are sent again with `observal.forward.replay = true`.

## Check forwarding status

```bash
observal api GET /api/v1/admin/otlp-forwarding --output json
```

For each destination, this reports whether it is paused, how many sessions and
lines it is behind, and its last delivery attempt. It never returns URLs or
headers.

## Langfuse and LangSmith

Langfuse and LangSmith accept traces, not logs. Use an
[OpenTelemetry export](opentelemetry-export.md) for them today; forwarding
traces is planned.
