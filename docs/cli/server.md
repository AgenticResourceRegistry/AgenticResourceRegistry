---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal server"
---

Manage local Agentic Resource Registry deployments. The lifecycle commands operate the embedded PostgreSQL, ClickHouse, Redis, and API processes. Upgrade, rollback, and version commands operate a local Docker Compose deployment.

Local filesystem, process, Docker, and database access are the authorization boundary. These commands do not require a reachable Agentic Resource Registry API or an API role.

## Command summary

| Command | Target | Purpose |
| --- | --- | --- |
| `start` | Embedded | Start dependencies and the API |
| `stop` | Embedded | Stop all services |
| `restart` | Embedded | Stop and start services |
| `status` | Embedded | Report service health and ports |
| `logs` | Embedded | Read or follow service logs |
| `install` | Embedded | Install verified dependency binaries |
| `config` | Embedded | Show local paths and ports |
| `reset` | Embedded | Delete database data and generated secrets |
| `upgrade` | Docker | Back up PostgreSQL and replace images |
| `rollback` | Docker | Restore PostgreSQL and the prior image version |
| `versions` | Docker | List image versions and managed backups |
| `migrate` | Databases | Move PostgreSQL registry and ClickHouse telemetry data |

## Embedded lifecycle

Start in the foreground:

```bash
observal server start
```

Start for automation:

```bash
observal server start --background --output json
```

JSON start and restart require `--background` because foreground mode remains attached until shutdown.

```bash
observal server restart --background --output json
observal server stop --output json
```

`start` accepts `--port/-p` and `--host`. When the default API port is occupied, it tries the documented local fallback ports and reports the selected port. An explicitly selected occupied port is a conflict. Background start and restart verify all four final service states before returning success; stop verifies that none remains running. Their JSON results include the verified `services` list, and an unverified transition exits with code 9 plus observed state in `error.result`.

Startup performs these steps in order:

1. Installs embedded dependencies when missing. Downloads require a published SHA-256 checksum and archives reject links and path traversal.
2. Starts PostgreSQL, ClickHouse, and Redis.
3. Applies PostgreSQL and ClickHouse migrations. Migration failures stop startup; the CLI never stamps a failed PostgreSQL schema as current.
4. Starts the API.
5. Bootstraps a local admin only on a fresh embedded server and persists the real access and refresh tokens. It never writes placeholder credentials or an API key.
6. Attempts telemetry hook installation. Optional hook failures are explicit warnings.

## Status and configuration

```bash
observal server status --output json
observal server config --output json
```

Status is a finite diagnosis command. It exits successfully when checks run, including when `healthy` is false.

```json
{
  "healthy": false,
  "services": [
    {"service": "postgres", "status": "running", "port": 5480},
    {"service": "clickhouse", "status": "stopped", "port": 8124},
    {"service": "redis", "status": "running", "port": 6380},
    {"service": "api", "status": "stopped", "port": 8000}
  ]
}
```

Configuration output contains paths and ports only. It never returns generated secrets.

## Logs

Read a bounded snapshot:

```bash
observal server logs --output json
observal server logs api --lines 200 --output json
```

Follow one service as JSON Lines:

```bash
observal server logs api --follow --output json
```

JSON follow requires one service so every event has an unambiguous `service` field. Valid services are `postgres`, `clickhouse`, `redis`, and `api`.

## Install and reset

```bash
observal server install --output json
observal server install --upgrade --output json
```

Reset deletes embedded database directories and the generated server secret. It does not delete CLI configuration, downloaded binaries, logs, or unrelated files.

Human mode confirms. JSON mode requires `--force`:

```bash
observal server reset --force --output json
```

Deletion is confined to the managed embedded data directory.

## Docker upgrade

Preview an upgrade:

```bash
observal server upgrade --dry-run --output json
```

Apply one non-interactively:

```bash
observal server upgrade --version 1.2.3 --force --output json
```

An upgrade validates the target version and image, acquires the server upgrade lock, creates a managed PostgreSQL backup unless `--skip-backup` is set, pulls images, atomically updates `OBSERVAL_VERSION`, recreates containers, and runs the configured health check. A failed health check requests the previous image version again and returns an unavailable error.

JSON mutation requires `--force`; dry run does not.

## Docker rollback

```bash
observal server rollback --force --output json
observal server rollback \
  --from-backup ~/.observal/backups/v1.2.2-20260521T120000 \
  --force --output json
```

Rollback accepts only backup directories under the managed backup root. It restores PostgreSQL, atomically restores the image version, recreates containers, and checks health.

**ClickHouse telemetry is not restored by this command.** JSON and human results state `clickhouse_restored: false`. Use [`observal server migrate`](/cli/migrate) for ClickHouse export and import.

## Docker versions

```bash
observal server versions --output json
```

The result distinguishes the current version, available GHCR images, and local PostgreSQL backups. Failure to query GHCR is reported as unavailable rather than as an empty registry.

## Error and output contract

All finite commands accept `--output table|json`. JSON success writes one document to stdout. Failures leave stdout empty and write one categorized error to stderr. Log following is the only JSON Lines stream.

| Code | Category | Typical server cause |
| --- | --- | --- |
| 2 | Usage | Invalid option or missing required argument |
| 4 | Permission | Unreadable local state or backup outside the managed root |
| 5 | Not found | Missing logs, Compose deployment, image, or backup |
| 6 | Conflict | Busy port, active upgrade lock, or existing destination |
| 7 | Validation | Missing JSON confirmation or invalid version |
| 9 | Unavailable | Dependency, Docker, migration, health, or network failure |

## Related

* [Database migration](/cli/migrate)
* [Self-hosted upgrades](/self-hosting/upgrades)
* [Backup and restore](/self-hosting/backup-and-restore)
* [`observal self`](/cli/self), for CLI binary versions
