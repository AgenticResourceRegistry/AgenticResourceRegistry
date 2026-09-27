-- SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
-- SPDX-License-Identifier: Apache-2.0
-- The activity projection must match the Phase 0 pinned publication proof DDL.
CREATE TABLE IF NOT EXISTS component_activity (
    project_id String, user_id String, harness LowCardinality(String), session_id String,
    projection_version UInt16, projection_generation UInt64,
    source_line_offset UInt64, source_block_key String, source_line_hash String,
    layer_hash String, component_type LowCardinality(String), component_id String,
    component_version_id String, tool_name String, tool_use_id String,
    event_time DateTime64(3, 'UTC'), result_state LowCardinality(String),
    attribution_method LowCardinality(String), matcher_version UInt16,
    extractor_version UInt16, row_revision UInt64 DEFAULT 1
) ENGINE = ReplacingMergeTree(row_revision)
ORDER BY (project_id, user_id, harness, session_id, projection_version,
          projection_generation, source_line_offset, source_block_key);
CREATE TABLE IF NOT EXISTS component_activity_publications (
    project_id String, user_id String, harness LowCardinality(String), session_id String,
    projection_version UInt16, projection_generation UInt64,
    status LowCardinality(String), source_revision String DEFAULT '',
    candidate_count UInt32 DEFAULT 0, attributed_count UInt32 DEFAULT 0,
    collision_count UInt32 DEFAULT 0, unmatched_count UInt32 DEFAULT 0,
    unknown_result_count UInt32 DEFAULT 0,
    attempted_at DateTime64(3, 'UTC') DEFAULT now64(3)
) ENGINE = MergeTree()
ORDER BY (project_id, user_id, harness, session_id, projection_version, projection_generation, status);
