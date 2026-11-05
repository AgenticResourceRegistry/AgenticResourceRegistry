---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Requirements"
---

Minimum and recommended specs for running the Agentic Resource Registry stack.

## Hardware

| Profile | CPU | Memory | Disk | Who it's for |
| --- | --- | --- | --- | --- |
| Developer | 2 vCPU | 4 GB | 20 GB | One engineer, local machine |
| Small team (≤10) | 2 vCPU | 6 GB | 50 GB | Small deployment, moderate telemetry |
| Team (10–50) | 4 vCPU | 12 GB | 200 GB + fast SSD | Typical production deployment |
| Large team (50+) | 8+ vCPU | 32 GB | 500 GB + fast SSD | High telemetry volume; consider externalizing ClickHouse |

Default Docker memory limits (the API, worker and web limits differ between source Compose and the one-line server package):

| Service | Source Compose | Server package |
| --- | --- | --- |
| `observal-api` | 1536 MB | 512 MB |
| `observal-worker` | 1536 MB | 512 MB |
| `observal-web` | 32 MB | 256 MB |
| `observal-db` | 512 MB | 512 MB |
| `observal-clickhouse` | 2 GB | 2 GB |
| `observal-redis` | 256 MB | 256 MB |
| `observal-lb` | 64 MB | 64 MB |
| `observal-grafana` (optional overlay) | 512 MB | 512 MB |

ClickHouse has the largest memory limit. Tune `CLICKHOUSE_MEMORY_LIMIT` for your workload; see [resource tuning](resource-tuning.md).

## Disk: where the data goes

The heaviest user of disk is **ClickHouse**. Growth depends on:

* Number and length of harness sessions
* Raw transcript record size
* `DATA_RETENTION_DAYS`

Session transcripts vary significantly by harness and tool output size. Measure representative workloads and plan 2 to 3 times headroom over observed growth.

Postgres stays under 500 MB for most deployments; it holds only registry metadata and user accounts.

## Software

| Software | Version | Notes |
| --- | --- | --- |
| Docker | ≥ 24.0 | With Compose v2 (`docker compose`, not `docker-compose`) |
| Linux / macOS host | any modern | Windows via WSL2 works |
| Bash / zsh | any | For the CLI install |

> [!NOTE]
> Homebrew's Docker formula is outdated and may ship an older Compose version. Install [Docker Desktop](https://docs.docker.com/get-docker/) or use your distro's upstream packages to get Docker Engine ≥ 24.0 with Compose v2.

For the **CLI** (developer machines, not the server):

* **Standalone binary** (recommended) -- no dependencies, just `curl | bash`
* Or Python **3.11, 3.12, or 3.13** with `uv`, `pipx`, or `pip`

## Network

* **Outbound HTTPS**: required for image downloads and enabled integrations such as OAuth, webhooks, external Git repositories, or model providers.
* **Inbound**: the server package routes the UI, API, and session telemetry through nginx on port `8000`.
* **Between services**: the private `observal-net` bridge handles application traffic.
* **Host bindings**: new server-package installs bind nginx, the direct web port, PostgreSQL, ClickHouse, Redis, Prometheus, and Grafana to `127.0.0.1` by default.
* **Secret access**: server-package setup records the operator's group as `OBSERVAL_SECRET_GID`; only that group is added to containers that need their service-specific files.

## TLS / HTTPS

Local loopback access can use HTTP. For every remote deployment, terminate TLS 1.2 or later with Caddy, nginx, an enterprise load balancer, or a cloud ingress. Point the proxy at the loopback nginx listener on `localhost:8000`.

Example Caddy configuration:

```caddyfile
observal.your-company.internal {
  reverse_proxy localhost:8000
}
```

Set `OBSERVAL_BIND_ADDRESS` to a non-loopback address only when an external TLS proxy cannot reach loopback, or when plaintext access is an intentional private-network risk. Existing server-package installations retain their previous bind address during setup upgrades.

See the [security assurance case](/security/assurance-case) for trust boundaries and residual network risks.

## Next

→ [Docker Compose setup](/self-hosting/docker-compose)
