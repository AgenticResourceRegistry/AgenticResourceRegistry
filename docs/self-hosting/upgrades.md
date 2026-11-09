---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Plan and verify an upgrade"
description: "Choose the deployment-specific upgrade path and separate image rollback from data recovery."
---

Upgrade the registry as a planned change, not a generic `git pull` or restart. Pick a target release/source revision, read its migration requirements, rehearse on representative restored data, and schedule any required maintenance.

## Before a manual upgrade

1. Identify the deployment artifact, current version, Compose project/files/profiles, mounted secrets, public URL, and actual volume names.
2. Back up PostgreSQL, application data/keys, encryption configuration, and the ClickHouse evidence your recovery objectives require. [Rehearse restoration](/self-hosting/backup-and-restore).
3. Review release notes and schema compatibility. A successful image change does not undo a migration.
4. Decide how to stop/drain every old API and worker before a migration that requires a homogeneous fleet.
5. Keep a verified previous artifact and a recovery plan; do not use arbitrary historical versions such as `0.9.0` as copyable targets.

<Warning>
  For complete-folder skill delivery, follow [the supervised rollout](/skill-folder-rollout). An off delivery gate does not serialize schema migration with old processes. Drain old API/workers before the new init runs, keep the gate off until the approved homogeneous rollout is verified, and do not enable it merely because a fresh instance works.
</Warning>

## Choose your deployment path

| Deployment | Upgrade mechanism | Do not assume |
| --- | --- | --- |
| Source Compose | Selected source revision and supervised build/cutover | `up --build` alone provides zero downtime or safe concurrent migration. |
| Downloaded server package | Stage the selected package, preserve/review configuration, then perform explicit cutover | Installer success necessarily means running containers were upgraded. |
| CLI-managed Docker deployment | Supported `observal server upgrade` workflow | It is the upgrade command for every source, packaged, or embedded deployment. |
| Kubernetes/cloud deployment | That release's reviewed infrastructure and migration procedure | Two stacks can share database volumes safely during an arbitrary blue/green upgrade. |

## Standard source Compose upgrade

Run from the intended checkout with your actual project name and any overlay/profile flags. Prepare the target source and build images first. During the planned maintenance window, stop/drain old writers **before** starting the new init:

```bash
# Select the reviewed target source revision, then prepare its API/web images.
docker compose -f docker/docker-compose.yml build observal-api observal-web
# During the maintenance window, stop old writers before starting init.
docker compose -f docker/docker-compose.yml stop observal-api observal-worker
# Init migrates before the new API starts; failed init must remain a blocker.
docker compose -f docker/docker-compose.yml up -d --no-build
```

Monitor `ps -a` and init logs. If init fails, do not start old writers against a partially changed schema or stamp it as current. Resolve the failure using the rehearsed migration/recovery procedure.

After successful startup, restart the load balancer, inspect the response body through the configured public route, and test sign-in plus an ordinary-user agent workflow. Confirm the selected version and runtime policy, not just container uptime.

## Server-package upgrades

Back up the package configuration, mounted credential files, databases, and application data before staging. Preserve the same Compose files, observability overlay/profile, project, volume bindings, and target image tag.

Rerunning `install-server.sh` against existing configuration can **stage** new package files without changing containers or applying migrations. Exit zero in that path means staging succeeded. Choosing to regenerate configuration can instead start services, so old writer processes must be drained before an installer path that starts a new init.

After reviewed staging, select the intended `OBSERVAL_VERSION`, stop old API/workers, pull the target init/API/worker/web images, and perform the cutover from the **installed package directory**:

```bash
# In the installed package directory; include your existing overlays/profile.
docker compose -f docker-compose.yml --env-file .env stop observal-api observal-worker
docker compose -f docker-compose.yml --env-file .env pull observal-init observal-api observal-worker observal-web
docker compose -f docker-compose.yml --env-file .env up -d --wait --wait-timeout 300
docker compose -f docker-compose.yml --env-file .env restart observal-lb
```

Use the selected release's image-tag convention and inspect the effective image values before proceeding. An old pinned tag can keep serving old images. The [Bash 3.2 packaged-setup limitation](/self-hosting/requirements#packaged-setup-on-macos) also remains relevant; this guide does not repair it.

## CLI-managed Docker upgrade

If the local CLI recognizes and manages your Docker deployment, preview an explicit target:

```bash
observal server upgrade --version TARGET_VERSION --dry-run --output json
```

Replace `TARGET_VERSION` with the selected released version. The command requires a recognized Compose directory and configured current version; it is not the embedded server lifecycle command.

After reviewing its plan and any release-specific migration requirements, the apply path requires explicit confirmation. It backs up PostgreSQL, replaces images, and checks health; a failed health check can request prior images. **That is not automatic database rollback.** The backup helper's ClickHouse export is schema information, not a full evidence backup. See [server reference](/cli/server#docker-upgrade).

## Rolling back

Before starting prior images, determine which migrations completed and whether the previous application can read/write the resulting schema. Do not assume additive compatibility, destructive migration reversal, or automatic downgrades.

An image-only rollback leaves database changes in place. The managed rollback workflow can restore PostgreSQL from its selected backup but does not restore ClickHouse telemetry. Inspect reported state and independently validate health and data. A process requesting an old image is not proof recovery succeeded.

If database recovery is required, stop all writers and use the rehearsed [restore procedure](/self-hosting/backup-and-restore) with matching secrets and key material. Never delete volumes to make a failed upgrade look like a fresh successful deployment.

## Availability and CLI versions

Stopping API/workers creates a service interruption even if the web UI, databases, and proxy remain running. Do not call that “zero downtime,” or promise a fixed outage length without measuring your deployment.

Blue/green or rolling upgrades require validated schema/application overlap, traffic routing, independent storage arrangements, and release-specific orchestration. Do not start two database containers on the same data volume as a shortcut.

Developer CLI versions must satisfy the server's compatibility check; this implementation checks exact versions on guarded CLI paths. Do not promise that a newer CLI works with an older server “within a release or two.” Follow [version guidance](/reference/documentation-version) and the command's reported mismatch recovery.

## Next

[Verify backups](/self-hosting/backup-and-restore), [review configuration](/self-hosting/configuration), and retain migration/health evidence before opening traffic or changing rollout gates.
