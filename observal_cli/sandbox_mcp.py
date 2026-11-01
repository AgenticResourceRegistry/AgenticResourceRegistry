# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Observal Sandbox MCP Server.

A lightweight MCP server that exposes registered sandboxes as tools.
When an agent has sandbox components, this server is auto-injected into
the agent's MCP config - giving the agent a `run_sandbox` tool it can
call naturally without prompt engineering.

Usage:
    observal-sandbox-mcp --sandboxes '<json>'

The --sandboxes arg is a JSON array of sandbox specs:
    [{"id": "uuid", "name": "python-pytest", "image": "python:3.12-slim",
      "timeout": 60, "entrypoint": "pytest", "network_policy": "none"}]
"""

from __future__ import annotations

import json
import subprocess
import sys

from loguru import logger as optic

# Minimal JSON-RPC stdio MCP implementation (no dependencies beyond stdlib + loguru).
# Stdio transport framing: one JSON-RPC message per line, no headers.

# Initialization-based protocol revisions this server speaks, newest first.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def _reject_constant(_value: str):
    """The JSON decoder otherwise accepts NaN and Infinity as extensions."""
    raise ValueError("Non-JSON numeric constant")


def _read_message() -> dict | None:
    """Read one newline-delimited JSON-RPC message from stdin. Returns None at EOF."""
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line, parse_constant=_reject_constant)
        except ValueError as e:
            optic.warning("sandbox mcp: unparseable line: {}", e)
            _send_message(_make_error(None, -32700, "Parse error"))
            continue
        if isinstance(msg, dict):
            return msg
        optic.warning("sandbox mcp: non-object message: {}", type(msg).__name__)
        _send_message(_make_error(None, -32600, "Invalid Request"))


def _send_message(msg: dict) -> None:
    """Write one JSON-RPC message to stdout as a single line."""
    sys.stdout.buffer.write(json.dumps(msg).encode() + b"\n")
    sys.stdout.buffer.flush()


def _make_response(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _valid_request_id(value: object) -> bool:
    return isinstance(value, (str, int)) and not isinstance(value, bool)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--sandboxes", required=True, help="JSON array of sandbox specs")
    args = parser.parse_args()

    sandboxes = json.loads(args.sandboxes)

    # Build tool definitions + direct tool_name -> sandbox map
    tool_to_sandbox: dict[str, dict] = {}
    tools = []
    for sb in sandboxes:
        tool_name = f"run_sandbox_{sb['name'].replace('-', '_')}"
        tool_to_sandbox[tool_name] = sb
        tools.append(
            {
                "name": tool_name,
                "description": (
                    f"Run a command in the '{sb['name']}' sandbox "
                    f"({sb.get('runtime_type', 'docker')}: {sb['image']}, timeout: {sb.get('timeout', 300)}s, "
                    f"network: {sb.get('network_policy', 'none')}). "
                    f"Default command: {sb.get('entrypoint', 'bash')}"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": f"Command to run inside the container. Default: {sb.get('entrypoint', 'bash')}",
                        },
                    },
                    "required": [],
                },
            }
        )

    while True:
        msg = _read_message()
        if msg is None:
            break

        # Responses to server requests are unexpected here, but are not malformed
        # requests. Notifications have no id and never receive a reply.
        if "method" not in msg:
            if "result" not in msg and "error" not in msg:
                req_id = msg.get("id")
                _send_message(_make_error(req_id if _valid_request_id(req_id) else None, -32600, "Invalid Request"))
            continue
        if "id" not in msg:
            continue

        req_id = msg["id"]
        if not _valid_request_id(req_id):
            _send_message(_make_error(None, -32600, "Invalid Request"))
            continue
        method = msg["method"]
        if not isinstance(method, str) or msg.get("jsonrpc") != "2.0":
            _send_message(_make_error(req_id, -32600, "Invalid Request"))
            continue

        if method == "initialize":
            params = msg.get("params")
            if not isinstance(params, dict):
                _send_message(_make_error(req_id, -32602, "Invalid params"))
                continue
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            _send_message(
                _make_response(
                    req_id,
                    {
                        "protocolVersion": version,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "observal-sandbox", "version": "1.0.0"},
                    },
                )
            )
        elif method == "ping":
            # MCP requires every receiver to answer ping with an empty result.
            _send_message(_make_response(req_id, {}))
        elif method == "tools/list":
            _send_message(_make_response(req_id, {"tools": tools}))
        elif method == "tools/call":
            params = msg.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                _send_message(_make_error(req_id, -32602, "Invalid params"))
                continue
            tool_name = params["name"]
            # Some clients send an explicit null when a tool takes no arguments.
            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                _send_message(_make_error(req_id, -32602, "Invalid params"))
                continue

            # Direct lookup from tool name to sandbox spec
            sb = tool_to_sandbox.get(tool_name)
            if not sb:
                _send_message(
                    _make_response(
                        req_id,
                        {
                            "content": [{"type": "text", "text": f"Unknown sandbox tool: {tool_name}"}],
                            "isError": True,
                        },
                    )
                )
                continue

            command = arguments.get("command") or sb.get("entrypoint") or "bash"
            timeout = sb.get("timeout", 300)
            image = sb["image"]
            sandbox_id = sb["id"]
            runtime_type = sb.get("runtime_type", "docker")
            resource_limits = sb.get("resource_limits", {}) or {}
            network_policy = sb.get("network_policy", "none")
            runtime_config = sb.get("runtime_config", {}) or {}

            # Run the sandbox
            try:
                result = subprocess.run(
                    [
                        "observal-sandbox-run",
                        "--sandbox-id",
                        sandbox_id,
                        "--image",
                        image,
                        "--runtime-type",
                        runtime_type,
                        "--timeout",
                        str(timeout),
                        "--network-policy",
                        network_policy,
                        "--resource-limits",
                        json.dumps(resource_limits),
                        "--runtime-config",
                        json.dumps(runtime_config),
                        "--command",
                        command,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout + 10,
                )
                output = result.stdout
                if result.stderr:
                    output += f"\n[stderr]\n{result.stderr}"
                if result.returncode != 0:
                    output += f"\n[exit code: {result.returncode}]"
                _send_message(
                    _make_response(
                        req_id,
                        {
                            "content": [{"type": "text", "text": output or "(no output)"}],
                            "isError": result.returncode != 0,
                        },
                    )
                )
            except subprocess.TimeoutExpired:
                _send_message(
                    _make_response(
                        req_id,
                        {
                            "content": [{"type": "text", "text": f"Sandbox timed out after {timeout}s"}],
                            "isError": True,
                        },
                    )
                )
            except FileNotFoundError:
                _send_message(
                    _make_response(
                        req_id,
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "observal-sandbox-run not found. Install: pip install 'observal-cli[sandbox]'",
                                }
                            ],
                            "isError": True,
                        },
                    )
                )
            except Exception as e:
                _send_message(
                    _make_response(
                        req_id,
                        {
                            "content": [{"type": "text", "text": f"Error: {e}"}],
                            "isError": True,
                        },
                    )
                )
        else:
            _send_message(_make_error(req_id, -32601, f"Method not found: {method}"))


if __name__ == "__main__":
    main()
