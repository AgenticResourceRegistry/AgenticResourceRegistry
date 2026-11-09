---
# SPDX-FileCopyrightText: 2026 Rishika Kaur <kaurrishika377@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Claude Code"
description: "Install and activate a registry agent in Claude Code, with scope and session-capture details."
---

Claude Code is a first-class Agentic Resource Registry harness integration. Agentic Resource Registry can install Claude Code agents, register MCP servers, add hooks, install skills, and collect session telemetry.

---

## Overview

Claude Code agent profiles are Markdown files with YAML frontmatter. Project agents live in `.claude/agents/`. User agents live in `~/.claude/agents/`.

Session telemetry is collected through the shared `observal_cli.hooks.session_push --harness claude-code` entry point, which runs on `UserPromptSubmit` and `Stop`. The hook reads Claude Code session JSONL files from `~/.claude/projects/<project>/<session_id>.jsonl`, reads only new lines since the last push, and sends them to Agentic Resource Registry. Subagent transcripts under `<session_id>/subagents/` are picked up as related sources.

There are two ways the hooks get installed:

- `observal agent pull` embeds them in the generated agent profile's `hooks:` frontmatter.
- `observal doctor patch` writes them into `~/.claude/settings.json` for all Claude Code sessions.

A session is credited to an agent only when the agent ran. That means a session started with `claude --agent <name>` (or the `agent` setting), or a subagent transcript whose agent type is a pulled agent. A pulled agent in the folder doesn't claim the other sessions started there.

---

## Supported capabilities

| Capability      | Support                                                                              |
| --------------- | ------------------------------------------------------------------------------------ |
| Agent profiles  | Project and user scope                                                               |
| Hook bridge     | `UserPromptSubmit` and `Stop`                                                        |
| Custom hooks    | Hook components attached to an agent are added to its `hooks:` frontmatter, or (opt-in) gated in `settings.json` |
| MCP servers     | Registered with `claude mcp add` and referenced from the agent's `mcpServers:` list  |
| Skills          | `.claude/skills/{name}/SKILL.md` and `~/.claude/skills/{name}/SKILL.md`              |
| Guidance files  | Scanned, never overwritten (see [Guidance files](#guidance-files))                   |
| Session parsing | Claude Code JSONL parser                                                             |
| Telemetry       | Session transcripts delivered through hooks and `observal reconcile`                 |

Custom hook events recognized by the registry: `PreToolUse`, `PostToolUse`, `Notification`, `Stop`, `SubagentStop`, `SessionStart`, `UserPromptSubmit`.

---

## Setup

### 1. Install the Agentic Resource Registry CLI

```bash
uv tool install 'observal-cli==1.16.0'
# or: pipx install 'observal-cli==1.16.0'
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

### 4. Activate the agent

Start a new Claude Code session in the target project with the generated agent filename, without `.md`:

```bash
claude --agent LOCAL_NAME
```

Replace `LOCAL_NAME` with the name in the path printed by the pull. A profile on disk does not automatically select the agent in an existing session. For a complete first-install walkthrough, see [Pull your first agent](/getting-started/quickstart).

### 5. Optional: install global session hooks

Human-mode `observal auth login` can install these automatically when Claude Code is already present, unless setup was skipped. To install or repair them explicitly:

```bash
observal doctor patch --harness claude-code
```

This reconciles Agentic Resource Registry's hooks into `~/.claude/settings.json` without removing hooks you added yourself. Global hooks can deliver every Claude Code session to the configured registry, not only sessions using a pulled agent. Review your team's data-handling policy before enabling them. Pull-generated agent hooks can already report sessions without this global setup. Use `observal doctor cleanup` to remove Registry-managed global instrumentation; it is not an uninstall command for the pulled agent.

---

## Config paths

| Purpose             | Project scope                      | User scope                         |
| ------------------- | ---------------------------------- | ---------------------------------- |
| Agent profile       | `.claude/agents/{name}.md`         | `~/.claude/agents/{name}.md`       |
| Skill definition    | `.claude/skills/{name}/SKILL.md`   | `~/.claude/skills/{name}/SKILL.md` |
| Gated agent command hooks (opt-in) | `.claude/settings.local.json` | `~/.claude/settings.json` |
| Custom hook scripts | `.claude/hooks/`                   | `.claude/hooks/`                   |
| Session JSONL       | `~/.claude/projects/<project>/{session_id}.jsonl` | same                |
| Agentic Resource Registry credentials | `~/.observal/config.json`         | `~/.observal/config.json`          |

Agentic Resource Registry writes hook config to `~/.claude/settings.json` (via `doctor patch`) and to agent frontmatter (via `agent pull`). It writes an agent's hooks to `.claude/settings.local.json` or `~/.claude/settings.json` only when you opt in with `agent pull --hooks=settings` (see [Agent hooks in headless sessions](#agent-hooks-in-headless-sessions)); otherwise those files are listed because Claude Code reads them.

---

## Agent profile format

Agentic Resource Registry generates Markdown agent profiles with YAML frontmatter:

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
          command: "<registry-launcher> --harness claude-code"
  Stop:
    - hooks:
        - type: command
          command: "<registry-launcher> --harness claude-code"
---

Agent instructions go here.
```

`<registry-launcher>` is illustrative, not a command to copy. Pull localizes the registry's hook launcher to the CLI installation on this machine; see [Hook launchers](#hook-launchers).

`model`, `tools`, `color`, and `mcpServers` are only emitted when set. Model names containing `opus`, `sonnet`, or `haiku` are normalized to those aliases.

---

## Hook launchers

`observal doctor patch --harness claude-code` reconciles the registry's global `UserPromptSubmit` and `Stop` hooks. Both invoke `observal_cli.hooks.session_push --harness claude-code`. Pull-generated agent hooks use the same session-push module, localized by the CLI before the profile is written.

On macOS/Linux, registry launchers use the CLI's actual Python interpreter:

- Installed package: isolated `-I` execution, ignoring inherited `PYTHON*` variables and the working directory.
- Source checkout: `-P` execution with an explicit `PYTHONPATH` for the trusted package root, rather than inheriting arbitrary project imports.

Windows launchers use the corresponding isolated command/bootstrap form. The server's bare `python3` placeholder is not a requirement that your shell's `python3` can import the CLI: pull rewrites it for the local installation.

### Inspect the generated hooks

```bash
observal doctor patch --harness claude-code --dry-run
```

Read the planned commands and settings changes. Registry-owned groups carry `_observal` version metadata. That value tracks the installed hook specification, not the Claude Code version. The source definition lives in `observal_cli/harness_specs/claude_code_hooks_spec.py`; do not maintain a second “current version” number by copying an example from these docs.

If the CLI installation or its interpreter moves, regenerate its managed hooks and re-pull affected agents, then inspect the plan again. Do not fix a launcher by adding arbitrary directories to your global Python path.

---

## Agent hooks in headless sessions

Claude Code runs hooks from an agent's frontmatter only in interactive sessions. In headless runs (`claude -p --agent …`, including Agentic Resource Registry delegation) they don't run. Pulling with `--hooks=settings` moves the agent's command hooks into `.claude/settings.local.json` (or `~/.claude/settings.json` for user scope), each wrapped by `observal_cli.hook_gate`. The gate runs a hook only while that agent is active, in interactive and headless sessions, and does nothing in other sessions.

```bash
observal agent pull alice/reviewer --harness claude-code --hooks=settings --dry-run
observal agent pull alice/reviewer --harness claude-code --hooks=settings
observal agent pull alice/reviewer --harness claude-code --hooks=frontmatter   # move them back
```

Each gated hook adds startup time on every matching event (about 40 ms on an Apple M1), even when the agent isn't active; the dry run measures it on your machine. Hook timeouts are not changed. The agent file no longer contains those hooks, so update any script that reads them there. The opt-in is POSIX-only. It was tested on Claude Code 2.1.286; other versions are allowed with a warning, and their sessions' hook evidence is reported as unverified rather than counted. See the [pull reference](../cli/pull.md#agent-hooks-in-headless-sessions) for ownership, conflicts and the `--on-unknown` policy.

### Maintainer review boundary: gated Agent hooks

Review this opt-in independently of ordinary telemetry hooks. `cmd_pull.py` validates flags, remembers placement, asks the server for hook bindings and **plans settings changes before writing installation files**. `agent_hooks.py` maps each binding to exactly one pinned component, reconciles only owned groups and preserves edited groups unless `--force-hooks` is explicit. `hook_gate.py` checks the harness-written `agent_type`; absent agent identity in a valid plain-session event skips, and malformed/unknown input skips by default (the explicit `--on-unknown=run` policy trades isolation for fail-open execution). Doctor must not delete these Agent-owned groups. The layer verifier must accept only the exact unedited installed group, not just a lockfile claim. Projection eligibility must separate frontmatter/headless, gated/active, unknown agent and untested Claude Code version; an unverified version is **not a zero-use session**.

Start review with `tests/test_agent_hooks_pull.py`, `tests/test_hook_gate.py`, `tests/test_agent_hooks.py`, and the hook-verification and projection suites. Check that dry runs write nothing, invalid settings reject before install, a default pull never adds a settings group, an explicit return to frontmatter removes only owned groups, and an edited group stays intact. These tests verify the implementation against recorded inputs; they do not prove that a future Claude Code release preserves hook-input semantics. Expand the tested version range only after new recordings.

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

Claude Code reads these instruction files. Agentic Resource Registry scans them but never overwrites them, and they are separate from Agentic Resource Registry-managed agent profiles:

```text
CLAUDE.md
.claude/CLAUDE.md
~/.claude/CLAUDE.md
CLAUDE.local.md
```

---

## OTLP telemetry

Agentic Resource Registry does not use Claude Code's OTLP telemetry. Do **not** configure these for Agentic Resource Registry's Claude Code integration:

```text
OTEL_*
CLAUDE_CODE_ENABLE_TELEMETRY
```

Telemetry flows through session push hooks and `observal reconcile` only.

---

## Caveats

- Project scope is the default for `agent pull`. User scope needs `--scope user`.
- Pull and doctor localize registry launchers to the CLI installation. Reinstalling or moving that installation may require regenerating hooks; see [Hook launchers](#hook-launchers).
- MCP registration depends on the `claude` CLI being installed.
- `doctor patch` only touches `~/.claude/settings.json`, not project settings.
- Session delivery depends on Claude Code's local JSONL files under `~/.claude/projects/`.
