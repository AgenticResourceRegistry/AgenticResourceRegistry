-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
-- SPDX-License-Identifier: Apache-2.0

-- Count each model call's tokens once (services/usage_dedup.py).
-- usage_key identifies the API response a transcript line's usage belongs to;
-- lines of one response share it and only one of them keeps the token counts.
-- cache_write_1h_tokens is the subset of cache_write_tokens written with
-- one-hour retention, which providers bill at 2x input instead of 1.25x.
-- Existing rows are keyed by the worker's usage-dedup backfill, one session at
-- a time, so this migration only adds columns.
ALTER TABLE session_events ADD COLUMN IF NOT EXISTS usage_key String DEFAULT '' CODEC(ZSTD(1));
ALTER TABLE session_events ADD COLUMN IF NOT EXISTS cache_write_1h_tokens Int32 DEFAULT 0;
