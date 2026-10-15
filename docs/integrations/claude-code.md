<!-- SPDX-FileCopyrightText: 2026 Rishika Kaur <kaurrishika377@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Claude Code

Claude Code is a first-class Observal harness integration. Observal can install Claude Code agents, register MCP servers, add hooks, install skills, and collect session telemetry.

---

## Overview

Claude Code agent profiles are Markdown files with YAML frontmatter. Project agents live in `.claude/agents/`. User agents live in `~/.claude/agents/`.

Session telemetry is collected through the shared `observal_cli.hooks.session_push --harness claude-code` entry point, which runs on `UserPromptSubmit` and `Stop`. The hook reads Claude Code session JSONL files from `~/.claude/projects/<project>/<session_id>.jsonl`, reads only new lines since the last push, and sends them to Observal. Subagent transcripts under `<session_id>/subagents/` are picked up as related sources.

There are two ways the hooks get installed:

- `observal agent pull` embeds them in the generated agent profile's `hooks:` frontmatter.
- `observal doctor patch` writes them into `~/.claude/settings.json` for all Claude Code sessions.

---

## Supported capabilities

| Capability      | Support                                                                              |
| --------------- | ------------------------------------------------------------------------------------ |
| Agent profiles  | Project and user scope                                                               |
| Hook bridge     | `UserPromptSubmit` and `Stop`                                                        |
| Custom hooks    | Hook components attached to an agent are added to its `hooks:` frontmatter           |
| MCP servers     | Registered with `claude mcp add` and referenced from the agent's `mcpServers:` list  |
| Skills          | `.claude/skills/{name}/SKILL.md` and `~/.claude/skills/{name}/SKILL.md`              |
| Guidance files  | Scanned, never overwritten (see [Guidance files](#guidance-files))                   |
| Session parsing | Claude Code JSONL parser                                                             |
| Telemetry       | Session transcripts delivered through hooks and `observal reconcile`                 |

Custom hook events recognized by the registry: `PreToolUse`, `PostToolUse`, `Notification`, `Stop`, `SubagentStop`, `SessionStart`, `UserPromptSubmit`.

---

## Setup

### 1. Install the Observal CLI

```bash
uv tool install observal-cli
# or: pipx install observal-cli
```

### 2. Authenticate

```bash
observal auth login
```

This writes credentials to `~/.observal/config.json`.

### 3. Pull an agent into Claude Code

```bash
observal agent pull <agent-name> --harness claude-code
```

Claude Code's default scope is project scope. By default, the agent is written to `.claude/agents/{name}.md`.

To install into the current user's Claude Code configuration:

```bash
observal agent pull <agent-name> --harness claude-code --scope user
```

User agents are written to `~/.claude/agents/{name}.md`.

### 4. Install global session hooks (optional)

```bash
observal doctor patch
```

This reconciles Observal's hooks into `~/.claude/settings.json` without removing hooks you added yourself. Use `observal doctor cleanup` to remove them.

---

## Config paths

| Purpose             | Project scope                      | User scope                         |
| ------------------- | ---------------------------------- | ---------------------------------- |
| Agent profile       | `.claude/agents/{name}.md`         | `~/.claude/agents/{name}.md`       |
| Skill definition    | `.claude/skills/{name}/SKILL.md`   | `~/.claude/skills/{name}/SKILL.md` |
| Hook config         | `.claude/settings.json`            | `~/.claude/settings.json`          |
| Custom hook scripts | `.claude/hooks/`                   | `.claude/hooks/`                   |
| Session JSONL       | `~/.claude/projects/<project>/{session_id}.jsonl` | same                |
| Observal credentials | `~/.observal/config.json`         | `~/.observal/config.json`          |

Observal itself only writes hook config to `~/.claude/settings.json` (via `doctor patch`) and to agent frontmatter (via `agent pull`). `.claude/settings.json` is listed because Claude Code reads it, not because Observal manages it.

---

## Agent profile format

Observal generates Markdown agent profiles with YAML frontmatter:

```markdown
---
name: my-agent
description: "Agent description"
model: sonnet
tools: Read, Grep
mcpServers:
  - github
hooks:
  UserPromptSubmit:
    - hooks:
        - type: command
          command: "python3 -m observal_cli.hooks.session_push"
  Stop:
    - hooks:
        - type: command
          command: "python3 -m observal_cli.hooks.session_push"
---

Agent instructions go here.
```

`model`, `tools`, `color`, and `mcpServers` are only emitted when set. Model names containing `opus`, `sonnet`, or `haiku` are normalized to those aliases.

---

## Hook spec

`observal doctor patch` writes the following into `~/.claude/settings.json` (current `HOOKS_SPEC_VERSION` is `11`):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "_observal": { "version": "11" },
        "hooks": [
          {
            "type": "command",
            "command": "<observal-python> -m observal_cli.hooks.session_push --harness claude-code"
          }
        ]
      }
    ],
    "Stop": [
      {
        "_observal": { "version": "11" },
        "hooks": [
          {
            "type": "command",
            "command": "<observal-python> -m observal_cli.hooks.session_push --harness claude-code"
          }
        ]
      }
    ]
  }
}
```

`<observal-python>` is the Python interpreter that has `observal_cli` installed. If the package is not importable, the command is prefixed with `PYTHONPATH=<package root>`. No environment variables are written to `settings.json`.

---

## Session push behavior

The hook is designed never to break Claude Code: any error is logged and swallowed, and a missing `~/.observal/config.json` leaves the session local. New JSONL lines are delivered to the session ingestion endpoint on each prompt, and `Stop` finalizes the session.

Delivery uses a local outbox and resumes after transient network failures. Sessions missed by the hooks can be backfilled with:

```bash
observal reconcile
```

---

## MCP servers

`observal agent pull` does not write `.mcp.json`. It registers each MCP server in the agent with `claude mcp add` and lists it under `mcpServers:` in the agent profile.

Command-based servers:

```bash
claude mcp add --scope <scope> <name> -e KEY=value -- <command> <args...>
```

URL-based servers (`streamable-http` maps to Claude Code's `http` transport, `sse` stays `sse`):

```bash
claude mcp add --transport http --scope <scope> <name> <url> -H "Header: value"
```

A project-scope pull registers servers in Claude Code's `local` scope (this project, only you), running the command from the pull's `--dir`. A user-scope pull registers them in Claude Code's `user` scope, so the agent finds them in every project. Before registering, the pull runs `claude mcp remove --scope <scope> <name>`, so pulling again replaces a server of the same name in that scope instead of failing. Header and environment values are masked in the pull's output.

The `claude` CLI must be on `PATH`; if it is missing or a command fails, the pull reports an error after writing the agent files and does not record the install.

`observal scan` discovers MCP servers from the project `.mcp.json` and from the user's Claude Code configuration, including plugins.

---

## Skills

Skills from an agent are installed to `.claude/skills/{name}/SKILL.md` (project scope) or `~/.claude/skills/{name}/SKILL.md` (user scope), either by cloning the skill's git source or by writing its registry content. `observal scan` discovers skills in `~/.claude/skills/`. Standalone skill tracking for conflict detection covers user-scope skill files only (`~/.claude/skills/{name}/SKILL.md`).

---

## Guidance files

Claude Code reads these instruction files. Observal scans them but never overwrites them, and they are separate from Observal-managed agent profiles:

```text
CLAUDE.md
.claude/CLAUDE.md
~/.claude/CLAUDE.md
CLAUDE.local.md
```

---

## OTLP telemetry

Observal does not use Claude Code's OTLP telemetry. Do **not** configure these for Observal's Claude Code integration:

```text
OTEL_*
CLAUDE_CODE_ENABLE_TELEMETRY
```

Telemetry flows through session push hooks and `observal reconcile` only.

---

## Caveats

- Project scope is the default for `agent pull`. User scope needs `--scope user`.
- `agent pull` hooks use bare `python3`, so `python3` on `PATH` must be able to import `observal_cli`. `doctor patch` uses the exact interpreter of the Observal install.
- MCP registration depends on the `claude` CLI being installed.
- `doctor patch` only touches `~/.claude/settings.json`, not project settings.
- Session delivery depends on Claude Code's local JSONL files under `~/.claude/projects/`.
