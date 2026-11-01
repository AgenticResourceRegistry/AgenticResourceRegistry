# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Drive observal_cli.sandbox_mcp over real stdio pipes using MCP's newline-delimited framing."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SANDBOX = {"id": "sb-123", "name": "python-pytest", "image": "python:3.12-slim", "timeout": 5, "entrypoint": "pytest"}

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake runner relies on a shebang script")


@pytest.fixture
def fake_runner_path(tmp_path):
    """Put a stand-in `observal-sandbox-run` on PATH that echoes its argv as JSON."""
    runner = tmp_path / "observal-sandbox-run"
    runner.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    runner.chmod(0o755)
    return f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"


def _run_session(messages: list[dict] | str | bytes, path: str) -> list[dict]:
    stdin = messages if isinstance(messages, (str, bytes)) else "".join(json.dumps(m) + "\n" for m in messages)
    proc = subprocess.run(
        [sys.executable, "-m", "observal_cli.sandbox_mcp", "--sandboxes", json.dumps([SANDBOX])],
        input=stdin,
        capture_output=True,
        text=not isinstance(stdin, bytes),
        timeout=30,
        cwd=ROOT,
        env={**os.environ, "PATH": path},
    )
    assert proc.returncode == 0, proc.stderr
    stdout = proc.stdout.decode("utf-8") if isinstance(proc.stdout, bytes) else proc.stdout
    lines = stdout.splitlines()
    assert all(line.startswith("{") for line in lines), stdout
    return [json.loads(line) for line in lines]


def _initialize(req_id: int, version: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "initialize",
        "params": {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "pytest", "version": "0"}},
    }


def test_initialize_list_and_call_over_newline_framing(fake_runner_path):
    replies = _run_session(
        [
            _initialize(1, "2025-06-18"),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "run_sandbox_python_pytest", "arguments": {"command": "pytest -q"}},
            },
        ],
        fake_runner_path,
    )

    # The notification gets no reply, so exactly one line per request, in order
    assert [r["id"] for r in replies] == [1, 2, 3]
    init, listed, called = replies

    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"] == {"tools": {}}

    assert [t["name"] for t in listed["result"]["tools"]] == ["run_sandbox_python_pytest"]

    assert called["result"]["isError"] is False
    argv = json.loads(called["result"]["content"][0]["text"])
    assert argv[argv.index("--sandbox-id") + 1] == "sb-123"
    assert argv[argv.index("--image") + 1] == "python:3.12-slim"
    assert argv[argv.index("--command") + 1] == "pytest -q"


def test_malformed_lines_return_errors_and_do_not_stop_later_requests(fake_runner_path):
    replies = _run_session('\n{"bad":\n[1, 2]\n' + json.dumps(_initialize(1, "2025-06-18")) + "\n", fake_runner_path)

    assert [reply.get("id") for reply in replies] == [None, None, 1]
    assert [reply["error"]["code"] for reply in replies[:2]] == [-32700, -32600]
    assert replies[2]["result"]["protocolVersion"] == "2025-06-18"


def test_non_utf8_line_returns_error_then_recovers(fake_runner_path):
    replies = _run_session(b"\xff\n" + json.dumps(_initialize(1, "2025-06-18")).encode() + b"\n", fake_runner_path)

    assert [reply.get("id") for reply in replies] == [None, 1]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1]["result"]["protocolVersion"] == "2025-06-18"


def test_invalid_request_ids_and_stray_responses_do_not_dispatch(fake_runner_path):
    replies = _run_session(
        [
            {"jsonrpc": "2.0", "id": None, "method": "tools/call", "params": {"name": "run_sandbox_python_pytest"}},
            {"jsonrpc": "2.0", "id": True, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 1.5, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 8},
            {"jsonrpc": "2.0", "id": [12]},
            {"jsonrpc": "2.0", "id": 7, "method": None},
            {"jsonrpc": "2.0", "id": 9, "result": {"tools": []}},
            {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "run_sandbox_python_pytest"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            _initialize(10, "2025-06-18"),
        ],
        fake_runner_path,
    )

    assert [reply["id"] for reply in replies] == [None, None, None, 8, None, 7, 10]
    assert [reply["error"]["code"] for reply in replies[:6]] == [-32600] * 6
    assert replies[6]["result"]["protocolVersion"] == "2025-06-18"


def test_invalid_jsonrpc_versions_cannot_dispatch_and_session_recovers(fake_runner_path):
    replies = _run_session(
        [
            {"id": 20, "method": "tools/call", "params": {"name": "run_sandbox_python_pytest"}},
            {"jsonrpc": "1.0", "id": 21, "method": "tools/list"},
            {"jsonrpc": 2.0, "id": 22, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": None, "id": 23, "method": "tools/list"},
            _initialize(24, "2025-06-18"),
        ],
        fake_runner_path,
    )

    assert [reply["id"] for reply in replies] == [20, 21, 22, 23, 24]
    assert [reply["error"]["code"] for reply in replies[:4]] == [-32600] * 4
    assert replies[4]["result"]["protocolVersion"] == "2025-06-18"


def test_non_json_constants_and_oversized_integer_recover(fake_runner_path):
    malformed = [
        '{"jsonrpc":"2.0","id":NaN,"method":"tools/list"}',
        '{"jsonrpc":"2.0","id":Infinity,"method":"tools/list"}',
        '{"jsonrpc":"2.0","id":-Infinity,"method":"tools/list"}',
        '{"jsonrpc":"2.0","id":' + "9" * 5000 + ',"method":"tools/list"}',
    ]
    session = "\n".join([*malformed, json.dumps(_initialize(11, "2025-06-18"))]) + "\n"
    replies = _run_session(session, fake_runner_path)

    assert [reply["id"] for reply in replies] == [None, None, None, None, 11]
    assert [reply["error"]["code"] for reply in replies[:4]] == [-32700] * 4
    assert replies[4]["result"]["protocolVersion"] == "2025-06-18"


def test_malformed_params_return_errors_and_do_not_stop_later_requests(fake_runner_path):
    replies = _run_session(
        [
            {**_initialize(1, "2025-06-18"), "params": []},
            {**_initialize(2, "2025-06-18"), "params": None},
            _initialize(3, "2025-06-18"),
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": []},
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "run_sandbox_python_pytest", "arguments": []},
            },
            {"jsonrpc": "2.0", "id": 6, "method": "tools/list"},
        ],
        fake_runner_path,
    )

    assert [reply["id"] for reply in replies] == [1, 2, 3, 4, 5, 6]
    assert [replies[i]["error"]["code"] for i in (0, 1, 3, 4)] == [-32602] * 4
    assert replies[2]["result"]["protocolVersion"] == "2025-06-18"
    assert [tool["name"] for tool in replies[5]["result"]["tools"]] == ["run_sandbox_python_pytest"]


def test_unsupported_protocol_version_falls_back_to_latest(fake_runner_path):
    from observal_cli.sandbox_mcp import SUPPORTED_PROTOCOL_VERSIONS

    (reply,) = _run_session([_initialize(1, "1999-01-01")], fake_runner_path)

    assert reply["result"]["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]


def test_ping_gets_an_empty_result(fake_runner_path):
    (reply,) = _run_session([{"jsonrpc": "2.0", "id": 1, "method": "ping"}], fake_runner_path)

    assert reply == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_null_tool_arguments_use_the_sandbox_entrypoint(fake_runner_path):
    (reply,) = _run_session(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "run_sandbox_python_pytest", "arguments": None},
            }
        ],
        fake_runner_path,
    )

    assert reply["result"]["isError"] is False
    argv = json.loads(reply["result"]["content"][0]["text"])
    assert argv[argv.index("--command") + 1] == "pytest"
