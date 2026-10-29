---
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Recovering rejected session batches"
---

A permanent ingest rejection (400/409/413/415/422) is **not an acknowledgement**. The CLI writes and fsyncs each original batch as an atomic owner-only entry in `~/.observal/telemetry_buffer.rejected.d/` (older `telemetry_buffer.rejected.jsonl` archives remain readable), removes it from the 256 MiB active SQLite outbox, and persists a destination/user/harness/session block in `telemetry_buffer.db`. Later batches for that session cannot be delivered across the gap; other sessions continue. The sync log reports the rejected outbox item ID and HTTP status. The archive contains raw transcript data: do not publish it.

The rejected row in SQLite records the exact archive filename (or legacy byte offset) selected by the successful removal transaction. An archive entry published by a stale process whose removal failed is an orphan and is never used for recovery or spooling, even if it was published later. If the selected entry is missing or damaged, recovery fails closed; do not substitute another matching archive entry.

On each outbox open, the legacy JSONL archive is statted; it is rescanned only when its device, inode, size, or modification/change time differs from the fingerprint committed with the import marker (including a missing or empty archive). Older databases with an ID-only marker are rescanned once. Intact legacy entries lacking an `item_id` have stable negative recovery IDs: `-(byte offset of the entry in the archive + 1)` (the first entry is `-1`). Repeated scans preserve existing recovery state. `stats()["failed"]` counts unresolved blocks. Invalid JSON lines are skipped so other intact entries remain recoverable; an identifiable but structurally invalid entry fails closed instead of marking the archive imported. Do not edit or reorder the archive after import: byte offsets identify its entries.

During a mixed-version upgrade, stop old workers before draining new workers if possible. An old worker can append between a new worker's last archive stat and delivery, or leave a partial line while writing; file metadata checks cannot make legacy JSONL appends and SQLite delivery one atomic operation. A detected change during import rolls back and must be retried. Keep the legacy archive and database together, and inspect any gaps before resuming delivery after old workers stop.

After correcting the cause of rejection, replay explicitly with Python in an environment containing Agentic Resource Registry:

```python
from observal_cli.telemetry_buffer import recover_quarantined

recover_quarantined(REJECTED_ITEM_ID)
```

If the source needs correction, pass `payload=corrected_payload` with the **same harness, session ID, start offset and number of lines**; the original remains in the archive. Recovery enqueues the batch durably *before* clearing the block. If there is not enough active capacity or an incompatible batch already occupies the source range, recovery fails and leaves the block in place. Do not delete the archive or the SQLite state to unblock a session: inspect and resolve the source conflict first. Recovery is idempotent after a crash during enqueue, but an already completed recovery cannot be replayed again under the same item ID. Back up the SQLite database and both archive locations together before any manual intervention. An interrupted append in a legacy JSONL archive may leave a corrupt line; intact records before or after it remain readable and new rejections go to atomic entries instead. Recovery of a damaged entry itself fails explicitly if no intact copy exists.

## Source files that shrink or move

If a session file becomes shorter than its recorded cursor or a pending final audit's posted byte offset, **or a scanner reports a different source path for the same checkpoint key**, Agentic Resource Registry marks that session `source_shrunk` in `~/.observal/sync_state.json` and refuses to acknowledge its pending outbox batches or send them again. A path change fails closed before posting or spooling from the new location, even if the old file remains intact. The sync state stores the first absolute source path locally so a final ACK can recheck file size even without a scanner; the path is not sent in the ingest payload. Legacy outboxes without a recorded path still rely on the shrink marker and cursor revision. A recorded path that is missing or invalid blocks final acknowledgement until locally repaired. A later append does **not** automatically clear the marker: bytes at the old cursor could now be a fragment of a new record. Other sessions can still deliver. Inspect the session file, the stored cursor, and the server checkpoint before attempting repair. Back up the source, `sync_state.json`, and `telemetry_buffer.db` together. Restore the missing original bytes from a trusted backup or reconcile the server and local state with operator oversight; only then remove that session's `source_shrunk` flag from the backed-up cursor state. Do not clear the marker merely because the file grew back to its prior size, and do not delete pending final audits to force progress.
