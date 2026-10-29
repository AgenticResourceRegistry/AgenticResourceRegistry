---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Docker Compose setup"
---

Step-by-step bring-up of the Agentic Resource Registry stack. End state: the core services are healthy, API responding at `http://localhost/health`, web UI at `http://localhost`. Prometheus and Grafana are optional.

## 1. Clone and configure

```bash
git clone https://github.com/AgenticResourceRegistry/AgenticResourceRegistry.git
cd AgenticResourceRegistry
cp .env.example .env
```

The `.env.example` ships with working direct-value defaults for local source development, including demo account credentials. You do not need to edit it for local development. Production server-package installs use generated files under `secrets/` instead; see [Configuration](/self-hosting/configuration#secret-files).

> [!NOTE]
> You need Docker Engine ≥ 24.0 with Compose v2 (`docker compose`, not `docker-compose`). Homebrew's Docker formula is outdated. Install [Docker Desktop](https://docs.docker.com/get-docker/) or use your distro's upstream packages. Verify with `docker version` and `docker compose version`.

## 2. Start the stack

Core stack only:

```bash
docker compose -f docker/docker-compose.yml up --build -d
```

With Prometheus only:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.observability.yml up --build -d
```

With Prometheus and Grafana:

```bash
COMPOSE_PROFILES=grafana docker compose -f docker/docker-compose.yml -f docker/docker-compose.observability.yml up --build -d
```

First build takes a few minutes (pulls images, builds `observal-api` and `observal-web`). Subsequent starts are fast.

## 3. Verify health

```bash
docker compose -f docker/docker-compose.yml ps
```

Every service should show `healthy` or `running`. The API waits for Postgres, ClickHouse, and Redis to pass health checks before starting. Expect 15–30 seconds on first boot.

Hit the health endpoint:

```bash
curl http://localhost/health
# {"status":"ok"}
```

## 4. Configure TLS (production only)

For local dev, `http://localhost` is fine. For production, put a TLS-terminating reverse proxy in front of the nginx LB. See [Requirements → TLS / HTTPS](/self-hosting/requirements#tls--https).

## 5. Bootstrap the first user

### Option A - demo accounts (fastest for trying it out)

`.env.example` seeds four demo accounts on first startup:

| Role        | Email                   | Password            |
| ----------- | ----------------------- | ------------------- |
| Super Admin | `super@demo.example`    | `super-changeme`    |
| Admin       | `admin@demo.example`    | `admin-changeme`    |
| Reviewer    | `reviewer@demo.example` | `reviewer-changeme` |
| User        | `user@demo.example`     | `user-changeme`     |

Log in with the CLI:

```bash
curl -fsSL https://raw.githubusercontent.com/AgenticResourceRegistry/AgenticResourceRegistry/main/install.sh | bash   # if you haven't already
observal auth login              # Email: super@demo.example, Password: super-changeme
```

**Remove demo accounts before real deployment.** Unset the `DEMO_*` env vars in `.env` and restart. Already-seeded accounts stay until you delete them manually (`observal admin delete-user <email>`).

### Option B - fresh bootstrap (recommended for production)

Remove `DEMO_*` from `.env` and start the stack. Run:

```bash
observal auth login
# Server URL: http://localhost
# No users detected - bootstrapping admin account.
# Email: alice@your-company.com
# Password: **************
```

The CLI detects that no users exist and interactively creates the first admin. The `/api/v1/auth/bootstrap` endpoint is restricted to localhost access for security.

## 6. Verify with the CLI

```bash
observal auth whoami
observal auth status

observal registry mcp list         # empty list - you haven't added anything yet
```

## 7. Stop, restart, rebuild

```bash
# Stop core and any optional monitoring containers
make down

# Stop and delete all data, including optional monitoring volumes
docker compose -f docker/docker-compose.yml -f docker/docker-compose.observability.yml --profile grafana down -v

# Restart one service
docker compose -f docker/docker-compose.yml restart observal-api

# Rebuild after code changes
docker compose -f docker/docker-compose.yml up --build -d observal-api
```

Makefile shortcuts from the repo root:

```bash
make logs                  # tail core service logs
make rebuild               # rebuild and restart core services
make up-prometheus         # start core services with Prometheus
make up-observability      # start core services with Prometheus and Grafana
make rebuild-prometheus    # rebuild core services with Prometheus
make rebuild-observability # rebuild core services with Prometheus and Grafana
```

## 8. Logs

```bash
docker compose -f docker/docker-compose.yml logs -f                # all
docker compose -f docker/docker-compose.yml logs -f observal-api   # one service
```

## 9. Port conflicts

If `docker compose up` fails with `port is already allocated`, remap host ports via env vars:

```bash
POSTGRES_HOST_PORT=5433 REDIS_HOST_PORT=6380 \
  docker compose -f docker/docker-compose.yml up --build -d
```

Every host port is configurable. See [Ports and volumes](/self-hosting/ports-and-volumes) for the full list.

## Next

→ [Configuration](/self-hosting/configuration): which env vars to change for production.
