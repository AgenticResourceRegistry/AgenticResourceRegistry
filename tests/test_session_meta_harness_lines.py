# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Insight session metadata is measured from every harness's own transcript lines."""

from __future__ import annotations

import json

from services.insights.session_meta_extractor import extract_session_meta


def _lines(*records: dict) -> list[str]:
    return [json.dumps(record) for record in records]


def _claude(line_type: str, ts: str, message: dict, **extra) -> dict:
    return {"type": line_type, "timestamp": ts, "cwd": "/repo", "message": message, **extra}


CLAUDE_USAGE = {
    "input_tokens": 10,
    "output_tokens": 5,
    "cache_read_input_tokens": 100,
    "cache_creation_input_tokens": 20,
}


def _assistant(message_id: str, *content: dict) -> dict:
    return {
        "id": message_id,
        "role": "assistant",
        "model": "claude-opus-5-5",
        "usage": CLAUDE_USAGE,
        "content": list(content),
    }


# One prompt, then three responses: an edit, a failing commit, and a closing reply.
# Claude Code writes each content block of a response as its own line.
CLAUDE_CODE = _lines(
    _claude("user", "2026-10-19T10:00:00.000Z", {"role": "user", "content": "rename x in a.py"}),
    _claude("assistant", "2026-10-19T10:00:02.000Z", _assistant("m1", {"type": "thinking", "thinking": ""})),
    _claude(
        "assistant",
        "2026-10-19T10:00:03.000Z",
        _assistant(
            "m1",
            {
                "type": "tool_use",
                "id": "t1",
                "name": "Edit",
                "input": {"file_path": "/repo/a.py", "old_string": "x = 1\ny = x", "new_string": "z = 1"},
            },
        ),
    ),
    _claude(
        "user",
        "2026-10-19T10:00:04.000Z",
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    ),
    _claude(
        "assistant",
        "2026-10-19T10:00:05.000Z",
        _assistant(
            "m2",
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "git commit -am rename"}},
        ),
    ),
    _claude(
        "user",
        "2026-10-19T10:00:06.000Z",
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": "Exit code 1\nNo such file"}
            ],
        },
    ),
    _claude("assistant", "2026-10-19T10:00:07.000Z", _assistant("m3", {"type": "text", "text": "Renamed."})),
    _claude(
        "user",
        "2026-10-19T10:00:08.000Z",
        {"role": "user", "content": "<local-command-caveat>generated</local-command-caveat>"},
        isMeta=True,
    ),
    _claude(
        "user",
        "2026-10-19T10:00:09.000Z",
        {"role": "user", "content": [{"type": "text", "text": "<system-reminder>\nbackground task done"}]},
    ),
)


def test_claude_code_session_is_measured():
    meta = extract_session_meta("s1", CLAUDE_CODE, "claude-code")

    assert (meta["user_message_count"], meta["assistant_message_count"]) == (1, 3)
    assert meta["first_prompt"] == "rename x in a.py"
    assert meta["tool_counts"] == {"Edit": 1, "Bash": 1}
    assert (meta["lines_added"], meta["lines_removed"], meta["files_modified"]) == (1, 2, 1)
    assert meta["languages"] == {"Python": 1}
    assert meta["git_commits"] == 1
    assert (meta["tool_errors"], meta["tool_error_categories"]) == (1, {"file_not_found": 1})
    # Usage repeated on each line of a response is counted once per response.
    assert (meta["input_tokens"], meta["output_tokens"]) == (30, 15)
    assert (meta["cache_read_tokens"], meta["cache_write_tokens"]) == (300, 60)
    assert meta["model_usage"]["claude-opus-5-5"]["messages"] == 3
    assert meta["project_path"] == "/repo"
    assert meta["duration_seconds"] == 9.0


def _codex(line_type: str, ts: str, payload: dict) -> dict:
    return {"type": line_type, "timestamp": ts, "payload": payload}


def _codex_user(ts: str, text: str) -> dict:
    return _codex(
        "response_item", ts, {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
    )


def _token_count(ts: str, input_tokens: int, cached: int, output: int) -> dict:
    total = {"input_tokens": input_tokens, "cached_input_tokens": cached, "output_tokens": output}
    return _codex("event_msg", ts, {"type": "token_count", "info": {"total_token_usage": total}})


PATCH = "*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+z = 1\n+w = 2\n*** End Patch\n"

# Codex 0.163 `exec` rollout: one prompt, three model responses (a shell run that fails,
# a patch, a closing reply). Usage comes from running totals, repeated once at the end.
CODEX = _lines(
    _codex("session_meta", "2026-10-19T10:00:00.000Z", {"cwd": "/repo"}),
    _codex("turn_context", "2026-10-19T10:00:00.100Z", {"model": "gpt-6-sol"}),
    _codex_user("2026-10-19T10:00:00.200Z", "<environment_context>\n  <cwd>/repo</cwd>\n</environment_context>"),
    _codex_user("2026-10-19T10:00:00.300Z", "commit the rename"),
    _codex("event_msg", "2026-10-19T10:00:00.301Z", {"type": "user_message", "message": "commit the rename"}),
    _codex("response_item", "2026-10-19T10:00:01.000Z", {"type": "reasoning", "summary": []}),
    _codex(
        "response_item",
        "2026-10-19T10:00:02.000Z",
        {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": "c1",
            "input": 'const r = await tools.exec_command({cmd:"git commit -am rename"}); text(r);',
        },
    ),
    _token_count("2026-10-19T10:00:02.100Z", 100, 40, 10),
    _codex(
        "response_item",
        "2026-10-19T10:00:03.000Z",
        {
            "type": "custom_tool_call_output",
            "call_id": "c1",
            "output": [
                {"type": "input_text", "text": "Script completed"},
                {"type": "input_text", "text": '{"exit_code":1}'},
            ],
        },
    ),
    _codex(
        "response_item",
        "2026-10-19T10:00:04.000Z",
        {"type": "custom_tool_call", "name": "apply_patch", "call_id": "c2", "input": PATCH},
    ),
    _token_count("2026-10-19T10:00:04.100Z", 250, 100, 30),
    _codex(
        "response_item",
        "2026-10-19T10:00:05.000Z",
        {"type": "custom_tool_call_output", "call_id": "c2", "output": "Success"},
    ),
    _codex(
        "response_item",
        "2026-10-19T10:00:06.000Z",
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Committed."}]},
    ),
    _codex("event_msg", "2026-10-19T10:00:06.001Z", {"type": "agent_message", "message": "Committed."}),
    _token_count("2026-10-19T10:00:06.100Z", 250, 100, 30),
)


def test_codex_session_is_measured():
    meta = extract_session_meta("s2", CODEX, "codex")

    assert (meta["user_message_count"], meta["assistant_message_count"]) == (1, 3)
    assert meta["first_prompt"] == "commit the rename"
    assert meta["tool_counts"] == {"exec": 1, "apply_patch": 1}
    assert meta["git_commits"] == 1
    assert meta["tool_errors"] == 1
    assert (meta["lines_added"], meta["lines_removed"], meta["files_modified"]) == (2, 1, 1)
    # Input excludes cached tokens; a repeated running total adds nothing.
    assert (meta["input_tokens"], meta["cache_read_tokens"], meta["output_tokens"]) == (150, 100, 30)
    assert meta["model_usage"]["gpt-6-sol"]["messages"] == 3
    assert meta["project_path"] == "/repo"


PI = _lines(
    {"type": "session", "cwd": "/repo", "timestamp": "2026-10-19T10:00:00.000Z"},
    {"type": "message", "timestamp": "2026-10-19T10:00:01.000Z", "message": {"role": "user", "content": "list files"}},
    {
        "type": "message",
        "timestamp": "2026-10-19T10:00:02.000Z",
        "message": {
            "role": "assistant",
            "model": "gpt-6-sol",
            "usage": {"input": 7, "output": 3},
            "content": [{"type": "toolCall", "id": "p1", "name": "bash", "arguments": {"command": "ls"}}],
        },
    },
    {
        "type": "message",
        "timestamp": "2026-10-19T10:00:03.000Z",
        "message": {"role": "toolResult", "toolName": "bash", "isError": False, "content": "a.py"},
    },
)


def test_pi_session_is_measured_as_before():
    meta = extract_session_meta("s3", PI, "pi")

    assert (meta["user_message_count"], meta["assistant_message_count"]) == (1, 1)
    assert meta["tool_counts"] == {"bash": 1}
    assert (meta["input_tokens"], meta["output_tokens"]) == (7, 3)
    assert meta["project_path"] == "/repo"


KIRO = _lines(
    {
        "version": "v1",
        "kind": "Prompt",
        "data": {"meta": {"timestamp": 1792404000}, "content": [{"kind": "text", "data": "fix a.py"}]},
    },
    {
        "version": "v1",
        "kind": "AssistantMessage",
        "data": {
            "content": [{"kind": "toolUse", "data": {"name": "write", "input": {"path": "a.py", "content": "x\ny"}}}]
        },
    },
    {"version": "v1", "kind": "ToolResults", "data": {"content": []}},
)


def test_kiro_session_is_measured_as_before():
    meta = extract_session_meta("s4", KIRO, "kiro")

    assert (meta["user_message_count"], meta["assistant_message_count"]) == (1, 1)
    assert meta["tool_counts"] == {"write": 1}
    assert (meta["lines_added"], meta["files_modified"]) == (2, 1)


def test_codex_patch_inside_exec_script_is_measured():
    script = 'text(await tools.apply_patch("' + PATCH.replace("\n", "\\n") + '"));\n'
    call = {"type": "custom_tool_call", "name": "exec", "call_id": "c1", "input": script}

    meta = extract_session_meta("s6", _lines(_codex("response_item", "2026-10-19T10:00:00.000Z", call)), "codex")

    assert (meta["lines_added"], meta["lines_removed"], meta["files_modified"]) == (2, 1, 1)


def test_pi_edit_with_empty_old_text_removes_no_lines():
    lines = _lines(
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "name": "edit",
                        "arguments": {"path": "a.py", "edits": [{"oldText": "", "newText": "x"}]},
                    }
                ],
            },
        }
    )

    assert extract_session_meta("s5", lines, "pi")["lines_removed"] == 0
