# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Build readable session transcripts from ClickHouse session_events.

Reads raw JSONL lines stored in session_events and formats them into a
human-readable transcript suitable for LLM facet extraction.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog

from ._deps import get_call_model, get_query

if TYPE_CHECKING:
    from .scope import SessionKey

logger = structlog.get_logger(__name__)

MAX_TRANSCRIPT_CHARS = 30000
SUMMARY_CHUNK_CHARS = 25000
MAX_PROMPT_CHARS = 500
MAX_ASSISTANT_CHARS = 300
MAX_TOOL_INPUT_CHARS = 200
MAX_TOOL_OUTPUT_CHARS = 200

CHUNK_SUMMARY_PROMPT = """Summarize this portion of a session transcript. Focus on:
1. What the user asked for
2. What the assistant did, including tools used and files modified
3. Any friction or issues
4. The outcome

Keep it concise. Preserve specific file names, error messages, and user feedback.

Respond with only this JSON shape:
{"summary": "3 to 5 sentence summary"}

TRANSCRIPT CHUNK:
"""


async def build_session_transcript(session: SessionKey) -> str:
    """Build a readable transcript for exactly one scoped session."""
    query = get_query()

    sql = """
        SELECT line_offset, event_type, tool_name, raw_line, content_preview
        FROM session_events FINAL
        WHERE project_id = {project_id:String} AND user_id = {user_id:String}
          AND harness = {harness:String} AND session_id = {sid:String}
        ORDER BY line_offset ASC
        FORMAT JSON
    """
    params = {
        "param_project_id": session.project_id,
        "param_user_id": session.user_id,
        "param_harness": session.harness,
        "param_sid": session.session_id,
    }

    try:
        r = await query(sql, params)
        r.raise_for_status()
        rows = r.json().get("data", [])
    except Exception as e:
        logger.warning("transcript_query_failed", session_id=session.session_id, error=str(e))
        return ""

    if not rows:
        return ""

    transcript = _format_rows(rows)
    if len(transcript) <= MAX_TRANSCRIPT_CHARS:
        return transcript
    return await _summarize_transcript(session.session_id, transcript)


def _format_rows(rows: list[dict]) -> str:
    """Parse raw_line JSONL rows and format as readable transcript."""
    lines: list[str] = []

    for row in rows:
        event_type = row.get("event_type", "")
        raw = row.get("raw_line", "")
        tool_name = row.get("tool_name", "")

        if not raw:
            continue

        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue

        line = _format_event(event_type, tool_name, parsed, row.get("content_preview", ""))
        if not line:
            continue

        lines.append(line)

    return "\n".join(lines)


async def _summarize_transcript(session_id: str, transcript: str) -> str:
    """Summarize an oversized transcript instead of cutting off the tail."""
    import services.dynamic_settings as ds

    call_model = get_call_model()
    model_override = await ds.get("insights.model_facets") or None
    chunks = [transcript[i : i + SUMMARY_CHUNK_CHARS] for i in range(0, len(transcript), SUMMARY_CHUNK_CHARS)]
    summaries: list[str] = []

    for index, chunk in enumerate(chunks, start=1):
        try:
            result = await call_model(CHUNK_SUMMARY_PROMPT + chunk, model_override=model_override, max_tokens=700)
            summary = result.get("summary", "") if isinstance(result, dict) else ""
        except Exception as e:
            logger.warning("transcript_summary_failed", session_id=session_id, chunk=index, error=str(e))
            summary = ""

        summaries.append(summary.strip() or chunk[:2000])

    return "\n".join(
        [
            f"Session: {session_id[:8]}",
            "[Long session summarized]",
            "",
            "\n\n[Next chunk]\n\n".join(summaries),
        ]
    )


def _format_event(event_type: str, tool_name: str, parsed: dict, row_content_preview: str = "") -> str:
    """Format a single parsed JSONL event into a transcript line."""
    if event_type == "user_prompt":
        text = _extract_text(parsed) or (row_content_preview if isinstance(row_content_preview, str) else "")
        if text:
            return f"[User]: {text[:MAX_PROMPT_CHARS]}"

    elif event_type == "assistant_text":
        text = _extract_assistant_text(parsed)
        if text:
            return f"[Assistant]: {text[:MAX_ASSISTANT_CHARS]}"

    elif event_type == "tool_call":
        name = tool_name or _extract_tool_name(parsed) or "unknown"
        input_summary = _extract_tool_input(parsed)
        return f"[Tool: {name}] {input_summary[:MAX_TOOL_INPUT_CHARS]}"

    elif event_type == "tool_result":
        name = tool_name or "unknown"
        is_error = parsed.get("is_error", False) or parsed.get("isError", False)
        content = _extract_tool_result(parsed)
        if is_error:
            return f"[Tool Result: {name}] ERROR: {content[:MAX_TOOL_OUTPUT_CHARS]}"
        # Skip non-error tool results to keep transcript focused
        return ""

    return ""


def _extract_text(parsed: dict) -> str:
    """Extract a classified user prompt from Codex, Kiro or conventional messages."""
    payload = parsed.get("payload")
    if isinstance(payload, dict):
        if parsed.get("type") == "event_msg" and payload.get("type") == "user_message":
            return payload.get("message", "") if isinstance(payload.get("message"), str) else ""
        if payload.get("type") == "user" and isinstance(payload.get("content"), str):
            return payload["content"]  # Kiro IDE
    if parsed.get("kind") == "Prompt" and isinstance(parsed.get("data"), dict):
        content = parsed["data"].get("content", [])
        if isinstance(content, list):
            return " ".join(
                item["data"]
                for item in content
                if isinstance(item, dict) and item.get("kind") == "text" and isinstance(item.get("data"), str)
            )
    msg = parsed.get("message", parsed)
    content = msg.get("content", "") if isinstance(msg, dict) else ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        )
    return ""


def _extract_assistant_text(parsed: dict) -> str:
    """Extract assistant text (skip tool_use blocks)."""
    msg = parsed.get("message", parsed)
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return " ".join(parts)
    return ""


def _extract_tool_name(parsed: dict) -> str:
    """Extract tool name from parsed event."""
    if "name" in parsed:
        return parsed["name"]
    msg = parsed.get("message", {})
    content = msg.get("content", [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("tool_use", "toolCall"):
                return block.get("name", "")
    return ""


def _extract_tool_input(parsed: dict) -> str:
    """Extract a summary of tool input."""
    # Direct format
    if "input" in parsed:
        inp = parsed["input"]
        if isinstance(inp, dict):
            if "command" in inp:
                return inp["command"]
            if "file_path" in inp or "path" in inp:
                return f"file: {inp.get('file_path') or inp.get('path')}"
            return json.dumps(inp)[:MAX_TOOL_INPUT_CHARS]
        return str(inp)[:MAX_TOOL_INPUT_CHARS]

    # Content array format
    msg = parsed.get("message", parsed)
    content = msg.get("content", [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("tool_use", "toolCall"):
                inp = block.get("input", block.get("arguments", {}))
                if isinstance(inp, dict):
                    if "command" in inp:
                        return inp["command"]
                    if "path" in inp:
                        return f"file: {inp['path']}"
                    return json.dumps(inp)[:MAX_TOOL_INPUT_CHARS]
    return ""


def _extract_tool_result(parsed: dict) -> str:
    """Extract content from a tool result."""
    content = parsed.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return str(content)[:200]
