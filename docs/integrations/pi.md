---
# SPDX-FileCopyrightText: 2026 Dheirav <dheirav2005@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Pi"
---

Pi is a first-class Observal harness integration. Observal can install Pi agent
profiles, configure MCP servers, expose skills, install the telemetry extension,
and collect Pi session telemetry.

---

## Overview

Pi is harness-centric: the whole of Pi is one agent. There is no per-agent
profile file the way Kiro or OpenCode have one. Instead, Observal writes the
agent's rules into `AGENTS.md`, which becomes Pi's system prompt, and writes
MCP servers and skills next to it.

To let several registry agents coexist, a user-scope `observal agent pull`
writes each agent into its own profile directory under
`~/.pi/agent/agents/{agent}/`. The Observal extension's `/agent` command swaps a
profile into the live `~/.pi/agent/` location. A project-scope pull writes the
rules straight to the project's `AGENTS.md`, which Pi reads directly.

Pi session telemetry uses an in-process TypeScript extension,
`~/.pi/agent/extensions/observal.ts`. It is installed by `observal doctor patch`
and is shared by every agent; agent pulls do not embed telemetry hooks.

---

## Supported capabilities

| Capability | Support |
|---|---|
| Agent profiles | Project and user scope, as `AGENTS.md` |
| Hook bridge | Pi extension (no shell hooks) |
| Extension events | `session_start`, `agent_end`, `session_shutdown` |
| MCP servers | `.pi/mcp.json` and `~/.pi/agent/mcp.json` |
| Agent prompt | Registry rules are written into the generated `AGENTS.md` |
| Guidance files | Scanned from `AGENTS.md`, `~/.pi/agent/AGENTS.md`, `.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md` |
| Skills | `.pi/skills/{name}/SKILL.md` and `~/.pi/agent/skills/{name}/SKILL.md` |
| Session parsing | Pi JSONL parser, including result-linked MCP invocation extraction |
| MCP component insights | Observed calls for verified, uniquely matched MCP servers with `pi-mcp-adapter`; otherwise coverage reports unavailable attribution, not zero use |
| Telemetry | Pi session transcripts delivered through the extension; `observal reconcile --harness pi` re-pushes recent sessions found under the Pi agent directory's `sessions/` folder |
| Model selection | Registry-backed Pi model catalog (`observal registry models list --harness pi`) |

---

## Setup

### 1. Install the Observal CLI

```bash
uv tool install observal-cli
# or: pipx install observal-cli
```

Pi 0.74.0 or newer is required by the telemetry extension.

### 2. Authenticate

```bash
observal auth login
```

This writes credentials to `~/.observal/config.json`. Unless you pass
`--no-setup` or `--output json`, login then installs the bundled Observal skills
into every detected harness (Pi counts once `~/.pi/` exists) and runs
`observal doctor`, which warns if the Pi telemetry extension is missing or
stale. A healthy install prints no Pi warning.

### 3. Pull an agent into Pi

```bash
observal agent pull <agent-name> --harness pi
```

Pi's default scope is user scope. The agent is written to
`~/.pi/agent/agents/{agent-name}/`.

To install into the current project:

```bash
observal agent pull <agent-name> --harness pi --scope project
```

Project pulls write the rules to `AGENTS.md` in the project root, which Pi
reads directly, and the agent's MCP servers and skills to
`.pi/agents/{agent-name}/`. Pi loads project MCP servers from `.pi/mcp.json`
and project skills from `.pi/skills/{name}/SKILL.md`, and `/agent` handles only
user-scope profiles, so activate them by hand:

```bash
cp .pi/agents/<agent-name>/mcp.json .pi/mcp.json
cp -r .pi/agents/<agent-name>/skills/. .pi/skills/
```

Merge instead of copying if `.pi/mcp.json` already lists other servers.

### 4. Install or refresh the telemetry extension

```bash
observal doctor patch --harness pi
```

This writes the bundled extension to `~/.pi/agent/extensions/observal.ts` when
it is missing, and refreshes a copy Observal recognises as its own when that
copy has fallen behind or been edited, recording the CLI version it came from
in an adjacent `.observal-extension.json`. A file Observal did not write is
reported and left alone.

If `npm:observal-pi` is registered in `~/.pi/agent/settings.json`, that takes
precedence: nothing is installed locally, and a local copy Observal recognises
as its own is removed (kept as `observal.ts.bak`) so Pi does not load the
extension twice. A local file Observal did not write is left alone. Restart Pi
or run `/reload` afterwards.

`doctor patch` refuses to run until `observal auth login` has written a server
URL, although it does not contact the server.

### 5. Activate the agent inside Pi

Inside a Pi session, run `/agent` and pick the pulled agent. See
[Agent profiles and swapping](#agent-profiles-and-swapping).

### 6. Check what is installed

```bash
observal scan --harness pi
observal doctor
```

---

## Config paths

| Purpose | Project scope | User scope |
|---|---|---|
| Agent rules | `AGENTS.md` (project root) | `~/.pi/agent/agents/{agent}/AGENTS.md` |
| MCP config | `.pi/agents/{agent}/mcp.json` | `~/.pi/agent/agents/{agent}/mcp.json` |
| Skill definition | `.pi/agents/{agent}/skills/{name}/SKILL.md` | `~/.pi/agent/agents/{agent}/skills/{name}/SKILL.md` |
| Active agent rules | `AGENTS.md` | `~/.pi/agent/AGENTS.md` |
| Active MCP config | `.pi/mcp.json` | `~/.pi/agent/mcp.json` |
| Active skills | `.pi/skills/{name}/SKILL.md` | `~/.pi/agent/skills/{name}/SKILL.md` |
| Guidance files | `AGENTS.md`, `.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md` | `~/.pi/agent/AGENTS.md` |
| Telemetry extension | – | `~/.pi/agent/extensions/observal.ts` |
| Pi settings | – | `~/.pi/agent/settings.json` |
| Observal credentials | `~/.observal/config.json` | `~/.observal/config.json` |
| Observal lockfile | `~/.observal/lockfile.json` | `~/.observal/lockfile.json` |
| Acknowledged cursors | `~/.observal/sync_state.json` | `~/.observal/sync_state.json` |
| Pending batches | `~/.observal/pi_session_outbox/` | `~/.observal/pi_session_outbox/` |

Pi MCP configs use the `mcpServers` key.

### MCP component insights

Pi itself has no built-in MCP. Install and configure `pi-mcp-adapter` so Pi
actually dispatches calls to servers listed in the active `mcp.json` (adapter
2.x) or `mcp-adapter.json` (adapter 3.x). Pulling an agent records an MCP-entry
fingerprint from the generated profile, but **does not activate that profile**:
use `/agent` or activate the project config before starting the session. Refresh
the Observal extension with `observal doctor patch --harness pi` and restart Pi.

The extension fingerprints the active MCP entries, checks for conflicting
definitions and unresolved imports, and sends verification status per pinned
server. MCP config contents and credentials are never uploaded: these files are
hash-only snapshot inputs. A verifier-versioned synthetic hash-only manifest
entry binds pinned fingerprints to the layer identity, so a changed install
fingerprint cannot reuse an older snapshot's verification result. Legacy pulls
without fingerprints remain unverified until pulled again.

The server links Pi assistant tool calls to their results by unique tool-call
ID. For supported adapter result shapes, it reads only the reported server,
call mode, tool name and success/error state; it does not retain prompts, tool
arguments, result bodies, or credentials in activity rows. A call contributes
to a component only when the reported server exactly matches **one verified
installed alias** in that session's layer. Calls without a trustworthy link or
verified mapping remain unattributed. Agent-level attribution and MCP component
attribution are separate; the latter is visible in the MCP's activity summary
and session list for authorized owners. Other component types do not gain
observed-call reports from this integration.

This is fixture-verified for `pi-mcp-adapter` 2.38.0, with relevant result
shapes checked against 3.2.0. An adapter that changes its result details or
config resolution needs new sanitized fixtures and extractor verification
before its calls can be treated as measured use. Existing session hashes are
sender-cached; changes **during** a session cannot be proven stable, and
historical sessions without verified layer snapshots cannot be retroactively
attributed.

---

## Agent profiles and swapping

Because Pi reads a single `AGENTS.md`, `mcp.json`, and `skills/` directory,
only one Observal agent can be active at a time. In user scope,
`observal agent pull` does not touch the active files. It writes into the
per-agent profile directory, and the extension's `/agent` command makes a
profile active:

1. On first use, `/agent` backs up the current `AGENTS.md`, `SYSTEM.md`,
   `mcp.json`, `skills/`, and `sandboxes/` into `~/.pi/agent/agents/default/`.
2. It removes the active `AGENTS.md`, `SYSTEM.md`, `mcp.json`, `skills/`, and
   `sandboxes/`, then copies in whichever of those the chosen profile contains.
   Items the profile lacks stay absent until you swap back to `default`.
3. When Observal credentials are configured, it records the chosen agent as
   `active_agent` in `~/.observal/config.json` and refreshes the layer
   snapshot.
4. It offers to reload the session so the new system prompt takes effect.

Run `/agent` with no argument to pick from installed profiles, or
`/agent <name>` to swap directly. Choose `default` to restore the backed-up
configuration and clear the active agent. `/agent` lists only user-scope
profiles under `~/.pi/agent/agents/`.

---

## Extension spec

Observal installs a TypeScript extension named `observal.ts`. It has no
runtime dependencies beyond `node:*` built-ins and is fail-open: telemetry
failures never interrupt Pi. Ingest and checkpoint calls time out after five
seconds, and the layer-snapshot upload after ten.

| Pi event | Observal use |
|---|---|
| `session_start` | Load config and cursors, upload the layer snapshot, recover stale sessions on startup, show `● observal` in the footer |
| `agent_end` | Push new session lines after each turn |
| `session_shutdown` | Push remaining lines and finalize the session |

The extension also registers two commands, `/agent` and `/obs-sync`:

| Command | Description |
|---|---|
| `/agent [name]` | Swap the active Observal agent profile |
| `/obs-sync` | Show lines pushed and the server URL |
| `/obs-sync flush` | Push pending lines now |
| `/obs-sync config` | Show the config file path and server URL |

`doctor patch` compares the installed file with the bundled source and replaces
stale copies.

---

## Attribution

Pi does not expose an Observal agent id in its session file. The extension
resolves attribution from Observal's own state:

1. `observal agent pull` records the agent name, id, version, scope, pull
   time, and directory under the `pi` harness in `~/.observal/lockfile.json`.
2. The pull also records that agent as `active_agent` in
   `~/.observal/config.json`, and `/agent` updates the entry whenever you
   swap profiles.
3. On `session_start`, the extension reads `active_agent` and looks it up in
   the lockfile entry for the configured server URL, matching by id first and
   then by name.
4. The session payload is sent with the resolved `agent_id` and
   `agent_version`.
5. If there is no `active_agent`, or it has no `id`, the session is sent with
   a null agent id.
   If there is an `active_agent` but no lockfile match, the stored id is sent
   as-is; no directory-based guessing takes place.

---

## Session push behavior

The Pi extension implements the same acknowledged delivery contract as the
Python harnesses:

1. Resolve the session JSONL file and id from Pi's session manager.
2. Read complete records after the acknowledged byte and line cursor in
   `~/.observal/sync_state.json`.
3. Persist each pending batch under `~/.observal/pi_session_outbox/` before
   network delivery, in chunks of at most 500 lines.
4. Retry the batch idempotently until the server returns a contiguous
   acknowledgement covering it.
5. Advance the local cursor only to that acknowledged checkpoint.
6. On `session_shutdown`, send a SHA-256 audit manifest and replay any range
   the server asks to repair.
7. On the next startup, retry every pending outbox batch, then re-push
   unfinished sessions from the current project that changed within the last
   seven days until five have been finalized. Attempts that fail do not count
   toward the five. A missing or corrupt cursor is rebuilt from the server
   checkpoint on the next push.

Pi's pending batches are the files under `~/.observal/pi_session_outbox/`.
`observal ops telemetry status` reports server ingest health and the Python
exporters' outbox; it does not count this directory.

---

## Agent profile format

Observal writes the agent's rules as plain Markdown into `AGENTS.md`. Pi loads
that file as the system prompt, so the whole file is the agent:

```markdown
# Reviewer

You are a code reviewer with the following specialization...
```

There is no frontmatter and no model field. `observal agent pull` does not
write a model into Pi's configuration; the model is chosen inside Pi.

---

## Skill file format

Pi skills live at:

| Scope | Path |
|---|---|
| Project | `.pi/skills/{name}/SKILL.md` |
| User | `~/.pi/agent/skills/{name}/SKILL.md` |

Example:

```markdown
---
description: "Runs the project test suite"
---

# Run Tests

Run `pytest -q` from the project root.
```

When Codex is also installed, the bundled Observal skills are written once to
the shared `~/.agents/skills/{name}/SKILL.md` location, which both harnesses
read, instead of being duplicated under `~/.pi/agent/skills/`.

---

## Caveats

**One active agent at a time.** A user-scope pull does not activate the
agent. Run `/agent` inside Pi, or copy the profile into `~/.pi/agent/` by hand.
`/agent` does not see project-scope profiles under `.pi/agents/`; copy their
`mcp.json` and `skills/` into `.pi/` as shown in Setup step 3.

**`/agent` replaces the active files.** The first swap backs up your existing
`AGENTS.md`, `SYSTEM.md`, `mcp.json`, `skills/`, and `sandboxes/` into the
`default` profile. Later swaps remove those five items and install only what
the chosen profile contains, so a profile without `SYSTEM.md` leaves Pi with no
`SYSTEM.md`. Edit the profile directory, not the active files, if you want
changes to survive a swap.

**The extension is shared per Pi install.** It is installed by `doctor patch`,
not by each agent pull, and lives only in user scope.

**Guidance files are scanned, with two exceptions.** Observal layers
`AGENTS.md`, `.pi/SYSTEM.md`, and `.pi/APPEND_SYSTEM.md` as context. Scanning
and `observal agent pull` never rewrite the project's `.pi/SYSTEM.md` or
`.pi/APPEND_SYSTEM.md`. A project-scope pull does write the project's
`AGENTS.md`, because that file is the agent's rules in Pi, and `/agent` swaps
do replace the user-scope `~/.pi/agent/SYSTEM.md` as described above.

**Attribution depends on the lockfile and `/agent`.** A profile copied by hand
does not update `active_agent`, so its sessions keep whatever binding
`~/.observal/config.json` already holds, or carry no agent id if it has none.

**MCP config is Pi-specific.** Pi uses `mcp.json` with the `mcpServers` key
under `.pi/` or `~/.pi/agent/`, not Claude Code or Kiro MCP paths.
