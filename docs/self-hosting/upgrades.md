<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Upgrades

Safe upgrade flow for the Observal server stack.

> [!IMPORTANT]
> When upgrading into the release that introduces complete-folder skill delivery, follow the [skill-folder rollout procedure](../skill-folder-rollout.md) instead of using the quick, standard, or blue/green instructions below unchanged. Those paths do not guarantee that old API and worker processes have stopped before the new init container runs PostgreSQL migrations. Keep folder delivery off, back up and rehearse the historical upgrade, and schedule a maintenance window to drain old processes **before** migration; enable delivery only after a homogeneous rollout and separate approval.

## Quick upgrade (recommended)

If you installed Observal via `observal server start` (the embedded stack), the CLI handles upgrades automatically:

```bash
observal server upgrade --dry-run --output json
observal server upgrade --version 0.9.0 --force --output json
```

This pulls new Docker images, backs up PostgreSQL, recreates containers, and runs health checks. If the health check fails, it requests the previous image version again. Local shell and Docker access authorize the operation; the command does not require a reachable API or API role.

See [`observal server upgrade`](../cli/server.md#observal-server-upgrade) for full details.

### Server-package upgrades

If you installed with `install-server.sh`, back up `.env`, `secrets/`, and the `apidata` and `pgdata` volumes before rerunning the installer for the target release. Setup detects the existing `.env`. Choose the default **No** response to preserve custom values and existing direct credentials. **This only stages the new package and retains a backup: no containers restart and no migrations run.** An exit code of zero means staging succeeded, not that the server has been upgraded. The installer records the prior bind address when an older install has no `OBSERVAL_BIND_ADDRESS` setting.

For the complete-folder release, follow the [supervised rollout](../skill-folder-rollout.md): keep the delivery gate off, stop *all* old API and worker processes before starting the new init container, then run Compose from the installed package with the same optional observability overlay/profile used by the old deployment. For a core-only package installation, after staging the package and verifying backups and the gate:

```bash
cd /opt/observal  # or the directory passed to --install-dir
# Set OBSERVAL_VERSION in the preserved .env to the selected release's image tag.
# For installer --version vX.Y.Z, the image tag is X.Y.Z (no leading v).
# An old pinned tag keeps serving old images; latest also requires an explicit pull.
docker compose -f docker-compose.yml --env-file .env stop observal-api observal-worker
docker compose -f docker-compose.yml --env-file .env pull observal-init observal-api observal-worker observal-web
docker compose -f docker-compose.yml --env-file .env up -d --wait --wait-timeout 300
docker compose -f docker-compose.yml --env-file .env restart observal-lb
curl -fsS http://127.0.0.1:8000/health  # adjust for the configured API_HOST_PORT
```

Add `-f docker-compose.observability.yml` and `--profile grafana` if those were used previously; do not silently drop optional services. Verify the API externally through the load balancer, not only via its container health check. Choosing **Yes** at the configuration prompt instead intentionally rebuilds the general configuration from the new template and starts services; for this release, **stop old API and worker processes before running the installer**, because setup will start the new init container. Core application, PostgreSQL, ClickHouse, Grafana, and demo credentials are preserved or migrated into restricted files, but unrelated custom environment entries must be reapplied.

## Before a manual upgrade

1. **Back up `pgdata`** and **`apidata`**. See [Backup and restore](backup-and-restore.md). Backing up `chdata` is nice-to-have; losing telemetry is painful but not catastrophic.
2. **Read the [CHANGELOG](https://github.com/Observal/Observal/blob/main/CHANGELOG.md)** for the releases you're jumping across. Note any breaking changes.
3. **Pin the version you're upgrading to**: don't `git pull main` blindly. Check out a release tag or a known-good commit.

## Standard upgrade

```bash
cd Observal

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

→ [Backup and restore](backup-and-restore.md)
