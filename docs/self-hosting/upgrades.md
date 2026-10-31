---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Upgrades"
---

Safe upgrade flow for the Agentic Resource Registry server stack.

> [!IMPORTANT]
> When upgrading into the release that introduces complete-folder skill delivery, follow the [skill-folder rollout procedure](/skill-folder-rollout) instead of using the quick, standard, or blue/green instructions below unchanged. Those paths do not guarantee that old API and worker processes have stopped before the new init container runs PostgreSQL migrations. Keep folder delivery off, back up and rehearse the historical upgrade, and schedule a maintenance window to drain old processes **before** migration; enable delivery only after a homogeneous rollout and separate approval.

## Docker image upgrade

For a Docker Compose installation whose **installed** file already points to the target release's GHCR repository:

```bash
observal server upgrade --version 1.16.0 --dry-run --output json
observal server upgrade --version 1.16.0 --force --output json
```

The CLI refuses an old `ghcr.io/observal` Compose file when the target images are available only in `ghcr.io/agenticresourceregistry`. It does not rewrite installed Compose files or perform a cross-repository migration. Pulling happens before the PostgreSQL backup or container changes; old writers are stopped before starting init. On failure after init may have run, the CLI tries to stop writers and requires manual recovery, **not** an automatic restart of the old API. A package with `OBSERVAL_VERSION=latest` must first be pinned to its *verified running version* for CLI upgrades. Local shell and Docker access authorize the operation; no reachable API or API role is required.

See [`observal server upgrade`](/cli/server#observal-server-upgrade) for full details.

### Server-package upgrades

If you installed with `install-server.sh`, back up `.env`, `secrets/`, `pgdata`, `apidata`, and `chdata` before staging the target release package. With an existing `.env`, the installer preserves it and the secrets, retains a copy of the previous package, and **never runs the downloaded setup script**: no containers restart or migrations run. It refuses an unidentifiable nonempty installation without `.env`. Other local package edits—including nginx rules, ClickHouse user/config files, and observability overrides—stay only in the backup: compare and reapply them before cutover. A zero exit code means *staged*, not upgraded. The preserved `OBSERVAL_VERSION=latest` or old pin may resolve to a different release under the new static Compose repository; do not start the staged stack until you have explicitly verified and pinned its image version. An absent `OBSERVAL_BIND_ADDRESS` remains absent (the packaged Compose default is loopback).

For the complete-folder release, follow the [supervised rollout](/skill-folder-rollout): keep the delivery gate off, stop *all* old API and worker processes before starting the new init container, then run Compose from the installed package with the same optional observability overlay/profile used by the old deployment. For a core-only package installation, after staging the package and verifying backups and the gate:

```bash
cd /opt/observal  # or the directory passed to --install-dir
# Pin OBSERVAL_VERSION in .env to the verified target image tag (X.Y.Z, no v).
# Unset any conflicting exported OBSERVAL_VERSION before Compose commands.
docker compose -f docker-compose.yml --env-file .env config --images
# Pull while the old stack is still serving: a missing tag must not cause downtime.
docker compose -f docker-compose.yml --env-file .env pull  # all images, including nginx and dependencies
# Stop and verify every old writer (including any overlays/replicas) before init.
docker compose -f docker-compose.yml --env-file .env stop observal-api observal-worker observal-init
docker compose -f docker-compose.yml --env-file .env ps --status running --services
# STOP HERE until a tested, consistent snapshot of pgdata, apidata and chdata is complete.
docker compose -f docker-compose.yml --env-file .env up -d --pull never --no-build --wait --wait-timeout 300
docker compose -f docker-compose.yml --env-file .env restart observal-lb
curl -fsS http://127.0.0.1:8000/health  # adjust for OBSERVAL_BIND_ADDRESS and API_HOST_PORT
```

Add `-f docker-compose.observability.yml` and `--profile grafana` if those were used previously; do not silently drop optional services. Verify the API externally through the load balancer, not only via its container health check. The installer no longer offers an existing-install **Yes** path: configuration replacement and service cutover require a supervised procedure. Backing up only the CLI PostgreSQL dump is not a complete rollback of a migrated database or ClickHouse data.

## Before a manual upgrade

1. **Back up `pgdata`** and **`apidata`**. See [Backup and restore](/self-hosting/backup-and-restore). Backing up `chdata` is nice-to-have; losing telemetry is painful but not catastrophic.
2. **Read the [CHANGELOG](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/CHANGELOG.md)** for the releases you're jumping across. Note any breaking changes.
3. **Pin the version you're upgrading to**: don't `git pull main` blindly. Check out a release tag or a known-good commit.

## Standard upgrade

```bash
cd AgenticResourceRegistry

# Fetch and check out the target version
git fetch --tags
git checkout v0.9.1

# Rebuild images
docker compose -f docker/docker-compose.yml pull
docker compose -f docker/docker-compose.yml up --build -d
# Nginx retains resolved upstream IPs across API/web container recreation.
# Wait for the new API to be healthy, then refresh the load balancer.
docker compose -f docker/docker-compose.yml exec observal-api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
docker compose -f docker/docker-compose.yml restart observal-lb

# Verify through the public load balancer
docker compose -f docker/docker-compose.yml ps
curl http://localhost/health
```

The init container applies pending Postgres Alembic migrations and ClickHouse SQL migrations before the API starts. Watch the init logs for migration output:

```bash
docker logs -f observal-init
# Running database migrations...
# Running ClickHouse migrations...
```

## Zero-downtime upgrade (small teams)

If you run a single instance and have a ~30-second maintenance window:

1. Back up `pgdata`, `apidata`, `chdata`.
2. Stop the API and worker: `docker compose stop observal-api observal-worker`.
3. Apply migrations out of band with `alembic upgrade head` and `python -m services.clickhouse.migrations` from `observal-server`, or run the init container once.
4. Pull/rebuild new images: `docker compose pull && docker compose build observal-api observal-worker`.
5. Start: `docker compose up -d`; wait for the new API to become healthy, then `docker compose restart observal-lb` so nginx resolves the new container IPs.
6. Smoke test through the public load balancer: `curl -fsS http://localhost/health`, then `observal auth status --output json && observal ops telemetry status --output json`.

Web UI, Postgres, ClickHouse, Redis stay up throughout. Users see a brief API outage (~15–30 s).

## Zero-downtime at scale

For blue/green upgrades on large deployments:

1. Run a second stack (`docker-compose.yml` with different project name and host ports) behind a reverse proxy.
2. Apply migrations before traffic cutover. Alembic handles Postgres and `services.clickhouse.migrations` handles ClickHouse. Additive migrations should work with API N-1 and N during the rollout.
3. Bring up the green stack pointing at the same `pgdata` / `chdata` / `apidata` volumes.
4. Flip the reverse proxy to green.
5. Decommission blue.

If a migration is **not** additive (rare, but happens: column drops, type changes), it gets called out in the CHANGELOG and requires a brief outage. Plan the window.

## Rolling back

If the new version breaks:

1. `docker compose -f docker/docker-compose.yml down`
2. `git checkout <previous-version>`
3. `docker compose -f docker/docker-compose.yml up --build -d`

**The catch:** if the failing version already applied a migration, downgrading to the previous API version may leave you running against a schema it doesn't know about. Options:

* **If the migration is additive** (most are): the previous version works fine against the newer schema.
* **If the migration is destructive**: restore from the pre-upgrade `pgdata` backup. This is why the backup is step 1.

## CLI upgrades

CLI upgrades are independent of server upgrades. Users:

```bash
observal self upgrade
```

The CLI speaks a stable contract with the server. A newer CLI works against an older server and vice versa, within a release or two.

On the first command after upgrading from a release that generated `observal-shim` entries, the CLI restores the original MCP command in known home and current-project config files. It writes a `.pre-unshim.bak` copy before each change. A malformed wrapped entry stops the command with a visible migration error rather than guessing at the original executable.

## Zero-downtime for the web UI

The web UI is a static Vite build and restarts instantly. Users see a brief reload if they are on the page during the deploy. No special handling required.

## Next

→ [Backup and restore](/self-hosting/backup-and-restore)
