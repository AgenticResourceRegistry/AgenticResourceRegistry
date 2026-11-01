-- SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
-- SPDX-License-Identifier: Apache-2.0
-- Phase 0 pinned logical DDL contract. {prefix} is replaced only with phase0_ci_ in local tests.
-- Phase 1/2 migration authors must compare production 006_/007_ DDL with this contract
-- and re-run these proofs. Test-only execution does not validate a migration runner.
CREATE TABLE IF NOT EXISTS {prefix}layer_components (
    project_id String, user_id String, layer_hash String, hash_schema_version UInt8,
    extractor_version UInt16, extraction_generation UInt64, occurrence_key String,
    component_type LowCardinality(String), source LowCardinality(String),
    harness LowCardinality(String), scope LowCardinality(String), parent_agent_id String,
    parent_agent_version String, raw_listing_id String, raw_name String, raw_version String,
    qualified_name String, local_name String, component_id String,
    component_version_id String, identity_status LowCardinality(String),
    verification_status LowCardinality(String), row_revision UInt64 DEFAULT 1
) ENGINE = ReplacingMergeTree(row_revision)
ORDER BY (project_id, user_id, layer_hash, extractor_version, extraction_generation, occurrence_key);
CREATE TABLE IF NOT EXISTS {prefix}layer_component_extractions (
    project_id String, user_id String, layer_hash String, extractor_version UInt16,
    extraction_generation UInt64, status LowCardinality(String),
    identity_conflict UInt8 DEFAULT 0, occurrence_count UInt32 DEFAULT 0,
    diagnostic_count UInt32 DEFAULT 0, attempted_at DateTime64(3, 'UTC') DEFAULT now64(3)
) ENGINE = MergeTree()
ORDER BY (project_id, user_id, layer_hash, extractor_version, extraction_generation, status);
CREATE TABLE IF NOT EXISTS {prefix}component_activity (
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
CREATE TABLE IF NOT EXISTS {prefix}component_activity_publications (
    project_id String, user_id String, harness LowCardinality(String), session_id String,
    projection_version UInt16, projection_generation UInt64,
    status LowCardinality(String), source_revision String DEFAULT '',
    candidate_count UInt32 DEFAULT 0, attributed_count UInt32 DEFAULT 0,
    collision_count UInt32 DEFAULT 0, unmatched_count UInt32 DEFAULT 0,
    unknown_result_count UInt32 DEFAULT 0,
    attempted_at DateTime64(3, 'UTC') DEFAULT now64(3)
) ENGINE = MergeTree()
ORDER BY (project_id, user_id, harness, session_id, projection_version, projection_generation, status);
