---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Data migration"
---

Use data migration when you need to move an Observal instance to a new deployment, validate a backup, or copy production data into a controlled recovery environment.

Only super admins can start migration jobs.

## What can be moved

- **Registry data**: users, agents, components, versions, settings, review records, and related PostgreSQL data.
- **Telemetry data**: sessions, checkpoints, layer snapshots, audit events, security events, and webhook delivery history stored in ClickHouse. Large tables are exported as bounded Parquet chunks, so total export size is not limited by ClickHouse query memory.
- **Registry + telemetry**: a full instance move when both stores are available.

## Before you start

1. Confirm both source and target instances are on compatible Observal versions.
2. Schedule a maintenance window if users are actively changing registry data.
3. Make sure the target deployment has enough disk space for uploaded artifacts.
4. Decide whether you need registry data only or registry plus telemetry.
5. Treat exported files like production backups. They can contain hashed credentials, API keys, and telemetry with PII.

## Export from the source instance

1. Open **Admin → Settings → Data Migration**.
2. Click **Migrate**.
3. Select **Export**.
4. Choose the export scope:
   - **Registry data** for PostgreSQL records only.
   - **Registry + telemetry** for a full move.
5. Click **Start export**.
6. Wait for the job to finish.
7. Download every artifact shown in the result.
8. Store the artifacts in a secure temporary location.

## Validate before import

Run validation on the target instance before importing.

1. Open **Admin → Settings → Data Migration** on the target instance.
2. Select **Validate**.
3. Upload the artifacts from the export. For **Registry + telemetry**, add both `pg_export.tar.gz` and `telemetry_export.tar.gz`; the picker retains files added in separate selections and lists each selected artifact.
4. Choose the same scope you plan to import.
5. Click **Start validation**.
6. Review the result:
   - Checksums should pass.
   - Table counts should match expectations.
   - Telemetry validation should not report broken registry references unless you intentionally skipped registry data.

Do not import artifacts that fail checksum validation.

## Import into the target instance

1. Open **Admin → Settings → Data Migration** on the target instance.
2. Select **Import**.
3. Upload the validated artifacts. For **Registry + telemetry**, both the PostgreSQL and telemetry archives are required.
4. Choose the import scope.
5. Imports normalize all project-keyed telemetry to the deployment project `default`.
6. Click **Start import**.
7. Wait for the job to finish.
8. Check agents, components, users, and sessions in the target instance.

PostgreSQL imports skip conflicting rows. Telemetry imports resume per checksummed Parquet chunk; a retry skips only chunks already completed for the same migration artifact.

## CLI alternative

The CLI uses the same shared migration core as the server jobs. Source commands read `DATABASE_URL` and `CLICKHOUSE_URL`; target commands read `TARGET_DATABASE_URL` and `TARGET_CLICKHOUSE_URL`.

```bash
observal server migrate export --file backup.tar.gz --output json
observal server migrate validate --archive backup.tar.gz --output json
observal server migrate import --archive backup.tar.gz --output json
```

Telemetry commands are separate:

```bash
observal server migrate export-telemetry --manifest backup-manifest.json --output-dir telemetry --output json
observal server migrate validate-telemetry --input-dir telemetry --output json
observal server migrate import-telemetry --input-dir telemetry --output json
```

## Cleanup

1. Confirm the target instance works.
2. Delete local copies of migration artifacts.
3. Remove temporary upload files from the target host if you copied them outside the UI.
4. Keep only the backup copy required by your retention policy.

## Troubleshooting

### Validation fails

Confirm the selected scope matches the uploaded files. **Registry + telemetry** requires both export archives, and the picker must show both filenames before submission. Re-download the artifacts from the source export if checksums fail; if they still fail, create a new export.

Large migration uploads are streamed through nginx and spooled to the persistent migration data volume instead of API memory. Ensure that volume has room for both the uploaded telemetry archive and its extracted Parquet files during validation/import.

### Import resumes completed telemetry chunks

Telemetry resume state is stored beside the extracted artifact in `.import_state.json`. A retry verifies the artifact and skips chunks already completed with the same checksum. It does not skip an entire month merely because the target already contains some rows from that month.

### ClickHouse reports a memory limit

The exporter automatically subdivides a memory-limited chunk and retries it. If the smallest supported chunk still fails, inspect the reported table and chunk ID for a pathological key distribution or a ClickHouse limit below the documented deployment minimum. Increasing the container limit should not be required merely because the total telemetry archive is large.

### Telemetry import has missing registry references

Import registry data first, then validate and import telemetry again.

### Jobs time out

Increase the migration job timeout setting or split registry and telemetry into separate operations.
