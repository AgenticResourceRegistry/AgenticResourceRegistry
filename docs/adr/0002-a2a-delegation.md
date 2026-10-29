---
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "ADR 0002: Agent-to-agent delegation over ARD and A2A"
---

**Status:** Accepted
**Date:** 2026-09-26
**Deciders:** Agentic Resource Registry maintainers
**Amends:** ADR 0001, Decision 7

## Context

ADR 0001 made every registry resource discoverable, but an Agent found that
way is only usable in the *next* session: it has to be pulled into a harness
first. So an agent working on a task cannot use a better-suited agent from the
registry for part of it, which is exactly the moment discovery is most useful.

The Agent2Agent protocol (A2A, v1.0.0, Linux Foundation) standardises how one
running agent hands a task to another it cannot see inside: an Agent Card
describes the agent and its endpoint; `SendMessage` creates a Task that moves
through `TASK_STATE_*` states and returns Artifacts; `GetTask` and
`CancelTask` follow it. ARD already lists A2A Agent Cards as a resource type
(`application/a2a-agent-card+json`).

Two kinds of agent matter to Agentic Resource Registry users:

1. **Registry Agents.** Installable configurations, not services. They run
   inside a harness.
2. **Remote agents.** Services other teams already run (triage, data, billing
   agents built on any framework) that publish A2A Agent Cards.

## Decision 1: Delegation is an A2A Task, whatever runs it

Every delegation is represented as an A2A v1.0 Task (JSON serialisation:
`ROLE_USER`/`ROLE_AGENT`, parts as `{"text": ...}`, `TASK_STATE_*` states,
Artifacts for results). Agentic Resource Registry's own bookkeeping lives under
`metadata.observal`. A local run and a remote call look the same to the
caller, and the local runner can be exposed over the A2A wire later without
changing its data model.

## Decision 2: Registry Agents run headless in a throwaway copy of the repository

A delegated registry Agent is installed with project scope into a shared clone
(`git clone --shared`, detached at HEAD, remote removed) of the caller's
repository, plus the caller's uncommitted and untracked, non-ignored files,
using the same server install call and file
writer as `observal agent pull`, minus setup commands, lockfile and
active-agent state. Its harness then runs one prompt non-interactively.

- Which harnesses can do this is a verified runtime fact, `headless_run`, in
  the shared harness registry. The CLI adapter's `headless_command` builds the
  argv; harnesses without an agent flag get the agent's instructions inlined.
  Verified today: Claude Code, Kiro CLI, Cursor CLI, Codex, OpenCode, Copilot
  CLI, Antigravity, Pi. Not yet: Goose, VS Code Copilot.
- The child may edit files; those edits never reach the caller's tree. The
  difference between the tree after the agent's config was written and the tree
  it left is returned as a `changes.patch` artifact. The caller decides whether
  to `git apply` it. Trees are recorded through a private index file: no
  commits, refs, hooks or signing. The clone borrows the caller's objects but
  has its own refs, config, hooks and stash, so git commands a child with a
  shell runs (branch, stash, commit, push) stay in the copy. This is not a
  sandbox: a harness with shell access can still reach any path the user can.
- Shell access is refused where the harness can refuse it (Claude Code print
  mode denies unapproved tools; Kiro trusts only file tools and the agent's own
  MCP servers; Copilot CLI denies `shell`; Cursor and Antigravity run
  sandboxed). The agent's own reviewed MCP servers are allowed.
- Pi loads project-local `.pi` files only for a trusted project, and trusting
  also runs the project's extensions. A Pi child is started with `--approve`
  only when Pi already trusts the caller's repository (its `trust.json`, or
  `defaultProjectTrust: always`); otherwise the delegation fails and says so.
- Workspaces live under `~/.observal/delegations/ws/`, not the shared temp
  directory, because harnesses read instruction and skill files from parent
  directories.
- A worker that dies leaves nothing behind for long: the next status check or
  delegation stops its child (on Linux only after confirming the process still
  runs in that workspace, so a reused pid is never killed) and removes the
  workspace. Where the child cannot be proven to be ours, it is left to finish
  and its workspace is removed by a later sweep.
- The depth, cycle and breadth guards read the chain from the environment the
  delegation sets, or, when a harness starts MCP servers with only an
  allowlisted environment (Codex), from the task whose workspace the server
  runs in.
- Outside a git repository the child gets an empty scratch directory.

Rejected alternative: install the agent at user scope and remove it after. It
permanently touches the developer's setup, races with their own sessions, and
leaves edits in their working tree.

## Decision 3: Remote A2A agents are governed imported entries

A remote agent is registered by its Agent Card URL
(`POST /api/v1/ard/imports/a2a`). It becomes a `discovery_entries` row with
`source_kind = imported`, `kind = external`, media type
`application/a2a-agent-card+json`. It has no native table: the entry is the
governed record.

- It starts `pending` and needs a reviewer (`.../{identifier}/review`), exactly
  like a native submission, authorized the same way: a team-visible agent by
  its team's owners and reviewers, a public one by global reviewers, a private
  one by admins; team owners and reviewers also see their team's pending
  agents in discovery so they can inspect them. Visibility is private, team,
  or public. Approving requires the `obs:artifactDigest` the reviewer
  inspected; the decision is refused (409) when the card changed in between.
- The reviewed card is pinned in the entry (`obs:agentCard`,
  `obs:a2aInterface`). Clients call the pinned endpoint, never a card fetched at
  call time. Re-registering a card whose content changed sends it back to
  review.
- Identifier: `urn:air:<card host>:a2a:<name>` when the card's host is a real
  domain, since that host is the publisher. Otherwise the entry is published
  under this registry's domain with its own UUID.
- Cards are fetched over https, without following redirects, capped at
  256 KiB, through the SSRF guard. Internal agents are allowed per host with
  `discovery.a2a_private_hosts` (plain http allowed for those hosts only).
- Imported entries are not listed in `/.well-known/ard.json`: the manifest
  describes this publisher's own resources.
- Both A2A v1.0 cards (`supportedInterfaces`) and v0.3 cards (`url`,
  `preferredTransport`, `additionalInterfaces`) are accepted. The client
  speaks the JSON-RPC binding, with v0.3 method names for 0.x agents.

## Decision 4: No proxy; credentials stay with the caller

The CLI calls a remote agent directly. The server never relays A2A traffic
(consistent with "remote URLs remain direct" in AGENTS.md). Agentic Resource Registry never
stores agent credentials: the client reads a token from
`OBSERVAL_A2A_TOKEN_<HOST>`, named after the host of the endpoint it is sent to
(`agents.acme.com` reads `OBSERVAL_A2A_TOKEN_AGENTS_ACME_COM`; `-` becomes
`__` so two hosts never share a name), and sends it as the card's security
scheme asks. There is no catch-all variable: a token only ever reaches the host
it is named for.

## Decision 5: How agents reach delegation

Every Agent installed with `observal agent pull` gets an `observal-agents` MCP
server (`python -m observal_cli.delegation.mcp_server`) with four tools:
`find_agents`, `delegate`, `get_task`, `cancel_task`. MCP is the one
integration point every harness has, so this needs no per-harness work. The
`discovery.delegation_enabled` setting (default on) controls the injection.
The entry carries no agent id, because one `observal-agents` entry is shared by
every agent pulled into the same project or user config. The server takes the
calling agent from `OBSERVAL_AGENT_ID`, which a delegated child always has.
`delegate` waits at most 50 seconds, below common MCP tool timeouts (Codex:
60 s), and returns a `task_id` that `get_task` follows.
People and scripts use `observal delegate find|run|status|reply|list|cancel`.

The harness's own tool-permission prompt for `delegate` is the consent point
for starting a delegation.

## Decision 6: Guards

Checked before anything runs:

- only approved entries whose search result says `obs:delegable: true`;
- depth: a delegated agent may delegate again, up to
  `OBSERVAL_DELEGATION_MAX_DEPTH` levels (default 2), carried to children in
  `OBSERVAL_DELEGATION_DEPTH`;
- breadth: a delegated agent runs headless with no permission prompt, so it
  may start at most three tasks of its own (counted by `parentTaskId`);
- cycles: an agent already in `OBSERVAL_DELEGATION_CHAIN` (by identifier and
  by agent id), or the caller itself, is refused.

## Decision 7: Delegability is its own field

Search results carry `obs:delegable` and, for remote agents,
`obs:availability: delegate`. Neither is blended into `score` (ADR 0001,
Decision 8). A registry Agent is delegable when it is approved and at least
one harness it supports has `headless_run`.

## Decision 8: Telemetry through existing channels

A delegation appends a capability-lock line in the caller's context
(`mode = delegated`, kind `agent` or `external`, with the task id and child
harness). Session upload already attaches those lines to the caller's session,
so traces show which agents a session delegated to. The child's own session is
ingested by its harness hooks as usual; Claude Code children run with a known
`--session-id` recorded on the task. The task store indexes the child's session
id and workspace, and session upload checks that index first, so the child's
session is attributed to the delegated agent and version even when it is
delivered after the workspace is gone (`observal reconcile`, recovery after a
crash). The user's lockfile is never touched. No OTLP, no new ingestion path.

## Amendment to ADR 0001, Decision 7

ADR 0001 said an Agentic Resource Registry Agent is never emitted as
`application/a2a-agent-card+json`. That still holds: registry Agents keep
`application/vnd.observal.agent+json`. What changes is that Agentic Resource Registry now
*ingests* A2A cards as a governed resource type and can *call* both kinds of
agent through one A2A-shaped task model.

## Deferred

- Serving registry Agents as A2A endpoints (`observal a2a serve`) so outside
  frameworks can call them. The task model is ready for it.
- Streaming (`SendStreamingMessage`) and push notifications; the client polls.
- Verifying signed Agent Cards.
- Linking parent and child sessions server-side beyond the capability lock.
- Headless support for Goose once verified against real installs.
