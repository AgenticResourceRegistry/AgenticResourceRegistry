# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Claude Code projector: stored transcript rows -> complete spans.

Span tree per session::

    invoke_agent claude-code         (the session; a subagent's hangs off its Agent tool span)
      turn N                         (one per user prompt)
        chat <model>                 (one per model response, grouped on message.id)
          execute_tool <name>        (paired on tool_use.id / tool_result.tool_use_id)

Structure comes from the transcript's own IDs, never from timestamps.  A span
is included only once it is complete, and nothing that arrives later can
change it, so the projection is monotonic:

- ``execute_tool``: when its ``tool_result`` arrives.  ``is_error`` sets ERROR.
- ``chat``: when a response with a different ``message.id`` starts, or its
  turn closes.  Usage is taken once per response, from its last line; Claude
  Code repeats the same ``usage`` on every line of a multi-line response.
- ``turn``: when the next user prompt arrives.
- ``invoke_agent`` (session), still-open turns and responses, and tool calls
  without a result: only with ``session_closed=True``.

Claude Code appends copies of earlier records when a session is resumed,
forked or relocated, sometimes under a new ``uuid``.  A record whose ``uuid``
was already seen, or whose timestamp and message are identical to an earlier
record's, is a replay and adds nothing to the spans.  Timestamps alone can't
tell: prompts queued while Claude Code is busy are written later with the
time they were queued.

Subagent sessions (``parent_session_id`` set) share the root session's trace.
Observal stores a subagent under its ``agentId``, and the parent's Agent tool
result names that ``agentId``, so both sides derive the spawning tool span's
ID on their own: ``span_id(root, "agent:<agentId>")``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import orjson
import xxhash

from services.otel.attributes import TOKEN_KEYS, clip, json_or_text, messages, pairs, semconv_input_tokens, to_int
from services.otel.ids import span_id, to_unix_nanos, trace_id_for
from services.otel.types import SPAN_KIND_CLIENT, SPAN_KIND_INTERNAL, Span, SpanEvent

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from services.otel.types import Row

HARNESS = "claude-code"

_INTERRUPTED_PREFIX = "[Request interrupted"


@dataclass
class _Turn:
    offset: int
    index: int
    start_ns: int
    end_ns: int
    prompt: str = ""
    output: str = ""
    offsets: list[int] = field(default_factory=list)
    events: list[SpanEvent] = field(default_factory=list)


@dataclass
class _Chat:
    message_id: str
    turn: _Turn
    start_ns: int
    end_ns: int
    model: str = ""
    stop_reason: str = ""
    tokens: dict[str, int] = field(default_factory=dict)
    texts: list[str] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    tool_calls: list[_Tool] = field(default_factory=list)
    offsets: list[int] = field(default_factory=list)
    error: bool = False


@dataclass
class _Tool:
    tool_id: str
    name: str
    input: str
    call_ns: int
    call_offset: int
    chat_span_id: str


def _load(row: Row) -> dict | None:
    raw = row.get("raw_line")
    if not raw:
        return None
    try:
        line = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return None
    return line if isinstance(line, dict) else None


def _text(content: object) -> str:
    """Plain text of a message or tool_result ``content`` (a string or a list of blocks)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif block.get("type") == "image":
            parts.append("[image]")
    return "\n".join(part for part in parts if part)


def _content(values: Mapping[str, str | None]) -> tuple[tuple[str, str], ...]:
    return tuple((key, value) for key, value in values.items() if value)


class _Walk:
    """One pass over a session's rows, emitting spans as they complete."""

    def __init__(self, rows: Sequence[Row]):
        first = rows[0]
        self.session_id = str(first.get("session_id") or "")
        self.parent_session_id = str(first.get("parent_session_id") or "") or None
        self.root_id = self.parent_session_id or self.session_id
        self.trace_id = trace_id_for(self.root_id)
        self.session_span_id = span_id(self.session_id, "session")
        self.user_id = str(first.get("user_id") or "")
        self.agent_id = next((str(r["agent_id"]) for r in rows if r.get("agent_id")), None)
        self.agent_version = next((str(r["agent_version"]) for r in rows if r.get("agent_version")), None)

        self.spans: list[Span] = []
        self.record_spans: dict[int, str] = {}

        self.turn: _Turn | None = None
        self.turn_count = 0
        self.chat: _Chat | None = None
        self.closed_chats: dict[str, str] = {}  # message.id -> span ID
        self.open_tools: dict[str, _Tool] = {}
        self.closed_tools: dict[str, str] = {}  # tool_use.id -> span ID
        self.seen_uuids: set[str] = set()
        self.seen_records: set[int] = set()  # fingerprints of (type, timestamp, message)
        self.main_conversation = False  # seen a user or assistant record that isn't a sidechain
        self.prev_ns: int | None = None
        self.first_ns: int | None = None
        self.last_ns: int | None = None
        self.first_prompt = ""
        self.last_output = ""
        self.session_offsets: list[int] = []
        self.session_events: list[SpanEvent] = []

    # -- IDs ---------------------------------------------------------------

    def _turn_span_id(self, turn: _Turn) -> str:
        return span_id(self.session_id, f"turn:{turn.offset}")

    def _chat_span_id(self, message_id: str) -> str:
        return span_id(self.session_id, f"chat:{message_id}")

    def _container_span_id(self) -> str:
        return self._turn_span_id(self.turn) if self.turn else self.session_span_id

    # -- walk --------------------------------------------------------------

    def walk(self, rows: Sequence[Row]) -> None:
        for row in rows:
            self._row(row)

    def _row(self, row: Row) -> None:
        offset = int(row["line_offset"])
        line = _load(row)
        if line is None:
            self._fallback(row, offset)
            return
        ts = to_unix_nanos(line.get("timestamp"))
        kind = str(line.get("type") or "")
        if kind in ("user", "assistant", "system") and self._is_replay(line, kind):
            self._replay(line, offset)
            return
        if line.get("isSidechain") and self.main_conversation:
            # A pre-2.0 subagent record written into the main transcript.
            self.record_spans[offset] = self._container_span_id()
            return
        if kind in ("user", "assistant") and not line.get("isSidechain"):
            # A subagent transcript is all sidechain records; only a main one mixes them in.
            self.main_conversation = True
        if kind == "assistant":
            self._assistant(line, offset, ts)
        elif kind == "user":
            self._user(line, offset, ts)
        elif kind == "system":
            self._event(offset, ts, f"system.{line.get('subtype') or 'message'}", str(line.get("content") or ""))
        else:
            self.record_spans[offset] = self._container_span_id()
        self._tick(ts)

    def _is_replay(self, line: dict, kind: str) -> bool:
        """Record this line as seen; True when an earlier line already carried it."""
        uuid = line.get("uuid")
        fingerprint = xxhash.xxh3_64_intdigest(
            orjson.dumps(
                [kind, line.get("timestamp"), line.get("message"), line.get("content")], option=orjson.OPT_SORT_KEYS
            )
        )
        replay = (isinstance(uuid, str) and uuid in self.seen_uuids) or fingerprint in self.seen_records
        if isinstance(uuid, str) and uuid:
            self.seen_uuids.add(uuid)
        self.seen_records.add(fingerprint)
        return replay

    def _replay(self, line: dict, offset: int) -> None:
        """Map a replayed record to the span its original went to, when that's known."""
        message = line.get("message") or {}
        message_id = str(message.get("id") or "")
        if line.get("type") == "assistant" and message_id in self.closed_chats:
            self.record_spans[offset] = self.closed_chats[message_id]
            return
        if self.chat is not None and line.get("type") == "assistant" and message_id == self.chat.message_id:
            self.record_spans[offset] = self._chat_span_id(message_id)
            return
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and str(block.get("tool_use_id") or "") in self.closed_tools:
                    self.record_spans[offset] = self.closed_tools[str(block["tool_use_id"])]
                    return
        self.record_spans[offset] = self._container_span_id()

    def _tick(self, ts: int | None) -> None:
        if ts is None:
            return
        self.first_ns = ts if self.first_ns is None else min(self.first_ns, ts)
        self.last_ns = ts if self.last_ns is None else max(self.last_ns, ts)
        self.prev_ns = ts
        if self.turn is not None:
            self.turn.end_ns = max(self.turn.end_ns, ts)

    def _fallback(self, row: Row, offset: int) -> None:
        """A row whose stored line can't be parsed, e.g. cut at ingest: use the stored columns."""
        tool_id = row.get("tool_id")
        ts = to_unix_nanos(row.get("timestamp"))
        if row.get("event_type") == "tool_call" and tool_id and ts is not None and self.chat is not None:
            tool_id = str(tool_id)
            if tool_id not in self.open_tools and tool_id not in self.closed_tools:
                self.open_tools[tool_id] = _Tool(
                    tool_id=tool_id,
                    name=str(row.get("tool_name") or "tool"),
                    input="",
                    call_ns=ts,
                    call_offset=offset,
                    chat_span_id=self._chat_span_id(self.chat.message_id),
                )
            self.record_spans[offset] = self._chat_span_id(self.chat.message_id)
            return
        self.record_spans[offset] = self._container_span_id()

    # -- records -----------------------------------------------------------

    def _user(self, line: dict, offset: int, ts: int | None) -> None:
        content = (line.get("message") or {}).get("content")
        results = (
            [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"]
            if isinstance(content, list)
            else []
        )
        if results:
            self._tool_results(line, results, offset, ts)
            return
        text = _text(content)
        if line.get("isCompactSummary"):
            self._event(offset, ts, "compact_summary", text)
        elif line.get("isMeta"):
            self._event(offset, ts, "user.meta", text)
        elif text.startswith(_INTERRUPTED_PREFIX):
            self._event(offset, ts, "user.interrupted", text)
        elif ts is None:
            self.record_spans[offset] = self._container_span_id()
        else:
            self._close_turn()
            self.turn_count += 1
            self.turn = _Turn(offset=offset, index=self.turn_count, start_ns=ts, end_ns=ts, prompt=text)
            self.turn.offsets.append(offset)
            self.first_prompt = self.first_prompt or text
            self.record_spans[offset] = self._turn_span_id(self.turn)

    def _assistant(self, line: dict, offset: int, ts: int | None) -> None:
        message = line.get("message") or {}
        message_id = str(message.get("id") or f"uuid:{line.get('uuid') or offset}")
        if message_id in self.closed_chats:
            self.record_spans[offset] = self.closed_chats[message_id]
            return
        if ts is None:
            self.record_spans[offset] = self._container_span_id()
            return
        chat = self.chat
        if chat is None or chat.message_id != message_id:
            self._close_chat()
            turn = self._ensure_turn(offset, ts)
            start = self.prev_ns if self.prev_ns is not None else ts
            chat = _Chat(message_id=message_id, turn=turn, start_ns=min(start, ts), end_ns=ts)
            self.chat = chat
        chat.end_ns = max(chat.end_ns, ts)
        chat.offsets.append(offset)
        chat.model = str(message.get("model") or chat.model)
        chat.stop_reason = str(message.get("stop_reason") or chat.stop_reason)
        chat.error = chat.error or bool(line.get("isApiErrorMessage"))
        usage = message.get("usage")
        if isinstance(usage, dict):
            # Every line of a response repeats the response's usage: keep the last, never sum.
            chat.tokens = {
                "input_tokens": to_int(usage.get("input_tokens")),
                "output_tokens": to_int(usage.get("output_tokens")),
                "cache_read_tokens": to_int(usage.get("cache_read_input_tokens")),
                "cache_write_tokens": to_int(usage.get("cache_creation_input_tokens")),
            }
        chat_span_id = self._chat_span_id(message_id)
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                chat.texts.append(str(block.get("text") or ""))
            elif kind == "thinking":
                chat.thinking.append(str(block.get("thinking") or ""))
            elif kind == "tool_use":
                tool_id = str(block.get("id") or "")
                if not tool_id or tool_id in self.open_tools or tool_id in self.closed_tools:
                    continue
                tool = _Tool(
                    tool_id=tool_id,
                    name=str(block.get("name") or "tool"),
                    input=json.dumps(block.get("input"), ensure_ascii=False) if "input" in block else "",
                    call_ns=ts,
                    call_offset=offset,
                    chat_span_id=chat_span_id,
                )
                self.open_tools[tool_id] = tool
                chat.tool_calls.append(tool)
        self.record_spans[offset] = chat_span_id

    def _tool_results(self, line: dict, results: list[dict], offset: int, ts: int | None) -> None:
        tool_use_result = line.get("toolUseResult")
        agent_id = tool_use_result.get("agentId") if isinstance(tool_use_result, dict) else None
        record_span: str | None = None
        for block in results:
            tool_use_id = str(block.get("tool_use_id") or "")
            if tool_use_id in self.closed_tools:
                record_span = record_span or self.closed_tools[tool_use_id]
                continue
            output = _text(block.get("content"))
            error = bool(block.get("is_error"))
            tool = self.open_tools.pop(tool_use_id, None)
            end = ts if ts is not None else (tool.call_ns if tool else None)
            if tool is None:
                if end is None:
                    continue
                # A result whose call isn't in this session (or wasn't stored).
                orphan_key = tool_use_id or f"offset:{offset}"
                span = self._tool_span(
                    tool_id=tool_use_id,
                    name="tool",
                    key=f"tool:{orphan_key}",
                    parent=self._container_span_id(),
                    start=end,
                    end=end,
                    arguments="",
                    output=output,
                    error=error,
                    offsets=(offset,),
                    extra={"observal.tool.orphan_result": True},
                )
            else:
                key = f"agent:{agent_id}" if agent_id and len(results) == 1 else f"tool:{tool.tool_id}"
                span = self._tool_span(
                    tool_id=tool.tool_id,
                    name=tool.name,
                    key=key,
                    parent=tool.chat_span_id,
                    start=tool.call_ns,
                    end=end if end is not None else tool.call_ns,
                    arguments=tool.input,
                    output=output,
                    error=error,
                    offsets=(tool.call_offset, offset),
                    extra={"observal.subagent.id": agent_id if key.startswith("agent:") else None},
                )
            self.spans.append(span)
            self.closed_tools[tool_use_id or f"offset:{offset}"] = span.span_id
            record_span = record_span or span.span_id
        self.record_spans[offset] = record_span or self._container_span_id()

    def _event(self, offset: int, ts: int | None, name: str, body: str) -> None:
        self.record_spans[offset] = self._container_span_id()
        if ts is None:
            return
        event = SpanEvent(
            name=name, time_ns=ts, content=_content({"observal.event.body": clip(body) if body else None})
        )
        if self.turn is not None:
            self.turn.events.append(event)
            self.turn.offsets.append(offset)
        else:
            self.session_events.append(event)
            self.session_offsets.append(offset)

    def _ensure_turn(self, offset: int, ts: int) -> _Turn:
        if self.turn is None:
            # Records before the first prompt, e.g. a session resumed mid-way.
            self.turn_count += 1
            self.turn = _Turn(offset=offset, index=self.turn_count, start_ns=ts, end_ns=ts)
        return self.turn

    # -- closing -----------------------------------------------------------

    def _tool_span(
        self,
        *,
        tool_id: str,
        name: str,
        key: str,
        parent: str,
        start: int,
        end: int,
        arguments: str,
        output: str,
        error: bool,
        offsets: tuple[int, ...],
        extra: dict[str, object],
    ) -> Span:
        sid = self.root_id if key.startswith("agent:") else self.session_id
        return Span(
            trace_id=self.trace_id,
            span_id=span_id(sid, key),
            parent_span_id=parent,
            name=f"execute_tool {name}",
            kind=SPAN_KIND_INTERNAL,
            start_ns=start,
            end_ns=max(end, start),
            attributes=pairs(
                {
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": name,
                    "gen_ai.tool.call.id": tool_id,
                    **extra,
                }
            ),
            content=_content(
                {
                    "gen_ai.tool.call.arguments": clip(arguments) if arguments else None,
                    "gen_ai.tool.call.result": clip(output) if output else None,
                    "input.value": clip(arguments) if arguments else None,
                    "output.value": clip(output) if output else None,
                }
            ),
            error=error,
            record_offsets=offsets,
        )

    def _close_chat(self) -> None:
        chat = self.chat
        if chat is None:
            return
        self.chat = None
        span_id_ = self._chat_span_id(chat.message_id)
        self.closed_chats[chat.message_id] = span_id_
        text = "\n\n".join(t for t in chat.texts if t)
        if text:
            chat.turn.output = text
            self.last_output = text
        tool_calls = [
            {"type": "tool_call", "id": tool.tool_id, "name": tool.name, "arguments": json_or_text(tool.input)}
            for tool in chat.tool_calls
        ]
        parts = (
            [{"type": "reasoning", "content": t} for t in chat.thinking if t]
            + [{"type": "text", "content": t} for t in chat.texts if t]
            + tool_calls
        )
        finish_reason = chat.stop_reason or ("tool_call" if tool_calls else "stop")
        input_tokens = semconv_input_tokens(chat.tokens)
        output_tokens = chat.tokens.get("output_tokens", 0)
        attributes: dict[str, object] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": chat.model,
            "gen_ai.response.model": chat.model,
            "gen_ai.response.id": chat.message_id,
            "gen_ai.response.finish_reasons": [chat.stop_reason] if chat.stop_reason else None,
        }
        for source, key in TOKEN_KEYS.items():
            if source != "input_tokens" and chat.tokens.get(source):
                attributes[key] = chat.tokens[source]
        if input_tokens:
            attributes["gen_ai.usage.input_tokens"] = input_tokens
        if input_tokens or output_tokens:
            attributes["gen_ai.usage.total_tokens"] = input_tokens + output_tokens
        output = text or (json.dumps(tool_calls, ensure_ascii=False) if tool_calls else "")
        self.spans.append(
            Span(
                trace_id=self.trace_id,
                span_id=span_id_,
                parent_span_id=self._turn_span_id(chat.turn),
                name=f"chat {chat.model}" if chat.model else "chat",
                kind=SPAN_KIND_CLIENT,
                start_ns=chat.start_ns,
                end_ns=chat.end_ns,
                attributes=pairs(attributes),
                content=_content(
                    {
                        "gen_ai.output.messages": messages("assistant", parts, finish_reason),
                        "output.value": clip(output) if output else None,
                    }
                ),
                error=chat.error,
                record_offsets=tuple(chat.offsets),
            )
        )

    def _close_turn(self) -> None:
        self._close_chat()
        turn = self.turn
        if turn is None:
            return
        self.turn = None
        self.spans.append(
            Span(
                trace_id=self.trace_id,
                span_id=self._turn_span_id(turn),
                parent_span_id=self.session_span_id,
                name=f"turn {turn.index}",
                kind=SPAN_KIND_INTERNAL,
                start_ns=turn.start_ns,
                end_ns=turn.end_ns,
                attributes=pairs({"observal.turn.index": turn.index}),
                content=_content(
                    {
                        "input.value": clip(turn.prompt) if turn.prompt else None,
                        "output.value": clip(turn.output) if turn.output else None,
                    }
                ),
                events=tuple(turn.events),
                record_offsets=tuple(turn.offsets),
            )
        )

    def close_session(self) -> None:
        self._close_turn()
        for tool in list(self.open_tools.values()):
            span = self._tool_span(
                tool_id=tool.tool_id,
                name=tool.name,
                key=f"tool:{tool.tool_id}",
                parent=tool.chat_span_id,
                start=tool.call_ns,
                end=tool.call_ns,
                arguments=tool.input,
                output="",
                error=False,
                offsets=(tool.call_offset,),
                extra={"observal.tool.incomplete": True},
            )
            self.spans.append(span)
        self.open_tools.clear()
        if self.first_ns is None or self.last_ns is None:
            return
        subagent = self.parent_session_id is not None
        self.spans.append(
            Span(
                trace_id=self.trace_id,
                span_id=self.session_span_id,
                parent_span_id=span_id(self.root_id, f"agent:{self.session_id}") if subagent else None,
                name="invoke_agent subagent" if subagent else f"invoke_agent {HARNESS}",
                kind=SPAN_KIND_INTERNAL,
                start_ns=self.first_ns,
                end_ns=self.last_ns,
                attributes=pairs(
                    {
                        "gen_ai.operation.name": "invoke_agent",
                        "session.id": self.root_id,
                        "gen_ai.conversation.id": self.session_id,
                        "user.id": self.user_id,
                        "gen_ai.agent.id": self.agent_id,
                        "observal.agent.version": self.agent_version,
                        "observal.harness": HARNESS,
                        "observal.session.id": self.session_id,
                        "observal.parent_session.id": self.parent_session_id,
                    }
                ),
                content=_content(
                    {
                        "gen_ai.input.messages": messages(
                            "user", [{"type": "text", "content": self.first_prompt}] if self.first_prompt else []
                        ),
                        "gen_ai.output.messages": messages(
                            "assistant",
                            [{"type": "text", "content": self.last_output}] if self.last_output else [],
                            "stop",
                        ),
                        "input.value": clip(self.first_prompt) if self.first_prompt else None,
                        "output.value": clip(self.last_output) if self.last_output else None,
                    }
                ),
                events=tuple(self.session_events),
                record_offsets=tuple(self.session_offsets),
            )
        )


class ClaudeCodeProjector:
    def project_spans(self, rows: Sequence[Row], *, session_closed: bool) -> list[Span]:
        if not rows:
            return []
        walk = _Walk(rows)
        walk.walk(rows)
        if session_closed:
            walk.close_session()
        return walk.spans

    def record_span_ids(self, rows: Sequence[Row]) -> Mapping[int, str]:
        if not rows:
            return {}
        walk = _Walk(rows)
        walk.walk(rows)
        return walk.record_spans
