---
# SPDX-FileCopyrightText: 2026 Thirumalesh <thirumaleshpinninti@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal-agents MCP server"
description: "Start the delegation MCP server, call its four tools, and register it in a harness."
---

`observal-agents` is the MCP server that lets a running agent find other approved agents in your registry and hand one of them a task. It gives a model the same operations that `observal delegate` gives people and scripts. For the end-to-end workflow, see [Delegate work with A2A](/guides/delegate-work). For the design, see [ADR 0002](/adr/0002-a2a-delegation).

> [!NOTE]
> The bundled `observal-agents` **skill**, which creates and publishes registry Agents, is a different thing with the same name. This page covers the MCP server.

## How the server works

- It uses the stdio transport: newline-delimited JSON-RPC 2.0 on stdin and stdout. Anything else the CLI prints goes to stderr, so it never corrupts the protocol stream.
- It answers `initialize`, `ping`, `tools/list`, and `tools/call`. Other methods return error `-32601`. Notifications get no reply.
- It supports MCP protocol versions `2025-06-18`, `2025-03-26`, and `2024-11-05`. When a client asks for another version, it answers with `2025-06-18`.
- It runs until stdin closes.
- `find_agents`, and `delegate` when it starts a task, call your registry, so the CLI must be signed in with `observal auth login`, or `OBSERVAL_TOKEN` or `OBSERVAL_ACCESS_TOKEN` must be set. `get_task` and `cancel_task` read and update tasks saved on this machine, so they work without credentials.

## Start the server

```bash
observal delegate mcp --harness claude-code
```

| Option | Description |
| --- | --- |
| `--harness`, `-i` | Harness this server runs inside. Without it, the server uses `OBSERVAL_HARNESS`, then the harness whose environment it was started from. If that is ambiguous, it is left unset. |
| `--parent-id` | UUID of the agent that owns this server. Without it, the server uses `OBSERVAL_AGENT_ID`, then the delegated task whose workspace it runs in. |
| `--output`, `-o` | Error format: `table` or `json`. MCP traffic always uses stdio. |

Pulled agents start the same server as a Python module, which takes `--harness` and `--parent-id`:

```bash
python -m observal_cli.delegation.mcp_server --harness claude-code
```

The harness is where `delegate` runs a registry agent unless the call passes its own `harness` argument. When the server's harness can't run the agent and other installed harnesses can, `delegate` fails and names them, and the model can retry with `harness` set to one of them.

To check that the server starts, list its tools:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | observal delegate mcp
```

## Tools

| Tool | What it does | Arguments |
| --- | --- | --- |
| `find_agents` | Searches the registry for approved registry agents and remote A2A agents that can take delegated tasks. | `task` (required): what you need done. `limit`: 1 to 10, default 5. |
| `delegate` | Hands one self-contained task to an agent, or answers a remote task that is waiting for input. | `message` (required): the complete task brief, or your reply. `agent`: an identifier from `find_agents` (`urn:air:...`); required unless you pass `task_id`. `task_id`: only to reply to a task waiting for input. `harness`: harness to run a registry agent in. `wait_seconds`: 0 to 50, default 45. |
| `get_task` | Returns the state and results of a delegated task, optionally waiting for it to finish. | `task_id` (required). `wait_seconds`: 0 to 50, default 0. |
| `cancel_task` | Stops a delegated task that is no longer needed. | `task_id` (required). |

Out-of-range numbers are clamped to the allowed range, and values that aren't numbers use the default.

### What the tools return

`find_agents` returns JSON with an `agents` list. Each agent has `identifier`, `name`, `kind` (`agent` or `a2a`), `description`, `score`, `version`, `harnesses`, `ref`, and `recommended`. When nothing matches, it returns `No approved agent in the registry matches that task. Do it yourself.`

`delegate`, `get_task`, and `cancel_task` return a text summary of the task:

- `task_id`, the agent's name and kind (and the harness that runs it), and the A2A state, such as `TASK_STATE_WORKING` or `TASK_STATE_COMPLETED`.
- The status message and any artifacts. File changes from a registry agent appear as a `changes.patch` diff with the path of the patch file.
- The next step: call `get_task` while the task is still running, call `delegate` with `task_id` when the agent needs input, or review the patch and apply it with `git apply`.

A registry agent works in an isolated copy of the repository. Its changes are **never applied for you**.

Errors come back as tool results with `isError: true`, not as protocol errors. When a tool fails unexpectedly, its message says the details are in the Observal log, which you can read with `observal ops logs`.

### Wait times

`delegate` waits for the task up to 45 seconds by default and at most 50, which keeps a typical call under common MCP client tool timeouts (Codex uses 60 seconds). The wait counts from the start of the call. When it runs out, the task keeps running; follow it with `get_task`.

The limit covers waiting for the task, not the registry lookups before it starts. If the registry answers with a `Retry-After` delay, the call can outlast your client's timeout, and the task can start without the client receiving its `task_id`. Run `observal delegate list` to find it.

## Register the server in a harness

### Pulled agents

`observal agent pull` adds an `observal-agents` entry to every Agent it installs, as long as the registry setting `discovery.delegation_enabled` is on (the default). The entry runs `-m observal_cli.delegation.mcp_server --harness <harness>` with the CLI's own Python interpreter. One entry is shared by every agent pulled into the same project or user config, so it carries no agent ID. A delegated child is identified by `OBSERVAL_AGENT_ID`, which delegation always sets for it.

### By hand

Register `observal delegate mcp --harness <harness>` as a stdio MCP server named `observal-agents`. For Claude Code:

```bash
claude mcp add observal-agents -- observal delegate mcp --harness claude-code
```

For another harness, add a stdio server with the command `observal` and the arguments `delegate`, `mcp`, `--harness`, and the harness ID to that harness's MCP configuration. Harness IDs are `claude-code`, `kiro`, `cursor`, `codex`, `copilot`, `copilot-cli`, `opencode`, `antigravity`, `goose`, and `pi`.

Copilot (VS Code) and Goose can host the server, but can't run a delegated registry agent themselves because they have no headless mode. From them, delegate to a remote A2A agent, or pass the `harness` argument to run a registry agent in another installed harness.

The harness's own permission prompt for the `delegate` tool is where you approve starting a delegation.

## Guards

These checks run before a delegation starts:

- Only approved agents that can take delegated tasks are accepted.
- A delegated agent can delegate again, down to `OBSERVAL_DELEGATION_MAX_DEPTH` levels (default 2).
- A delegated agent can start at most three tasks of its own.
- An agent that is already working on the task higher up the chain is refused, so delegation can't loop.
- A task message can be at most 100,000 characters.

See [ADR 0002, Decision 6](/adr/0002-a2a-delegation#decision-6-guards) for how the chain is tracked.

## Environment variables

| Variable | Used for |
| --- | --- |
| `OBSERVAL_HARNESS` | The server's harness when `--harness` isn't passed. |
| `OBSERVAL_AGENT_ID` | The calling agent when `--parent-id` isn't passed. Delegation sets it for every registry agent it runs. |
| `OBSERVAL_DELEGATION_MAX_DEPTH` | How many levels deep delegation can go. Default 2. |
| `OBSERVAL_A2A_TOKEN_<HOST>` | The token sent to a remote A2A agent on that host. See [ADR 0002, Decision 4](/adr/0002-a2a-delegation#decision-4-no-proxy-credentials-stay-with-the-caller). |
| `OBSERVAL_TOKEN`, `OBSERVAL_ACCESS_TOKEN` | Registry credentials when the CLI isn't signed in. |

Delegated tasks are stored in `~/.observal/delegations/`, and each registry agent's working copy is under `~/.observal/delegations/ws/`.
