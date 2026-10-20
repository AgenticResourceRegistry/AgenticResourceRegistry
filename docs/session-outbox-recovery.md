---
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Recovering rejected session batches"
---

A permanent ingest rejection (400/409/413/415/422) is **not an acknowledgement**. The CLI writes and fsyncs each original batch as an atomic owner-only entry in `~/.observal/telemetry_buffer.rejected.d/` (older `telemetry_buffer.rejected.jsonl` archives remain readable), removes it from the 256 MiB active SQLite outbox, and persists a destination/user/harness/session block in `telemetry_buffer.db`. Later batches for that session cannot be delivered across the gap; other sessions continue. The sync log reports the rejected outbox item ID and HTTP status. The archive contains raw transcript data: do not publish it.

The rejected row in SQLite records the exact archive filename (or legacy byte offset) selected by the successful removal transaction. An archive entry published by a stale process whose removal failed is an orphan and is never used for recovery or spooling, even if it was published later. If the selected entry is missing or damaged, recovery fails closed; do not substitute another matching archive entry.

On first outbox open after an upgrade, intact legacy JSONL entries lacking an `item_id` are indexed in SQLite without rewriting the archive. Their stable recovery IDs are negative: the ID is `-(byte offset of the entry in the archive + 1)` (the first entry is `-1`). `stats()["failed"]` counts these blocks; rows with invalid JSON are skipped so other intact entries remain recoverable. Do not edit or reorder the archive after import: the byte offsets identify its entries.

After correcting the cause of rejection, replay explicitly with Python in an environment containing Observal:

```python
from observal_cli.telemetry_buffer import recover_quarantined

recover_quarantined(REJECTED_ITEM_ID)
```

If the source needs correction, pass `payload=corrected_payload` with the **same harness, session ID, start offset and number of lines**; the original remains in the archive. Recovery enqueues the batch durably *before* clearing the block. If there is not enough active capacity or an incompatible batch already occupies the source range, recovery fails and leaves the block in place. Do not delete the archive or the SQLite state to unblock a session: inspect and resolve the source conflict first. Recovery is idempotent after a crash during enqueue, but an already completed recovery cannot be replayed again under the same item ID. Back up the SQLite database and both archive locations together before any manual intervention. An interrupted append in a legacy JSONL archive may leave a corrupt line; intact records before or after it remain readable and new rejections go to atomic entries instead. Recovery of a damaged entry itself fails explicitly if no intact copy exists.
