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
`UserPromptSubmit` and `Stop` for incremental capture, plus `SessionEnd` for
late teardown metadata. All three use the shared
`observal_cli.hooks.session_push --harness kiro` entry point. `SessionEnd`
captures late records and credits; it does not authorize finalization.

Kiro's V3 engine and IDE read the standalone file; the IDE does not fire
inline agent hooks. CLI binary version alone does not establish the active
engine: CLI 2.x can run V2 or V3. Shared hooks can collect **all** Kiro sessions
in that scope, including ordinary conversations using MCPs or skills, subject
to the configured collection policy. Agent
attribution is separate: sessions without a verified registry agent remain
unattributed.

Inline hooks do **not** hide an agent from the IDE agent picker. What does is
`allowedTools` or `toolsSettings` without a `permissions` block: Kiro IDE 1.x
`ProfileLoader` rejects such a profile outright (`reasonCode cli_only_agent`).
Empty CLI-only placeholders are stripped on pull. Non-empty custom values
are preserved and require the native permissions configuration where applicable.

### Engine-aware hook placement

V2 uses inline `userPromptSubmit`/`stop` hooks in the agent profile, even when
an IDE is also installed. V3 uses enabled standalone coverage and omits covered
inline events. Global coverage also suppresses duplicate workspace hooks;
unrelated user hooks and profile settings are preserved. CLI 2.x compatibility
is retained for uncovered events. Ordinary V2 chats without an installed
agent's inline hooks can be backfilled with `observal reconcile --harness kiro`.

Engine detection checks `OBSERVAL_KIRO_ENGINE`, then the selected global
runtime's `settings/cli.json`, followed by the CLI setting and binary-version
fallback. Native Kiro's `chat.agentEngine` is **global-only**: a workspace
`.kiro/settings/cli.json` entry does not override it. Profile and hook paths
remain scoped to the target project, but neither the target's nor the caller's
workspace engine entry can select hook placement. This also holds when
`kiro-cli` is absent from the repair command's `PATH`.

See Kiro's [settings scopes](https://kiro.dev/docs/cli/reference/settings/#settings-scopes)
and [saved harness default](https://kiro.dev/docs/cli/agent-harness/#save-a-default-harness).

Configure the intended engine before pull or patch. After switching engines:

```bash
kiro-cli settings chat.agentEngine v3  # or v2
observal doctor patch --harness kiro --output json
```

A future `--v2`/`--v3` launch override is not known at profile-write time.
Patch with the matching `OBSERVAL_KIRO_ENGINE` first, then re-patch for the
saved engine afterward. An unpatched override is outside the single-invocation
guarantee. `OBSERVAL_KIRO_CLI_VERSION` and `OBSERVAL_KIRO_IDE` (`1`/`0`)
override binary/IDE detection, not the native global engine setting.

A temporary `OBSERVAL_KIRO_ENGINE` affects **every locked Kiro profile in the
current registry** during doctor patch, not only the agent for one launch. A
V3 override can strip inline hooks needed by other chats using the global V2 default. Do not patch
under the override while affected chats run, or start those chats until you
unset the override, re-run doctor patch using the same authentication/runtime
roots, and verify that their normal placements are restored.

The hook reads Kiro CLI JSONL files from `~/.kiro/sessions/cli/` and IDE
transcripts from workspace session directories. It sends new records since the
last push. Reconciliation backfills missed CLI and IDE sessions, including
separately stored delegated sub-executions.

---

## Supported capabilities

| Capability | Support |
|---|---|
| Agent profiles | Project and user scope |
| Hook bridge | `UserPromptSubmit`, `Stop`, and capture-only `SessionEnd` in standalone hooks |
| Custom hooks | `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `PostFileSave`, `PostFileCreate`, `PostFileDelete` |
| V2 engine | Inline agent hooks, including when an IDE is also installed |
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

Pulling an agent installs shared hooks for its scope. Repair an existing Kiro
runtime and locked profiles with:

```bash
observal doctor patch --harness kiro --output json
```

Doctor recognizes complete standalone prompt/Stop coverage even without agent
profiles. An older two-event file can still report installed, so refresh it
and verify an enabled `SessionEnd` command in the active scope before checking
automatic late credits. These shared hooks do not carry an agent UUID; V3
attribution comes from native session metadata. V2 keeps per-agent inline hooks.

---

## Config paths

| Purpose | Project scope | User scope |
|---|---|---|
| Agent profile | `.kiro/agents/{name}.json` | `~/.kiro/agents/{name}.json` |
| Guidance files | `.kiro/steering/*.md`, `AGENTS.md` | `~/.kiro/steering/*.md` |
| MCP config | `.kiro/settings/mcp.json` | `~/.kiro/settings/mcp.json` |
| Skill definition | `.kiro/skills/{name}/SKILL.md` | `~/.kiro/skills/{name}/SKILL.md` |
| Standalone hook config | `.kiro/hooks/observal.json` | `~/.kiro/hooks/observal.json` |
| Custom hook scripts | `.kiro/hooks/` | `~/.kiro/hooks/` |
| Legacy V2 session JSONL | `~/.kiro/sessions/cli/{session_id}.jsonl` | `~/.kiro/sessions/cli/{session_id}.jsonl` |
| Legacy V2 credit metadata | `~/.kiro/sessions/cli/{session_id}.json` | `~/.kiro/sessions/cli/{session_id}.json` |
| Agentic Resource Registry credentials | `~/.observal/config.json` | `~/.observal/config.json` |
| Last session cache | `~/.observal/.kiro-session` | `~/.observal/.kiro-session` |

Kiro MCP configs use the `mcpServers` key. V2 inline hooks additionally live
inside the agent profile. V3/IDE transcripts live under
`~/.kiro/sessions/{workspace}/{session_id}/messages.jsonl`, with a sibling
`session.json`; their late credits come from `usage_summary` records.

`KIRO_HOME` selects the native runtime root used by discovery, credits, and
doctor. It does not change Observal's authentication/outbox home or migrate
previously installed user profiles. Recheck agent availability when changing
runtime storage, and use the same runtime root when reconciling.

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
        "command": "python -I -m observal_cli.hooks.session_push --harness kiro --placement standalone"
      }
    },
    {
      "name": "observal-session-push-stop",
      "trigger": "Stop",
      "action": {
        "type": "command",
        "command": "python -I -m observal_cli.hooks.session_push --harness kiro --placement standalone"
      }
    },
    {
      "name": "observal-session-push-sessionend",
      "trigger": "SessionEnd",
      "action": {
        "type": "command",
        "command": "python -I -m observal_cli.hooks.session_push --harness kiro --placement standalone"
      }
    }
  ]
}
```

The command carries no `OBSERVAL_AGENT_ID`. One file per scope serves every
agent, so an id baked into it would attribute every session to whichever agent
was pulled last. Attribution comes from session metadata instead.

This example illustrates the schema, not a launcher to copy verbatim.
Pull and doctor generate commands using the active interpreter and an isolated
or source-safe launcher. The `--placement` tag lets runtime acceptance prefer
standalone capture for an observed V3 transcript without disabling V2 inline hooks.

### Legacy CLI 2.x inline hooks

The V2 engine reads hooks from the agent JSON. A CLI 2.x installation retains
inline compatibility; for configured V3, covered events are omitted rather than
invoked twice. The native global V2 setting also preserves inline delivery
when the repair command cannot detect a CLI binary; workspace engine entries
are ignored just as they are by Kiro. Inline hooks carry
`OBSERVAL_AGENT_ID` because an agent profile belongs to one agent:

```json
{
  "hooks": {
    "userPromptSubmit": [
      {
        "command": "OBSERVAL_AGENT_ID=<agent-uuid> python -I -m observal_cli.hooks.session_push --harness kiro --placement inline"
      }
    ]
  }
}
```

V2 can coexist with an IDE. Installing an IDE is not a reason to withhold the
V2 inline route. When V3 standalone coverage is missing or partial and inline
compatibility is available, only uncovered events retain an inline fallback.

### Attribution

The hooks file is shared by every agent, so the hook command cannot identify
one. Attribution is resolved from the session itself:

1. CLI V3 names the selected agent in a matching, bounded
   `session.json.agentMode`, before `session_start` necessarily exists.
   `session_start.agentType` and agent-scoped `hookId` records supply older
   transcript evidence. Conflicting companion/session-start identity remains
   unattributed rather than selecting another installed agent.
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
6. Advance the local cursor from the ACK. Explicit permanently rejected-gap
   recovery is reported separately and does not bypass final-authority checks.
7. Recover missing or corrupt local state from the authenticated server checkpoint.
8. On finalization, compare the SHA-256 audit manifest and replay any affected range.

V2 retains a delayed finalization worker. V3/IDE captures automatically but does
**not** rely on detached workers for verified finalization: native shutdown can
terminate hook descendants. `SessionEnd` captures late records, credits, and
available companion model metadata without marking the session final. Fresh
teardown metadata is spooled before drainage, so an older model-only batch can
be coalesced with credits before transport.

After a V3/IDE chat exits, finalize matching Stop intent from an independent terminal:

```bash
observal reconcile --harness kiro --since 24 --output json

# If the native runtime root was relocated:
KIRO_HOME=/absolute/path/used/by/kiro observal reconcile --harness kiro --since 24 --output json
```

Use the same Observal authentication home and native runtime root as the chat.
Both examples' `--since 24` window filters transcript modification time, including
sessions with pending Stop intent. Choose a window covering the oldest missed
transcript, such as `--since 168` for a week (supported range: 1–8,760 hours).
A fresh Stop intent does not include a transcript outside that window. The
window filters discovery, not already queued outbox drainage; zero discovered
sessions does not mean all pending finalization is complete.

Reconciliation is an integrity-finalization step, not a prerequisite for agent
attribution or automatic credits. A capture ACK, scheduled worker, or process
exit is not completion evidence. Quiet/permission-paused sessions without a
matching Stop remain non-final.

Stop authority is tied to a generation and prompt fingerprint. A resumed prompt
cancels old authority, including queued final semantics and waiting V2 workers,
without deleting queued records or credits. Old unscoped queued finals remain
deliverable but are not silently granted new authority during an upgrade; drain
pending deliveries before upgrading when possible. Verify final cursors and
retired Stop intent rather than inferring completion from successful capture.
In reconcile JSON, `pushed` reports record delivery and can accompany a final
metadata send; it is not by itself proof of local finalization.

For read-only local inspection, use the shipped `read_cursor_status` helper and
a SQLite connection opened with `mode=ro`, as shown in the
[packaged recovery reference](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/observal_cli/skills/observal-advanced/references/recovery-workflows.md#inspect-local-kiro-completion-read-only).
Check each collected parent/child cursor's validity, finalized flag and source
coverage, the matching Stop intent, and queued batches under the same auth/runtime
roots. The recipe does not contact ingestion, drain/migrate the outbox, or modify
cursors/intents. Support bundles and aggregate telemetry counts alone do not expose
all three local facts, and missing state must not be treated as completion.

---

## Agent profile format

Kiro agent profiles are JSON. A simplified profile looks like:

```json
{
  "name": "my-agent",
  "model": "auto",
  "prompt": "You are a Kiro agent with the following specialization...",
  "tools": []
}
```

The `model` field is present when a model is resolved for the agent. Native
permissions, upgrade state, and unrelated user hooks are preserved when
refreshing an existing profile.

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

**Standalone hooks are per scope, not per agent.** Pull and doctor refresh the
shared `observal.json` file; doctor can repair an existing native root without
locked agents. Global V3 coverage omits duplicate project events, and doctor
skips creating a new empty owned file. Refresh old two-event files before
validating teardown credits. V2 additionally needs inline per-agent hooks
carrying `OBSERVAL_AGENT_ID`.

**Default scope is user.** `observal agent pull <agent-name> --harness kiro`
writes to `~/.kiro/agents/` unless `--scope project` is set.

**No Claude Code subagent layout.** Kiro uses its native V2/V3 transcript
layouts and V3 delegated sub-executions, not Claude Code's `subagents/` directory.

**MCP config is Kiro-specific.** Kiro uses `.kiro/settings/mcp.json` and
`~/.kiro/settings/mcp.json`, not Claude Code MCP paths.
