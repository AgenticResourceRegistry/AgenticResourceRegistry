---
# SPDX-FileCopyrightText: 2026 Faatih <22oo1cso41faatih@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Copilot"
---

Copilot is a first-class Agentic Resource Registry harness integration. Agentic Resource Registry can install
GitHub Copilot agents, configure MCP servers, expose skills, and bridge
telemetry hooks.

---

## Overview

Copilot agent profiles are Markdown files stored in
`.github/agents/`.

Copilot currently supports project installation scope only. MCP servers,
hooks, skills, and agent profiles are managed through the project
configuration.

---

## Supported capabilities

| Capability      | Support                              |
| --------------- | ------------------------------------ |
| Agent profiles  | Project scope                        |
| Hook bridge     | Supported                            |
| MCP servers     | `.vscode/mcp.json`                   |
| Skills          | `.github/skills/{name}/SKILL.md`     |
| Session parsing | Built-in `copilot-cli` session parser |
| Default scope   | Project                              |

---

## Setup

### 1. Install the Agentic Resource Registry CLI

```bash
uv tool install observal-cli
# or: pipx install observal-cli
```

### 2. Authenticate

```bash
observal auth login
```

This writes credentials to `~/.observal/config.json`.

### 3. Pull an agent into Copilot

```bash
observal agent pull <agent-name> --harness copilot
```

By default, the agent is written to:

```
.github/agents/{name}.agent.md
```

---

## Config paths

| Purpose | Project scope |
| -------- | ------------- |
| Agent profile | `.github/agents/{name}.agent.md` |
| MCP config | `.vscode/mcp.json` |
| Skills | `.github/skills/{name}/SKILL.md` |
| Hook config | `.github/hooks/{name}.json` |

Copilot MCP configuration uses the `servers` key.

---

## Hook spec

### Event map

| Agentic Resource Registry event | Copilot event |
| -------------- | ------------- |
| `SessionStart` | `SessionStart` |
| `UserPromptSubmit` | `UserPromptSubmit` |
| `PreToolUse` | `PreToolUse` |
| `PostToolUse` | `PostToolUse` |
| `Stop` | `Stop` |

---

## Session parsing and delivery

Copilot uses the built-in `copilot-cli` server parser while retaining separate source adapters for VS Code Copilot and Copilot CLI. VS Code hook events are durably materialized as JSONL-compatible source records; Copilot CLI discovers its native `events.jsonl` sessions.

Both adapters route through the shared spool-first delivery engine. Hook execution writes to the durable outbox and defers network work, retries keep stable source indexes, and cursors advance only after contiguous server acknowledgement. Recovery uses the authenticated server checkpoint. Finalization audits the complete source hash and rewinds/replays a mismatched range.

---

## Caveats

**Only project scope is supported.** Agents are installed into
`.github/agents/`.

**Configuration is split across project directories.** MCP servers are stored
in `.vscode/mcp.json`, while hooks and skills live under `.github/`.

**Rules are not supported.** Copilot supports agent profiles, MCP servers,
skills, and hooks, but does not expose a rules configuration.
