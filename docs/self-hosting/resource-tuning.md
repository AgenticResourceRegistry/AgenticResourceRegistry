---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Resource Tuning"
---

Connection pool sizes, query limits, and timeout configuration. These settings control how Observal connects to its backing stores (PostgreSQL, Redis, ClickHouse). Most deployments work fine with defaults. Tune when you see connection timeouts, pool exhaustion, or slow queries under load.

## When to Tune

- **Connection pool errors** in API logs ("pool exhausted", "connection timeout")
- **Slow dashboard loads** under concurrent users (increase pool sizes)
- **OOM kills** on the API container (decrease pool sizes, each connection uses memory)
- **ClickHouse query timeouts** on large trace datasets (increase timeout)

## PostgreSQL

### DB Pool Size

Number of persistent database connections maintained in the connection pool.

| Value | Effect |
|-------|--------|
| `10` (default) | Suitable for small teams (< 20 concurrent users) |
| `20` | Medium deployments (20-100 users) |
| `50` | Large deployments (100+ concurrent users) |

**Memory impact:** Each connection uses approximately 5MB of RAM on the API server.

**When to increase:** You see "pool exhausted" errors or requests queuing during peak usage.

**When to decrease:** Running on memory-constrained containers, or your PostgreSQL instance has a low `max_connections` limit.

### DB Max Overflow

Temporary connections created when the pool is full. These are closed after use.

| Value | Effect |
|-------|--------|
| `20` (default) | Allows bursts of up to 30 total connections (pool + overflow) |
| `0` | No overflow; requests wait for a pool connection (safest for DB) |
| `50` | High burst tolerance; use when traffic is very spiky |

**Total max connections** = pool_size + max_overflow. Ensure your PostgreSQL `max_connections` is at least this value plus a buffer for admin connections.

## Redis

### Redis Max Connections

Maximum concurrent connections to Redis.

| Value | Effect |
|-------|--------|
| `50` (default) | Handles most workloads |
| `100` | High-traffic deployments with heavy pub/sub (GraphQL subscriptions) |
| `20` | Constrained environments with limited Redis resources |

**When to increase:** "Connection pool exhausted" errors in Redis client logs, or high latency on GraphQL subscriptions.

### Redis Timeout

Socket timeout in seconds for Redis operations.

| Value | Effect |
|-------|--------|
| `2.0` (default) | Balanced; detects failures quickly without false positives |
| `5.0` | Use when Redis is on a high-latency network (cross-region) |
| `1.0` | Aggressive; faster failure detection but may false-positive on slow queries |

**When to increase:** Redis is in a different availability zone or region, causing occasional timeout errors on valid operations.

## ClickHouse

### ClickHouse Max Connections

Maximum HTTP connections to ClickHouse for analytics queries.

| Value | Effect |
|-------|--------|
| `20` (default) | Sufficient for most dashboard and trace query workloads |
| `50` | Heavy analytics usage with many concurrent dashboard viewers |
| `10` | Small deployments or shared ClickHouse clusters |

### ClickHouse Keepalive

Persistent connections kept alive between requests.

| Value | Effect |
|-------|--------|
| `10` (default) | Reduces connection overhead for frequent queries |
| `5` | Lower memory usage, slightly higher latency on first query per burst |
| `20` | Faster response for sustained dashboard usage |

### ClickHouse Query Timeout

Maximum seconds a single ClickHouse query can run before cancellation.

| Value | Effect |
|-------|--------|
| `10.0` (default) | Prevents runaway queries; sufficient for most trace lookups |
| `30.0` | Allow complex aggregation queries on large datasets |
| `5.0` | Strict; kills slow queries fast but may break large time-range dashboards |

**When to increase:** Dashboard "query timeout" errors on wide time ranges or high-cardinality group-by queries.

### Skip DDL on Startup

Skip ClickHouse schema migrations on server startup.

| Value | Effect |
|-------|--------|
| `false` (default) | Schema migrations run automatically on every startup |
| `true` | Skip DDL when a separate deployment migration job applies ClickHouse migrations |

**When to enable:** Large ClickHouse clusters where DDL operations are slow or require coordination, or when running multiple API replicas (only one should run migrations).

### Query Memory Limit

Maximum memory a single ClickHouse query can use (in bytes).

| Value | Effect |
|-------|--------|
| `10000000000` / 10GB (default) | Generous; allows complex aggregations |
| `5000000000` / 5GB | Conservative; prevents a single query from consuming all memory |
| `20000000000` / 20GB | For dedicated ClickHouse instances with abundant RAM |

### GROUP BY Spill Threshold

Row count at which GROUP BY operations spill to disk instead of keeping everything in memory.

| Value | Effect |
|-------|--------|
| `1000000` (default) | Spill after 1M grouped rows; balances speed and memory |
| `500000` | More aggressive spilling; lower memory usage but slower |
| `5000000` | Keep more in memory; faster but higher peak memory usage |

### ORDER BY Spill Threshold

Row count at which ORDER BY operations spill to disk.

| Value | Effect |
|-------|--------|
| `1000000` (default) | Same tradeoff as GROUP BY threshold |

### JOIN Memory Limit

Maximum memory for JOIN operations (in bytes).

| Value | Effect |
|-------|--------|
| `5000000000` / 5GB (default) | Allows large JOINs for cross-referencing traces |
| `2000000000` / 2GB | Conservative; may fail on very large trace correlations |
| `10000000000` / 10GB | For heavy analytics workloads |
