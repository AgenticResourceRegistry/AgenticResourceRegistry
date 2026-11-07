---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Understand ports and persistent storage"
description: "Identify the actual host bindings and volumes before exposing, backing up, or restoring a registry."
---

Source Compose and the downloaded server package do not use identical host bindings. Identify your artifact and inspect its effective configuration before connecting clients or moving data.

## Host ports

The following defaults come from the source and packaged Compose files in this documented revision. Container ports are not automatically public host ports.

| Endpoint | Source Compose host binding | Package host binding | Selection input |
| --- | --- | --- | --- |
| Load balancer: UI + API | All interfaces, port 80 | Loopback, port 8000 | Source `LB_HOST_PORT`; package `API_HOST_PORT` and bind-address setting |
| Direct web UI | All interfaces, port 3000 | Loopback, port 3000 | `WEB_HOST_PORT` |
| PostgreSQL | Loopback, port 5432 | Loopback, port 5432 | `POSTGRES_HOST_PORT` |
| ClickHouse HTTP | Loopback, port 8123 | Loopback, port 8123 | `CLICKHOUSE_HOST_PORT` |
| Redis | Loopback, port 6379 | Loopback, port 6379 | `REDIS_HOST_PORT` |
| Prometheus (optional) | Loopback, port 9090 | Configured package overlay binding | `PROMETHEUS_HOST_PORT` |
| Grafana (optional) | Loopback, port 3001 | Configured package overlay binding | `GRAFANA_HOST_PORT` |

The API's container port 8000 is internal in the source core: users normally reach it through the load balancer. The worker has no public endpoint, but it also connects to PostgreSQL; it is not limited to Redis and ClickHouse.

For source conflicts, remap the intended bindings when creating the containers:

```bash
LB_HOST_PORT=8080 WEB_HOST_PORT=3002 POSTGRES_HOST_PORT=5433 REDIS_HOST_PORT=6380 \
  docker compose -f docker/docker-compose.yml up --build -d
```

Environment defaults do not imply firewall protection. Source web/LB bindings are broad unless you explicitly restrict them in reviewed Compose overrides. The package's default loopback policy can differ on preserved existing installations. See [TLS requirements](/self-hosting/requirements#tls-/-https).

## Persistent volumes

| Logical volume | Current source mount | What it contains |
| --- | --- | --- |
| `pgdata` | `/var/lib/postgresql` | PostgreSQL 18's data directory hierarchy |
| `chdata` | `/var/lib/clickhouse` | ClickHouse records, table state, and evidence |
| `redisdata` | `/data` | Redis persistence/runtime state |
| `apidata` | `/data` in API/worker | Signing keys and persistent application artifacts |
| `grafanadata` (optional) | `/var/lib/grafana` | Custom Grafana state |

The logical Compose name is not necessarily the Docker volume name. Project prefixes, overrides, external volumes, and bind mounts can change it. Inspect the actual containers' mounts:

```bash
docker compose -f docker/docker-compose.yml ps -a
docker inspect CONTAINER_ID --format '{{json .Mounts}}'
```

Replace the ID. Mount inspection shows locations, not authorization to delete or copy them. Avoid exposing sensitive host path metadata unnecessarily.

`down` preserves named volumes; `down --volumes` deletes the targeted project's persistent stores. A missing guessed volume name can cause a helper to create an empty new volume instead of recovering data. [Backup and restore](/self-hosting/backup-and-restore) resolves the real mounts and uses a separate recovery destination.

## Security posture

Read-only roots, temporary writable paths, resource limits, and no-new-privileges apply to selected services as declared in the shipped files—not a blanket guarantee on every dependency. A shared bridge network does not protect data once a service's host port is published.

Inside the network, API/worker connection URLs use service names. Developer machines use the intended public API/LB URL; they do not authenticate the registry by connecting directly to a database port.

## Next

[Configure public access and secrets](/self-hosting/configuration), [back up the identified storage](/self-hosting/backup-and-restore), and [verify migrations before cutover](/self-hosting/upgrades).
