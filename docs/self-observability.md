---
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Self-Observability"
---

Observal exposes health probes, structured logs, and Prometheus metrics for monitoring itself.

## Health Endpoints

| Endpoint | Purpose | Use Case |
|----------|---------|----------|
| `GET /livez` | Liveness probe | K8s/Docker: is the process alive? (no I/O) |
| `GET /healthz` | Liveness probe (alias) | Same as `/livez` |
| `GET /readyz` | Readiness probe | K8s/Docker: can the API serve traffic? Checks Postgres, ClickHouse, Redis |
| `GET /health` | Readiness probe (alias) | Same as `/readyz` |
| `GET /metrics` | Prometheus metrics | Scrape target for Prometheus |

### Readiness response example

```json
{
  "status": "ok",
  "postgres": "ok",
  "initialized": true,
  "clickhouse": "ok",
  "redis": "ok"
}
```

When a dependency is down, `status` becomes `"degraded"` or `"unhealthy"` (HTTP 503 for Postgres failure).

## Structured Logging

All logs are JSON-formatted by default. Every log line includes:

- `timestamp` (ISO 8601)
- `level` (info, warning, error)
- `logger` (module name)
- `event` (what happened)
- `request_id` (during HTTP requests, auto-generated or from `X-Request-ID` header)

### Configuration

| Env Variable | Default | Options |
|-------------|---------|---------|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `json` | `json` (production), `console` (local dev, colored and human-readable) |

### Request ID Propagation

Every request gets a UUID via the `X-Request-ID` header:
- If the client sends one, it's reused (must be a valid UUID)
- Otherwise a new one is generated
- It's returned in the response and bound to all log lines for that request

## Prometheus Metrics

Available at `GET /metrics`. Key metrics:

| Metric | Type | Description |
|--------|------|-------------|
| `http_requests_total` | counter | Total requests by method, handler, status |
| `http_request_duration_seconds` | histogram | Latency by handler |
| `http_request_duration_highr_seconds` | histogram | Latency with fine-grained buckets |
| `http_response_size_bytes` | summary | Response sizes by handler |
| `process_resident_memory_bytes` | gauge | RSS memory |
| `process_cpu_seconds_total` | counter | CPU time |

## Grafana Dashboard

A pre-built dashboard is provisioned automatically at:

```
http://localhost:3001 → Dashboards → Observal → Observal API Health
```

It shows: API status, uptime, request rate by status code, latency percentiles (p50/p95/p99), top endpoints, memory, and CPU.

## Docker Compose

The `docker-compose.yml` includes:
- **observal-prometheus**: scrapes `/metrics` every 15s
- **observal-grafana**: visualizes metrics (port 3001, login: admin/admin)

The API healthcheck uses `/readyz` to verify all dependencies before marking the container healthy.
