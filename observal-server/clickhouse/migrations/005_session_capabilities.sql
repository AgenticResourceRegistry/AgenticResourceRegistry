-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
-- SPDX-License-Identifier: Apache-2.0

-- Which discovery resources a session actually used. Rows arrive with the
-- session push payload (capabilities_used) and come from the CLI's local
-- capability lock; one row per (session, resource, version). Re-delivery of
-- the same use replaces the earlier row rather than duplicating it.
CREATE TABLE IF NOT EXISTS session_capabilities (
        project_id      String,
        user_id         String,
        harness         LowCardinality(String),
        session_id      String,
        identifier      String DEFAULT '',            -- urn:air:... when the CLI knew it
        kind            LowCardinality(String),       -- agent | mcp | skill | hook | prompt | sandbox
        component_id    String DEFAULT '',            -- native UUID when known
        native_ref      String DEFAULT '',            -- namespace/slug@version
        version         String DEFAULT '',
        digest          String DEFAULT '',
        mode            LowCardinality(String),       -- context | next-session
        source          LowCardinality(String),       -- discover-cli | install | pull | gateway
        confidence      LowCardinality(String),       -- exact | window | loose
        used_at         DateTime64(3, 'UTC'),
        ingested_at     DateTime64(3, 'UTC') DEFAULT now64(3),
        INDEX idx_sc_session_id session_id TYPE bloom_filter(0.001) GRANULARITY 1,
        INDEX idx_sc_identifier identifier TYPE bloom_filter(0.001) GRANULARITY 1
    ) ENGINE = ReplacingMergeTree(ingested_at)
    TTL toDateTime(used_at) + INTERVAL 730 DAY
    ORDER BY (project_id, user_id, harness, session_id, kind, component_id, identifier, version)
;
