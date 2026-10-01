-- SPDX-FileCopyrightText: 2026 Observal Contributors
-- SPDX-License-Identifier: Apache-2.0
-- Skill evidence shares the versioned activity rails but never MCP semantics:
-- each activity row says what kind of fact it is, and each publication says
-- which projection it completes, so MCP and skill coverage stay independent.
-- Existing rows are MCP calls and MCP publications.
ALTER TABLE component_activity ADD COLUMN IF NOT EXISTS evidence_kind LowCardinality(String) DEFAULT 'call';
ALTER TABLE component_activity_publications ADD COLUMN IF NOT EXISTS evidence_type LowCardinality(String) DEFAULT 'mcp';
-- A verified skill is bound to the SHA-256 of the absolute SKILL.md path its
-- verifier hashed. Skill evidence is attributed only to that exact location;
-- an empty value (any older extraction) never matches.
ALTER TABLE layer_components ADD COLUMN IF NOT EXISTS location_sha256 String DEFAULT '';
