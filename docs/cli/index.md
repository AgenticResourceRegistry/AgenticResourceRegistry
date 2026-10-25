---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Nithin-Bhargav-07 <gaddamnithinbhargav@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Overview"
---

Complete reference for the `observal` CLI. Every subcommand has its own page; this overview is the index.

> **New to Observal?** Start with [Quickstart](/getting-started/quickstart) and come back here when you need a specific command.

## Command groups

| Command                                                                                    | What it does                                                                          |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| [`observal api`](/cli/api)                                                                   | Call authenticated JSON endpoints without a dedicated command                         |
| [`observal auth`](/cli/auth)                                                                 | Authentication and account management                                                 |
| [`observal config`](/cli/config)                                                             | Local CLI configuration, aliases                                                      |
| [`observal discover`](/cli/discover)                                                         | Find approved resources for a task and use them in the current session                |
| [`observal scan`](/cli/scan)                                                                 | Discover what's installed across your harnesses (read-only)                           |
| [`observal share`](/cli/share)                                                               | Share and pull scoped, version-pinned repository Agent sets                           |
| [`observal outdated`](/cli/outdated)                                                         | Compare installed agent and component versions with the active registry               |
| [`observal reconcile`](/cli/reconcile)                                                       | Backfill sessions missed by automatic telemetry delivery                              |
| [`observal inbox`](/cli/inbox)                                                               | Read and update the signed-in user's work and event feed                               |
| [`observal agent pull`](/cli/pull)                                                           | Install a published agent into a harness                                               |
| [`observal registry`](/cli/registry)                                                         | Publish and manage components (MCP / skill / hook / prompt / sandbox)                 |
| [`observal registry recommend`](/cli/recommend)                                              | Get personalized component recommendations                                            |
| [`observal registry version`](/cli/component)                                                | Publish and inspect component versions                                                |
| [`observal registry models`](/cli/models)                                                    | Inspect packaged harness model catalogs                                               |
| [`observal agent`](/cli/agent)                                                               | Create, install, and manage agents                                                     |
| [`observal team`](/cli/team)                                                                 | Manage teamspaces, members, join requests, and invitations                            |
| [`observal ops`](/cli/ops)                                                                   | Observability and operations (sessions, telemetry, logs, insights)                    |
| [`observal admin`](/cli/admin)                                                               | Core administration and submission review                                             |
| [`observal doctor support`](/cli/support)                                                   | Generate and inspect redacted diagnostic bundles                                      |
| [`observal doctor`](/cli/doctor)                                                             | Diagnose harness compatibility; `doctor patch` applies instrumentation                |
| [`observal server migrate`](/cli/migrate) | Export/import PostgreSQL registry (shallow copy) and ClickHouse telemetry (deep copy) |
| [`observal self`](/cli/self)                                                                 | Upgrade or downgrade the CLI                                                          |
| [`observal registry prompt`](/cli/prompt)                                                    | Manage reusable prompts in the registry                                               |
| [`observal server`](/cli/server)                                                             | Manage the embedded server (start, stop, upgrade, rollback)                           |
| [`observal registry skill`](/cli/skill)                                                      | Submit, browse, and install portable skill packages                                   |

## Global options

Any subcommand accepts these.

| Option      | Short | Description                             |
| ----------- | ----- | --------------------------------------- |
| `--version` | `-V`  | Print the CLI version and exit          |
| `--verbose` | `-v`  | Verbose output                          |
| `--debug`   | -     | Debug-level logging (extremely verbose) |
| `--help`    | -     | Show help for any command or subcommand |

## JSON list contract

Every dedicated list command returns the same envelope:

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 0
}
```

Paginated commands preserve server totals and requested pages. Unpaginated commands use page 1 and the returned item count as `page_size`. Detail and mutation commands return direct objects. `observal api` intentionally preserves raw endpoint JSON.

## Exit codes

Consistent across all commands:

| Code | Meaning                                      |
| ---- | -------------------------------------------- |
| 0    | Success                                      |
| 1    | Unexpected or uncategorized failure          |
| 2    | Usage error                                  |
| 3    | Authentication required or failed            |
| 4    | Permission denied                            |
| 5    | Resource not found                           |
| 6    | Conflict with current state                  |
| 7    | Validation failure                           |
| 8    | Rate limit reached                           |
| 9    | Network, service, or dependency unavailable  |
| 10   | CLI and server version mismatch              |
| 11   | Batch completed with one or more item errors |

Errors identify the failed operation, resource, remediation, and server request ID when available. Repairable failures may include a safe structured `result`; internal details appear only with `--debug`.

When JSON output is selected, errors are written to stderr as one JSON object and stdout remains clean. Startup migrations and update notices never write to JSON stdout. A nonfatal startup migration warning is a structured `warning` object on stderr.

```json
{
  "error": {
    "category": "not_found",
    "message": "The requested resource was not found.",
    "operation": "Show agent",
    "resource": "agent registry",
    "remediation": "Check the identifier or list available agents.",
    "request_id": "01J...",
    "http_status": 404,
    "exit_code": 5
  }
}
```

## Non-interactive mode

For scripts and CI, pair flags with environment variables:

```bash
export OBSERVAL_SERVER_URL=https://observal.your-company.internal
export OBSERVAL_API_KEY='replace-with-your-key'

observal ops traces --limit 100 --output json | jq
```

Full env var reference: [Environment variables](/reference/environment-variables).

## Output formats

Read-heavy commands (`list`, `show`, `traces`, `spans`) support `--output`:

```bash
observal registry mcp list --output table    # default, TTY-friendly
observal registry mcp list --output json     # machine-readable
```

## Aliases

IDs get long fast. Create shortcuts:

```bash
observal config alias my-mcp 498c17ac-1234-4567-89ab-cdef01234567
observal registry mcp show @my-mcp
```

See [`observal config`](/cli/config) for details.

## Next

→ [`observal auth`](/cli/auth): you'll need to log in first.
