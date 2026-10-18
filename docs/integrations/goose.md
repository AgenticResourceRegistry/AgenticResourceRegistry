---
# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Goose"
---

[Goose](https://goose-docs.ai/) is a first-class Observal harness integration.
Observal can install Goose custom agents, configure MCP extensions, expose
skills, install a Goose hook plugin, and collect Goose session telemetry.

Supported Goose versions: **1.10.0 and later** (the release that moved session
storage to SQLite), verified against **goose 1.45.0**. Goose CLI and Goose
Desktop share the same configuration, skills, agents, plugins, and session
database, so a single install covers both.

---

## Overview

Goose custom agents are Markdown files with YAML frontmatter. Project agents
live in `.agents/agents/`, user agents in `~/.agents/agents/`.

MCP servers are Goose *extensions*. Goose reads them from a single user-level
`config.yaml`, so Observal merges its extensions into that file and leaves every
other key — providers, models, global settings — untouched.

Session telemetry uses a Goose hook plugin. `observal doctor patch --harness
goose` writes `~/.agents/plugins/observal/`, containing a `plugin.json` manifest
and a `hooks/hooks.json` file. The hooks run
`observal_cli.hooks.session_push --harness goose`, which reads Goose's SQLite
session store and pushes new records through Observal's shared acknowledged
delivery pipeline.

---

## Supported capabilities

| Capability | Support |
|---|---|
| Agent profiles | Project and user scope |
| Hook bridge | Goose plugin (`hooks/hooks.json`) |
| Hook events | `SessionStart`, `UserPromptSubmit`, `Stop`, `SessionEnd` |
| MCP servers | `extensions` in `~/.config/goose/config.yaml` |
| MCP transports | `stdio` and `streamable_http` |
| Agent prompt | Registry prompts are written into the generated Goose agent file |
| Skills | `.agents/skills/{name}/SKILL.md` and `~/.agents/skills/{name}/SKILL.md` |
| Session parsing | SQLite session store mirrored to JSONL |
| Telemetry | Hook push plus `observal reconcile` |
| Model selection | Registry-backed Goose model catalog |

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

### 3. Pull an agent into Goose

```bash
observal agent pull <agent-name> --harness goose
```

Goose's default scope is user scope, so the agent is written to
`~/.agents/agents/{name}.md`. To install into the current project:

```bash
observal agent pull <agent-name> --harness goose --scope project
```

MCP extensions are always written to `~/.config/goose/config.yaml`, because
that is the only file Goose reads extensions from.

### 4. Install or refresh the Goose hook plugin

```bash
observal doctor patch --harness goose
```

Restart your Goose session afterwards so the plugin is discovered.

### 5. Check what is installed

```bash
observal scan --harness goose
observal doctor
```

---

## Config paths

| Purpose | Project scope | User scope |
|---|---|---|
| Agent profile | `.agents/agents/{name}.md` | `~/.agents/agents/{name}.md` |
| MCP extensions | — | `~/.config/goose/config.yaml` |
| Skill definition | `.agents/skills/{name}/SKILL.md` | `~/.agents/skills/{name}/SKILL.md` |
| Hook plugin | `.agents/plugins/observal/` | `~/.agents/plugins/observal/` |
| Session database | — | `~/.local/share/goose/sessions/sessions.db` |
| Observal credentials | `~/.observal/config.json` | `~/.observal/config.json` |
| Observal lockfile | `~/.observal/lockfile.json` | `~/.observal/lockfile.json` |

User-scope locations follow Goose's own resolution order:

1. `$GOOSE_PATH_ROOT` when set to an absolute path — config becomes
   `$GOOSE_PATH_ROOT/config`, data `$GOOSE_PATH_ROOT/data`, and skills, agents
   and plugins move to `$GOOSE_PATH_ROOT/.agents/`.
2. `$XDG_CONFIG_HOME` / `$XDG_DATA_HOME` when set.
3. `~/.config/goose` and `~/.local/share/goose`.

On Windows, Goose reads `%APPDATA%\Block\goose\config\config.yaml` and
`%APPDATA%\Block\goose\data\sessions\sessions.db`. Scanning, `doctor`, and
session reading resolve those locations automatically. `observal agent pull`
still writes the documented Unix paths, so Windows users should move the
generated `config.yaml` extensions into the `%APPDATA%` config by hand.

---

## Agent profile format

```markdown
---
name: my-agent
description: Reviews code for correctness and risk
model: gpt-5.5
---

You are a Goose agent with the following specialization...
```

Only `name` is required by Goose. `model` is emitted when the install resolves a
model from the Goose model catalog.

---

## Extension format

Observal writes MCP servers as Goose extensions:

```yaml
extensions:
  filesystem:
    type: stdio
    name: filesystem
    enabled: true
    cmd: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    envs: {}
    env_keys: []
    timeout: 300
  remote-tools:
    type: streamable_http
    name: remote-tools
    enabled: true
    uri: https://example.com/mcp
    headers: {}
    envs: {}
    env_keys: []
    timeout: 300
```

Observal writes MCP servers as `stdio` or `streamable_http` extensions and only
ever touches the entries it owns. `observal scan` additionally reports legacy
`sse` extensions. Goose's `builtin`, `platform` and `frontend` extensions run
in-process and `inline_python` carries code rather than a command, so none of
them are reported or rewritten.

---

## Hook plugin spec

`~/.agents/plugins/observal/hooks/hooks.json`:

```json
{
  "hooks": {
    "SessionStart":     [{ "hooks": [{ "type": "command", "command": "...", "timeout": 30 }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "...", "timeout": 30 }] }],
    "Stop":             [{ "hooks": [{ "type": "command", "command": "...", "timeout": 30 }] }],
    "SessionEnd":       [{ "hooks": [{ "type": "command", "command": "...", "timeout": 30 }] }]
  }
}
```

Rules deliberately omit `matcher`: Goose treats `matcher` as a regular
expression, so a bare `"*"` would be invalid and the rule would be skipped.

Goose runs every hook through `sh -c` on all platforms, passes the event JSON on
stdin, waits for the command, and enforces the rule's `timeout`. The Observal
hook therefore only reads Goose's database and appends to the local outbox; the
network upload is handed to a detached worker so a slow or unreachable Observal
server can never stall a Goose turn.

The hook writes nothing to stdout and never exits `2`, so it can never block a
`PreToolUse` tool call or a `Stop` turn boundary. `doctor patch` preserves any
non-Observal rules you add to the same plugin.

Goose records each discovered plugin under a `plugins` map in `config.yaml`
keyed by its absolute path, so the plugin activates itself on the next session.

To disable the plugin without removing it, add it to `disabledPlugins` in
`~/.config/goose/settings.json`. To remove it entirely:

```bash
observal doctor cleanup --harness goose
```

### Hook payload coverage

Verified against goose 1.45.0:

| Event | `working_dir` | Notes |
|---|---|---|
| `SessionStart` | no | Fires once per session |
| `UserPromptSubmit` | no | Carries the prompt in `message` |
| `Stop` | yes | Fires at the end of **every turn**, not the session |
| `SessionEnd` | no | Emitted by the goose **CLI** only |

Because most events omit `working_dir`, Observal reads the session's working
directory from `sessions.working_dir` instead, which keeps agent attribution and
layer hashing correct on every event.

---

## Session push behavior

Goose stores sessions in SQLite rather than JSONL, so the CLI projects each
session onto an append-only JSONL mirror at
`~/.observal/sessions/goose/<session_id>.jsonl`. Everything downstream — the
durable outbox, acknowledged delivery, checkpoint recovery, and the final
SHA-256 audit — is the shared engine used by every other harness.

1. A hook event resolves the Goose session id from the payload.
2. `sessions.db` is opened through a `file:...?mode=ro` URI with a busy timeout,
   so Observal never writes to, migrates, or checkpoints Goose's database, and
   never blocks a Goose session that is writing concurrently.
3. Rows newer than the mirror's cursor are appended as JSONL records. The mirror
   only grows, which keeps previously acknowledged byte offsets valid.
4. The batch is spooled to the local outbox and a detached worker performs the
   upload, so the hook returns in milliseconds regardless of server health.
5. `SessionEnd` appends a `session_end` boundary carrying the session's token
   and cost totals, then finalizes delivery with an integrity hash.
6. Delegated (subagent) sessions linked by `parent_session_id` are mirrored and
   delivered alongside their parent.

Mirror records:

| Record | Meaning |
|---|---|
| `{"type": "session", ...}` | Session metadata: name, working directory, provider, model, parent session |
| `{"type": "message", ...}` | One `messages` row: role, content blocks, per-message token usage |
| `{"type": "session_end", ...}` | Final boundary with accumulated token and cost totals |

Goose content blocks are parsed into trace events: `text`, `thinking`,
`redactedThinking`, `toolRequest`, `toolResponse` (including failures),
`toolConfirmationRequest`, and `error`.

### Backfilling existing sessions

```bash
observal reconcile --harness goose
observal reconcile --harness goose --since 720 --dry-run
```

Reconcile reads the same read-only database, mirrors any session updated within
the window, and drains it through the outbox.

---

## Caveats

**The hook plugin is shared per machine.** It is installed by `doctor patch`,
not by each agent pull.

**MCP extensions are user scope only.** Goose reads extensions from one
`config.yaml`, so `--scope project` still writes extensions to the user config.
Agents and skills honour the requested scope. Goose rewrites that same file when
it registers plugins, and neither Goose nor Observal preserves YAML comments in
it.

**Goose Desktop sessions finalize later.** Only the goose CLI emits
`SessionEnd`. Desktop sessions are still captured turn by turn through `Stop`;
their final integrity audit runs from background recovery or
`observal reconcile`.

**Legacy JSONL sessions are not imported.** Goose imports pre-1.10 `.jsonl`
files into `sessions.db` on upgrade, and Observal reads the database, so those
sessions are covered. Leftover `.jsonl` files on disk are ignored.

**Rewritten conversations replay.** `goose session --resume --edit` rewrites a
session's messages. The mirror is append-only, so the rewritten history is
appended rather than replacing what was already delivered.

**Late writes land on the next event.** If Goose persists a message after the
`SessionEnd` hook has read the database, that row is delivered by the next hook
event or by `observal reconcile`.
