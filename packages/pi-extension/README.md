<!--
SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
SPDX-License-Identifier: Apache-2.0
-->

# observal-pi

Session telemetry extension for [Pi](https://pi.dev) that pushes conversation traces to your [Observal](https://obs-sync.dev) server.

## Install

```bash
observal doctor patch --harness pi
```

Doctor installs the bundled TypeScript extension directly at `~/.pi/agent/extensions/observal.ts` and records the CLI version it came from in `~/.pi/agent/extensions/.observal-extension.json`. `observal auth login` does the same automatically.

Alternatively, register this package in `~/.pi/agent/settings.json`:

```json
{ "packages": ["npm:observal-pi"] }
```

The two modes are mutually exclusive. With the npm package registered, Observal never writes the local file; it only reports when a pinned version has fallen behind the CLI. Pi loads both channels if both are present, which sends every session twice, so doctor removes a local copy it recognizes as its own once npm is configured (keeping it as `observal.ts.bak`). A local file Observal did not write is always left alone.

## Prerequisites

1. An Observal account (run `observal auth login` to authenticate)
2. Pi installed (`>=0.74.0`)

## What it does

- **Incremental push:** After each user prompt (`agent_end`), durably stages new JSONL lines before sending them to Observal
- **Acknowledged checkpoints:** Advances byte and line cursors only after a contiguous server acknowledgement
- **Final push:** On session exit, sends remaining lines and a SHA-256 audit manifest; mismatches replay from the requested range
- **Crash recovery:** Retries durable pending batches and rebuilds missing/corrupt cursors from the authenticated server checkpoint
- **Status indicator:** Shows `● observal` in the footer with line count
- **Installed-item notices (Pi pilot):** Interactive/RPC startup launches a non-blocking, detached `observal _startup-apply` worker. Frozen accounts receive notices only; the worker never installs without account-scoped `observal unfreeze`. Verified newer versions and author release notes are shown via Pi notifications; late results remain under `~/.observal/update-notices/` for the next UI-capable session. Print/JSON modes do not launch a worker. **Automatic installation remains off by default.** `observal unfreeze` on the locally authenticated account enables eligible UI sessions; only unedited, Observal-owned Pi user-scope installs with approved releases can be modified (agent profiles with their bundled skills and `mcp.json`, registry-direct standalone skills, and `registry mcp install --managed` MCP entries); everything else stays manual and the notice says why. Do not enable it on a production profile until the pilot is complete. The worker must not be killed during commit; Pi shutdown may precede the notice, which is replayed later. A private write-ahead `.pending` notice is persisted before the normal `agent pull` subprocess; if its result cannot be verified, the next UI session warns that files may have changed and further automatic installs for that account/registry are blocked until the local state is inspected. For a stopped installer, this pilot keeps a private backup of the old owned files. It restores them only when the lock and baseline bytes/modes are unchanged and every file matches either old or planned bytes **and mode**; otherwise the pending notice retains the backup for manual inspection and blocks retries. This is not a transactional guarantee. Updated saved profiles are **not** active in the current session or automatically copied into an active profile: select the agent again with `/agent` and reload. Use `observal outdated` for an explicit fresh check.

## Commands

| Command | Description |
|---------|-------------|
| `/obs-sync` | Show sync status (lines pushed, server URL) |
| `/obs-sync flush` | Force push pending lines now |
| `/obs-sync config` | Show config file path and server URL |

## Design

- **Zero dependencies**: only `node:*` built-ins
- **Fail-open**: never throws, never crashes pi. If the server is unreachable, pi continues normally
- **5s timeout**: telemetry HTTP calls abort after 5 seconds; the separate startup update worker admits installs for at most 90 seconds (registry calls stop 15 seconds earlier) and is never killed once an installer has started
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
