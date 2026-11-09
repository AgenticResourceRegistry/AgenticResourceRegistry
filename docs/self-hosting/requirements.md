---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Requirements"
---

Sizing and software starting points for the registry stack. These are not load-test guarantees; measure your workload and account for the combined service limits, database growth, and backup headroom.

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
* Runtime raw/session retention policy (`data.retention_days` and `retention.*`)

Session transcripts vary significantly by harness and tool output size. Measure representative workloads and plan 2 to 3 times headroom over observed growth.

PostgreSQL growth depends on users, resource/version content, reviews, and settings. Measure it separately from ClickHouse rather than assuming a fixed metadata-only size.

## Software

| Software | Version | Notes |
| --- | --- | --- |
| Docker Engine | ≥ 25.0 | Required by source `healthcheck.start_interval`; use a currently supported, patched release. |
| Docker Compose | ≥ 2.20.2 | Use `docker compose`; the single-node `!override` example needs ≥ 2.24.4. |
| Linux / macOS host | any modern | Windows via WSL2 works |
| Bash / zsh | any | For the CLI install |

> [!NOTE]
> Homebrew's Docker formula is outdated and may ship an older Compose version. Install [Docker Desktop](https://docs.docker.com/get-docker/) or use your distro's upstream packages to get Docker Engine ≥ 25.0 with Compose ≥ 2.20.2. The source healthcheck uses `start_interval`; [Engine 25 introduced it](https://docs.docker.com/engine/release-notes/25.0/#new) and [Compose supports it from 2.20.2](https://docs.docker.com/reference/compose-file/services/#healthcheck).

### Packaged setup on macOS

The server package's `setup.sh` currently fails with `profile_args[@]: unbound variable` when the default observability profile is run under macOS Bash 3.2. This was reproduced on both the docs branch and the pre-docs baseline. The corresponding headless setup tests pass with GNU Bash in a Linux container; that is not certification of all package workflows.

This limitation concerns the **server package setup**, not proof that the standalone CLI installer is broken. Do not assume every shell accepted by a CLI install command can run packaged server setup. Until the implementation is corrected or your shell is verified, use the [source evaluation guide](/getting-started/local-evaluation) rather than claiming a successful packaged deployment.

For the **CLI** (developer machines, not the server):

* **Standalone binary** -- no Python required; installation and integrations still need their own binaries, permissions, and model access
* Or Python **3.11 or newer** matching the CLI package requirements, preferably installed as an isolated tool with `uv` or `pipx`

## Network

* **Outbound HTTPS**: required for image downloads and enabled integrations such as OAuth, webhooks, external Git repositories, or model providers.
* **Inbound**: the server package routes the UI, API, and session telemetry through nginx on port `8000`.
* **Between services**: the private `observal-net` bridge handles application traffic.
* **Host bindings**: new server-package installs bind nginx, the direct web port, PostgreSQL, ClickHouse, Redis, Prometheus, and Grafana to `127.0.0.1` by default.
* **Secret access**: server-package setup records the operator's group as `OBSERVAL_SECRET_GID`; only that group is added to containers that need their service-specific files.

## TLS / HTTPS

Local loopback access can use HTTP. For remote deployment, use an approved TLS-terminating reverse proxy or ingress. Point it at the intended restricted load-balancer binding, not an assumed universal port: the server package defaults to port 8000, while source Compose defaults to port 80 and needs an explicit restricted binding for this layout.

Example Caddy configuration for a package listener on loopback port 8000:

```caddyfile
observal.your-company.internal {
  reverse_proxy localhost:8000
}
```

Set `OBSERVAL_BIND_ADDRESS` to a non-loopback address only when an external TLS proxy cannot reach loopback, or when plaintext access is an intentional private-network risk. Existing server-package installations retain their previous bind address during setup upgrades.

See the [security assurance case](/security/assurance-case) for trust boundaries and residual network risks.

## Next

→ [Docker Compose setup](/self-hosting/docker-compose)
