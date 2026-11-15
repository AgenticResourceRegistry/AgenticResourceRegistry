# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""observal-agents: the MCP server that lets a pulled agent use other agents.

    python -m observal_cli.delegation.mcp_server --harness claude-code [--parent-id <agent uuid>]

Added to every Agent that ``observal agent pull`` installs (ADR 0002), and
usable on its own (``observal delegate mcp``). Tools:

    find_agents   approved registry and remote A2A agents that fit a task
    delegate      hand one of them a task (or answer a remote task waiting for input)
    get_task      state and results of a delegated task, optionally waiting
    cancel_task   stop a delegated task

Stdio transport, newline-delimited JSON-RPC 2.0, stdlib only. Anything
printed by the rest of the CLI goes to stderr so it can never corrupt the
protocol stream.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, TextIO

from loguru import logger as optic

from observal_cli.delegation import progress, service, tasks
from observal_cli.errors import CliError, _boundary_active

SERVER_NAME = "observal-agents"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
# Below common MCP client tool timeouts (Codex: 60 s); the task keeps running and get_task follows it.
DEFAULT_WAIT_SECONDS = 45
MAX_WAIT_SECONDS = 50
# How often a followed task publishes new child output; well inside the tool timeout.
PROGRESS_SLICE_SECONDS = 2.0

TOOLS: list[dict] = [
    {
        "name": "find_agents",
        "description": (
            "Search your organization's Observal registry for approved agents that can take over a task: "
            "registry agents (run in an isolated copy of this repository) and remote A2A agents. Use it when "
            "part of the work needs a specialist you do not have (security review, a service's own agent, "
            "test generation, a different model). Returns identifiers to pass to `delegate`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What you need done, in plain words."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate",
        "description": (
            "Hand one self-contained task to an agent from `find_agents`. Write the message as a complete brief: "
            "the agent does not see this conversation. A registry agent works in a throwaway copy of the "
            "repository; any file changes come back as a patch that is NOT applied, so review it and apply it "
            "yourself if it is right. Waits up to wait_seconds, then returns a task_id you can poll with "
            "get_task. To answer a remote agent that asked for input, pass its task_id and your answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Identifier from find_agents (urn:air:...)."},
                "message": {"type": "string", "description": "The complete task brief, or your reply."},
                "task_id": {"type": "string", "description": "Only to reply to a task waiting for input."},
                "harness": {
                    "type": "string",
                    "description": "Harness to run a registry agent in. Omit it to use your own harness; "
                    "pass it only when the delegation names the harnesses to choose from.",
                },
                "wait_seconds": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_WAIT_SECONDS,
                    "default": DEFAULT_WAIT_SECONDS,
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "get_task",
        "description": "Get the state and results of a delegated task. Optionally wait for it to finish.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "wait_seconds": {"type": "integer", "minimum": 0, "maximum": MAX_WAIT_SECONDS, "default": 0},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "cancel_task",
        "description": "Stop a delegated task that is no longer needed.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
]


class Server:
    def __init__(self, *, harness: str | None, parent_id: str | None, out: TextIO) -> None:
        self.harness = harness
        self.parent_id = parent_id
        self.out = out
        self._progress_seen: dict[str, int] = {}

    # ── Transport ────────────────────────────────────────────────────────

    def send(self, message: dict) -> None:
        self.out.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.out.flush()

    def serve(self, source: TextIO) -> None:
        for line in source:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            response = self.handle(request)
            if response is not None:
                self.send(response)

    # ── Dispatch ─────────────────────────────────────────────────────────

    def handle(self, request: Any) -> dict | None:
        if not isinstance(request, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
        method = request.get("method")
        req_id = request.get("id")
        if req_id is None:
            return None  # notification (initialized, cancelled, ...)
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        if method == "initialize":
            requested = str(params.get("protocolVersion") or "")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            result = {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": _cli_version()},
                "instructions": (
                    "Use find_agents then delegate when a self-contained part of the task is better done by a "
                    "specialist agent from the organization's registry. Delegated changes come back as patches "
                    "for you to review; nothing is applied automatically."
                ),
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = self.call_tool(
                str(params.get("name") or ""),
                params.get("arguments") or {},
                _progress_token(params),
            )
        else:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def call_tool(self, name: str, arguments: dict, progress_token: Any = None) -> dict:
        """Run one tool and wrap its outcome, so a failure is reported and not raised."""
        try:
            text = self._run_tool(name, arguments if isinstance(arguments, dict) else {}, progress_token)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except service.DelegationError as exc:
            return _tool_error(exc.message)
        except CliError as exc:
            return _tool_error(exc.message + (f" {exc.remediation}" if exc.remediation else ""))
        except Exception as exc:  # never let one bad call take the server down
            optic.exception("observal-agents tool {} failed", name)
            return _tool_error(f"{name} failed: {type(exc).__name__}. Details are in the Observal log.")

    def _run_tool(self, name: str, args: dict, progress_token: Any = None) -> str:
        """Run one tool by name and return the text the client reads."""
        # The wait budget covers the whole call, not just the part spent following:
        # the client's timeout started before find_agents or start were answered.
        started = time.monotonic()
        wait = _bounded(args.get("wait_seconds"), DEFAULT_WAIT_SECONDS if name == "delegate" else 0)
        if name == "find_agents":
            agents = service.find_agents(str(args.get("task") or ""), limit=_bounded(args.get("limit"), 5, 1, 10))
            if not agents:
                return "No approved agent in the registry matches that task. Do it yourself."
            return json.dumps({"agents": agents}, indent=2, ensure_ascii=False)
        if name == "delegate":
            # A client that asked for progress keeps the wait here, so output can be forwarded as it arrives.
            wait_here = 0 if progress_token is not None else wait
            if args.get("task_id"):
                task = service.reply(str(args["task_id"]), str(args.get("message") or ""), wait_seconds=wait_here)
            else:
                agent = str(args.get("agent") or "").strip()
                if not agent:
                    raise service.DelegationError("Pass the agent identifier from find_agents.")
                task = service.start(
                    agent,
                    str(args.get("message") or ""),
                    harness=str(args.get("harness") or "").strip() or None,
                    parent_harness=self.harness,
                    parent_agent_id=self.parent_id,
                    wait_seconds=wait_here,
                )
            return service.summarize(self._with_progress(task, wait, progress_token, started=started))
        if name == "get_task":
            task_id = str(args.get("task_id") or "")
            if progress_token is None:
                return service.summarize(service.wait(task_id, wait))
            return service.summarize(self._with_progress(service.get(task_id), wait, progress_token, started=started))
        if name == "cancel_task":
            return service.summarize(service.cancel(str(args.get("task_id") or "")))
        raise service.DelegationError(f"Unknown tool: {name}")

    def _with_progress(self, task: dict, seconds: int, token: Any, *, started: float | None = None) -> dict:
        """Follow a task, forwarding its child's new output as MCP progress notifications.

        The progress token is optional in the protocol: without one this is the
        plain wait, so a client that does not ask pays nothing. ``started`` is the
        monotonic time the tool call began, so answering the call can come out of
        the same budget instead of pushing the reply past the client's timeout.

        The cursor lives on the server, one per task: a client polls get_task every
        few seconds, and each call must forward only what it has not already sent
        rather than replaying the whole backlog of a long run.
        """
        if token is None:
            return task
        deadline = (time.monotonic() if started is None else started) + seconds
        task_id = task["id"]
        seen = self._progress_seen.get(task_id, 0)
        while True:
            final = tasks.is_final(task)
            events, seen = progress.drain(task_id, seen, rounds=None if final else progress.DRAIN_ROUNDS)
            self._progress_seen[task_id] = seen
            for event in events:
                self.notify_progress(token, event)
            if final or time.monotonic() >= deadline:
                return task
            # Never sleep past the budget this tool call promised the client.
            remaining = deadline - time.monotonic()
            task = service.wait(task["id"], min(PROGRESS_SLICE_SECONDS, max(0.0, remaining)))

    def notify_progress(self, token: Any, event: dict) -> None:
        """Send one notifications/progress for a sidecar event."""
        text = f"{event.get('kind') or 'line'}: {event.get('text') or ''}"
        self.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {
                    "progressToken": token,
                    "progress": int(event.get("seq") or 0),
                    "message": text[:400],
                },
            }
        )


def _progress_token(params: dict) -> Any:
    """The client's progress token, when it asked to be kept informed."""
    meta = params.get("_meta")
    return meta.get("progressToken") if isinstance(meta, dict) else None


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _bounded(value: Any, default: int, low: int = 0, high: int = MAX_WAIT_SECONDS) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _cli_version() -> str:
    """The installed CLI version, for the initialize response."""
    try:
        from importlib.metadata import version

        return version("observal-cli")
    except Exception:
        return "0.0.0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="observal-agents")
    parser.add_argument("--harness", default=None, help="Harness this server runs inside")
    parser.add_argument("--parent-id", default=None, help="UUID of the agent that owns this server")
    args = parser.parse_args(argv)

    harness = args.harness or service.caller_harness()
    # One observal-agents entry is shared by every agent in a project or user config, so the parent
    # is not baked into it: a delegated child runs as its own agent (OBSERVAL_AGENT_ID, or its workspace).
    parent_id = args.parent_id or service.parent_agent_id()
    protocol_out = sys.stdout
    sys.stdout = sys.stderr  # stray prints must never reach the protocol stream
    _boundary_active.set(True)  # CLI errors become tool errors, not terminal output
    try:
        Server(harness=harness, parent_id=parent_id, out=protocol_out).serve(sys.stdin)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
