---
# SPDX-FileCopyrightText: 2026 Faatih <22oo1cso41faatih@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Cursor"
---

Cursor is a first-class Agentic Resource Registry harness integration. Agentic Resource Registry can install Cursor agents,
configure MCP servers, and collect Cursor session telemetry.

---

## Overview

Cursor agent profiles are Markdown files. Project agents live in `.cursor/agents/`.
User agents live in `~/.cursor/agents/`.

Cursor supports project and user installation scopes. MCP servers, hooks,
and agent profiles are managed under the `.cursor` configuration directory.

---

## Supported capabilities

| Capability      | Support                                                     |
| --------------- | ----------------------------------------------------------- |
| Agent profiles  | Project and user scope                                      |
| Hook bridge     | Supported                                                   |
| MCP servers     | `.cursor/mcp.json` and `~/.cursor/mcp.json`                 |
| Session parsing | Built-in Cursor session parser                              |
| Default scope   | Project                                                     |

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

### 3. Pull an agent into Cursor

```bash
observal agent pull <agent-name> --harness cursor
```

Cursor's default scope is project scope. By default, the agent is written to
`.cursor/agents/{name}.md`.

To install into your user configuration:

```bash
observal agent pull <agent-name> --harness cursor --scope user
```

User agents are written to `~/.cursor/agents/{name}.md`.

---

## Config paths

| Purpose       | Project scope                    | User scope                         |
| ------------- | -------------------------------- | ---------------------------------- |
| Agent profile | `.cursor/agents/{name}.md`       | `~/.cursor/agents/{name}.md`       |
| MCP config    | `.cursor/mcp.json`               | `~/.cursor/mcp.json`               |
| Skills        | `.cursor/skills/{name}/SKILL.md` | `~/.cursor/skills/{name}/SKILL.md` |
| Hook config   | `.cursor/hooks.json`             | `~/.cursor/hooks.json`             |

Cursor MCP configs use the `mcpServers` key.

---

## Hook spec

### Event map

| Agentic Resource Registry event     | Cursor event         |
| ------------------ | -------------------- |
| `PreToolUse`       | `preToolUse`         |
| `PostToolUse`      | `postToolUse`        |
| `Stop`             | `sessionEnd`         |
| `SessionStart`     | `sessionStart`       |
| `UserPromptSubmit` | `beforeSubmitPrompt` |
| `SubagentStop`     | `subagentStop`       |

---

## Session delivery and parsing

Cursor keeps its built-in `cursor` parser. Its adapter resolves the transcript path supplied by Cursor, with project-directory discovery as a fallback, and discovers separately stored subagent transcripts.

Cursor hook timeouts require a split delivery path: the hook synchronously writes complete transcript records and Stop-event token usage into the durable SQLite outbox, then a detached shared worker performs network delivery. The local byte/line cursor is not advanced by the hook or worker launch; it advances only after the server acknowledges a contiguous checkpoint. A delayed finalizer captures records written after Cursor's Stop event, restores stale local state from the server checkpoint, and performs the final hash audit. Audit mismatches rewind and replay the affected range.

---

## Caveats

**Default scope is project.** `observal agent pull <agent-name> --harness cursor`
writes to `.cursor/agents/` unless `--scope user` is specified.

**Configuration lives under `.cursor`.** Agent profiles, MCP configuration,
skills, and hooks are stored in the `.cursor` directory for project
installs and under `~/.cursor` for user installs.

**Auto model sentinel configuration is not available.**
