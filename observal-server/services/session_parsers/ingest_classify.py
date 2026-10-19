# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Per-harness JSONL line classifiers for the ingest pipeline.

Each harness has its own JSONL format.  This module provides:

  classify(harness, parsed)        -> event_type str | None (None = skip line)
  extract_preview(harness, parsed, event_type) -> str
  extract_tool_info(harness, parsed) -> (tool_name, tool_id)

Dispatch is **strict**: passing an unknown harness raises ``KeyError`` so new
harnesses cannot silently fall through to a wrong classifier.  When adding
support for a new harness:

  1. Add its entry to ``observal_shared/harness_registry.py`` with a ``"session_parser"``
     key referencing one of the parser IDs registered in ``_CLASSIFIERS`` below.
  2. If the harness has a novel JSONL format, implement ``_classify_<name>``,
     ``_preview_<name>``, and ``_tool_info_<name>`` and register them.
  3. If the harness uses an existing format (e.g. Claude Code), point its
     ``"session_parser"`` at the existing parser ID.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from .base import dict_field, list_field, str_field, strip_cursor_xml_tags
from .codex import user_prompt_text as _codex_user_prompt_text

_PREVIEW_MAX = 500


# ---------------------------------------------------------------------------
# Claude Code classifier
# ---------------------------------------------------------------------------

_META_TYPES = frozenset({"summary", "completion_start", "tool_use_result", "tool_use_delta"})

_ATTACHMENT_TYPE_MAP: dict[str, str] = {
    "file": "attachment_file",
    "url": "attachment_url",
    "image": "attachment_image",
    "directory": "attachment_dir",
    "project_knowledge": "attachment_knowledge",
}


def _classify_claude_code(parsed: dict) -> str | None:
    """Classify one Claude Code JSONL line.

    Returns the event_type string, or None to skip the line entirely
    (empty continuation signals).
    """
    line_type = str_field(parsed, "type")

    if line_type in _META_TYPES:
        return "meta"

    if line_type == "system":
        return "system"

    if line_type == "user":
        content = dict_field(parsed, "message").get("content", [])
        if not content:
            return None  # empty continuation signal
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    return "tool_result"
            return "user_prompt"
        return "user_prompt"

    if line_type == "assistant":
        content = dict_field(parsed, "message").get("content", [])
        if isinstance(content, list) and content:
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "thinking":
                        return "thinking"
                    if btype == "tool_use":
                        return "tool_call"
                    if btype == "text":
                        return "assistant_text"
        return "assistant_text"

    if line_type == "attachment":
        attachment = parsed.get("attachment")
        attachment_type = str_field(attachment, "type") if isinstance(attachment, dict) else ""
        return _ATTACHMENT_TYPE_MAP.get(attachment_type, "system")

    # Unknown type -- store as system so nothing is silently dropped
    return "system"


def _preview_claude_code(parsed: dict, event_type: str) -> str:
    try:
        line_type = parsed.get("type", "")
        if line_type in ("user", "assistant"):
            content = parsed.get("message", {}).get("content", [])
            if isinstance(content, str):
                return content[:_PREVIEW_MAX]
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype == "text":
                        parts.append(block.get("text", "")[:_PREVIEW_MAX])
                    elif btype == "thinking":
                        parts.append(block.get("thinking", "")[:_PREVIEW_MAX])
                    elif btype == "tool_use":
                        parts.append(f"[tool_use: {block.get('name', '')}]")
                    elif btype == "tool_result":
                        inner = block.get("content", "")
                        if isinstance(inner, list):
                            for item in inner:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    parts.append(item.get("text", "")[:_PREVIEW_MAX])
                        elif isinstance(inner, str):
                            parts.append(inner[:_PREVIEW_MAX])
                return " ".join(parts)[:_PREVIEW_MAX]
        if line_type == "attachment":
            attachment = parsed.get("attachment", {})
            name = attachment.get("name") or attachment.get("type", "")
            return f"[attachment: {name}]"[:_PREVIEW_MAX]
        if line_type == "system":
            return parsed.get("content", "")[:_PREVIEW_MAX]
    except Exception:
        pass
    return ""


def _tool_info_claude_code(parsed: dict) -> tuple[str | None, str | None]:
    content = dict_field(parsed, "message").get("content", [])
    if not isinstance(content, list):
        return None, None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return block.get("name"), block.get("id")
    return None, None


# ---------------------------------------------------------------------------
# Kiro classifier
# ---------------------------------------------------------------------------


# Kiro IDE transcript payload types that carry no analysable content.
_KIRO_IDE_META_TYPES = frozenset(
    {
        "session_start",
        "session_event",
        "session_metadata",
        "turn_start",
        "turn_end",
        "usage_summary",
        "sub_agent_start",
        "sub_agent_complete",
        "pending_interaction",
        "interaction_resolved",
        "ContextualHookInvoked",
    }
)


def _kiro_ide_payload(parsed: dict) -> dict | None:
    """Return the IDE record's payload, or None when the line is CLI-shaped.

    The IDE envelopes every record as ``{id, timestamp, payload}`` and puts the
    discriminator on ``payload.type``; the CLI writes a flat record keyed by
    ``kind``. Both arrive on the ``kiro`` harness, so the shape decides.
    """
    payload = parsed.get("payload")
    if isinstance(payload, dict) and payload.get("type"):
        return payload
    return None


def _classify_kiro_ide(payload: dict) -> str | None:
    """Classify one Kiro IDE transcript payload."""
    ptype = str_field(payload, "type")

    if ptype == "user":
        # Skip empty continuation prompts, matching the CLI classifier.
        return "user_prompt" if str_field(payload, "content").strip() else None

    if ptype == "assistant":
        # Reasoning is the model's internal trace, not user-visible output.
        return "thinking" if str_field(payload, "operationType") == "Reasoning" else "assistant_text"

    if ptype == "tool_call":
        return "tool_call"

    if ptype == "tool_result":
        return "tool_result"

    if ptype in _KIRO_IDE_META_TYPES:
        return "meta"

    # Unknown IDE payload -- store as system so nothing is silently dropped.
    return "system"


def _classify_kiro(parsed: dict) -> str | None:
    """Classify one Kiro JSONL line, in either the CLI or IDE layout.

    CLI records use ``kind`` (not ``type``) with values:
      Prompt           -> user prompt
      AssistantMessage -> assistant text or tool call
      ToolResults      -> tool result

    IDE records are enveloped and dispatch on ``payload.type`` instead.
    """
    ide = _kiro_ide_payload(parsed)
    if ide is not None:
        return _classify_kiro_ide(ide)

    kind = str_field(parsed, "kind")

    if kind == "Prompt":
        data = dict_field(parsed, "data")
        content = list_field(data, "content")
        # Skip empty prompts
        if not any(
            isinstance(item, dict) and item.get("kind") == "text" and str_field(item, "data").strip()
            for item in content
        ):
            return None
        return "user_prompt"

    if kind == "AssistantMessage":
        data = dict_field(parsed, "data")
        content = list_field(data, "content")
        for item in content:
            if isinstance(item, dict) and item.get("kind") == "toolUse":
                return "tool_call"
        return "assistant_text"

    if kind == "ToolResults":
        return "tool_result"

    # Unknown Kiro kind -- store as system
    return "system"


def _preview_kiro(parsed: dict, event_type: str) -> str:
    ide = _kiro_ide_payload(parsed)
    if ide is not None:
        if ide.get("type") == "tool_call":
            return f"[tool_use: {str_field(ide, 'toolName')}]"[:_PREVIEW_MAX]
        return str_field(ide, "content")[:_PREVIEW_MAX]
    try:
        kind = parsed.get("kind", "")
        data = parsed.get("data", {}) if isinstance(parsed.get("data"), dict) else {}
        content = data.get("content", [])

        if kind == "Prompt":
            parts = [
                item.get("data", "")
                for item in content
                if isinstance(item, dict) and item.get("kind") == "text" and isinstance(item.get("data"), str)
            ]
            return " ".join(parts)[:_PREVIEW_MAX]

        if kind == "AssistantMessage":
            parts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("kind") == "text":
                    parts.append(str(item.get("data", ""))[:_PREVIEW_MAX])
                elif item.get("kind") == "toolUse":
                    name = item.get("data", {}).get("name", "") if isinstance(item.get("data"), dict) else ""
                    parts.append(f"[tool_use: {name}]")
            return " ".join(parts)[:_PREVIEW_MAX]

        if kind == "ToolResults":
            parts = []
            for item in content:
                if not isinstance(item, dict) or item.get("kind") != "toolResult":
                    continue
                item_data = item.get("data", {})
                if not isinstance(item_data, dict):
                    continue
                for rc in item_data.get("content", []):
                    if isinstance(rc, dict) and rc.get("kind") == "text":
                        parts.append(str(rc.get("data", ""))[:_PREVIEW_MAX])
            return " ".join(parts)[:_PREVIEW_MAX]
    except Exception:
        pass
    return ""


def _tool_info_kiro(parsed: dict) -> tuple[str | None, str | None]:
    ide = _kiro_ide_payload(parsed)
    if ide is not None:
        if ide.get("type") in ("tool_call", "tool_result"):
            return ide.get("toolName"), ide.get("toolCallId")
        return None, None
    kind = str_field(parsed, "kind")
    content = list_field(dict_field(parsed, "data"), "content")
    if kind == "AssistantMessage":
        for item in content:
            if isinstance(item, dict) and item.get("kind") == "toolUse":
                item_data = item.get("data", {})
                if isinstance(item_data, dict):
                    return item_data.get("name"), item_data.get("toolUseId")
    return None, None


# ---------------------------------------------------------------------------
# Cursor classifier
# ---------------------------------------------------------------------------


def _classify_cursor(parsed: dict) -> str | None:
    """Classify one Cursor JSONL line.

    Cursor uses ``role`` (not ``type``) with values matching Claude Code's
    content block structure.  The ``message.content`` array contains blocks
    with ``type: text|tool_use|tool_result|thinking``.
    """
    role = str_field(parsed, "role")

    if role == "user":
        content = dict_field(parsed, "message").get("content", [])
        if not content:
            return None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    return "tool_result"
            return "user_prompt"
        return "user_prompt"

    if role == "assistant":
        content = dict_field(parsed, "message").get("content", [])
        if isinstance(content, list) and content:
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "thinking":
                        return "thinking"
                    if btype == "tool_use":
                        return "tool_call"
                    if btype == "text":
                        return "assistant_text"
        return "assistant_text"

    if not role:
        return None

    return "system"


def _preview_cursor(parsed: dict, event_type: str) -> str:
    """Extract preview from Cursor JSONL (same content structure as Claude Code)."""
    try:
        content = parsed.get("message", {}).get("content", [])
        if isinstance(content, str):
            return strip_cursor_xml_tags(content)[:_PREVIEW_MAX]
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    text = block.get("text", "")
                    if event_type == "user_prompt":
                        text = strip_cursor_xml_tags(text)
                    parts.append(text[:_PREVIEW_MAX])
                elif btype == "thinking":
                    parts.append(block.get("thinking", "")[:_PREVIEW_MAX])
                elif btype == "tool_use":
                    parts.append(f"[tool_use: {block.get('name', '')}]")
                elif btype == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        for item in inner:
                            if isinstance(item, dict) and item.get("type") == "text":
                                parts.append(item.get("text", "")[:_PREVIEW_MAX])
                    elif isinstance(inner, str):
                        parts.append(inner[:_PREVIEW_MAX])
            return " ".join(parts)[:_PREVIEW_MAX]
    except Exception:
        pass
    return ""


def _tool_info_cursor(parsed: dict) -> tuple[str | None, str | None]:
    content = dict_field(parsed, "message").get("content", [])
    if not isinstance(content, list):
        return None, None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return block.get("name"), block.get("id")
    return None, None


# ---------------------------------------------------------------------------
# Pi classifier
# ---------------------------------------------------------------------------

_PI_SKIP_TYPES = frozenset({"session", "label", "session_info", "custom"})


def _classify_pi(parsed: dict) -> str | None:
    """Classify one Pi JSONL line.

    Pi uses ``type: "toolCall"`` (not ``tool_use``) in content blocks, and
    ``message.role: "toolResult"`` as a top-level message role (not a content
    block inside user messages like Claude Code).
    """
    line_type = str_field(parsed, "type")

    if line_type in _PI_SKIP_TYPES:
        return None

    if line_type in ("model_change", "thinking_level_change", "custom_message"):
        return "system"

    if line_type in ("compaction", "branch_summary"):
        return "meta"

    if line_type == "message":
        msg = dict_field(parsed, "message")
        role = msg.get("role", "")

        if role == "user":
            content = msg.get("content", [])
            if not content:
                return None
            return "user_prompt"

        if role == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type", "")
                        if btype == "thinking":
                            return "thinking"
                        if btype == "toolCall":
                            return "tool_call"
                        if btype == "text":
                            return "assistant_text"
            return "assistant_text"

        if role == "toolResult":
            return "tool_result"

        if role == "bashExecution":
            return "tool_result"

        if role in ("custom", "branchSummary", "compactionSummary"):
            return "system"

    # Unknown type -- store as system so nothing is silently dropped
    return "system"


def _preview_pi(parsed: dict, event_type: str) -> str:
    """Extract preview text from a Pi JSONL line."""
    try:
        line_type = parsed.get("type", "")

        if line_type == "message":
            msg = parsed.get("message", {})
            role = msg.get("role", "")
            content = msg.get("content", [])

            if role in ("user", "assistant"):
                if isinstance(content, str):
                    return content[:_PREVIEW_MAX]
                if isinstance(content, list):
                    parts: list[str] = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type", "")
                        if btype == "text":
                            parts.append(block.get("text", "")[:_PREVIEW_MAX])
                        elif btype == "thinking":
                            parts.append(block.get("thinking", "")[:_PREVIEW_MAX])
                        elif btype == "toolCall":
                            parts.append(f"[tool_call: {block.get('name', '')}]")
                    return " ".join(parts)[:_PREVIEW_MAX]

            if role == "toolResult":
                tool_name = msg.get("toolName", "")
                inner = msg.get("content", [])
                if isinstance(inner, list):
                    for item in inner:
                        if isinstance(item, dict) and item.get("type") == "text":
                            return f"[{tool_name}] {item.get('text', '')}"[:_PREVIEW_MAX]
                return f"[{tool_name}]"[:_PREVIEW_MAX]

            if role == "bashExecution":
                return f"$ {msg.get('command', '')}"[:_PREVIEW_MAX]

        if line_type == "model_change":
            return f"Model: {parsed.get('provider', '')}/{parsed.get('modelId', '')}"[:_PREVIEW_MAX]

        if line_type == "thinking_level_change":
            return f"Thinking: {parsed.get('thinkingLevel', '')}"[:_PREVIEW_MAX]

        if line_type in ("compaction", "branch_summary"):
            return parsed.get("summary", "")[:_PREVIEW_MAX]

        if line_type == "custom_message":
            c = parsed.get("content", "")
            if isinstance(c, str):
                return c[:_PREVIEW_MAX]

    except Exception:
        pass
    return ""


def _tool_info_pi(parsed: dict) -> tuple[str | None, str | None]:
    """Extract (tool_name, tool_id) from a Pi JSONL line."""
    if parsed.get("type") != "message":
        return None, None
    msg = dict_field(parsed, "message")
    role = msg.get("role", "")

    if role == "assistant":
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "toolCall":
                    return block.get("name"), block.get("id")

    if role == "toolResult":
        return msg.get("toolName"), msg.get("toolCallId")

    return None, None


# ---------------------------------------------------------------------------
# Codex classifier
# ---------------------------------------------------------------------------


def _classify_codex(parsed: dict) -> str | None:
    """Classify one Codex CLI JSONL line."""
    line_type = str_field(parsed, "type")

    if line_type == "event_msg":
        payload_type = str_field(dict_field(parsed, "payload"), "type")
        if payload_type == "agent_message":
            return "assistant_text"
        if payload_type == "token_count":
            return "meta"
        if payload_type in ("task_started", "task_complete"):
            return "system"
        return "system"

    if line_type == "response_item":
        payload = dict_field(parsed, "payload")
        role = payload.get("role", "")
        content = payload.get("content", [])
        payload_type = payload.get("type", "")

        if payload_type == "function_call":
            return "tool_call"
        if payload_type == "function_call_output":
            return "tool_result"

        if role == "user":
            return "user_prompt" if _codex_user_prompt_text(payload) is not None else "system"
        if role == "assistant":
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type", "")
                        if btype == "function_call":
                            return "tool_call"
                        if btype == "function_call_output":
                            return "tool_result"
            return "assistant_text"
        if role == "developer":
            return "system"
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "function_call_output":
                    return "tool_result"
        return "system"

    if line_type in ("session_meta", "turn_context"):
        return "system"

    return "system"


# ---------------------------------------------------------------------------
# Copilot CLI classifier
# ---------------------------------------------------------------------------


def _classify_copilot_cli(parsed: dict) -> str | None:
    """Classify one Copilot CLI JSONL line."""
    event = parsed.get("event")
    event_type = str_field(event, "type") if isinstance(event, dict) else str_field(parsed, "type")

    if not event_type:
        return None

    if event_type == "user.message":
        return "user_prompt"
    if event_type == "assistant.message":
        return "assistant_text"
    if event_type == "assistant.message_delta":
        return None
    if event_type == "tool.call":
        return "tool_call"
    if event_type in ("tool.result", "tool.execution_complete"):
        return "tool_result"
    if event_type == "agent.thinking":
        return "thinking"
    if event_type == "assistant.usage":
        return "usage"
    if event_type in ("session.start", "session.end"):
        return "system"

    return "system"


def _preview_codex(parsed: dict, event_type: str) -> str:
    """Extract preview from Codex CLI JSONL."""
    try:
        line_type = parsed.get("type", "")

        if line_type == "event_msg":
            payload = parsed.get("payload", {})
            payload_type = payload.get("type", "")
            if payload_type == "user_message":
                return str(payload.get("message", ""))[:_PREVIEW_MAX]
            if payload_type == "agent_message":
                return str(payload.get("message", ""))[:_PREVIEW_MAX]
            return ""

        if line_type == "response_item":
            payload = parsed.get("payload", {})
            # A typed prompt's preview is what was typed, not the context blocks sent with it.
            typed = _codex_user_prompt_text(payload)
            if typed is not None:
                return typed[:_PREVIEW_MAX]
            content = payload.get("content", [])
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype in ("input_text", "output_text"):
                        parts.append(block.get("text", "")[:_PREVIEW_MAX])
                    elif btype == "function_call":
                        parts.append(f"[tool_call: {block.get('name', '')}]")
                    elif btype == "function_call_output":
                        output = block.get("output", "")
                        parts.append(str(output)[:_PREVIEW_MAX])
                return " ".join(parts)[:_PREVIEW_MAX]
    except Exception:
        pass
    return ""


def _preview_copilot_cli(parsed: dict, event_type: str) -> str:
    """Extract preview from Copilot CLI JSONL."""
    try:
        event = parsed.get("event")
        if isinstance(event, dict):
            data = {k: v for k, v in event.items() if k != "type"}
            etype = event.get("type", "")
        else:
            data = parsed.get("data", {})
            if not isinstance(data, dict):
                return ""
            etype = parsed.get("type", "")

        if etype == "user.message":
            content = data.get("content", "")
            if isinstance(content, dict):
                content = content.get("text", "")
            return str(content)[:_PREVIEW_MAX]

        if etype == "assistant.message":
            content = data.get("content", "")
            if isinstance(content, dict):
                content = content.get("text", "")
            return str(content)[:_PREVIEW_MAX]

        if etype == "tool.call":
            name = data.get("name", data.get("toolName", ""))
            return f"[tool_call: {name}]"[:_PREVIEW_MAX]

        if etype in ("tool.result", "tool.execution_complete"):
            output = data.get("output", data.get("result", ""))
            if isinstance(output, dict):
                output = output.get("textResultForLlm", output.get("text", ""))
            return str(output)[:_PREVIEW_MAX]

        if etype == "agent.thinking":
            content = data.get("content", data.get("thinking", ""))
            return str(content)[:_PREVIEW_MAX]

        if etype == "session.start":
            context = data.get("context", {})
            cwd = context.get("cwd", "") if isinstance(context, dict) else ""
            return f"session start (cwd: {cwd})"[:_PREVIEW_MAX] if cwd else "session start"

    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Antigravity CLI
# ---------------------------------------------------------------------------

_ANTIGRAVITY_SKIP_TYPES = {"CONVERSATION_HISTORY", "SYSTEM_PROMPT"}
_ANTIGRAVITY_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def _classify_antigravity(parsed: dict) -> str | None:
    source = str_field(parsed, "source")
    line_type = str_field(parsed, "type")

    if line_type in _ANTIGRAVITY_SKIP_TYPES:
        return None
    if source == "USER_EXPLICIT" and line_type == "USER_INPUT":
        return "user_prompt"
    if source == "MODEL" and line_type == "PLANNER_RESPONSE":
        return "tool_call" if parsed.get("tool_calls", []) else "assistant_text"
    if source == "MODEL" and line_type not in ("PLANNER_RESPONSE", "USER_INPUT"):
        return "tool_result"
    if source == "SYSTEM":
        return "system"
    return None


def _preview_antigravity(parsed: dict, event_type: str) -> str:
    try:
        content = parsed.get("content", "")
        if not content:
            return ""
        if event_type == "user_prompt":
            m = _ANTIGRAVITY_USER_REQUEST_RE.search(content)
            return (m.group(1).strip() if m else content)[:_PREVIEW_MAX]
        if event_type == "assistant_text":
            return content[:_PREVIEW_MAX]
        if event_type == "tool_call":
            tool_calls = parsed.get("tool_calls", [])
            if tool_calls:
                names = [tc.get("name", "") for tc in tool_calls]
                return f"{content[:200]} [tools: {', '.join(names)}]"[:_PREVIEW_MAX]
            return content[:_PREVIEW_MAX]
        if event_type == "tool_result":
            line_type = parsed.get("type", "")
            return f"[{line_type}] {content[:200]}"[:_PREVIEW_MAX]
    except Exception:
        pass
    return ""


def _tool_info_codex(parsed: dict) -> tuple[str | None, str | None]:
    if parsed.get("type") != "response_item":
        return None, None
    payload = dict_field(parsed, "payload")
    if payload.get("type") == "function_call":
        return payload.get("name"), payload.get("call_id")
    content = payload.get("content", [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "function_call":
                return block.get("name"), block.get("call_id")
    return None, None


def _tool_info_copilot_cli(parsed: dict) -> tuple[str | None, str | None]:
    event = parsed.get("event")
    if isinstance(event, dict):
        if event.get("type", "") == "tool.call":
            data = {k: v for k, v in event.items() if k != "type"}
            return data.get("name", data.get("toolName")), parsed.get("agentId")
    else:
        if parsed.get("type", "") == "tool.call":
            data = parsed.get("data", {})
            if isinstance(data, dict):
                return data.get("name", data.get("toolName")), parsed.get("id")
    return None, None


def _tool_info_antigravity(parsed: dict) -> tuple[str | None, str | None]:
    tool_calls = parsed.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls and isinstance(tool_calls[0], dict):
        return tool_calls[0].get("name"), None
    source = str_field(parsed, "source")
    line_type = str_field(parsed, "type")
    if source == "MODEL" and line_type not in ("PLANNER_RESPONSE", "USER_INPUT"):
        return line_type.lower(), None
    return None, None


# ---------------------------------------------------------------------------
# Goose classifier
# ---------------------------------------------------------------------------

# goose content block tags, in the order they decide a line's event type.
_GOOSE_THINKING_BLOCKS = frozenset({"thinking", "redactedThinking"})
_GOOSE_REQUEST_BLOCKS = frozenset({"toolRequest", "frontendToolRequest", "toolConfirmationRequest"})


def _goose_block_types(parsed: dict) -> list[str]:
    content = parsed.get("content")
    if not isinstance(content, list):
        return []
    return [block.get("type", "") for block in content if isinstance(block, dict)]


def _classify_goose(parsed: dict) -> str | None:
    """Classify one line of a mirrored goose session.

    The mirror carries ``session`` / ``session_end`` boundaries plus one
    ``message`` record per row of goose's ``messages`` table.
    """
    record_type = parsed.get("type", "")
    if record_type in ("session", "session_end"):
        return "system"
    if record_type != "message":
        return "system"

    blocks = _goose_block_types(parsed)
    if not blocks:
        return None  # empty message row carries no signal
    # First significant block wins, as in _classify_claude_code.
    for block in blocks:
        if block in _GOOSE_THINKING_BLOCKS:
            return "thinking"
        if block in _GOOSE_REQUEST_BLOCKS:
            return "tool_call"
        if block == "toolResponse":
            return "tool_result"
    return "user_prompt" if parsed.get("role") == "user" else "assistant_text"


def _goose_tool_call_text(block: dict) -> str:
    call = block.get("toolCall") if isinstance(block.get("toolCall"), dict) else {}
    value = call.get("value") if isinstance(call.get("value"), dict) else {}
    name = value.get("name") or block.get("toolName") or ""
    return f"[tool_use: {name}]" if name else ""


def _goose_tool_result_text(block: dict) -> str:
    result = block.get("toolResult") if isinstance(block.get("toolResult"), dict) else {}
    if "error" in result:
        return str(result.get("error") or "")
    value = result.get("value")
    content = value.get("content") if isinstance(value, dict) else value
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return " ".join(
        str(item.get("text") or "") for item in content if isinstance(item, dict) and item.get("type") == "text"
    )


def _preview_goose(parsed: dict, event_type: str) -> str:
    try:
        record_type = parsed.get("type", "")
        if record_type == "session":
            return f"[session: {parsed.get('name') or parsed.get('session_id') or ''}]"[:_PREVIEW_MAX]
        if record_type == "session_end":
            return "[session end]"
        content = parsed.get("content")
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                parts.append(str(block.get("text") or ""))
            elif block_type == "thinking":
                parts.append(str(block.get("thinking") or ""))
            elif block_type == "redactedThinking":
                parts.append("[redacted thinking]")
            elif block_type in _GOOSE_REQUEST_BLOCKS:
                parts.append(_goose_tool_call_text(block))
            elif block_type == "toolResponse":
                parts.append(_goose_tool_result_text(block))
        return " ".join(part for part in parts if part)[:_PREVIEW_MAX]
    except Exception:
        return ""


def _tool_info_goose(parsed: dict) -> tuple[str | None, str | None]:
    content = parsed.get("content")
    if not isinstance(content, list):
        return None, None
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in _GOOSE_REQUEST_BLOCKS:
            continue
        call = block.get("toolCall") if isinstance(block.get("toolCall"), dict) else {}
        value = call.get("value") if isinstance(call.get("value"), dict) else {}
        return value.get("name") or block.get("toolName"), block.get("id")
    return None, None


# ---------------------------------------------------------------------------
# Registry  -- add new parsers here, update harness_registry.py session_parser key
# ---------------------------------------------------------------------------

_ClassifyFn = Callable[[dict], "str | None"]
_PreviewFn = Callable[[dict, str], str]
_ToolInfoFn = Callable[[dict], "tuple[str | None, str | None]"]
_Classifier = tuple[_ClassifyFn, _PreviewFn, _ToolInfoFn]

# Maps session_parser ID -> (classify_fn, preview_fn, tool_info_fn)
_CLASSIFIERS: dict[str, _Classifier] = {
    "claude-code": (_classify_claude_code, _preview_claude_code, _tool_info_claude_code),
    "codex": (_classify_codex, _preview_codex, _tool_info_codex),
    "copilot-cli": (_classify_copilot_cli, _preview_copilot_cli, _tool_info_copilot_cli),
    "kiro": (_classify_kiro, _preview_kiro, _tool_info_kiro),
    "cursor": (_classify_cursor, _preview_cursor, _tool_info_cursor),
    "goose": (_classify_goose, _preview_goose, _tool_info_goose),
    "opencode": (_classify_claude_code, _preview_claude_code, _tool_info_claude_code),
    "pi": (_classify_pi, _preview_pi, _tool_info_pi),
    "antigravity": (_classify_antigravity, _preview_antigravity, _tool_info_antigravity),
}


def get_classifier(harness: str) -> tuple:
    """Return (classify_fn, preview_fn, tool_info_fn) for the given harness.

    Looks up the ``session_parser`` key in ``harness_registry.HARNESS_REGISTRY`` and
    dispatches to the matching entry in ``_CLASSIFIERS``.

    Raises ``KeyError`` if the harness is not registered or has no session_parser,
    so new harnesses cannot silently fall through to a wrong classifier.
    Raises ``ValueError`` if the harness has session_parser=None (no parser configured).
    """
    from observal_shared.harness_registry import HARNESS_REGISTRY

    parser_id = HARNESS_REGISTRY[harness]["session_parser"]  # KeyError = unknown harness
    if parser_id is None:
        raise ValueError(f"No session parser configured for harness: {harness}")
    return _CLASSIFIERS[parser_id]  # KeyError = unimplemented parser


def classify(harness: str, parsed: dict) -> str | None:
    """Classify a single parsed JSONL line for the given harness."""
    classify_fn, _, _ = get_classifier(harness)
    return classify_fn(parsed)


def extract_preview(harness: str, parsed: dict, event_type: str) -> str:
    """Extract a short preview string for the given harness."""
    _, preview_fn, _ = get_classifier(harness)
    return preview_fn(parsed, event_type)


def extract_tool_info(harness: str, parsed: dict) -> tuple[str | None, str | None]:
    """Extract (tool_name, tool_id) for the given harness."""
    _, _, tool_info_fn = get_classifier(harness)
    return tool_info_fn(parsed)


# ---------------------------------------------------------------------------
# Timestamp extraction -- harness-specific
# ---------------------------------------------------------------------------


def _ts_claude_code(parsed: dict) -> str | None:
    """Return ClickHouse timestamp string from a Claude Code JSONL line, or None."""
    raw = parsed.get("timestamp")
    if not raw:
        return None
    ts = str(raw).replace("T", " ").rstrip("Z")
    if "." not in ts:
        ts += ".000"
    return ts


def _ts_kiro(parsed: dict) -> str | None:
    """Return ClickHouse timestamp string from a Kiro JSONL line, or None.

    The CLI embeds unix epoch *seconds* at ``data.meta.timestamp``. Only Prompt
    lines carry one; AssistantMessage and ToolResults inherit it.

    The IDE instead puts an ISO-8601 timestamp at the top level of every
    record. Without reading it every IDE event was stamped with its upload
    time, collapsing whole conversations into a few milliseconds and making
    durations, ordering and response-time stats meaningless.
    """
    if isinstance(parsed.get("payload"), dict):
        raw = parsed.get("timestamp")
        if not isinstance(raw, str) or not raw:
            return None
        # Parsed rather than string-trimmed: the value goes straight into a
        # DateTime64 column, and an offset that is not UTC, or any other
        # non-empty malformed string, would be sent verbatim and can reject the
        # whole insert batch. Returning None lets ingestion fall back to a
        # valid timestamp for that record instead.
        try:
            from datetime import UTC, datetime

            parsed_ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed_ts.tzinfo is not None:
            parsed_ts = parsed_ts.astimezone(UTC)
        return parsed_ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    data = parsed.get("data")
    if not isinstance(data, dict):
        return None
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return None
    epoch_s = meta.get("timestamp")
    if not epoch_s:
        return None
    try:
        from datetime import UTC, datetime

        dt = datetime.fromtimestamp(float(epoch_s), tz=UTC)
        return dt.strftime("%Y-%m-%d %H:%M:%S.000")
    except Exception:
        return None


def _ts_cursor(parsed: dict) -> str | None:
    """Return ClickHouse timestamp string from a Cursor JSONL line, or None.

    Cursor does not embed timestamps in its JSONL transcript lines.
    Return None so the caller uses ingestion time (now) as fallback.
    """
    return None


def _ts_pi(parsed: dict) -> str | None:
    """Return ClickHouse timestamp string from a Pi JSONL line, or None.

    Pi uses ISO timestamps at the entry level (``parsed["timestamp"]``).
    """
    raw = parsed.get("timestamp")
    if not raw:
        return None
    ts = str(raw).replace("T", " ").rstrip("Z")
    if "." not in ts:
        ts += ".000"
    return ts


def _ts_copilot_cli(parsed: dict) -> str | None:
    """Return ClickHouse timestamp string from a Copilot CLI JSONL line, or None."""
    raw = parsed.get("ts")
    if not raw:
        event = parsed.get("event")
        if isinstance(event, dict):
            raw = event.get("ts")
    if not raw:
        return None
    ts = str(raw).replace("T", " ").rstrip("Z")
    if "." not in ts:
        ts += ".000"
    return ts


def _ts_antigravity(parsed: dict) -> str | None:
    """Return ClickHouse timestamp string from an Antigravity transcript line."""
    raw = parsed.get("created_at")
    if not raw:
        return None
    ts = str(raw).replace("T", " ").rstrip("Z")
    if "." not in ts:
        ts += ".000"
    return ts


_TS_EXTRACTORS: dict[str, object] = {
    "claude-code": _ts_claude_code,
    "codex": _ts_claude_code,  # Codex uses same timestamp format as Claude Code
    "kiro": _ts_kiro,
    "cursor": _ts_cursor,
    "goose": _ts_claude_code,  # mirrored goose records carry an ISO `timestamp`
    "opencode": _ts_claude_code,
    "pi": _ts_pi,
    "copilot-cli": _ts_copilot_cli,
    "antigravity": _ts_antigravity,
}


def extract_timestamp(harness: str, parsed: dict) -> str | None:
    """Return a ClickHouse-formatted timestamp string for a JSONL line, or None.

    Returns None when the line has no timestamp -- callers should use a
    sentinel or inherit from a previous line rather than silently defaulting.
    Raises KeyError for unknown harnesses.
    Raises ValueError if the harness has session_parser=None (no parser configured).
    """
    from observal_shared.harness_registry import HARNESS_REGISTRY

    parser_id = HARNESS_REGISTRY[harness]["session_parser"]  # KeyError = unknown harness
    if parser_id is None:
        raise ValueError(f"No session parser configured for harness: {harness}")
    extractor = _TS_EXTRACTORS[parser_id]  # KeyError = unimplemented
    return extractor(parsed)  # type: ignore[call-arg,operator]


# ---------------------------------------------------------------------------
# Per-harness extra rows (written after the main ingest loop)
# ---------------------------------------------------------------------------
# Each per-harness module owns its own extra_ingest_rows() function.
# Register new harnesses here by importing and adding to _EXTRA_ROWS_HANDLERS.

from services.session_parsers.kiro import extra_ingest_rows as _kiro_extra_rows  # noqa: E402


def _no_extra_rows(*_args, **_kwargs) -> list[dict]:
    """Default: no extra rows for this harness."""
    return []


_ExtraRowsFn = Callable[..., list[dict]]

_EXTRA_ROWS_HANDLERS: dict[str, _ExtraRowsFn] = {
    "kiro": _kiro_extra_rows,
    "claude-code": _no_extra_rows,
    "codex": _no_extra_rows,
    "cursor": _no_extra_rows,
    "goose": _no_extra_rows,
    "opencode": _no_extra_rows,
    "pi": _no_extra_rows,
    "copilot-cli": _no_extra_rows,
    "antigravity": _no_extra_rows,
}


def get_extra_rows(
    harness: str,
    session_id: str,
    project_id: str,
    user_id: str,
    agent_id: str | None,
    agent_version: str | None,
    total_credits: float | None,
) -> list[dict]:
    """Return any extra ClickHouse rows to insert after the main ingest loop.

    Dispatches to per-harness handlers via session_parser ID.
    Unknown harnesses fall back to _no_extra_rows (fail-open).
    """
    from observal_shared.harness_registry import HARNESS_REGISTRY

    parser_id = HARNESS_REGISTRY.get(harness, {}).get("session_parser", "claude-code")
    handler = _EXTRA_ROWS_HANDLERS.get(parser_id, _no_extra_rows)
    return handler(session_id, project_id, user_id, agent_id, agent_version, harness, total_credits)
