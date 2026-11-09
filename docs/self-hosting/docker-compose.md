---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Run the source Compose stack"
description: "Bring up a local source deployment, verify it, and understand production differences."
---

This guide runs the **source** Compose stack. It is not the downloadable server package or the embedded `observal server start` runtime. For a quick registry evaluation, follow [Try a registry locally](/getting-started/local-evaluation) and continue to the first-agent workflow.

## Before you start

You need Git, Docker Engine 25 or newer, Compose 2.20.2 or newer, Python 3.11 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/), and enough resources for the [eight-service core](/self-hosting/index#architecture-at-a-glance). Choose a separate checkout/project for evaluation so you do not accidentally reuse a valuable database.

The source defaults include demo credentials and publish some ports on all interfaces. Restrict network exposure and never treat those defaults as a production configuration.

## Clone and configure

```bash
git clone --branch v1.16.0 --depth 1 https://github.com/AgenticResourceRegistry/AgenticResourceRegistry.git
cd AgenticResourceRegistry
cp .env.example .env
```

For development evaluation, the example provides working direct database credentials. For team deployment, prepare [boot configuration, secrets, URLs, and access policy](/self-hosting/configuration) before creating containers. Record the selected source commit; do not let a moving `main` checkout define a production upgrade implicitly.

## Start the stack

```bash
docker compose -f docker/docker-compose.yml up --build -d
```

This builds source API/web images. Init applies or stamps the appropriate database schema state and runs ClickHouse migrations before API startup. Init should exit successfully; it is not expected to stay running.

If using the optional monitoring overlay, keep the same files/profile on subsequent commands:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.observability.yml up --build -d
```

For Grafana as well, add `--profile grafana`. Review its credentials, ports, and outbound plugin downloads separately.

## Verify health

```bash
docker compose -f docker/docker-compose.yml ps -a
docker compose -f docker/docker-compose.yml logs --tail 50 observal-init
curl http://localhost/health
```

With default source ports, the load balancer serves `http://localhost`; direct web access is at port 3000. Expect successful init plus healthy dependencies/API/web and a running worker/load balancer. Do not assume a fixed startup time.

Read the response body: HTTP 200 with `status: degraded` is not a fully healthy stack. Inspect the failed dependency before proceeding. A container's health check does not replace checking the public load-balancer route.

## Establish the intended account

For demo evaluation, use [the local guide](/getting-started/local-evaluation), which deliberately exercises an ordinary user and separate reviewer.

For a fresh non-demo deployment, source Compose's API environment must have its `DEMO_*` defaults explicitly removed/overridden. [The configuration guide](/self-hosting/configuration#demo-accounts) provides the exact override. Merely deleting `.env` entries or setting `SEED_DEMO_ACCOUNTS=false` is not enough in the inspected path.

After restricting access, install a CLI matching the source revision and run:

```bash
uv tool install --editable .
observal auth login --server http://localhost --no-setup
observal auth whoami
```

Use the actual configured URL. On a genuinely empty server, login collects details and creates the first administrator through `/api/v1/auth/init`. That is not the separate localhost-only bootstrap endpoint; secure the initialization network path before opening the server. See [authentication](/self-hosting/authentication#the-first-administrator).

## Verify the registry journey

```bash
observal agent list
```

An empty list can be valid. Create/review an evaluation agent if needed, then [inspect, preview, pull, and activate a release](/getting-started/quickstart). Installation and provider authentication are separate.

## Operate without deleting data

```bash
docker compose -f docker/docker-compose.yml logs --tail 100 observal-api
docker compose -f docker/docker-compose.yml down
```

`down` preserves named volumes. **Do not add `--volumes` as a restart or repair step**: it deletes the deployment's persistent stores. A port conflict or failed login is not permission to reset a database.

Use [ports and volumes](/self-hosting/ports-and-volumes) to remap conflicts and identify the real volume names. After an environment change, recreate affected containers with the intended configuration; `restart` alone keeps the old container environment. After API/web recreation, restart the load balancer and verify the external route so stale upstream addresses do not hide an otherwise healthy deployment.

## Production and upgrades

Configure TLS, real credentials, intended public origins, trusted proxy boundaries, usage reporting, backups, and recovery before team access. Read [planned upgrades](/self-hosting/upgrades): a general rebuild can run migrations while old processes still serve, and is not a safe universal rollout procedure.

## Next

[Configuration](/self-hosting/configuration) → [authentication](/self-hosting/authentication) → [backup and restore](/self-hosting/backup-and-restore) → [upgrades](/self-hosting/upgrades).

<a id="packaged-image-checkpoint"></a>

## v1.16.0 packaged-image checkpoint

The release server archive supplies configuration, not a source build. Pin `OBSERVAL_VERSION=1.16.0` and inspect its rendered Compose model. Its ClickHouse entrypoint can attempt to write into the read-only users directory unless user setup is skipped in favor of explicit configuration. In the isolated artifact check, startup used an operator-owned generated password-hash XML and `CLICKHOUSE_SKIP_USER_SETUP=1` in a separate override. This is not passwordless access or permission to weaken authentication; verify the approved credential configuration and health checks together. No unmodified installer or production deployment acceptance is claimed by that rehearsal.
