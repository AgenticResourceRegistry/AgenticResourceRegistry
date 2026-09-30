<!-- SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# OpenTelemetry export

Observal can export a stored session as an OpenTelemetry trace over standard
OTLP, so any OpenTelemetry-compatible backend can ingest it: an OpenTelemetry
Collector, Jaeger, Grafana Tempo, Arize Phoenix, Langfuse, or a commercial
APM. The export uses only the OpenTelemetry GenAI semantic conventions; there
is no vendor-specific code or configuration.

Sessions are still stored as raw transcript lines. The trace is built when you
export it: a per-harness projector reads the stored lines and builds spans
from the transcript's own IDs (response IDs, tool call IDs, parent links), so
projector fixes improve past sessions too, and ingestion integrity checks keep
working on the original lines.

## Harness support

| Harness | Export |
| --- | --- |
| Claude Code | yes |
| Other harnesses | not yet; the API answers `422` |

Projectors are added one harness at a time.

## Export from the CLI

Print sessions as OTLP/JSON Lines (one request per session):

```bash
observal ops export-trace <session-id>
```

Write the most recent sessions to a file:

```bash
observal ops export-trace --recent 20 --file traces.jsonl
```

Push to any OTLP/HTTP endpoint. `/v1/traces` is appended unless the URL
already ends with it, matching `OTEL_EXPORTER_OTLP_ENDPOINT`. Each session is
sent as its own request.

```bash
observal ops export-trace --recent 20 --endpoint http://localhost:4318
```

| Flag | Purpose |
| --- | --- |
| `SESSION_ID...` | Sessions to export |
| `--recent N` | Also export the N most recent sessions (same list as `observal ops traces`) |
| `--include-content` | Include prompts, responses and tool input/output |
| `--file PATH` | Write OTLP/JSON Lines to a file |
| `--endpoint URL` | Push to an OTLP/HTTP endpoint |
| `--protocol` | `http/protobuf` (default) or `http/json` |
| `--header KEY=VALUE` | Header for `--endpoint`, repeatable |
| `--output json` | Print the export summary as JSON |

Pass credentials through environment variables so they do not end up in your
shell history. Header values are never printed.

### Choosing a protocol

OTLP/HTTP has no content negotiation, so the exporter cannot detect what a
receiver accepts. As with the OpenTelemetry SDKs, you choose it:

* `http/protobuf` is the OTLP default and the encoding every OTLP/HTTP
  receiver supports. Use it unless a receiver documents otherwise.
* `http/json` is available for receivers that prefer JSON.
* If a receiver answers `415 Unsupported Media Type`, the error names the
  other protocol to retry with.
* For gRPC-only backends, run an OpenTelemetry Collector with an OTLP/HTTP
  receiver and let it forward over gRPC or any other exporter.

Rejected spans reported by the receiver (OTLP partial success) are shown per
session in either protocol.

### Files

Files and stdout use the OTLP/JSON Lines format, one `ExportTraceServiceRequest`
per line. The Collector's `otlpjsonfile` receiver can replay them into any
pipeline.

## Export from the API

```
GET /api/v1/sessions/{session_id}/otlp?encoding=json&include_content=false
```

`encoding` is `json` (default) or `protobuf`. The body is an OTLP
`ExportTraceServiceRequest` that can be POSTed unchanged to any `/v1/traces`
endpoint accepting that encoding. The `X-Observal-Span-Count` header carries
the number of spans. Access follows the session detail endpoint: users export
their own sessions, and admins with trace access export any session.

## Verified receivers

| Receiver | Version | `http/protobuf` | `http/json` |
| --- | --- | --- | --- |
| OpenTelemetry Collector (contrib) | 0.137.0 | yes | yes |
| Langfuse (self-hosted) | 4.46.0 | yes | yes |
| Jaeger | 1.62.0 | not tested | yes |

Example endpoints:

| Backend | `--endpoint` | `--header` |
| --- | --- | --- |
| OpenTelemetry Collector | `http://<host>:4318` | as configured |
| Langfuse | `https://<langfuse-host>/api/public/otel` | `Authorization=Basic <base64 of public_key:secret_key>` |

Other backends work if they accept OTLP/HTTP traces; see their OTLP ingestion
documentation for the endpoint and authentication header.

## Trace shape

Each session, with its subagents, becomes one trace:

```
invoke_agent claude-code            the session
  turn N                            one per user prompt
    chat <model>                    one per model response
      execute_tool <tool>           one per tool call, under the response that made it
        invoke_agent subagent       a subagent, under the Agent tool call that spawned it
```

| Span | Key attributes |
| --- | --- |
| Session | `gen_ai.operation.name=invoke_agent`, `session.id` (the root session, also on subagents), `gen_ai.conversation.id`, `user.id`, `gen_ai.agent.id`, `observal.harness`, `observal.agent.version`, `observal.parent_session.id` on subagents |
| Model call | `gen_ai.operation.name=chat`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens`, `gen_ai.response.finish_reasons`; error status for API error responses |
| Tool call | `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, `gen_ai.tool.call.id`; error status when the tool result reports an error; `observal.tool.incomplete` for a call that never got a result, `observal.subagent.id` for an Agent call |

Token usage is counted once per model response. Claude Code writes a response
over several transcript lines and repeats its usage on each; the export takes
it once.

With `--include-content`, content is set on the standard attributes:
`gen_ai.input.messages` and `gen_ai.output.messages` (model responses,
reasoning and tool calls as message parts) and `gen_ai.tool.call.arguments` /
`gen_ai.tool.call.result`. A plain-text `input.value` / `output.value` copy is
added because many backends display only that pair.

`gen_ai.usage.input_tokens` follows the semantic conventions and includes
cache reads and writes. Observal's own dashboards count input without cache,
so exported input totals are higher by the cached amount; backends subtract
the `cache_*` counts to show uncached input.

System records, compaction, interruptions and injected meta messages become
span events on their turn.

## Limitations

* **Durations are derived.** Transcripts record one timestamp per record, not
  start and end times. A model call runs from the previous record to its
  response, and a tool call runs from the call to its result. Waterfalls are
  accurate to the transcript, not to API latency.
* **Model input is not reconstructed.** A model call carries its output
  messages. The full prompt context sent to the model is not rebuilt.
* **Re-exports duplicate on append-only backends.** Trace and span IDs are
  derived from the session, so a re-export reuses them, but Jaeger and
  Langfuse v4 store every copy. Export finished sessions once.
* **Content is redacted and capped.** Secrets are redacted at ingest, and each
  content attribute is capped at 32,000 characters.
* **An export treats the session as finished.** A tool call still waiting for
  its result is exported with zero duration and `observal.tool.incomplete`.
* **Replayed history is skipped.** When Claude Code resumes or forks a session
  it appends copies of earlier records; they add no spans.
* **Lines cut at ingest lose detail.** A line longer than the ingest limit is
  stored truncated; its tool call is still paired from the stored columns, but
  its content is gone.
