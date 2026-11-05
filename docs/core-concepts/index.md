---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Core concepts"
---

The vocabulary you need to be productive with Agentic Resource Registry.

## The registry

The registry stores users, agents, components, review state, and alert rules in PostgreSQL. Session events and aggregates are stored separately in ClickHouse. See [Session tracking and reconciliation](/core-concepts/session-tracking) for the complete session data flow.

## Registry components

Six component types are available. Agents bundle the other five.

| Type | What it is |
| --- | --- |
| **Agent** | A complete, installable AI agent. Bundles MCP servers, skills, hooks, prompts, and sandboxes into one YAML. |
| **MCP Server** | A [Model Context Protocol](https://modelcontextprotocol.io/) server, the tools an agent can call. |
| **Skill** | A portable instruction package agents load on demand. |
| **Hook** | A lifecycle callback that runs on session start, tool use, session end, and other supported events. |
| **Prompt** | A named, parameterized prompt template with variable substitution. |
| **Sandbox** | A Docker execution environment for running code the agent generates. |

Anyone can publish. Admin review controls what appears in the public listing, but your own items are usable immediately without approval.

## Canonical identity

An agent or component has a canonical `namespace/slug` identity. The namespace is the owner's username. The slug is stable within that namespace. Display names can change without changing the canonical identity.

UUIDs are the stable reconciliation key. The server supplies canonical namespace, slug, display name, qualified name, and review status. Installed versions in the local lockfile remain local pins.

## Versions and locks

Agents and components are versioned. An approved version never changes.

Every agent version pins each of its components to one exact version, recorded with a content digest. Installing that agent version always installs those component versions, even after newer ones are approved. Agent versions released before pinning are the exception: a component they never locked installs at its latest approved release, with a warning. Only the agent's author moves them, by releasing a new agent version, which goes through review like any other.

Installs are pinned too. The first `observal agent pull` in a project records the agent version it installed in `observal.lock`, a file meant to be committed. Later pulls, by anyone in that project, install the same agent version until someone moves it with `--upgrade` or `--version`. `~/.observal/lockfile.json` is each machine's record of what it has installed where. `--strict` refuses any install that does not match its lock.

See [`observal agent pull`](/cli/pull#pinned-versions) and [`observal agent release`](/cli/agent#release-and-versions).

## Deployment mode

SSO-only access is controlled by `deployment.sso_only`:

| Mode | Self-registration | Bootstrap | Auth |
| --- | --- | --- | --- |
| `deployment.sso_only=false` (default) | Yes | Yes, a fresh server creates an admin on first login | Email and password or API key |
| `deployment.sso_only=true` | No | No | SSO only |

Most self-hosters use `deployment.sso_only=false`.

## Next

* [Session tracking and reconciliation](/core-concepts/session-tracking)
* [Use cases](/use-cases)
