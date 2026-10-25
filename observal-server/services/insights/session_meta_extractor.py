# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic per-session metadata extraction from raw JSONL transcripts.

Reads session_events.raw_line from ClickHouse and computes rich stats
per session: lines added/removed, git commits, languages, tool errors,
response times, interruptions, file modifications, subagent/MCP usage,
message timestamps, and more.

Modeled after the pi /insights extension's extractSessionStats and
buildSessionMeta functions, adapted for cross-user server-side execution.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import structlog

from services.insight_version_filters import agent_version_filter
from services.session_parsers import meta_records

from ._deps import get_query

logger = structlog.get_logger(__name__)

# Language detection by file extension
EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".py": "Python",
    ".rb": "Ruby",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".md": "Markdown",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".sh": "Shell",
    ".css": "CSS",
    ".html": "HTML",
    ".c": "C",
    ".cpp": "C++",
    ".cs": "C#",
    ".kt": "Kotlin",
    ".swift": "Swift",
}


def _ext(path: str) -> str:
    """Get file extension from path."""
    _, ext = os.path.splitext(path)
    return ext.lower()


def _count_newlines(s: str) -> int:
    return s.count("\n")


def _extract_text_from_content(content) -> str:
    """Extract plain text from message content (string or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return " ".join(parts)
    return str(content) if content else ""


def extract_session_meta(session_id: str, raw_lines: list[str], harness: str = "") -> dict:
    """Extract deterministic metadata from raw JSONL lines for one session.

    Lines are first converted to Pi-style records by the *harness*'s
    ``session_parsers`` converter, so every harness is measured the same way.

    Returns a dict with all computable stats (no LLM needed).
    """
    tool_counts: dict[str, int] = {}
    languages: dict[str, int] = {}
    tool_error_categories: dict[str, int] = {}
    files_modified: set[str] = set()
    user_response_times: list[float] = []
    message_hours: list[int] = []
    user_message_timestamps: list[str] = []

    git_commits = 0
    git_pushes = 0
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    total_cost = 0.0
    user_interruptions = 0
    tool_errors = 0
    uses_subagent = False
    uses_mcp = False
    lines_added = 0
    lines_removed = 0
    user_message_count = 0
    assistant_message_count = 0
    failed_response_count = 0
    first_prompt = ""
    total_messages = 0
    model_usage: dict[str, dict] = {}  # {model: {input_tokens, output_tokens, cost, messages}}

    last_assistant_ts: float | None = None
    start_time: str | None = None
    end_time: str | None = None
    project_path = ""
    seen_tool_ids: set[str] = set()

    entries: list[dict] = []
    for raw in raw_lines:
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    if harness:
        entries = meta_records(harness, entries)

    for entry in entries:
        entry_type = entry.get("type", "")
        ts_str = entry.get("timestamp", "")

        # Kiro format: {"version": "v1", "kind": "Prompt"|"AssistantMessage", "data": {...}}
        entry_kind = entry.get("kind", "")
        if entry_kind and not entry_type:
            kiro_data = entry.get("data", {})
            if isinstance(kiro_data, dict):
                kiro_meta = kiro_data.get("meta") if isinstance(kiro_data.get("meta"), dict) else {}
                epoch_s = kiro_meta.get("timestamp") if kiro_meta else None
                if epoch_s:
                    from datetime import UTC as _UTC

                    kiro_ts = datetime.fromtimestamp(epoch_s, tz=_UTC).isoformat()
                    if start_time is None or kiro_ts < start_time:
                        start_time = kiro_ts
                    if end_time is None or kiro_ts > end_time:
                        end_time = kiro_ts

                if entry_kind == "Prompt":
                    user_message_count += 1
                    total_messages += 1
                    content = kiro_data.get("content", [])
                    text_parts = [
                        item.get("data", "")
                        for item in content
                        if isinstance(item, dict) and item.get("kind") == "text"
                    ]
                    if not first_prompt and text_parts:
                        first_prompt = "\n".join(text_parts)[:200]
                elif entry_kind == "AssistantMessage":
                    assistant_message_count += 1
                    total_messages += 1
                    content = kiro_data.get("content", [])
                    for item in content:
                        if isinstance(item, dict) and item.get("kind") == "toolUse":
                            tool_data = item.get("data", {})
                            if isinstance(tool_data, dict):
                                tool_name = tool_data.get("name", "unknown")
                                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                                # Extract file/lines info from tool input
                                args = tool_data.get("input", {})
                                if isinstance(args, dict):
                                    file_path = args.get("path", "") or args.get("file_path", "")
                                    if file_path:
                                        ext = _ext(file_path)
                                        lang = EXTENSION_TO_LANGUAGE.get(ext)
                                        if lang:
                                            languages[lang] = languages.get(lang, 0) + 1
                                    if tool_name == "write" and file_path:
                                        files_modified.add(file_path)
                                        content_str = args.get("content", "")
                                        if isinstance(content_str, str) and content_str:
                                            lines_added += _count_newlines(content_str) + 1
                                    elif tool_name == "edit" and file_path:
                                        files_modified.add(file_path)
                                        old = args.get("oldStr", "") or args.get("old_string", "")
                                        new = args.get("newStr", "") or args.get("new_string", "")
                                        if isinstance(old, str) and old:
                                            lines_removed += _count_newlines(old) + 1
                                        if isinstance(new, str) and new:
                                            lines_added += _count_newlines(new) + 1
                                    elif tool_name == "bash":
                                        cmd = args.get("command", "")
                                        if isinstance(cmd, str):
                                            if "git commit" in cmd:
                                                git_commits += 1
                                            if "git push" in cmd:
                                                git_pushes += 1
                elif entry_kind == "ToolResults":
                    total_messages += 1
            continue

        # Track time range (Claude Code / Pi format)
        if ts_str:
            if start_time is None or ts_str < start_time:
                start_time = ts_str
            if end_time is None or ts_str > end_time:
                end_time = ts_str

        # Session header
        if entry_type == "session":
            project_path = entry.get("cwd", "")
            continue

        if entry_type != "message":
            continue

        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "")
        total_messages += 1

        # Parse timestamp for response time tracking
        msg_ts: float | None = None
        if ts_str:
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                msg_ts = dt.timestamp()
            except (ValueError, TypeError):
                pass

        # ── Assistant message ──
        if role == "assistant":
            if msg.get("stopReason") == "error" and not msg.get("content"):
                # The provider refused the request (bad credentials, an outage), so
                # nothing reached the user: Pi records it like this, and so does the
                # Claude Code normaliser for its API error notices.
                failed_response_count += 1
                continue
            assistant_message_count += 1
            if msg_ts:
                last_assistant_ts = msg_ts

            # Token usage
            usage = msg.get("usage", {})
            msg_model = msg.get("model", "") or ""
            if usage:
                msg_in = int(usage.get("input", 0) or 0)
                msg_out = int(usage.get("output", 0) or 0)
                input_tokens += msg_in
                output_tokens += msg_out
                cache_read_tokens += int(usage.get("cacheRead", 0) or 0)
                cache_write_tokens += int(usage.get("cacheWrite", 0) or 0)
                msg_cost = 0.0
                cost = usage.get("cost", {})
                if isinstance(cost, dict) and cost.get("total"):
                    msg_cost = float(cost["total"])
                    total_cost += msg_cost
                # Per-model usage tracking
                if msg_model:
                    if msg_model not in model_usage:
                        model_usage[msg_model] = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "messages": 0}
                    model_usage[msg_model]["input_tokens"] += msg_in
                    model_usage[msg_model]["output_tokens"] += msg_out
                    model_usage[msg_model]["cost"] += msg_cost
                    model_usage[msg_model]["messages"] += 1
            elif msg_model:
                # No usage block but model is present (count message)
                if msg_model not in model_usage:
                    model_usage[msg_model] = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "messages": 0}
                model_usage[msg_model]["messages"] += 1

            # Tool calls in content
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "toolCall":
                        continue

                    tool_name = block.get("name", "")
                    tool_id = block.get("id", "")

                    if tool_id and tool_id in seen_tool_ids:
                        continue
                    if tool_id:
                        seen_tool_ids.add(tool_id)

                    tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                    # What the call does, when the harness names its tools differently from Pi.
                    kind = block.get("kind") or tool_name

                    if kind == "subagent":
                        uses_subagent = True
                    if tool_name.startswith("mcp__") or tool_name == "mcp":
                        uses_mcp = True

                    args = block.get("arguments", {})
                    if not isinstance(args, dict):
                        try:
                            args = json.loads(args) if isinstance(args, str) else {}
                        except (json.JSONDecodeError, TypeError):
                            args = {}

                    file_path = args.get("path", "") or args.get("file_path", "")

                    if file_path:
                        ext = _ext(file_path)
                        lang = EXTENSION_TO_LANGUAGE.get(ext)
                        if lang:
                            languages[lang] = languages.get(lang, 0) + 1

                    if kind == "write" and file_path:
                        files_modified.add(file_path)
                        content_str = args.get("content", "")
                        if isinstance(content_str, str) and content_str:
                            lines_added += _count_newlines(content_str) + 1

                    if kind == "edit" and file_path:
                        files_modified.add(file_path)
                        edits = args.get("edits", [])
                        if isinstance(edits, list):
                            for e in edits:
                                if isinstance(e, dict):
                                    # A patch edit names its own file.
                                    if isinstance(e.get("path"), str) and e["path"]:
                                        files_modified.add(e["path"])
                                    old = e.get("oldText", "") or e.get("old_string", "")
                                    new = e.get("newText", "") or e.get("new_string", "")
                                    if isinstance(old, str) and old:
                                        lines_removed += _count_newlines(old) + 1
                                    if isinstance(new, str) and new:
                                        lines_added += _count_newlines(new) + 1

                    if kind == "bash":
                        cmd = args.get("command", "")
                        if isinstance(cmd, str):
                            if "git commit" in cmd:
                                git_commits += 1
                            if "git push" in cmd:
                                git_pushes += 1

        # ── User message ──
        elif role == "user":
            content = msg.get("content", [])
            text = _extract_text_from_content(content)

            if text.strip():
                user_message_count += 1
                if not first_prompt:
                    first_prompt = text.strip()[:300]

                if "[Request interrupted by user" in text:
                    user_interruptions += 1

                if msg_ts:
                    try:
                        dt = datetime.fromtimestamp(msg_ts)
                        message_hours.append(dt.hour)
                        user_message_timestamps.append(dt.isoformat())
                    except (ValueError, OSError):
                        pass

                    if last_assistant_ts is not None:
                        gap_sec = msg_ts - last_assistant_ts
                        if 2 < gap_sec < 3600:
                            user_response_times.append(gap_sec)

        # ── Tool result ──
        elif role == "toolResult":
            is_error = msg.get("isError", False)
            if is_error:
                tool_errors += 1
                tool_name = msg.get("toolName", "other")
                # Categorize errors
                content = msg.get("content", [])
                error_text = _extract_text_from_content(content).lower()
                if "not found" in error_text or "no such file" in error_text:
                    cat = "file_not_found"
                elif "permission" in error_text:
                    cat = "permission_denied"
                elif "command failed" in error_text or "exit code" in error_text:
                    cat = "command_failed"
                elif "edit" in tool_name.lower() or "failed to apply" in error_text:
                    cat = "edit_failed"
                elif "reject" in error_text or "user" in error_text:
                    cat = "user_rejected"
                elif "file changed" in error_text or "modified" in error_text:
                    cat = "file_changed"
                else:
                    cat = "other"
                tool_error_categories[cat] = tool_error_categories.get(cat, 0) + 1

    # Compute duration
    duration_seconds = 0.0
    if start_time and end_time:
        try:
            t_start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            t_end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            duration_seconds = (t_end - t_start).total_seconds()
        except (ValueError, TypeError):
            pass

    return {
        "session_id": session_id,
        "project_path": project_path,
        "start_time": start_time or "",
        "end_time": end_time or "",
        "duration_seconds": duration_seconds,
        "user_message_count": user_message_count,
        "assistant_message_count": assistant_message_count,
        "failed_response_count": failed_response_count,
        "total_messages": total_messages,
        "tool_counts": tool_counts,
        "languages": languages,
        "git_commits": git_commits,
        "git_pushes": git_pushes,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "total_cost": total_cost,
        "first_prompt": first_prompt,
        "user_interruptions": user_interruptions,
        "tool_errors": tool_errors,
        "tool_error_categories": tool_error_categories,
        "uses_subagent": uses_subagent,
        "uses_mcp": uses_mcp,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "files_modified": len(files_modified),
        "user_response_times": user_response_times,
        "message_hours": message_hours,
        "user_message_timestamps": user_message_timestamps,
        "model_usage": model_usage,
    }


async def fetch_session_stats(
    agent_id: str,
    period_start: str,
    period_end: str,
    agent_name: str = "",
    agent_version: str | None = None,
) -> dict[str, dict]:
    """Fetch per-session stats (credits, harness) from session_stats_agg.

    Returns {session_id: {"credits": float, "harness": str}} for enriching metas.
    """
    query = get_query()

    sql = """
        SELECT session_id, total_credits, harness, layer_hash
        FROM session_stats_agg FINAL
        WHERE (agent_id = {agent_id:String} OR agent_id = {agent_name:String})
          AND last_event_time >= {t_start:String}
          AND last_event_time <= {t_end:String}
          AND __AGENT_VERSION_FILTER__
        GROUP BY session_id, total_credits, harness, layer_hash
        FORMAT JSON
    """.replace("__AGENT_VERSION_FILTER__", agent_version_filter())
    params = {
        "param_agent_id": agent_id,
        "param_agent_name": agent_name,
        "param_t_start": period_start,
        "param_t_end": period_end,
        "param_agent_version": agent_version or "",
    }

    try:
        r = await query(sql, params)
        r.raise_for_status()
        rows = r.json().get("data", [])
        return {
            row["session_id"]: {
                "credits": float(row.get("total_credits") or 0),
                "harness": row.get("harness", ""),
                "layer_hash": row.get("layer_hash", ""),
            }
            for row in rows
        }
    except Exception as e:
        logger.warning("fetch_session_stats_agg_failed", error=str(e))

    fallback_sql = """
        SELECT
            session_id,
            max(credits) AS total_credits,
            anyIf(harness, harness != '') AS harness,
            anyIf(layer_hash, layer_hash IS NOT NULL AND layer_hash != '') AS layer_hash
        FROM session_events FINAL
        WHERE (agent_id = {agent_id:String} OR agent_id = {agent_name:String})
          AND timestamp >= {t_start:String}
          AND timestamp <= {t_end:String}
          AND __AGENT_VERSION_FILTER__
        GROUP BY session_id
        FORMAT JSON
    """.replace("__AGENT_VERSION_FILTER__", agent_version_filter(nullable=True))
    try:
        r = await query(fallback_sql, params)
        r.raise_for_status()
        rows = r.json().get("data", [])
        return {
            row["session_id"]: {
                "credits": float(row.get("total_credits") or 0),
                "harness": row.get("harness", ""),
                "layer_hash": row.get("layer_hash", ""),
            }
            for row in rows
        }
    except Exception as e:
        logger.warning("fetch_session_stats_fallback_failed", error=str(e))
        return {}


async def fetch_all_session_transcripts(
    agent_id: str,
    period_start: str,
    period_end: str,
    agent_name: str = "",
    agent_version: str | None = None,
    batch_size: int = 50,
) -> dict[str, tuple[str, list[str]]]:
    """Fetch raw JSONL lines for all sessions of an agent from ClickHouse.

    Batches by session_id to avoid loading 200MB+ into memory at once.
    For 20 users x 100 sessions, fetches ~50 sessions at a time.

    Returns {session_id: (harness, [raw_line, ...])} for all sessions in the period.
    """
    query = get_query()

    # First, get the list of session_ids from the lightweight session_stats_agg
    id_sql = """
        SELECT session_id
        FROM session_stats_agg FINAL
        WHERE (agent_id = {agent_id:String} OR agent_id = {agent_name:String})
          AND last_event_time >= {t_start:String}
          AND last_event_time <= {t_end:String}
          AND __AGENT_VERSION_FILTER__
        GROUP BY session_id
        ORDER BY min(last_event_time)
        FORMAT JSON
    """.replace("__AGENT_VERSION_FILTER__", agent_version_filter())
    params = {
        "param_agent_id": agent_id,
        "param_agent_name": agent_name,
        "param_t_start": period_start,
        "param_t_end": period_end,
        "param_agent_version": agent_version or "",
    }

    try:
        r = await query(id_sql, params)
        r.raise_for_status()
        session_ids = [row["session_id"] for row in r.json().get("data", [])]
    except Exception as e:
        logger.warning("fetch_session_ids_agg_failed", error=str(e))
        fallback_id_sql = """
            SELECT DISTINCT session_id
            FROM session_events FINAL
            WHERE (agent_id = {agent_id:String} OR agent_id = {agent_name:String})
              AND timestamp >= {t_start:String}
              AND timestamp <= {t_end:String}
              AND __AGENT_VERSION_FILTER__
            ORDER BY session_id
            FORMAT JSON
        """.replace("__AGENT_VERSION_FILTER__", agent_version_filter(nullable=True))
        try:
            r = await query(fallback_id_sql, params)
            r.raise_for_status()
            session_ids = [row["session_id"] for row in r.json().get("data", [])]
        except Exception as fallback_error:
            logger.error("fetch_session_ids_failed", error=str(fallback_error))
            return {}

    if not session_ids:
        return {}

    logger.info("fetching_transcripts", total_sessions=len(session_ids), batch_size=batch_size)

    # Fetch raw lines in batches
    all_sessions: dict[str, tuple[str, list[str]]] = {}

    for i in range(0, len(session_ids), batch_size):
        batch_ids = session_ids[i : i + batch_size]

        batch_sql = """
            SELECT session_id, harness, raw_line
            FROM session_events FINAL
            WHERE session_id IN ({ids:Array(String)})
              AND raw_line != ''
            ORDER BY session_id, line_offset
            FORMAT JSON
        """
        params = {"param_ids": "[" + ",".join(f"'{sid.replace(chr(39), '')}" + "'" for sid in batch_ids) + "]"}

        try:
            r = await query(batch_sql, params)
            r.raise_for_status()
            rows = r.json().get("data", [])
            for row in rows:
                sid = row["session_id"]
                all_sessions.setdefault(sid, (row.get("harness") or "", []))[1].append(row["raw_line"])
        except Exception as e:
            logger.warning("fetch_batch_failed", batch_start=i, error=str(e))
            continue

    logger.info(
        "fetched_session_transcripts",
        agent_id=agent_id,
        sessions=len(all_sessions),
        total_lines=sum(len(lines) for _, lines in all_sessions.values()),
    )
    return all_sessions


async def extract_all_session_metas(
    agent_id: str,
    period_start: str,
    period_end: str,
    agent_name: str = "",
    agent_version: str | None = None,
) -> list[dict]:
    """Fetch transcripts and extract deterministic metadata for all sessions.

    This is the main entry point: fetches raw lines from ClickHouse,
    then runs extract_session_meta on each session.
    Also enriches each meta with credits and harness info from session_stats_agg.
    """
    transcripts = await fetch_all_session_transcripts(
        agent_id,
        period_start,
        period_end,
        agent_name=agent_name,
        agent_version=agent_version,
    )

    # Fetch per-session stats (credits, harness, layer hash) for enrichment
    session_stats = await fetch_session_stats(
        agent_id,
        period_start,
        period_end,
        agent_name=agent_name,
        agent_version=agent_version,
    )

    metas = []
    failed_runs = 0
    for session_id, (harness, lines) in transcripts.items():
        # Enrich with credits, harness, layer hash, and report version scope from session_stats_agg
        stats = session_stats.get(session_id, {})
        harness = harness or stats.get("harness", "")
        meta = extract_session_meta(session_id, lines, harness)
        if meta["user_message_count"] and not meta["assistant_message_count"]:
            # Prompts that never got a response: the run failed before reaching the
            # model (missing credentials, a crash), so it says nothing about the agent.
            # Formats the extractor cannot read count neither and are kept.
            failed_runs += 1
            continue
        meta["credits"] = stats.get("credits", 0.0)
        meta["harness"] = harness
        meta["layer_hash"] = stats.get("layer_hash", "")
        meta["agent_version"] = agent_version or ""
        metas.append(meta)

    logger.info(
        "extracted_session_metas",
        total=len(transcripts),
        substantive=len(metas),
        failed_runs=failed_runs,
    )
    return metas


def aggregate_metas(metas: list[dict]) -> dict:
    """Aggregate per-session metadata into a summary (like pi's aggregateData).

    Returns a single dict with totals, distributions, and top-N lists.
    """
    agg: dict = {
        "total_sessions": len(metas),
        "total_messages": 0,
        "total_duration_hours": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_read_tokens": 0,
        "total_cache_write_tokens": 0,
        "total_cost": 0.0,
        "total_credits": 0.0,
        "total_lines_added": 0,
        "total_lines_removed": 0,
        "total_files_modified": 0,
        "git_commits": 0,
        "git_pushes": 0,
        "total_tool_errors": 0,
        "total_interruptions": 0,
        "sessions_using_subagent": 0,
        "sessions_using_mcp": 0,
        "tool_counts": {},
        "languages": {},
        "tool_error_categories": {},
        "projects": {},
        "user_response_times": [],
        "message_hours": [],
        "days_active": set(),
        "harnesses": set(),
        "sessions_with_tokens": 0,
        "sessions_with_credits": 0,
        "model_usage": {},  # {model: {input_tokens, output_tokens, cost, messages, sessions}}
    }

    for meta in metas:
        agg["total_messages"] += meta["total_messages"]
        agg["total_duration_hours"] += meta["duration_seconds"] / 3600
        agg["total_input_tokens"] += meta["input_tokens"]
        agg["total_output_tokens"] += meta["output_tokens"]
        agg["total_cache_read_tokens"] += meta["cache_read_tokens"]
        agg["total_cache_write_tokens"] += meta["cache_write_tokens"]
        agg["total_cost"] += meta["total_cost"]
        agg["total_credits"] += meta.get("credits", 0.0)
        if meta.get("input_tokens", 0) > 0 or meta.get("output_tokens", 0) > 0:
            agg["sessions_with_tokens"] += 1
        if meta.get("credits", 0) > 0:
            agg["sessions_with_credits"] += 1
        if meta.get("harness"):
            agg["harnesses"].add(meta["harness"])
        agg["total_lines_added"] += meta["lines_added"]
        agg["total_lines_removed"] += meta["lines_removed"]
        agg["total_files_modified"] += meta["files_modified"]
        agg["git_commits"] += meta["git_commits"]
        agg["git_pushes"] += meta["git_pushes"]
        agg["total_tool_errors"] += meta["tool_errors"]
        agg["total_interruptions"] += meta["user_interruptions"]

        if meta["uses_subagent"]:
            agg["sessions_using_subagent"] += 1
        if meta["uses_mcp"]:
            agg["sessions_using_mcp"] += 1

        # Merge tool counts
        for tool, count in meta["tool_counts"].items():
            agg["tool_counts"][tool] = agg["tool_counts"].get(tool, 0) + count

        # Merge languages
        for lang, count in meta["languages"].items():
            agg["languages"][lang] = agg["languages"].get(lang, 0) + count

        # Merge model usage
        for model, usage in meta.get("model_usage", {}).items():
            if model not in agg["model_usage"]:
                agg["model_usage"][model] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": 0.0,
                    "messages": 0,
                    "sessions": 0,
                }
            agg["model_usage"][model]["input_tokens"] += usage.get("input_tokens", 0)
            agg["model_usage"][model]["output_tokens"] += usage.get("output_tokens", 0)
            agg["model_usage"][model]["cost"] += usage.get("cost", 0.0)
            agg["model_usage"][model]["messages"] += usage.get("messages", 0)
            agg["model_usage"][model]["sessions"] += 1

        # Merge tool error categories
        for cat, count in meta["tool_error_categories"].items():
            agg["tool_error_categories"][cat] = agg["tool_error_categories"].get(cat, 0) + count

        # Projects
        if meta["project_path"]:
            proj = meta["project_path"].rsplit("/", 1)[-1] or meta["project_path"]
            agg["projects"][proj] = agg["projects"].get(proj, 0) + 1

        # Response times
        agg["user_response_times"].extend(meta["user_response_times"])

        # Message hours
        agg["message_hours"].extend(meta["message_hours"])

        # Days active
        if meta["start_time"]:
            agg["days_active"].add(meta["start_time"][:10])

    # Finalize
    agg["days_active"] = len(agg["days_active"])
    agg["harnesses"] = sorted(agg["harnesses"])

    # Sort tool counts by frequency
    agg["top_tools"] = sorted(agg["tool_counts"].items(), key=lambda x: -x[1])[:15]
    agg["top_languages"] = sorted(agg["languages"].items(), key=lambda x: -x[1])[:10]

    # ── Model tier classification ─────────────────────────────────────────
    # Classify models by observed cost-per-token. Models with significant
    # usage but near-zero cost are "subscription" (credit-based plans).
    model_tiers: dict[str, str] = {}
    model_cpt: dict[str, float] = {}  # cost per 1k tokens

    for model, usage in agg["model_usage"].items():
        total_tok = usage["input_tokens"] + usage["output_tokens"]
        if total_tok > 0:
            model_cpt[model] = (usage["cost"] / total_tok) * 1000

    cpt_values = sorted(v for v in model_cpt.values() if v > 0)
    cpt_median = cpt_values[len(cpt_values) // 2] if cpt_values else 0.01

    for model, usage in agg["model_usage"].items():
        total_tok = usage["input_tokens"] + usage["output_tokens"]
        cpt = model_cpt.get(model, 0)
        # Subscription: significant usage with near-zero cost
        if (total_tok > 10000 and usage["cost"] < 0.01) or (usage["messages"] > 20 and (not cpt or cpt < 0.001)):
            model_tiers[model] = "subscription"
        elif cpt > cpt_median * 3:
            model_tiers[model] = "high"
        elif cpt < cpt_median * 0.4:
            model_tiers[model] = "low"
        else:
            model_tiers[model] = "mid"
        # Annotate the usage dict with tier
        usage["tier"] = model_tiers[model]
        usage["cost_per_1k_tokens"] = round(cpt, 4) if cpt else 0

    agg["model_tiers"] = model_tiers

    return agg
