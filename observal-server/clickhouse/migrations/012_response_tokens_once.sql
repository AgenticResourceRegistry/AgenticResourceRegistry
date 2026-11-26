-- SPDX-FileCopyrightText: 2026 Observal Contributors
-- SPDX-License-Identifier: Apache-2.0
-- Claude Code writes one transcript line per content block of an API response and
-- repeats the whole response's usage on each line, so summing rows counted every
-- response's tokens once per line. Ingest now keys rows by API response
-- (message.id plus requestId, as services.session_ingest._response_key_claude_code)
-- and keeps the tokens on one row per response. This applies the same rule to rows
-- already stored: the rendered row with the most output tokens keeps them (lowest
-- line_offset on a tie), the other rows of the response are zeroed, and every keyed
-- row records its response_key so later pushes of a split response find it.
--
-- Copies are written with ingested_at + 1 ms: newer, so they replace the stored row,
-- without making old sessions look freshly ingested to scans that filter on
-- ingested_at. raw_line and every other column are copied unchanged.
--
-- Only assistant lines carry a message id, and they always classify as one of the
-- three event types below, so other rows (tool results hold the largest payloads)
-- are never parsed. A few stored lines are not valid JSON because secret redaction
-- swallowed a closing quote inside the content; the message and request ids sit
-- outside the content, so for those lines they are read textually.
ALTER TABLE session_events ADD COLUMN IF NOT EXISTS response_key String DEFAULT '' CODEC(ZSTD(1));

INSERT INTO session_events
(
    session_id, project_id, user_id, agent_id, agent_version, layer_hash,
    harness, line_offset, source_end_offset, line_hash, source_sha256,
    is_source_record, rendered, event_type, timestamp, uuid, parent_uuid,
    tool_name, tool_id, content_preview, content_length, raw_line,
    ingested_at, credits, parent_session_id, input_tokens, output_tokens,
    cache_read_tokens, cache_write_tokens, model, raw_line_truncated, response_key
)
SELECT
    e.session_id, e.project_id, e.user_id, e.agent_id, e.agent_version, e.layer_hash,
    e.harness, e.line_offset, e.source_end_offset, e.line_hash, e.source_sha256,
    e.is_source_record, e.rendered, e.event_type, e.timestamp, e.uuid, e.parent_uuid,
    e.tool_name, e.tool_id, e.content_preview, e.content_length, e.raw_line,
    e.ingested_at + toIntervalMillisecond(1), e.credits, e.parent_session_id,
    if(k.carrier, e.input_tokens, 0), if(k.carrier, e.output_tokens, 0),
    if(k.carrier, e.cache_read_tokens, 0), if(k.carrier, e.cache_write_tokens, 0),
    e.model, e.raw_line_truncated, k.response_key_backfill
FROM session_events AS e FINAL
INNER JOIN
(
    SELECT
        project_id, user_id, harness, session_id, line_offset, response_key_backfill,
        rendered = 1 AND row_number() OVER (
            PARTITION BY project_id, user_id, harness, session_id, response_key_backfill
            ORDER BY rendered DESC, output_tokens DESC, line_offset ASC
        ) = 1 AS carrier
    FROM
    (
        SELECT
            project_id, user_id, harness, session_id, line_offset, rendered, output_tokens,
            isValidJSON(raw_line) AS valid_json,
            if(valid_json, JSONExtractString(raw_line, 'message', 'id'),
               extract(raw_line, '"message":\\s*\\{[^{}]*?"id":\\s*"([^"\\\\]+)"')) AS message_id,
            if(valid_json, JSONExtractString(raw_line, 'requestId'),
               extract(raw_line, '"requestId":\\s*"([^"\\\\]+)"')) AS request_id,
            if(request_id = '', message_id, concat(message_id, ':', request_id)) AS response_key_backfill
        FROM session_events FINAL
        WHERE harness IN ('claude-code', 'cursor', 'opencode') AND is_source_record = 1 AND response_key = ''
          AND event_type IN ('assistant_text', 'thinking', 'tool_call')
    )
    WHERE message_id != ''
) AS k
ON e.project_id = k.project_id AND e.user_id = k.user_id AND e.harness = k.harness
   AND e.session_id = k.session_id AND e.line_offset = k.line_offset
WHERE e.harness IN ('claude-code', 'cursor', 'opencode') AND e.is_source_record = 1 AND e.response_key = ''
  AND e.event_type IN ('assistant_text', 'thinking', 'tool_call');

-- Rebuild the summaries from the corrected rows; mirrors refresh_session_summary in
-- services/clickhouse/insert.py. Its microsecond summary_version outranks the
-- millisecond rows session_stats_mv wrote for the insert above.
INSERT INTO session_stats_agg
(
    project_id, session_id, agent_id, agent_version, user_id, parent_session_id, harness, layer_hash,
    first_event_time, last_event_time, event_count, prompt_count, tool_call_count, tool_result_count,
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, total_credits, model,
    summary_version, updated_at, last_source_ingest_at
)
SELECT
    project_id,
    session_id,
    coalesce(anyIf(agent_id, agent_id IS NOT NULL AND agent_id != ''), ''),
    coalesce(anyIf(agent_version, agent_version IS NOT NULL AND agent_version != ''), ''),
    user_id,
    coalesce(anyIf(parent_session_id, parent_session_id IS NOT NULL), ''),
    harness,
    coalesce(anyIf(layer_hash, layer_hash IS NOT NULL AND layer_hash != ''), ''),
    minIf(timestamp, rendered = 1 AND timestamp > '1971-01-01 00:00:00' AND timestamp < '2099-01-01 00:00:00'),
    maxIf(timestamp, rendered = 1 AND timestamp > '1971-01-01 00:00:00' AND timestamp < '2099-01-01 00:00:00'),
    countIf(rendered = 1),
    countIf(rendered = 1 AND event_type = 'user_prompt'),
    countIf(rendered = 1 AND event_type = 'tool_call'),
    countIf(rendered = 1 AND event_type = 'tool_result'),
    sumIf(input_tokens, rendered = 1),
    sumIf(output_tokens, rendered = 1),
    sumIf(cache_read_tokens, rendered = 1),
    sumIf(cache_write_tokens, rendered = 1),
    max(credits),
    if(countIf(rendered = 1 AND model != '') > 0,
       anyLastIf(model, rendered = 1 AND model != ''),
       anyLastIf(model, event_type = 'kiro_session_model')),
    toUInt64(toUnixTimestamp64Micro(now64(6))),
    now64(3),
    maxIf(ingested_at, is_source_record = 1 AND rendered = 1)
FROM session_events FINAL
WHERE harness IN ('claude-code', 'cursor', 'opencode')
GROUP BY project_id, session_id, user_id, harness;
