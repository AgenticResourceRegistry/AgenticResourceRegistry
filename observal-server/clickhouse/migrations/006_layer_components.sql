-- SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
-- SPDX-License-Identifier: Apache-2.0
-- The layer projection must match the Phase 0 pinned publication proof DDL.
CREATE TABLE IF NOT EXISTS layer_components (
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
CREATE TABLE IF NOT EXISTS layer_component_extractions (
    project_id String, user_id String, layer_hash String, extractor_version UInt16,
    extraction_generation UInt64, status LowCardinality(String),
    identity_conflict UInt8 DEFAULT 0, occurrence_count UInt32 DEFAULT 0,
    diagnostic_count UInt32 DEFAULT 0, attempted_at DateTime64(3, 'UTC') DEFAULT now64(3)
) ENGINE = MergeTree()
ORDER BY (project_id, user_id, layer_hash, extractor_version, extraction_generation, status);
