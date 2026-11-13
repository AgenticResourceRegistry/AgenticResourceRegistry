---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Understand registry and evidence storage"
description: "Know what each persistent store holds and how schema changes and recovery are coordinated."
---

The registry's relational state and session evidence live in different stores. Preserving one does not recover the other.

| Store | Main role | Schema/change mechanism |
| --- | --- | --- |
| PostgreSQL | Users, resources, versions, reviews, teams, settings, and relational state | Alembic plus the reviewed fresh-schema bootstrap path |
| ClickHouse | Session/evidence aggregates, layer snapshots/components, audit/security records, and webhook delivery data | Versioned SQL migrations |
| Redis | Jobs, pub/sub, cached settings, and authentication/revocation coordination | Runtime state and configured persistence |

Current source images and the eight-service inventory are listed in [self-hosting overview](/self-hosting/index#architecture-at-a-glance). Do not infer schema or mount compatibility from an old PostgreSQL/ClickHouse version written in a guide.

## PostgreSQL

Agent releases, component pins, review state, and settings are relational data. API access uses JWTs; there is no current `api_keys` authentication table/workflow to provision as an onboarding step. Audit and security event storage belongs to the ClickHouse pipeline, not a blanket Postgres-only audit model.

Init distinguishes a fresh database from an existing versioned schema. It creates/stamps the appropriate fresh state or applies Alembic migrations to a recognized versioned state. A partial or unversioned existing database is refused rather than blindly stamped as current.

Stop/drain incompatible old API and worker writers before initialization for an upgrade. `observal server migrate` moves data through migration tooling; it is not the same operation as applying Alembic schema migrations. See [planned upgrades](/self-hosting/upgrades).

## ClickHouse

Session records, aggregates, checkpoints, layer snapshots/component mappings, component activity, audit/security events, and delivery records have different table engines and retention behavior. Use the application's query layer or inspect the actual engine before choosing `FINAL`, aggregation, or deletion semantics.

SQL schema files live in `observal-server/clickhouse/migrations/`; the runner records applied files. Alembic does not replace that runner. Init runs it after the PostgreSQL stage, before the API starts.

`data.retention_days` is the configured raw-session TTL policy, not the obsolete `DATA_RETENTION_DAYS` example. Scheduled `retention.*` controls and derived-evidence cleanup are separate. Policy and actual storage behavior can differ: source Compose skips the runtime TTL initializer through `SKIP_DDL_ON_STARTUP`, and the fresh rehearsal had no effective `session_events` TTL. Verify the table definition and [retention limits](/self-hosting/data-retention) before relying on expiration; a setting value or a schema export is not a restorable backup.

## Redis and authentication

Redis is not only a disposable cache. Queue and token/revocation operations depend on it, and authentication can fail closed when it is unavailable. After loss/recovery, validate account access and pending work instead of promising every task requeues automatically.

## Externalizing a database

An external endpoint needs compatible schema/version, network reachability, authentication, startup dependencies, health checks, and backup procedures. Changing `DATABASE_URL` or `CLICKHOUSE_URL` alone does not remove the original Compose dependency or make migrations safe.

## Recovery

Use [backup and restore](/self-hosting/backup-and-restore) for matching relational data, application keys/encryption material, and ClickHouse evidence. Do not reset all volumes to recover a single store or repair a version mismatch.
