---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal doctor support"
---

Generate and inspect redacted diagnostic support bundles.

Both workflows support `--output table|json`. Bundle generation writes a `.tar.gz` archive as its intended side effect. JSON mode returns archive metadata and never prompts.

## Generate a bundle

```bash
observal doctor support bundle --output json
```

Choose the archive path with the hard-renamed `--file/-f` option:

```bash
observal doctor support bundle \
  --file /tmp/observal-support.tar.gz \
  --logs-since 2h \
  --no-include-system \
  --output json
```

The former archive-path meaning of `--output/-o` has been removed. `--output/-o` now consistently selects `table` or `json`.

### Options

| Option | Purpose |
| --- | --- |
| `--file`, `-f` | Archive path; defaults to a timestamped file in the current directory |
| `--logs-since` | Positive duration up to 30 days, such as `30m`, `2h`, or `1d12h` |
| `--include-system`, `--no-include-system` | Include or omit local system metrics |
| `--force`, `--yes` | Overwrite an existing archive and accept the size warning |
| `--output`, `-o` | Human or JSON result |

Existing archives prompt in human mode. JSON mode fails with a conflict unless `--force` or `--yes` is present.

JSON returns:

```json
{
  "path": "/tmp/observal-support.tar.gz",
  "size_bytes": 43120,
  "remote_status": "collected",
  "warnings": [],
  "collector_results": {},
  "redaction_counts": {}
}
```

## Collection behavior

Remote collectors use `POST /api/v1/support/collect` and require an administrator account. They provide:

* Application and migration versions
* PostgreSQL, ClickHouse, and Redis health
* Allowlisted configuration
* Aggregate table counts
* Error fingerprints
* Recent redacted log records

Local collectors provide allowlisted configuration processing and optional OS, CPU, memory, disk, and container-runtime details.

Remote collection is optional by design. Authentication, permission, version, network, or response failures are recorded in `remote_status` and `warnings`; local collection continues. This fallback is never silent.

Individual collector failures are retained in the manifest and JSON result. If no collector produces data, the command exits with code 9 and does not create an archive.

## Privacy and integrity

Support bundles contain aggregate diagnostics, not customer database rows or trace payloads.

Protections include:

* Configuration allowlisting before archive creation
* Redaction of passwords, tokens, API keys, private keys, authorization values, URL credentials, JWTs, AWS access keys, and high-entropy strings
* Redaction of collector error text before it enters the manifest
* Rejection of unknown collector names and unsafe archive paths
* Hashed host identity rather than a raw hostname
* SHA-256 inventory entries for every archived file
* Atomic archive replacement with mode `0600`

The manifest records collector status, redaction counts, file sizes, and hashes. Review every bundle before sharing it because logs and deployment metadata can still be operationally sensitive.

## Size budget

The uncompressed budget is 100 MB. Human mode asks before exceeding it. JSON mode requires `--force` and never prompts.

## Inspect a bundle

```bash
observal doctor support inspect ./observal-support.tar.gz --output json
```

Show one regular file:

```bash
observal doctor support inspect ./observal-support.tar.gz \
  --show health/postgres.json \
  --output json
```

JSON returns the manifest, safe regular-file inventory, schema warnings, and optional shown content:

```json
{
  "manifest": {"bundle_schema_version": "1"},
  "files": [{"path": "health/postgres.json", "size_bytes": 91}],
  "warnings": [],
  "shown": {"path": "health/postgres.json", "content": "{\"status\":\"ok\"}"}
}
```

Inspection never extracts files. It rejects traversal paths, links requested through `--show`, malformed or oversized manifests, and shown files larger than 1 MB. Unsafe archive members are omitted and reported as warnings.

## Exit codes

| Code | Meaning |
| --- | --- |
| 5 | Bundle or requested member not found |
| 6 | Destination archive already exists |
| 7 | Invalid duration, unsafe archive, malformed manifest, oversized display, or missing non-interactive confirmation |
| 9 | No diagnostic data or archive write failure |

## Related

* [`observal doctor`](/cli/doctor): diagnose and repair telemetry instrumentation
* [`observal ops telemetry status`](/cli/ops): check telemetry flow
* [Troubleshooting](/self-hosting/troubleshooting): deployment diagnosis
