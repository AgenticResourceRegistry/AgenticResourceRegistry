---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal reconcile"
---

Backfill local session records missed by automatic hook or extension delivery.

Reconcile is a manual recovery command, not the normal collection path. The server cannot scan files on a developer machine. Automatic hooks and extensions wake the same durable delivery engine during normal harness activity; reconcile scans recent local sources on demand.

## When to use it

Run reconcile when:

* Telemetry instrumentation was installed after sessions already existed
* The machine or server was offline
* Delivery was interrupted
* Durable outbox records remain pending
* Recent local history needs to be verified or backfilled

Routine use is unnecessary when automatic delivery is healthy.

## Examples

Preview the default seven-day window without network or cursor changes:

```bash
observal reconcile --dry-run --output json
```

Backfill every installed harness:

```bash
observal reconcile --output json
```

Target one harness and a 24-hour discovery window:

```bash
observal reconcile --harness kiro --since 24 --output json
```

The discovery window accepts 1 through 8,760 hours.

## Delivery behavior

A non-dry run:

1. Validates configuration and the harness selection before any outbox side effect.
2. Retries the existing durable outbox.
3. Discovers recent session sources through installed harness adapters.
4. Skips locally finalized sources that have not grown.
5. Recovers the contiguous server checkpoint for unfinished sources.
6. Queues and sends only complete records after that checkpoint.
7. Sends final metadata when all records were uploaded but the session was not finalized.
8. Leaves transient failures queued for the next automatic wake-up or reconcile run.
9. Quarantines permanent server rejections and reports them explicitly.

Acknowledged checkpoints make repeated runs idempotent.

A dry run only reads local sources and cursor state. It does not drain the outbox, contact the ingest API, or update cursor state.

## JSON result

```json
{
  "dry_run": false,
  "since_hours": 168,
  "outbox_drained": true,
  "targets": [
    {
      "harness": "kiro",
      "discovered": 2,
      "pushed": 1,
      "finalized": 0,
      "queued": 1,
      "rejected": 0,
      "would_push": 0,
      "would_finalize": 0,
      "up_to_date": 0,
      "skipped": 0,
      "errors": 0,
      "sessions": [
        {"session_id": "session-1", "status": "pushed", "bytes_new": 512},
        {"session_id": "session-2", "status": "queued", "bytes_new": 128}
      ]
    }
  ],
  "summary": {
    "discovered": 2,
    "pushed": 1,
    "finalized": 0,
    "queued": 1,
    "rejected": 0,
    "would_push": 0,
    "would_finalize": 0,
    "up_to_date": 0,
    "skipped": 0,
    "errors": 0
  },
  "rejections": []
}
```

`outbox_drained: false` means durable records remain pending. A queued session is safely stored for retry and is not reported as delivered. Permanent ingestion failures include their HTTP status in `rejections`.

## Exit codes

| Code | Meaning |
| --- | --- |
| 3 | Session delivery identity is not configured |
| 7 | Unknown harness or invalid discovery window |
| 9 | Outbox storage or session discovery is unavailable |

Per-session source read failures and checkpoint mismatches are explicit result items so other sessions can still be recovered.

## Related

* [`observal doctor`](/cli/doctor): configure and verify automatic telemetry instrumentation
* [`observal ops telemetry status`](/cli/ops): inspect server and durable outbox health
* [Session tracking](/core-concepts/session-tracking): automatic and recovery delivery architecture
