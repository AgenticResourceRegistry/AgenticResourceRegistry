---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Back up and rehearse recovery"
description: "Create protected backups and validate restoration without deleting the live deployment."
---

A backup is useful when you can restore the data, secrets, and software state it depends on. This guide gives a **single-node source Compose** rehearsal path; packaged/cloud deployments must use their actual project, mounts, credentials, and storage procedures.

## What to preserve

| Store/material | What it protects | Recovery concern |
| --- | --- | --- |
| PostgreSQL | Users, resources, versions, reviews, settings, and relational state | Preserve the database plus the secret needed to decrypt stored sensitive values. |
| Application data (`apidata`) | JWT key material and other persistent application artifacts | Restoring PostgreSQL alone does not restore signing keys or every artifact. |
| Configuration and mounted secrets | `SECRET_KEY`, database credentials, provider/SP secrets, mount/project metadata | New random secrets can make restored encrypted values unusable. |
| ClickHouse (`chdata`) | Sessions, aggregates, installation/evidence snapshots, audit and security records | A schema export is **not** a backup of these records. |
| Redis (`redisdata`) | Queue/persistence and authentication/runtime coordination state | Do not assume lost jobs or revocation state automatically reappears on worker restart. |
| Optional Grafana data/configuration | Operator-created dashboards and provisioned definitions | Preserve custom content according to your deployment. |

Define recovery point/time objectives for each store. ClickHouse audit/evidence may be critical for your organization; do not universally rank it as dispensable. Use protected off-host copies and a tested recovery cadence.

<Warning>
  **This recipe is not a complete recovery set by itself:** it archives PostgreSQL, application material, and ClickHouse, but does not back up or restore Redis. Require a separate validated Redis persistence backup/restore and queue-reconciliation procedure, recorded with the recovery set, before calling recovery complete. A `redisdata` volume alone does not prove recent in-memory jobs or revocations were persisted. Keep restored API/workers isolated until authentication revocations—including changes after the backup—are reconciled or old credentials are invalidated.
</Warning>

## Before creating a backup

Record the software revision, schema migration state, Compose project/files/profiles, volume names, and mounted secret locations. Pick a maintenance window or a validated snapshot procedure that keeps the relevant stores consistent.

The commands below run from the source checkout, use Bash, and assume the example database/user `observal`/`postgres`. Replace those if your deployment differs. Keep overlays/profile flags consistent. Store backups outside the checkout:

```bash
umask 077
BACKUP_DIR="$HOME/registry-backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
# Add -p YOUR_PROJECT and any overlay/profile flags to this function if used.
dc() { docker compose -f docker/docker-compose.yml "$@"; }
```

Do not paste a guessed Docker volume prefix. Resolve the actual app and ClickHouse mounts **before** stopping services:

```bash
API_CONTAINER="$(dc ps -q observal-api)"
CH_CONTAINER="$(dc ps -q observal-clickhouse)"
APP_VOLUME="$(docker inspect "$API_CONTAINER" --format '{{range .Mounts}}{{if and (eq .Type "volume") (eq .Destination "/data")}}{{.Name}}{{end}}{{end}}')"
CH_VOLUME="$(docker inspect "$CH_CONTAINER" --format '{{range .Mounts}}{{if and (eq .Type "volume") (eq .Destination "/var/lib/clickhouse")}}{{.Name}}{{end}}{{end}}')"
test -n "$APP_VOLUME" && test -n "$CH_VOLUME"
```

This example assumes one relevant API and ClickHouse container. For replicas or external databases, use the corresponding consistent backup procedure instead of treating multiple container IDs as one path.

## Back up the relational and application state

Drain/stop all API and worker writers. This is an intentional interruption; the database remains running for a logical dump:

```bash
dc stop observal-api observal-worker
dc exec -T observal-db pg_dump -U postgres -Fc -d observal > "$BACKUP_DIR/postgres.dump"
test -s "$BACKUP_DIR/postgres.dump"
```

Use an approved helper image to archive the identified application volume without writing to it. `alpine:3` is an evaluation example; pin an approved image/digest for your operational procedure:

```bash
HELPER_IMAGE='alpine:3'
docker run --rm --mount "type=volume,source=$APP_VOLUME,target=/data,readonly" \
  "$HELPER_IMAGE" tar czf - -C /data . > "$BACKUP_DIR/apidata.tar.gz"
test -s "$BACKUP_DIR/apidata.tar.gz"
```

Securely preserve the matching configuration and externally mounted secrets separately; the app volume does not contain every host credential. Do not print those files or commit them. Record checksums and protect the backup's provenance, confidentiality, and integrity.

## Back up ClickHouse evidence

For a cold volume archive, keep writers stopped, stop ClickHouse, and archive its identified volume read-only:

```bash
dc stop observal-clickhouse
docker run --rm --mount "type=volume,source=$CH_VOLUME,target=/data,readonly" \
  "$HELPER_IMAGE" tar czf - -C /data . > "$BACKUP_DIR/clickhouse.tar.gz"
test -s "$BACKUP_DIR/clickhouse.tar.gz"
```

A native ClickHouse backup is an alternative only after its destination, authentication, restore procedure, and storage policy are configured and verified. A generic `BACKUP ... Disk('backups', ...)` command does not establish that the stock deployment has such a disk. See [ClickHouse backup documentation](https://clickhouse.com/docs/en/operations/backup).

Inspect command exit codes and archive contents. A non-empty file alone is not proof of a complete backup. If any step fails, preserve the error and resolve the service state deliberately; do not continue a cron chain as if a backup succeeded.

Resume the original services only after the covered backups and required separate Redis procedure succeed, with confirmed data-store health. Verify health through the actual load balancer and reconcile pending work. Restart the load balancer if API/web containers were recreated.

## Rehearse restoration in isolation

<Warning>
  Do **not** run `down --volumes` against the live deployment as a restore step. Create separate rehearsal storage and inspect its actual mounts. Never let a test restore write into the production volumes or send cloned credentials to production integrations.
</Warning>

1. Prepare a separate project and fresh destination stores matching the backed-up software/schema requirements.
2. Restore the intended configuration and encryption material securely, with outbound production integrations blocked. Keep API/workers stopped while data is restored.
3. Restore PostgreSQL into the explicitly identified fresh destination database. For a logical dump, use `pg_restore --exit-on-error` and check the result; do not treat every non-zero restore status as harmless.
4. Restore trusted application-data and ClickHouse archives into their separate destination volumes while the relevant services are stopped. Verify archive paths/provenance and ownership before extraction. A bind mount or empty replacement volume is not the recovered data.
5. Validate schema state before running init. An existing, unversioned, or partially initialized schema must not simply be stamped as current. Apply only the reviewed upgrade path needed for the target version.
6. Restore Redis using the separately validated procedure and reconcile persistence/checkpoints and queued work before workers can consume it. Do not silently replace it with an empty store.
7. **Authentication cutover gate:** restoring old signing keys with empty/older Redis can accept a revoked, unexpired access token because its revocation marker is missing. Redis fail-closed behavior protects against connection failure, not absent markers. Reconcile revocations after the backup from an authoritative source, or use an approved procedure to invalidate old access tokens and restored refresh/session credentials; key restoration or key rotation alone is not proof that all old credentials are rejected. Keep API access restricted until representative revoked-token rejection and intended login/recovery are verified.
8. Start compatible data stores and the API on the isolated validation network, check the health response body, and test ordinary-user permissions before authorizing external access, workers, or outbound integrations.

For a **fresh disposable destination database only**, the logical restore pattern is:

```bash
# DEST_PROJECT and DEST_DB must identify the isolated rehearsal, not production.
docker compose -p DEST_PROJECT -f docker/docker-compose.yml exec -T observal-db \
  pg_restore --exit-on-error --no-owner --no-privileges -U postgres -d DEST_DB \
  < "$BACKUP_DIR/postgres.dump"
```

The destination database must already exist, and the no-owner/no-privileges choice must match the rehearsal's role model. Production recovery may need original roles/grants preserved through its own planned procedure.

## Verify recovery

Compare expected account/resource/version/review counts and representative records, key identity, encrypted-setting readability, and required ClickHouse evidence. Verify restored permissions as an ordinary user. If preserving token continuity is a recovery objective, test it rather than inferring it from a restored key filename; Redis and revocation/expiry state also matter.

Back up before every migration/upgrade, keep the previous recovery set until acceptance, and rehearse representative historical recovery periodically. The CLI-managed upgrade helper's PostgreSQL backup and optional ClickHouse **schema** export do not replace this full recovery plan.

## Next

[Plan an upgrade](/self-hosting/upgrades), [check storage/network bindings](/self-hosting/ports-and-volumes), and keep a recovery checklist with measured results instead of an untested deletion/reset recipe.
