<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Operational workflows

## Contents

- Rankings and feedback
- Traces and sessions
- Telemetry diagnosis
- Logs

## Rankings and feedback

```bash
observal ops top --type agent --output json
observal ops top --type mcp --output json
observal ops feedback NAMESPACE/SLUG --type mcp --output json
```

Ratings are user mutations. Verify the returned rating:

```bash
observal ops rate NAMESPACE/SLUG --stars 5 --type mcp --comment 'Worked great' --output json
observal ops rate-update NAMESPACE/SLUG --type mcp --stars 4 --output json
observal ops rate-delete NAMESPACE/SLUG --type mcp --yes --output json
```

Stars accept 1 through 5. Deletion requires confirmation in JSON mode.

## Traces and sessions

Start with a narrow window and increase it only if needed.

```bash
observal ops traces --limit 20 --output json
observal ops traces --platform kiro --days 7 --output json
observal ops traces --turn --limit 5 --output json
observal ops traces --span --limit 3 --output json
```

Report filters, count, time range, platforms, and notable failure signals. Avoid reproducing raw prompts, tool arguments, or outputs unless they are needed and authorized.

Export sessions as OpenTelemetry traces to any OTLP/HTTP receiver (protobuf by default, `--protocol http/json` otherwise):

```bash
observal ops export-trace <session-id> --file traces.jsonl --output json
observal ops export-trace --recent 20 --endpoint http://localhost:4318 --output json
```

Only add `--include-content` when the user asked to send prompts and outputs to that destination. Never print or log header values.

## Telemetry diagnosis

```bash
observal ops telemetry status --output json
```

Inspect server event counts, local outbox state, warnings, and health fields. Diagnose in this order:

1. Authentication and server reachability.
2. Local outbox backlog or delivery failure.
3. Whether recent sessions exist for the requested harness.
4. Hook or extension installation with `doctor`.
5. Reconciliation only for sessions that were missed.

Use core diagnosis before patching:

```bash
observal doctor --output json
observal doctor patch --harness kiro --dry-run --output json
```

Do not fabricate synthetic telemetry or telemetry environment variables.

## Logs

Use a finite read by default:

```bash
observal ops logs --no-follow --output json
observal ops logs --remote --level WARNING --output json
```

Following logs emit JSON Lines. Remote logs require admin authority. Summarize relevant events and redact tokens, credentials, request bodies, and customer data.
