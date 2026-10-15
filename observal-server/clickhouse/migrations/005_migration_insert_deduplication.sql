-- SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
-- SPDX-License-Identifier: Apache-2.0

-- Chunked telemetry migration assigns a deterministic insert_deduplication_token
-- to each artifact chunk. Non-replicated MergeTree tables disable block
-- deduplication by default, so retain enough recent block IDs to make an
-- ambiguous-response retry safe.
ALTER TABLE session_events
    MODIFY SETTING non_replicated_deduplication_window = 100000;

ALTER TABLE session_checkpoints
    MODIFY SETTING non_replicated_deduplication_window = 100000;

ALTER TABLE session_stats_agg
    MODIFY SETTING non_replicated_deduplication_window = 100000;

ALTER TABLE layer_snapshots
    MODIFY SETTING non_replicated_deduplication_window = 100000;

ALTER TABLE audit_log
    MODIFY SETTING non_replicated_deduplication_window = 100000;

ALTER TABLE security_events
    MODIFY SETTING non_replicated_deduplication_window = 100000;

ALTER TABLE webhook_deliveries
    MODIFY SETTING non_replicated_deduplication_window = 100000;
