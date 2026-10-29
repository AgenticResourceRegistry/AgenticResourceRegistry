<!--
SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
SPDX-License-Identifier: Apache-2.0
-->

# observal-pi

Session telemetry extension for [Pi](https://pi.dev) that pushes conversation traces to your [Agentic Resource Registry](https://obs-sync.dev) server.

## Install

```bash
observal doctor patch --harness pi
```

Doctor installs the bundled TypeScript extension directly at `~/.pi/agent/extensions/observal.ts` and records the CLI version it came from in `~/.pi/agent/extensions/.observal-extension.json`. `observal auth login` does the same automatically. A local file Agentic Resource Registry did not write is always left alone.

This package is not published to npm. The CLI wheel is the only distribution channel.

## Maintainer packaging constraint

`extensions/observal.ts` is the Pi entrypoint **and** the file the Python wheel bundles and `observal doctor patch --harness pi` copies to a user's extensions directory. Do not extract runtime helpers into imported source files: a local CLI install receives only this file, so relative imports would fail there. A future source-module split needs a reproducible single-file build artifact, a wheel packaging check, and tests of the copied local file. Keep the artifact's zero-runtime-dependency and fail-open behavior. The extension's current boundaries are agent-switch transactions, registry hook execution, layer/pin verification, and acknowledged session delivery; keep those contracts distinct when extracting modules.

## Prerequisites

1. An Agentic Resource Registry account (run `observal auth login` to authenticate)
2. Pi installed (`>=0.74.0`)

## What it does

- **Incremental push:** After each user prompt (`agent_end`), durably stages new JSONL lines before sending them to Agentic Resource Registry
- **Acknowledged checkpoints:** Advances byte and line cursors only after a contiguous server acknowledgement
- **Final push:** On session exit, sends remaining lines and a SHA-256 audit manifest; mismatches replay from the requested range
- **Crash recovery:** Retries durable pending batches and rebuilds missing/corrupt cursors from the authenticated server checkpoint
- **Status indicator:** Shows `● observal` in the footer with line count

## Commands

| Command | Description |
|---------|-------------|
| `/obs-sync` | Show sync status (lines pushed, server URL) |
| `/obs-sync flush` | Force push pending lines now |
| `/obs-sync config` | Show config file path and server URL |

## Design

- **Zero dependencies**: only `node:*` built-ins
- **Fail-open**: never throws, never crashes pi. If the server is unreachable, pi continues normally
- **5s timeout**: all HTTP calls abort after 5 seconds
- **Chunked uploads**: batches of 500 lines max per request
- **Retry-safe**: pending batches retain stable source indexes and are retried until acknowledged

## Configuration

The extension reads credentials from `~/.observal/config.json` (written by `observal auth login`):

```json
{
  "server_url": "https://your-server.observal.dev",
  "access_token": "..."
}
```

Acknowledged cursors are stored atomically in `~/.observal/sync_state.json`. Unacknowledged Pi batches remain in `~/.observal/pi_session_outbox/` until the server confirms a contiguous checkpoint.

## License

Apache-2.0. See [LICENSE](./LICENSE)
