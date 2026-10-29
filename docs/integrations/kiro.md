---
# SPDX-FileCopyrightText: 2026 Rajat <rajattempest8736@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Kiro"
---

Kiro is a first-class Agentic Resource Registry harness integration. Agentic Resource Registry can install Kiro agents,
configure MCP servers, add hooks, expose skills, and collect Kiro session telemetry.

---

## Overview

Kiro agent profiles are JSON files. Project agents live in `.kiro/agents/`.
User agents live in `~/.kiro/agents/`.

When Agentic Resource Registry installs a Kiro agent, it writes telemetry hooks into a standalone
hooks file — `~/.kiro/hooks/observal.json` (user scope) or
`.kiro/hooks/observal.json` (project scope) — in the v1 schema, using the
`UserPromptSubmit` and `Stop` triggers. Both hooks run the shared
`observal_cli.hooks.session_push --harness kiro` entry point.

Kiro IDE 1.0 and CLI 3.0 read the standalone file; the IDE does not fire
inline agent hooks. Once installed, shared hooks collect **all** Kiro sessions
in that scope, including ordinary conversations using MCPs or skills. Agent
attribution is separate: sessions without a verified registry agent remain
unattributed.

Inline hooks do **not** hide an agent from the IDE agent picker. What does is
`allowedTools` or `toolsSettings` without a `permissions` block: Kiro IDE 1.x
`ProfileLoader` rejects such a profile outright (`reasonCode cli_only_agent`).
Agentic Resource Registry no longer emits either field and strips them on pull.

### Legacy Kiro CLI 2.x

Kiro CLI 2.x only understands inline agent hooks. When Agentic Resource Registry detects CLI
2.x, it additionally writes legacy inline `userPromptSubmit`/`stop` hooks into
the agent JSON, even when the IDE is installed. Ordinary CLI 2.x sessions do
not fire these agent-specific hooks; use `observal reconcile --harness kiro`
to backfill them.

Detection can be overridden with `OBSERVAL_KIRO_CLI_VERSION` and
`OBSERVAL_KIRO_IDE` (`1`/`0`).

The hook reads Kiro CLI JSONL files from `~/.kiro/sessions/cli/` and IDE
transcripts from workspace session directories. It sends new records since the
last push. Reconciliation backfills missed CLI and IDE sessions, including
separately stored delegated sub-executions.

---

## Supported capabilities

| Capability | Support |
|---|---|
| Agent profiles | Project and user scope |
| Hook bridge | `UserPromptSubmit` and `Stop` in `.kiro/hooks/observal.json` |
| Custom hooks | `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `PostFileSave`, `PostFileCreate`, `PostFileDelete` |
| Legacy CLI 2.x | Inline agent hooks, only when no Kiro IDE is installed |
| MCP servers | `.kiro/settings/mcp.json` and `~/.kiro/settings/mcp.json` |
| Agent prompt | Registry prompts are embedded in the generated Kiro agent profile |
| Guidance files | Scanned from steering files and `AGENTS.md`, not overwritten |
| Skills | `.kiro/skills/{name}/SKILL.md` and `~/.kiro/skills/{name}/SKILL.md` |
| Session parsing | Kiro JSONL parser |
| Telemetry | Kiro session transcripts delivered through hooks and reconciliation |
| Model selection | Registry-backed Kiro model catalog |

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

### 3. Pull an agent into Kiro

```bash
observal agent pull <agent-name> --harness kiro
```

Kiro's default scope is user scope. By default, the agent is written to
`~/.kiro/agents/{name}.json`.

To install into the current project:

```bash
observal agent pull <agent-name> --harness kiro --scope project
```

Project agents are written to `.kiro/agents/{name}.json`.

### 4. Refresh Kiro hooks

Pull the agent again to refresh its Agentic Resource Registry hook commands.

`doctor patch` does not install generic Kiro hooks: pulling an agent installs
shared hooks for its scope. These hooks do not carry that agent's UUID and
capture ordinary sessions too. CLI 2.x still needs per-agent inline hooks.

---

## Config paths

| Purpose | Project scope | User scope |
|---|---|---|
| Agent profile | `.kiro/agents/{name}.json` | `~/.kiro/agents/{name}.json` |
| Guidance files | `.kiro/steering/*.md`, `AGENTS.md` | `~/.kiro/steering/*.md` |
| MCP config | `.kiro/settings/mcp.json` | `~/.kiro/settings/mcp.json` |
| Skill definition | `.kiro/skills/{name}/SKILL.md` | `~/.kiro/skills/{name}/SKILL.md` |
| Hook config | Embedded in `.kiro/agents/{name}.json` | Embedded in `~/.kiro/agents/{name}.json` |
| Custom hook scripts | `.kiro/hooks/` | `~/.kiro/hooks/` |
| Session JSONL | `~/.kiro/sessions/cli/{session_id}.jsonl` | `~/.kiro/sessions/cli/{session_id}.jsonl` |
| Credit metadata | `~/.kiro/sessions/cli/{session_id}.json` | `~/.kiro/sessions/cli/{session_id}.json` |
| Agentic Resource Registry credentials | `~/.observal/config.json` | `~/.observal/config.json` |
| Last session cache | `~/.observal/.kiro-session` | `~/.observal/.kiro-session` |

Kiro MCP configs use the `mcpServers` key.

---

## Hook spec

Agentic Resource Registry writes the telemetry hooks to the standalone v1 hooks file —
`~/.kiro/hooks/observal.json` (user scope) or `.kiro/hooks/observal.json`
(project scope):

```json
{
  "version": "v1",
  "hooks": [
    {
      "name": "observal-session-push-userpromptsubmit",
      "trigger": "UserPromptSubmit",
      "action": {
        "type": "command",
        "command": "python -m observal_cli.hooks.session_push --harness kiro"
      }
    },
    {
      "name": "observal-session-push-stop",
      "trigger": "Stop",
      "action": {
        "type": "command",
        "command": "python -m observal_cli.hooks.session_push --harness kiro"
      }
    }
  ]
}
```

The command carries no `OBSERVAL_AGENT_ID`. One file per scope serves every
agent, so an id baked into it would attribute every session to whichever agent
was pulled last. Attribution comes from session metadata instead.

On non-Windows platforms, generated server config may use `python3` instead of
`python`. During `observal agent pull`, the CLI rewrites Agentic Resource Registry hook commands to use
the active Python interpreter.

### Legacy CLI 2.x inline hooks

Kiro CLI 2.x predates the standalone file and reads hooks only from the agent
JSON. On a machine whose `kiro-cli` reports major version 2, Agentic Resource Registry *also*
writes inline `userPromptSubmit`/`stop` hooks into each agent profile, carrying
`OBSERVAL_AGENT_ID` since an agent profile belongs to exactly one agent:

```json
{
  "hooks": {
    "userPromptSubmit": [
      {
        "command": "OBSERVAL_AGENT_ID=<agent-uuid> python -m observal_cli.hooks.session_push --harness kiro"
      }
    ]
  }
}
```

Both formats are written on such machines. Having the IDE installed is not a
reason to withhold the inline copy: the two surfaces coexist, and the IDE loads
an agent carrying inline hooks and simply never fires them.

### Attribution

The hooks file is shared by every agent, so the hook command cannot identify
one. Attribution is resolved from the session itself:

1. A session started as an agent — picked from the IDE's agent dropdown —
   records that agent in `session_start.agentType`, and the agent's own profile
   hooks fire with an agent-scoped `hookId`. Either names the agent.
2. A session that delegates records `sub_agent_start` with `subAgentName`, and
   the delegated agent's work is written to a separate child transcript. The
   parent conversation is collected but is **not** attributed to the delegate.
3. CLI sessions carry the active agent in the session metadata written beside
   the transcript.
4. The CLI selects the active server URL under `registries` in
   `~/.observal/lockfile.json`, then looks up that name under that registry's
   `kiro` harness.
5. A verified agent receives its lockfile id and version. Ordinary sessions
   and unknown agents are still collected, with no agent id rather than a
   guessed one. Legacy CLI 2.x inline hooks may carry `OBSERVAL_AGENT_ID`.

### Event map

| Agentic Resource Registry event | Kiro event |
|---|---|
| `SessionStart` | `agentSpawn` |
| `UserPromptSubmit` | `userPromptSubmit` |
| `PreToolUse` | `preToolUse` |
| `PostToolUse` | `postToolUse` |
| `Stop` | `stop` |

`preToolUse` and `postToolUse` hooks can include a `matcher`. Agentic Resource Registry uses `*`
when no matcher is set.

---

## Session push behavior

Kiro uses the shared acknowledged session delivery engine:

1. Resolve the Kiro session ID from the hook payload or `~/.observal/.kiro-session`.
2. Find `~/.kiro/sessions/cli/{session_id}.jsonl` through the Kiro adapter.
3. Read complete records after the acknowledged byte/line cursor.
4. Persist new batches to `~/.observal/telemetry_buffer.db` before network delivery.
5. Retry batches idempotently until the server returns a contiguous checkpoint covering them.
6. Advance the local cursor only to that acknowledged checkpoint.
7. Recover missing or corrupt local state from the authenticated server checkpoint.
8. On finalization, compare the SHA-256 audit manifest and replay any affected range.

On `stop`, a delayed stable-file pass captures late records and finalizes the cursor. The adapter also reads `~/.kiro/sessions/cli/{session_id}.json` and durably sends Kiro credit usage, including when no transcript lines were added by the final hook.

---

## Agent profile format

Agentic Resource Registry generates Markdown agent profiles like this:

```markdown
---
name: my-agent
model: claude-sonnet-4
---

You are a Kiro agent with the following specialization...
```

The `model` field is present when a model is resolved for the agent.

---

## Skill file format

Kiro skills live at:

| Scope | Path |
|---|---|
| Project | `.kiro/skills/{name}/SKILL.md` |
| User | `~/.kiro/skills/{name}/SKILL.md` |

Example:

```markdown
---
description: "Runs the project test suite"
task_type: testing
---

# Run Tests

Run `pytest -q` from the project root.
```

---

## Caveats

**Guidance files are scan-only.** Agentic Resource Registry layers Kiro steering files and
`AGENTS.md` as context, but does not overwrite them during pull.

**Hooks are per scope, not per agent.** Pulling an agent refreshes the shared
`observal.json` hooks file for that scope, which then serves every Kiro agent
there — so the file exists even when no agent is locked. Pull the agent again
to replace an older Kiro-specific push command with the shared acknowledged
exporter. On legacy CLI 2.x, inline per-agent hooks carrying
`OBSERVAL_AGENT_ID` are written in addition.

**Default scope is user.** `observal agent pull <agent-name> --harness kiro`
writes to `~/.kiro/agents/` unless `--scope project` is set.

**No Claude Code subagent layout.** Kiro reads
`~/.kiro/sessions/cli/{session_id}.jsonl`. It does not scan Claude Code's
`subagents/` directory.

**MCP config is Kiro-specific.** Kiro uses `.kiro/settings/mcp.json` and
`~/.kiro/settings/mcp.json`, not Claude Code MCP paths.
