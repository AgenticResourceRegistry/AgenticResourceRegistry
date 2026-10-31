<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Server operations

## Contents

- Service lifecycle
- Upgrade and rollback
- PostgreSQL migration
- ClickHouse telemetry migration
- Safety checks

Local server commands use shell, filesystem, Docker, and database authority. API roles do not constrain that local authority.

## Service lifecycle

```bash
observal server status --output json
observal server start --background --output json
observal server restart --background --output json
observal server logs api --lines 100 --output json
observal server stop --output json
```

JSON start and restart require background mode. Verify final service status rather than trusting the launch response alone.

## Upgrade and rollback

Read current and available versions first:

```bash
observal server versions --output json
observal server upgrade --dry-run --output json
```

Execute only the requested direction:

```bash
observal server upgrade --version VERSION --force --output json
observal server rollback --from-backup ~/.observal/backups/vVERSION-TIMESTAMP --force --output json
```

Select the correct managed backup explicitly before rollback; `--from-backup` is required, even with `--force`. PostgreSQL restoration is not an exact clean-schema downgrade, and ClickHouse telemetry is unchanged. Verify Docker services and their running images with `docker compose ps` and check `/health` through the load balancer. `observal server status` checks the separate embedded server; `observal server versions` reads the configured `.env` version, not running images.

## PostgreSQL migration

Export, validate, then import:

```bash
observal server migrate export --file registry.tar.gz --output json
observal server migrate validate --archive registry.tar.gz --output json
observal server migrate import --archive registry.tar.gz --output json
```

Source commands read `DATABASE_URL`; target commands read `TARGET_DATABASE_URL`. Keep URLs out of output and logs. Never replace these commands with hand-written SQL.

## ClickHouse telemetry migration

```bash
observal server migrate export-telemetry --manifest registry.manifest.json --output-dir telemetry-export --output json
observal server migrate validate-telemetry --input-dir telemetry-export --output json
observal server migrate import-telemetry --input-dir telemetry-export --output json
```

Source commands read `CLICKHOUSE_URL`; target commands read `TARGET_CLICKHOUSE_URL`. Export requires a new destination directory. Validate files and Registry references before import.

## Safety checks

- Confirm source, destination, and backup location before import, upgrade, rollback, or reset.
- Use dry run when available.
- Stop after validation failure. Do not import a damaged archive.
- Report counts, versions, warnings, and final service health.
- Never expose database URLs, generated secrets, archive contents, or customer rows.
