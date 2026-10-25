---
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
# SPDX-License-Identifier: Apache-2.0
name: observal-agents
command: observal
description: "Creates, forks, authors, validates, publishes, updates, versions, pulls, archives, restores, transfers, and manages co-authors for Observal Agents. Use when the user wants to build or install an Agent, change an Agent definition, publish a draft, release a version, or manage Agent ownership."
version: 2.3.0
owner: observal
---

# Managing Observal Agents

## Execution contract

1. Execute commands with a 60 second timeout.
2. **Use machine output by default:** add `--output json` whenever supported. Parse list results from `items` and pagination fields.
3. Use `--help` before acting when a path or flag is uncertain.
4. Keep workflows noninteractive. Supply required fields, `--no-prompt`, and confirmation flags.
5. Use UUIDs or `qualified_name` values returned by JSON. Never automate with row numbers.
6. Prefer native `agent init`, `agent add`, and `agent build` over hand-written scaffolding or custom validation.
7. Verify every publish, release, pull, ownership, and lifecycle mutation.
8. Never print MCP environment values, headers, tokens, or other secrets.
9. Mutations are sent once. After an uncertain transport failure, read Agent state before retrying.

## Choose the workflow

| User intent | Workflow |
| --- | --- |
| Find or inspect an Agent | Discover and inspect |
| Install an existing Agent into a harness | Pull and verify |
| Create a simple Agent in one call | Direct create |
| Customize an approved Agent without changing its source | Fork and edit |
| Author an Agent with components or files | Init, add, build, publish |
| Change the current listing without a new reviewed version | Update in place |
| Publish a reviewed patch, minor, or major version | Release |
| Create many Agents from a prepared file | Bulk create |
| Archive, restore, transfer, or manage co-authors | Lifecycle and collaboration |

Read [Agent workflows](references/agent-workflows.md) completely before executing the selected workflow.

## State rules

- `create` without complete flags starts a wizard. Agents must provide all required inputs or use a file.
- `fork` copies only an approved source release into a new independent draft. An inaccessible source is shown as Source unavailable, never as a saved source reference.
- `publish --update` changes the current Agent in place. A fork scaffold uses `agent_id` to target its draft, not a similarly named approved Agent.
- `release --bump` creates a reviewed version. Do not use update when the user asked for a release.
- Public teamspace publication may remain private with a pending visibility or listing review. Report the actual returned status.
- Pull success requires checking `files`, `warnings`, and `setup_commands`. Partial setup is not success.
- A 409 is a decision point, not a generic retry signal. Read current state before choosing update or release.

## Completion

Report the canonical Agent identity, resulting status and version, files changed for local operations, warnings, and the smallest next action. Verify with `agent show`, `agent versions`, or `scan` when the mutation response is not sufficient.
