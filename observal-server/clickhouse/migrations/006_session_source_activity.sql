-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
-- SPDX-License-Identifier: Apache-2.0

-- Kiro CLI only timestamps prompts; later tool events inherit that prompt time.
-- Keep the event timeline intact, but track arrival of real source records
-- separately so an active long turn does not appear completed.
ALTER TABLE session_stats_agg
    ADD COLUMN IF NOT EXISTS last_source_ingest_at DateTime64(3, 'UTC') DEFAULT toDateTime64(0, 3, 'UTC');
