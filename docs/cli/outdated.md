---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal outdated"
---

Compare versions recorded in `~/.observal/lockfile.json` with the latest versions available from the active Agentic Resource Registry registry. The command never installs an update.

## Synopsis

```bash
observal outdated
observal outdated --harness claude-code
observal outdated --output json --no-report
```

## Checked items

The command checks:

* Pulled agents
* Separately installed MCP servers
* Separately installed skills
* Separately installed hooks

Components bundled inside a pulled agent are not checked independently. Agent versions pin exact component versions, so update the agent when a newer agent version is reported. The exception is an agent version released before pinning: a component it never locked installs at its latest approved release, with a warning, and `--strict` refuses it. Agent authors use [`observal agent outdated`](/cli/agent#check-component-pins) to see which pinned components have newer releases and which are unlocked.

A plain `observal agent pull` keeps an installed agent on its locked version, so the upgrade command for an agent includes `--upgrade`.

The command reads only the active registry section of the lockfile. It requires authenticated registry access to retrieve current versions.

## Options

| Option | Description |
| --- | --- |
| `--harness <harness>` | Check one registered harness instead of every harness in the active lockfile section |
| `--output table` | Render a Rich table; this is the default |
| `--output json` | Emit one machine-readable JSON document |
| `--report` | Send outdated findings to the user Inbox; this is the default |
| `--no-report` | Suppress the Inbox write while still contacting the registry for version checks |

Valid harnesses are `cursor`, `kiro`, `claude-code`, `codex`, `copilot`, `copilot-cli`, `opencode`, `antigravity`, `goose`, and `pi`.

## Table output

Table output includes every installed item that was checked. Each row has one of these statuses:

| Status | Meaning |
| --- | --- |
| `outdated` | A newer registry version exists |
| `current` | The pinned version is current or newer |
| `missing` | The pinned item no longer exists in the active registry |
| `unknown` | The lockfile has no version for the item; reinstall it with the suggested command to record one |

Outdated and unknown rows are followed by a type-specific command using the canonical `namespace/slug` identity. Agents use `observal agent pull --upgrade`; standalone components use their matching registry install command.

A missing item is an item-level result, not a command failure, so a completed comparison containing missing rows exits successfully.

## JSON output

JSON output has a stable top-level object:

```json
{
  "items": [
    {
      "id": "11111111-1111-4111-8111-111111111111",
      "qualified_name": "acme/reviewer",
      "name": "reviewer",
      "namespace": "acme",
      "slug": "reviewer",
      "type": "agent",
      "harness": "claude-code",
      "current_version": "1.0.0",
      "latest_version": "2.0.0",
      "status": "outdated",
      "outdated": true,
      "error": null,
      "upgrade_command": "observal agent pull acme/reviewer --harness claude-code --no-prompt --upgrade"
    }
  ],
  "summary": {
    "total": 1,
    "outdated": 1,
    "current": 0,
    "missing": 0,
    "unknown": 0
  },
  "report": {
    "requested": true,
    "attempted": true,
    "succeeded": true,
    "created": 1,
    "superseded": 0,
    "error": null
  }
}
```

An empty lockfile returns the same shape with an empty `items` array and zero summary counts. Missing rows include a categorized item-level error with any available request ID. Command-level JSON errors are written to stderr and leave stdout empty.

## Inbox reporting

With reporting enabled, outdated findings are sent to the signed-in user's Inbox after the comparison. Duplicate findings are deduplicated by item and latest version.

Inbox reporting is best-effort. If the comparison succeeds but reporting fails, the comparison still exits successfully and the `report` object contains the categorized reporting error. Human output prints a warning. Unexpected reporting failures are not suppressed.

Use `--no-report` when no Inbox mutation is wanted. This does not make the command offline because registry reads are still required.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Comparison completed, including empty, current, outdated, or missing results |
| `1` | Unexpected or uncategorized failure, including an unexpected reporting failure |
| `2` | Invalid command syntax or output mode |
| `3` | Authentication is missing or invalid |
| `4` | Registry or filesystem permission denied |
| `7` | Invalid harness, malformed lockfile, or invalid installed version |
| `8` | Registry rate limit reached |
| `9` | Registry unavailable, timed out, or returned invalid version data |
| `10` | CLI and server versions are incompatible |

## Related

* [`observal agent pull`](/cli/pull): update a pulled agent
* [`observal registry`](/cli/registry): install a standalone component
* [`observal config`](/cli/config): inspect the active server configuration
