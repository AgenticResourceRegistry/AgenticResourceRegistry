<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Santhosh Raja <santhoshpkraja2004@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Setup Guide

Everything you need to get Observal running locally for development or self-hosted production.

> **Full operator docs** live at [observal.gitbook.io](https://observal.gitbook.io/observal) ([`/docs`](docs/) in this repo). This file covers the fastest path from zero to a working stack.
>
> The steps below use the source Compose stack for development. The one-line server-package installer instead generates restricted files under `secrets/`, stores only `NAME_FILE` paths in `.env`, and binds published ports to loopback. The same install command runs guided setup with a terminal or safe defaults without one, so CI and coding agents need no special flag. See [Configuration](docs/self-hosting/configuration.md#secret-files) for the packaged layout and rotation rules.

---

## Prerequisites

| Requirement       | Minimum               | Notes                                                                                                                                                                                                                                    |
| ----------------- | --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Docker Engine** | 24.0+ with Compose v2 | Use `docker compose` (not `docker-compose`). Homebrew Docker is often outdated - use [Docker Desktop](https://docs.docker.com/get-docker/) or your distro's upstream packages. Check with `docker version` and `docker compose version`. |
| **Python**        | 3.11+                 | Only needed if you install the CLI via Python or run tests.                                                                                                                                                                              |
| **uv**            | latest                | Recommended for CLI dev installs: `curl -LsSf https://astral.sh/uv/install.sh \| sh`                                                                                                                                                     |
| **RAM**           | 4 GB+                 | ClickHouse is the memory consumer. 6 GB recommended for comfortable use.                                                                                                                                                                 |
| **Disk**          | 5 GB+                 | For Docker images and data volumes.                                                                                                                                                                                                      |

---

## 1. Clone and configure

```bash
git clone https://github.com/Observal/Observal.git
cd Observal
cp .env.example .env
```

`.env.example` ships with working defaults; you don't need to edit anything for local development. Demo accounts (`super@demo.example` / `super-changeme`, etc.) are seeded automatically on first start.

> **Before a real deployment from source:** change `SECRET_KEY`, `POSTGRES_PASSWORD`, `CLICKHOUSE_PASSWORD`, and unset all `DEMO_*` variables. Server-package installs generate these credentials as files automatically. See the [Self-hosting overview](docs/self-hosting/README.md), [Configuration](docs/self-hosting/configuration.md), [Databases](docs/self-hosting/databases.md), and [Upgrades](docs/self-hosting/upgrades.md).

---

## 2. Start the stack

```bash
make up
```

Or without Make:

```bash
docker compose -f docker/docker-compose.yml up --build -d
```

First build pulls images and compiles the Vite frontend. Expect 3 to 5 minutes. Subsequent starts are under 30 seconds.

**What comes up (10 services):**

| Service               | URL                     | Purpose                                  |
| --------------------- | ----------------------- | ---------------------------------------- |
| `observal-lb` (nginx) | `http://localhost`      | Reverse proxy (API + Web)                |
| `observal-web`        | `http://localhost:3000` | Web UI (Vite static app, direct access)  |
| `observal-api`        | internal                | FastAPI backend                           |
| `observal-worker`     | internal                | Background jobs (arq)                    |
| `observal-init`       | internal                | Runs DB migrations on startup then exits |
| `observal-db`         | `localhost:5432`        | PostgreSQL 16 (registry data)            |
| `observal-clickhouse` | `localhost:8123`        | ClickHouse (session and audit events)    |
| `observal-redis`      | `localhost:6379`        | Job queue + pub/sub                      |
| `observal-prometheus` | `http://localhost:9090` | Metrics scraping                         |
| `observal-grafana`    | `http://localhost:3001` | Metrics dashboards                       |

---

## 3. Verify health

```bash
docker compose -f docker/docker-compose.yml ps
```

All services except `observal-init` (which exits after migrations) should show `healthy` or `running`. The API waits for Postgres, ClickHouse, and Redis before starting. Allow 15–30 seconds on first boot.

Confirm the API is up:

```bash
curl http://localhost/health
# {"status":"ok","initialized":true}
```

Open the web UI at **http://localhost**.

---

## 4. Install the CLI

**Development install from source** (editable, picks up local changes):

```bash
uv tool install --editable .
```

**Via PyPI:**

```bash
uv tool install observal-cli
# or: pipx install observal-cli
```

Verify: `observal --version`

---

## 5. Log in

```bash
observal auth login
```

On a fresh server this prompts:

1. **Server URL** → press Enter for `http://localhost`
2. **Login method** → `[E]mail`
3. **Email / Password** → use a demo account:

| Role        | Email                   | Password            |
| ----------- | ----------------------- | ------------------- |
| Super Admin | `super@demo.example`    | `super-changeme`    |
| Admin       | `admin@demo.example`    | `admin-changeme`    |
| Reviewer    | `reviewer@demo.example` | `reviewer-changeme` |
| User        | `user@demo.example`     | `user-changeme`     |

Check it worked:

```bash
observal auth whoami
# super@demo.example (super_admin)

observal auth status
# Server:  http://localhost - OK
# Auth:    super@demo.example (super_admin)
# Buffer:  0 pending events
```

---

## 6. Run the tests

```bash
make test       # fast, quiet
make test-v     # verbose
```

Or directly:

```bash
cd observal-server && uv run --with pytest --with pytest-asyncio --with pyyaml \
  pytest ../tests/ tests/ ../observal_cli/tests/ -q
```

All tests mock external services. No Docker or live databases needed to run tests.

---

## 7. Instrument your harnesses

Already have Claude Code, Kiro, Cursor, or another harness configured? Install session telemetry hooks without changing MCP commands:

```bash
observal scan                              # read-only: see what's installed
observal doctor patch --all-harnesses      # install session telemetry hooks
observal doctor                            # verify everything wired correctly
```

`scan` never modifies files. `doctor patch` only manages session telemetry hooks.

---

## 8. Common operations

```bash
make down       # stop all services
make rebuild    # rebuild images and restart
make logs       # tail all service logs
make lint       # ruff check
make format     # ruff format + fix
make check      # pre-commit on all files
make hooks      # install pre-commit hooks
```

Restart a single service:

```bash
docker compose -f docker/docker-compose.yml restart observal-api
```

Wipe all data (destructive):

```bash
docker compose -f docker/docker-compose.yml down -v
```

---

## 9. Port conflicts

Every host port is overridable via env var:

| Variable               | Default | Service        |
| ---------------------- | ------- | -------------- |
| `API_HOST_PORT`        | `8000`  | nginx LB → API |
| `WEB_HOST_PORT`        | `3000`  | Web UI         |
| `POSTGRES_HOST_PORT`   | `5432`  | PostgreSQL     |
| `CLICKHOUSE_HOST_PORT` | `8123`  | ClickHouse     |
| `REDIS_HOST_PORT`      | `6379`  | Redis          |
| `PROMETHEUS_HOST_PORT` | `9090`  | Prometheus     |
| `GRAFANA_HOST_PORT`    | `3001`  | Grafana        |

Example:

```bash
API_HOST_PORT=8001 WEB_HOST_PORT=3001 \
  docker compose -f docker/docker-compose.yml up --build -d
```

---

## Further reading

Deployment and operations:

| Topic | Link |
| ----- | ---- |
| Self-hosting overview | [Self-Hosting](docs/self-hosting/README.md) |
| Single-node deployment | [Single-node deployment](docs/self-hosting/single-node-deploy.md) |
| Docker Compose deployment | [Docker Compose setup](docs/self-hosting/docker-compose.md) |
| Production hardening | [Configuration](docs/self-hosting/configuration.md) |
| Databases and migrations | [Databases](docs/self-hosting/databases.md) |
| Ports and volumes | [Ports and volumes](docs/self-hosting/ports-and-volumes.md) |
| Upgrade safely | [Upgrades](docs/self-hosting/upgrades.md) |
| Backup and restore | [Backup and restore](docs/self-hosting/backup-and-restore.md) |
| Troubleshooting | [Troubleshooting](docs/self-hosting/troubleshooting.md) |

Product setup:

| Topic | Link |
| ----- | ---- |
| 5-minute first trace | [Quickstart](docs/getting-started/quickstart.md) |
| All environment variables | [Environment variables](docs/reference/environment-variables.md) |
| Configure SSO / OIDC | [Authentication and SSO](docs/self-hosting/authentication.md) |
