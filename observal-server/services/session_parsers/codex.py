# SPDX-FileCopyrightText: 2026 Tanvi Reddy <reddyplayer22@gmail.com>
# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Codex CLI JSONL session parser (READ path).

Normalizes raw ClickHouse rows into frontend-displayable events for the
trace viewer. Codex uses a distinct format:
  - {"type": "event_msg", "payload": {"type": "user_message|agent_message|..."}}
  - {"type": "response_item", "payload": {"role": "user|assistant", "content": [...]}}
  - {"type": "session_meta|turn_context"}
"""

from __future__ import annotations

import json
import re

from .base import (
    basic_event,
    dict_field,
    list_field,
    load_line,
    meta_assistant,
    meta_session,
    meta_time,
    meta_tool_call,
    meta_tool_result,
    meta_usage,
    meta_user,
    pick_timestamp,
    str_field,
    token_count,
    uncached_input_tokens,
)

# Codex records injected context (environment, AGENTS.md, shell output) as user
# messages too; these prefixes mark it. Everything else in a user message is typed.
_CONTEXT_PREFIXES = (
    "<environment_context>",
    "<user_instructions>",
    "<user_shell_command>",
    "<turn_aborted>",
    "# AGENTS.md instructions for ",
)


def user_prompt_text(payload: dict) -> str | None:
    """The text the user typed in a Codex ``response_item`` message, or None.

    Codex writes every user turn as a ``response_item`` message with role
    ``user``; some versions also echo it as an ``event_msg`` ``user_message``,
    which is therefore not counted, so each prompt is counted once.
    """
    if payload.get("role") != "user":
        return None
    texts = [
        str(block.get("text") or "")
        for block in list_field(payload, "content")
        if isinstance(block, dict) and block.get("type") == "input_text"
    ]
    typed = [text for text in texts if text.strip() and not text.lstrip().startswith(_CONTEXT_PREFIXES)]
    return "\n".join(typed) if typed else None


def parse_rows(rows: list[dict]) -> list[dict]:
    """Parse raw_line Codex JSONL rows into normalized frontend events."""
    events: list[dict] = []

    for row in rows:
        raw_line = row.get("raw_line", "")
        ingested_at = row.get("ingested_at", "")
        row_ts = row.get("timestamp", "")
        harness = row.get("harness", "codex")

        if not raw_line:
            events.append(basic_event(row))
            continue

        parsed = load_line(raw_line)
        if parsed is None:
            events.append(basic_event(row))
            continue

        ts = pick_timestamp(parsed.get("timestamp"), row_ts, ingested_at)
        line_type = parsed.get("type", "")

        if line_type == "event_msg":
            _handle_event_msg(parsed, ts, harness, events)
        elif line_type == "response_item":
            _handle_response_item(parsed, ts, harness, events)
        elif line_type in ("session_meta", "turn_context"):
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "system",
                    "body": line_type,
                    "attributes": {},
                    "service_name": harness,
                }
            )
        else:
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "system",
                    "body": line_type or "unknown",
                    "attributes": {},
                    "service_name": harness,
                }
            )

    return events


def _handle_event_msg(parsed: dict, ts: str, harness: str, events: list[dict]) -> None:
    payload = parsed.get("payload", {})
    if not isinstance(payload, dict):
        return
    payload_type = payload.get("type", "")

    if payload_type == "user_message":
        # Echo of a response_item user message, which carries the prompt.
        events.append(
            {
                "timestamp": ts,
                "event_name": "system",
                "body": "user_message",
                "attributes": {},
                "service_name": harness,
            }
        )
    elif payload_type == "agent_message":
        message = payload.get("message", "")
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_assistant_response",
                "body": str(message)[:200],
                "attributes": {"tool_response": str(message)},
                "service_name": harness,
            }
        )
    elif payload_type == "token_count":
        # Emit token usage as attributes for the detail view to sum
        info = dict_field(payload, "info")
        usage = dict_field(info, "last_token_usage") or dict_field(info, "total_token_usage")
        # Try to get model from rate_limits (Codex sometimes includes it)
        model = ""
        rate_limits = payload.get("rate_limits", {})
        if isinstance(rate_limits, dict):
            model = rate_limits.get("model", "")
        # Codex input_tokens includes cached_input_tokens (OpenAI convention).
        cached = token_count(usage, "cached_input_tokens")
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_token_usage",
                "body": "token_count",
                "attributes": {
                    "input_tokens": str(uncached_input_tokens(token_count(usage, "input_tokens"), cached)),
                    "output_tokens": str(token_count(usage, "output_tokens")),
                    "cache_read_tokens": str(cached),
                    "model": model,
                },
                "service_name": harness,
            }
        )
    elif payload_type in ("task_started", "task_complete"):
        events.append(
            {
                "timestamp": ts,
                "event_name": "system",
                "body": payload_type,
                "attributes": {},
                "service_name": harness,
            }
        )
    else:
        events.append(
            {
                "timestamp": ts,
                "event_name": "system",
                "body": payload_type,
                "attributes": {},
                "service_name": harness,
            }
        )


def _handle_response_item(parsed: dict, ts: str, harness: str, events: list[dict]) -> None:
    payload = parsed.get("payload", {})
    if not isinstance(payload, dict):
        return
    role = payload.get("role", "")
    content = list_field(payload, "content")
    payload_type = payload.get("type", "")

    # Direct function_call / function_call_output at payload level. custom_tool_call
    # is Codex's freeform-input tool (apply_patch, exec scripts): "input", not "arguments".
    if payload_type in ("function_call", "custom_tool_call"):
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_pretooluse",
                "body": payload.get("name", ""),
                "attributes": {
                    "tool_name": payload.get("name", ""),
                    "tool_input": payload.get("arguments") or payload.get("input") or "",
                    "tool_use_id": payload.get("call_id", ""),
                },
                "service_name": harness,
            }
        )
        return

    if payload_type in ("function_call_output", "custom_tool_call_output"):
        output = payload.get("output", "")
        if isinstance(output, list):
            output = "\n".join(str(block.get("text", "")) for block in output if isinstance(block, dict))
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_posttooluse",
                "body": payload.get("name", "tool_result"),
                "attributes": {
                    "tool_name": payload.get("name", ""),
                    "tool_response": str(output)[:500],
                    "tool_use_id": payload.get("call_id", ""),
                    "success": "true",
                },
                "service_name": harness,
            }
        )
        return

    prompt = user_prompt_text(payload)
    if prompt is not None:
        events.append(
            {
                "timestamp": ts,
                "event_name": "hook_userpromptsubmit",
                "body": prompt[:200],
                "attributes": {"tool_input": prompt},
                "service_name": harness,
            }
        )
    elif role == "user":
        # Injected context (AGENTS.md, environment) - system, not real user input
        events.append(
            {
                "timestamp": ts,
                "event_name": "system",
                "body": "context",
                "attributes": {},
                "service_name": harness,
            }
        )
    elif role == "assistant":
        # Check for tool calls vs text response
        text_parts = []
        tool_calls = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "output_text":
                    text_parts.append(str_field(block, "text"))
                elif btype == "function_call":
                    tool_calls.append(block)
                elif btype == "function_call_output":
                    events.append(
                        {
                            "timestamp": ts,
                            "event_name": "tool_result",
                            "body": block.get("name", "tool_result"),
                            "attributes": {
                                "tool_name": block.get("name", ""),
                                "tool_response": str(block.get("output", ""))[:500],
                            },
                            "service_name": harness,
                        }
                    )

        for tc in tool_calls:
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "tool_call",
                    "body": tc.get("name", ""),
                    "attributes": {
                        "tool_name": tc.get("name", ""),
                        "tool_input": json.dumps(tc.get("arguments", {})),
                        "tool_use_id": tc.get("call_id", ""),
                    },
                    "service_name": harness,
                }
            )

        if text_parts and not tool_calls:
            body = " ".join(text_parts)[:200]
            events.append(
                {
                    "timestamp": ts,
                    "event_name": "hook_assistant_response",
                    "body": body,
                    "attributes": {"tool_response": " ".join(text_parts)},
                    "service_name": harness,
                }
            )
    elif role == "developer":
        events.append(
            {
                "timestamp": ts,
                "event_name": "system",
                "body": "developer instructions",
                "attributes": {},
                "service_name": harness,
            }
        )
    else:
        events.append(
            {
                "timestamp": ts,
                "event_name": "system",
                "body": role or "response_item",
                "attributes": {},
                "service_name": harness,
            }
        )


# ---------------------------------------------------------------------------
# Insight meta records
# ---------------------------------------------------------------------------

# Codex tools that run shell commands; their command text is measured like Pi's bash.
_SHELL_TOOLS = frozenset({"shell", "local_shell", "exec", "exec_command", "unified_exec", "container.exec"})
_SUBAGENT_TOOLS = frozenset({"spawn_agent"})
_PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")
_EXIT_CODE = re.compile(r'(?:"exit_code"\s*:\s*|Exit code:?\s*|exited with code\s*)(-?\d+)')


def _tool_text(payload: dict) -> str:
    """The tool call's input: Codex sends ``arguments`` as JSON text, or ``input`` as free text."""
    value = payload.get("arguments") or payload.get("input") or ""
    return value if isinstance(value, str) else json.dumps(value)


def _shell_command(text: str) -> str:
    try:
        args = json.loads(text)
    except ValueError:
        return text
    if not isinstance(args, dict):
        return text
    command = args.get("cmd") or args.get("command") or ""
    return " ".join(str(part) for part in command) if isinstance(command, list) else str(command)


def _patch_edits(text: str) -> list[dict]:
    """One edit per file in an ``apply_patch`` envelope, holding its removed and added lines."""
    if "*** Begin Patch" not in text:
        return []
    if "*** Begin Patch\\n" in text:
        text = text.replace("\\n", "\n")  # the patch is a quoted string inside an exec script
    edits: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        match = _PATCH_FILE.match(line.strip())
        if match:
            current = {"path": match.group(2).strip(), "old": [], "new": []}
            edits.append(current)
        elif current is not None and not line.startswith(("***", "@@")):
            if line.startswith("+"):
                current["new"].append(line[1:])
            elif line.startswith("-"):
                current["old"].append(line[1:])
    return [{"path": e["path"], "oldText": "\n".join(e["old"]), "newText": "\n".join(e["new"])} for e in edits]


def _meta_tool_call(payload: dict) -> dict:
    name = str_field(payload, "name")
    text = _tool_text(payload)
    edits = _patch_edits(text)
    if edits:
        return meta_tool_call(name, str_field(payload, "call_id"), {"path": edits[0]["path"], "edits": edits}, "edit")
    if name in _SHELL_TOOLS:
        return meta_tool_call(name, str_field(payload, "call_id"), {"command": _shell_command(text)}, "bash")
    if name in _SUBAGENT_TOOLS:
        return meta_tool_call(name, str_field(payload, "call_id"), {}, "subagent")
    try:
        args = json.loads(text) if text else {}
    except ValueError:
        args = {}
    return meta_tool_call(name, str_field(payload, "call_id"), args if isinstance(args, dict) else {})


def _output_text(payload: dict) -> str:
    output = payload.get("output", "")
    if isinstance(output, list):
        return "\n".join(str(block.get("text", "")) for block in output if isinstance(block, dict))
    return str(output)


def meta_records(entries: list[dict]) -> list[dict]:
    """Convert Codex rollout lines into insight meta records.

    A model response is the run of output items (reasoning, messages, tool calls)
    between two inputs (a user turn or a tool output); it counts as one assistant
    message. Token usage comes from ``token_count`` events as the change in the
    running total, so repeated events are not counted twice.
    """
    records: list[dict] = []
    tool_names: dict[str, str] = {}
    model = ""
    response: dict | None = None  # the open model response, closed by the next input
    last_response: dict | None = None
    last_total: dict = {}

    for entry in entries:
        ts = str_field(entry, "timestamp")
        payload = dict_field(entry, "payload")
        line_type = str_field(entry, "type")
        payload_type = str_field(payload, "type")

        if line_type == "session_meta":
            records.append(meta_session(str_field(payload, "cwd"), ts))
            continue
        if line_type == "turn_context":
            model = str_field(payload, "model") or model
        elif line_type == "event_msg" and payload_type == "thread_settings_applied":
            model = str_field(dict_field(payload, "thread_settings"), "model") or model
        elif line_type == "event_msg" and payload_type == "token_count":
            total = dict_field(dict_field(payload, "info"), "total_token_usage")
            if total and last_response is not None:
                delta = {key: token_count(total, key) - token_count(last_total, key) for key in total}
                cached = max(delta.get("cached_input_tokens", 0), 0)
                written = max(delta.get("cache_write_input_tokens", 0), 0)
                previous = last_response["message"].get("usage") or meta_usage(0, 0)
                last_response["message"]["usage"] = meta_usage(
                    previous["input"] + uncached_input_tokens(max(delta.get("input_tokens", 0), 0), cached, written),
                    previous["output"] + max(delta.get("output_tokens", 0), 0),
                    previous["cacheRead"] + cached,
                    previous["cacheWrite"] + written,
                )
            if total:
                last_total = total
        elif line_type == "response_item":
            if payload_type in ("function_call", "custom_tool_call"):
                if response is None:
                    response = last_response = meta_assistant(ts, model)
                    records.append(response)
                tool_names[str_field(payload, "call_id")] = str_field(payload, "name")
                response["message"]["content"].append(_meta_tool_call(payload))
                continue
            if payload_type in ("function_call_output", "custom_tool_call_output"):
                text = _output_text(payload)
                codes = [int(code) for code in _EXIT_CODE.findall(text)]
                name = tool_names.get(str_field(payload, "call_id"), "")
                records.append(meta_tool_result(ts, name, any(code != 0 for code in codes), text))
                response = None
                continue
            prompt = user_prompt_text(payload)
            if prompt is not None:
                records.append(meta_user(ts, prompt))
                response = None
                continue
            if payload_type in ("message", "reasoning") and payload.get("role") != "user":
                if payload.get("role") in ("assistant", None) and response is None:
                    response = last_response = meta_assistant(ts, model)
                    records.append(response)
                if response is not None:
                    continue
        if ts:
            records.append(meta_time(ts))
    return records
