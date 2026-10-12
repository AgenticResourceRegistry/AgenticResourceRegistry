-- SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
-- SPDX-License-Identifier: Apache-2.0

-- OTLP forwarding watermarks: how far each destination has acknowledged each
-- session, per signal. forwarded_line is the highest source line_offset a
-- destination accepted with a 2xx; rows above it and at or below the session
-- checkpoint are still to send. The highest version wins, so an integrity
-- rewind is just a newer row with a lower forwarded_line. replay_through is
-- the old watermark a rewind moved back from: rows up to it are sent again
-- marked as replays.
CREATE TABLE IF NOT EXISTS otlp_forward_state (
        destination_id  String,
        signal          LowCardinality(String),       -- logs | traces
        project_id      String,
        user_id         String,
        harness         LowCardinality(String),
        session_id      String,
        forwarded_line  Int64,
        replay_through  Int64 DEFAULT -1,
        session_closed  UInt8 DEFAULT 0,
        version         UInt64,
        updated_at      DateTime64(3, 'UTC') DEFAULT now64(3)
    ) ENGINE = ReplacingMergeTree(version)
    ORDER BY (destination_id, signal, project_id, user_id, harness, session_id)
    TTL toDateTime(updated_at) + INTERVAL 730 DAY
;

-- One row per OTLP POST attempt, for the admin status endpoint and debugging.
-- Never holds URLs, headers or payload content.
CREATE TABLE IF NOT EXISTS otlp_forward_deliveries (
        delivery_id     UUID,
        destination_id  String,
        signal          LowCardinality(String),
        session_id      String,
        attempt         UInt8,
        status_code     Nullable(UInt16),
        status          LowCardinality(String),       -- delivered | retry | failed
        records         UInt32,
        rejected        UInt32 DEFAULT 0,
        payload_bytes   UInt32,
        duration_ms     Float32,
        error           Nullable(String),
        timestamp       DateTime64(3, 'UTC')
    ) ENGINE = MergeTree()
    PARTITION BY toYYYYMM(timestamp)
    ORDER BY (destination_id, signal, timestamp)
    TTL toDateTime(timestamp) + INTERVAL 90 DAY
;
