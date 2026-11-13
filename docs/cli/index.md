---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Nithin-Bhargav-07 <gaddamnithinbhargav@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "CLI reference"
description: "Find command groups, output contracts, secure automation inputs, and error behavior."
---

Index of the main `observal` command groups, output conventions, and related guides. Use the selected command's help for the exact options in your installed release. [Match documentation and software versions](/reference/documentation-version).

> **New to Agentic Resource Registry?** Start with [Quickstart](/getting-started/quickstart) and come back here when you need a specific command.

## Command groups

| Command                                                                                    | What it does                                                                          |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| [`observal api`](/cli/api)                                                                   | Call authenticated JSON endpoints without a dedicated command                         |
| [`observal auth`](/cli/auth)                                                                 | Authentication and account management                                                 |
| [`observal config`](/cli/config)                                                             | Local CLI configuration, aliases                                                      |
| [`observal discover`](/cli/discover)                                                         | Find approved resources for a task and use them in the current session                |
| [`observal scan`](/cli/scan)                                                                 | Inspect local configuration; `--inventory` is the read-only mode                           |
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
| [`observal review`](/guides/review-and-publish)                                              | Inspect candidates, review, and verify publication policy                             |
| [`observal admin`](/cli/admin)                                                               | Deployment settings, users, and administrative operations                                             |
| [`observal doctor support`](/cli/support)                                                   | Generate and inspect redacted diagnostic bundles                                      |
| [`observal doctor`](/cli/doctor)                                                             | Diagnose harness compatibility; `doctor patch` applies instrumentation                |
| [`observal server migrate`](/cli/migrate) | Export/import PostgreSQL registry archives and ClickHouse telemetry separately |
| [`observal self`](/cli/self)                                                                 | Upgrade or downgrade the CLI                                                          |
| [`observal registry prompt`](/cli/prompt)                                                    | Manage reusable prompts in the registry                                               |
| [`observal server`](/cli/server)                                                             | Manage server lifecycle and supported deployment/upgrade workflows                           |
| [`observal registry skill`](/cli/skill)                                                      | Submit, browse, and install portable skill packages                                   |

## Global options

Place root options before the command group, for example `observal --verbose agent list`. Use `observal --version` for the CLI version and per-command `--help` for local options. A command-specific `--version`, such as `agent pull --version`, selects a resource release rather than printing the platform version.

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

The shared error contract uses these codes. Older command surfaces can have documented exceptions; see the [scan exit behavior](/cli/scan#exit-codes).

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

Commands using the shared JSON error contract write errors to stderr as one object and leave stdout clean. The legacy scan unknown-harness path instead emits plain-text diagnostics with exit 1; do not parse that as a categorized error object. Startup migrations and update notices never write to JSON stdout. A nonfatal startup migration warning is a structured `warning` object on stderr.

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
export OBSERVAL_SERVER_URL=https://registry.example.com
export OBSERVAL_ACCESS_TOKEN_FILE=/run/secrets/observal_access_token

observal agent list --output json
```

Provision that file through your approved secret mechanism; its value is a JWT access token, not an API key. Registry access does not supply harness/provider credentials. Mutations can require additional explicit inputs or confirmation flags.

Full env var reference: [Environment variables](/reference/environment-variables).

## Output formats

Commands that expose `--output` can render table or JSON. JSON is not a universal dry run: installation/mutation commands can still write files or change server state. Use their documented preview/no-write options. For example:

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
