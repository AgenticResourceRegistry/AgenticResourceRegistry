-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
-- SPDX-License-Identifier: Apache-2.0

-- Do not infer/backfill root declarations from lossy legacy summaries.
-- Different declarations use different keys: a later claim cannot erase a
-- conflicting root/child relationship. Repeated identical claims may replace.
CREATE TABLE IF NOT EXISTS session_parent_evidence (
    project_id String,
    user_id String,
    harness LowCardinality(String),
    session_id String,
    relation LowCardinality(String),
    parent_session_id String DEFAULT '',
    evidence_version UInt64,
    received_at DateTime64(3, 'UTC') DEFAULT now64(3)
) ENGINE = ReplacingMergeTree(evidence_version)
PARTITION BY sipHash64(project_id, user_id, harness, session_id) % 64
ORDER BY (project_id, user_id, harness, session_id, relation, parent_session_id);
