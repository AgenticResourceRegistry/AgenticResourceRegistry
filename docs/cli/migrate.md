<!-- SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# `observal server migrate`

Move PostgreSQL registry data and ClickHouse telemetry between Observal deployments.

Migration uses the supplied database connections directly. Local shell and database access are the authorization boundary; the command does not authenticate against a configured Observal API.

Install the optional dependency first:

```bash
pip install 'observal-cli[migrate]'
```

Keep connection URLs in environment variables or secret files managed by the shell. Source commands read `DATABASE_URL` and `CLICKHOUSE_URL`; target commands read `TARGET_DATABASE_URL` and `TARGET_CLICKHOUSE_URL`. Explicit URL options remain available when no secret is embedded. Do not paste credentials into shared shell history, logs, or issue reports. JSON results and categorized errors never echo a connection URL.

## Workflow

1. Export PostgreSQL. This creates a checksummed registry archive and a migration manifest.
2. Validate and import PostgreSQL on the target.
3. Export ClickHouse using the PostgreSQL migration manifest.
4. Validate and import ClickHouse on the target.

PostgreSQL must be imported first so referenced users and agents exist before telemetry validation.

## PostgreSQL export

```bash
observal server migrate export \
  --file registry.tar.gz \
  --output json
```

`--file/-f` selects the archive destination. `--output/-o` always selects `table` or `json`. Existing destinations fail with a conflict instead of being overwritten.

The archive and sidecar manifest are written atomically with owner-only permissions. A partial archive is not published after failure.

Example JSON fields:

```json
{
  "archive": "registry.tar.gz",
  "manifest": "registry.manifest.json",
  "migration_id": "7b84e503-63af-4b89-a1cd-abf48f0452f3",
  "table_counts": {"users": 8, "agents": 21},
  "total_rows": 342,
  "size_bytes": 1048576,
  "duration_seconds": 2.4
}
```

## PostgreSQL validation and import

```bash
observal server migrate validate \
  --archive registry.tar.gz \
  --output json

observal server migrate import \
  --archive registry.tar.gz \
  --output json
```

Validation checks archive structure and SHA-256 checksums. When a target URL is provided, it also compares table row counts. Checksum failure returns a categorized validation error. Row-count differences remain explicit result data.

Import verifies checksums before insertion. Existing rows are skipped according to the migration service's idempotent import rules. The result contains per-table inserted and skipped counts plus warnings.

## ClickHouse export

ClickHouse export requires the PostgreSQL sidecar manifest and a new destination directory:

```bash
observal server migrate export-telemetry \
  --manifest registry.manifest.json \
  --output-dir telemetry-export \
  --output json
```

The destination must not already exist. This lets the exporter remove the complete directory after failure without touching pre-existing files. The directory and streamed Parquet files use restrictive permissions and atomic temporary files.

The export covers active session, checkpoint, layer, audit, security, and webhook tables. Older sources may omit tables. Telemetry is divided into deterministic time-and-hash chunks so each ClickHouse query and Parquet file stays bounded independently of the total dataset size. Oversized or memory-limited chunks are split again automatically. `telemetry_manifest.json` records every chunk's range, shard, checksum, size, row count, and migration ID.

## ClickHouse validation and import

```bash
observal server migrate validate-telemetry \
  --input-dir telemetry-export \
  --output json

observal server migrate import-telemetry \
  --input-dir telemetry-export \
  --output json
```

Telemetry validation checks:

* Parquet checksums
* Manifest row counts against target ClickHouse when supplied
* Agent and user references against target PostgreSQL when supplied

Checksum failure is fatal. Row-count differences and orphan groups are returned explicitly.

Telemetry import is resumable per Parquet chunk. Successfully imported chunk IDs and checksums are recorded in `.import_state.json` in the input directory, so a retry skips only verified completed chunks rather than an entire table or month. Deterministic insert tokens protect ambiguous retries on current non-replicated MergeTree schemas. Imported project-keyed rows normalize to the deployment project `default`, and session summaries are rebuilt after event import so summaries reflect complete sessions rather than individual insert blocks.

## Human and JSON behavior

All six leaves accept `--output table|json`. Human mode renders progress and summaries. JSON mode is finite, prompt-free, suppresses progress and warnings from stdout, and returns one result document. Failures leave stdout empty and emit one categorized error to stderr.

Cleartext ClickHouse transport with credentials produces a human warning. JSON mode does not print a banner; operators should use `clickhouses://` for TLS.

## Exit codes

| Code | Category | Typical migration cause |
| --- | --- | --- |
| 2 | Usage | Missing required option or unsupported output mode |
| 4 | Permission | Destination or source path is not accessible |
| 5 | Not found | Archive, manifest, or input directory is missing |
| 6 | Conflict | Archive or telemetry destination already exists |
| 7 | Validation | Invalid archive, failed checksum, or missing phase prerequisite |
| 9 | Unavailable | Optional dependency, database, network, or migration service failure |

## Recommended sequence

```bash
# Source
observal server migrate export --file registry.tar.gz --output json
observal server migrate export-telemetry \
  --manifest registry.manifest.json \
  --output-dir telemetry-export \
  --output json

# Target
observal server migrate validate --archive registry.tar.gz --output json
observal server migrate import --archive registry.tar.gz --output json
observal server migrate validate-telemetry \
  --input-dir telemetry-export \
  --output json
observal server migrate import-telemetry \
  --input-dir telemetry-export \
  --output json
```
