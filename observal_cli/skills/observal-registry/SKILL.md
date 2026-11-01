---
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com> -->
# SPDX-License-Identifier: Apache-2.0
name: observal-registry
command: observal
description: "Searches, recommends, bulk-submits, installs, forks, edits, versions, archives, restores, transfers, and manages co-authors for Observal MCP servers, skills, hooks, prompts, sandboxes, and registered remote A2A agents. Use when the user wants to find components, publish one or many they control, install them into a harness, or manage their lifecycle."
version: 2.5.0
owner: observal
---

# Managing Registry Components

## Execution contract

1. Execute commands with a 60 second timeout.
2. **Use machine output by default:** add `--output json` whenever supported. Parse list results from `items` and pagination fields.
3. Run the leaf command's `--help` when any path, flag, enum, or payload shape is uncertain.
4. Supply all required inputs and confirmation flags. Do not leave an agent waiting at a prompt.
5. Reuse returned UUIDs and `qualified_name` values. Never automate with row numbers or ambiguous bare names.
6. Verify installs, submissions, edits, versions, ownership changes, and lifecycle transitions.
7. Submit or modify only components the user owns or is authorized to manage.
8. Never expose environment values, headers, tokens, private source data, or submitted secret fields.
9. Mutations are sent once. After an uncertain transport failure, read component state before retrying.
10. Authentication is optional for approved public content when the server setting `deployment.public_registry_enabled` is enabled; it is disabled by default on self-hosted deployments. Public list, show, install, and prompt render commands use `https://public.observal.io` by default. Authenticate before submitting, editing, reviewing, rating, or accessing private team content.

## Choose the workflow

| User intent | Read |
| --- | --- |
| Find, inspect, recommend, or install components | [Discovery and installation](references/discovery-and-installation.md) |
| Submit one component or a mixed bulk file, or register a remote A2A agent | [Component submission](references/component-submission.md) |
| Fork, edit, version, archive, restore, transfer, or manage co-authors | [Registry lifecycle](references/registry-lifecycle.md) |

Read only the selected reference, and read it completely before executing.

## Registry rules

- Search with the user's natural-language terms, then narrow by type, namespace, team, harness, or category only when useful.
- Open-ended requests such as "what am I missing?" use personalized recommendations before keyword search.
- `personalized: false` means popularity fallback, not a personal recommendation.
- Team members can see authorized private teamspace items. Use `TEAM_HANDLE/ITEM_SLUG` for direct references.
- Draft, pending, rejected, and approved items have different edit behavior. Read status before mutating.
- Forks copy only currently approved sources and approved releases into independent drafts. Do not disclose source identity if the API reports `forked_from.available: false`.
- A successful submit can still be pending review. Report the returned status instead of saying it is published.
- Bulk files are structurally validated before mutation. Inspect every per-entry result and verify uncertain retries by canonical identity.
- Prefer an existing installed dependency or native CLI path. Do not invent wrappers or telemetry variables.

## Completion

Report component type, canonical identity, version, status, target harness or scope when applicable, warnings, and any review or setup step still required.
