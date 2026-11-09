---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Resource Tuning"
---

Connection pools, HTTP timeouts, and query settings have different configuration/application paths. Measure saturation and backing-store capacity before tuning; a larger pool can increase load rather than improve throughput.

The defaults below are the constructor inputs in the v1.16.0 release, not capacity recommendations. Operators must verify their actual process/container environment and every process that owns a client.

## When to Tune

Inspect connection waits, pool exhaustion, request/query latency, memory, and database limits first. Do not infer a pool size from team size or a fixed per-connection memory estimate.

Pool inputs (`DB_*`, `REDIS_*`, `CLICKHOUSE_*`) are boot-time settings. The similarly named stored `resource.*` fields can show older defaults, but these constructors consume `config.settings`, not those stored values. Saving them does not rebuild a live pool. Set the inputs through the deployment's process/container configuration and recreate affected consumers deliberately; a simple restart does not replace a container's environment.

## PostgreSQL

### DB Pool Size

`DB_POOL_SIZE` defaults to **30**. Each SQLAlchemy engine can retain up to that many pooled connections; this does not mean all 30 are opened eagerly. The engine enables connection pre-ping. Verify observed utilization and database capacity before changing it.

### DB Max Overflow

`DB_MAX_OVERFLOW` defaults to **50**, allowing temporary burst connections beyond the retained pool. With the defaults, one engine can reach **80 connections** (`30 + 50`). An example override of zero disables overflow; it is a tuning choice, not the default or a universal safety setting.

Budget PostgreSQL connections across **all API/worker processes and engine instances**, plus migration jobs, maintenance tools, other applications, and administrative headroom. With N independent engines at these defaults, their combined configured ceiling alone is `N × 80`; actual simultaneous demand can be lower. A database limit of 80 is not adequate merely because one process can reach 80. Pool increases require a measured connection/memory budget and compatible database `max_connections`.

## Redis

### Redis Max Connections

The shared Redis client's `REDIS_MAX_CONNECTIONS` defaults to **200 per pool/process**. It is not a server-wide cap. Pub/sub subscription helpers create separate pools with a limit of 2, and ARQ/other clients have their own pools. Include those and process counts when budgeting Redis resources.

### Redis Timeout

`REDIS_SOCKET_TIMEOUT` defaults to **2.0 seconds** and is passed to the shared pool's connect and socket timeouts. Longer timeouts are optional responses to measured latency, not guaranteed fixes for exhaustion or backend overload. Auth can fail closed when Redis is unavailable; do not weaken that boundary to hide a timeout.

## ClickHouse

### ClickHouse Max Connections

`CLICKHOUSE_MAX_CONNECTIONS` defaults to **100**. The lazy shared `httpx.AsyncClient` consumes it as the HTTP connection cap per client/process, not the ClickHouse server's total connection budget. Account for API/worker clients and other query producers.

### ClickHouse Keepalive

The client currently passes **`CLICKHOUSE_MAX_CONNECTIONS` to both** `httpx.Limits.max_connections` and `max_keepalive_connections`. Thus the effective default keepalive limit is **100**.

A separate `CLICKHOUSE_MAX_KEEPALIVE` field is declared with default 100, but this constructor does not consume it. Changing that field alone does not change the live client or establish independently tunable keepalive behavior. Likewise the stored `resource.clickhouse_max_keepalive` value is not proof of the applied limit. This distinction needs a separate implementation change if independent tuning is required.

### ClickHouse Query Timeout

`CLICKHOUSE_TIMEOUT` defaults to **10.0 seconds** for the HTTP client timeout, not a universal server-side query cancellation deadline. Some maintenance requests override the HTTP timeout. The shared query path separately sends a default `max_execution_time` of **300 seconds**; server/user limits and explicit query settings can also matter. An HTTP timeout does not prove the database query was cancelled.

### Skip DDL on Startup

Control the API runtime schema initializer, not the deployment's versioned migration jobs. `SKIP_DDL_ON_STARTUP` is a boot-time setting.

| Value | Effect |
| --- | --- |
| `false` (code default) | Run the API's runtime table/column and ClickHouse initialization path |
| `true` (source Compose) | Skip that runtime initialization path; separate init/migration jobs still need to run |

Do not toggle it as a generic performance fix or assume an API restart applies migrations/retention. Source Compose sets it true and uses a separate initializer; the inspected runtime ClickHouse TTL application is also skipped. Follow [coordinated upgrades](/self-hosting/upgrades) and [effective retention checks](/self-hosting/data-retention#verify-storage-and-scheduled-retention).

### Query Memory Limit

`resource.max_query_memory_mb` maps to ClickHouse `max_memory_usage` when a positive stored override is applied. The application converts MB to bytes with `value × 1,000,000`. There is no 10 GB application default in this mapping; absent an applied override, verify the ClickHouse user/server/query limits.

### GROUP BY Spill Threshold

`resource.group_by_spill_mb` maps to `max_bytes_before_external_group_by`. This is a **memory threshold**, not a grouped-row count. An applied value of 250 MB, for example, becomes 250,000,000 bytes; it is an example, not a default or workload recommendation.

### ORDER BY Spill Threshold

`resource.sort_spill_mb` maps to `max_bytes_before_external_sort`, also in decimal MB converted to bytes—not a row count.

### JOIN Memory Limit

`resource.join_memory_mb` maps to `max_bytes_in_join` using the same conversion. There is no fixed 5 GB application default supplied by this mapping.

These four query override keys are the fields consumed by `apply_resource_settings`; they are not pool constructors. Missing, nonpositive, or invalid values do not establish an applied limit. Inspect the stored values, application result, and actual queries under the relevant process/revision. Runtime initialization/application can be skipped; configured values alone are not proof of enforcement. Choose limits from measurements and verify them against the selected ClickHouse configuration before relying on them.
