---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Meghana Inti <meghanainti149@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal scan"
---

Inspect local configuration without mistaking configuration discovery for native activation or use. **Use `--inventory` for the strictly read-only, local-only contract.** Plain scan can run legacy MCP configuration and local lockfile migrations before discovery; table output may also query the authenticated registry.

To configure session capture deliberately, use [doctor's preview](/cli/doctor). Inventory never starts instrumentation or submission.

## Synopsis

```bash
observal scan --inventory --harness claude-code --output json
```

Inventory returns a versioned object with `inventory_schema_version`, `inventory`, and `diagnostics`, not the generic list envelope. It bounds traversal and redacts launch details: URL userinfo/fragments/query parameters are removed; suspicious URL paths can make a launch incomplete. It exposes input names, not token values, raw prompts, or command arguments. Unsupported/unsafe launches have `launch: null` and diagnostics rather than a guessed registry identity.

Component names and filenames can still be user-supplied/private information. Inspect output before sharing. Package-manager installation alone is not configured-capability evidence. Use [discovery](/cli/discover) for approved registry resources and submit only an inspected resource you are authorized to publish.

## Options

| Option | Description |
| --- | --- |
| `--harness`, `-i` | Limit discovery to a registered harness; omit to consider all registered adapters. |
| `--inventory` | Bounded/redacted local evidence; no registry request, local lockfile write, migration, or submission. |
| `--output`, `-o` | Table or JSON; this does not make ordinary scan read-only. |

Supported harness identifiers come from the current registry. Use `scan --help` and [integration support](/integrations/index); discovery depth varies and an absent entry does not prove the resource is absent or unusable.

## What it does

Inventory reads supported home/project configuration through adapters and returns observed evidence plus diagnostics. It does not execute discovered commands, install hooks, authenticate MCPs, or certify availability in a running model session.

Without `--inventory`, scan uses its older discovery path. Startup may unwrap legacy MCP entries and migrate local tracking, with backups where supported. JSON returns discovered `harnesses`, `mcps`, `skills`, `hooks`, and `agents`; table mode may additionally compare names with authenticated registry listings. Neither path is a complete cross-harness component inventory. Do not distribute ordinary scan's raw discovered objects as if they had inventory's bounded/redacted contract.

Native paths are described in the [config-file reference](/reference/config-files), not a second hardcoded path table here.

## Example

Run in the target project:

```bash
observal scan --inventory --output json
```

An empty inventory succeeds:

```json
{
  "inventory_schema_version": 1,
  "inventory": [],
  "diagnostics": []
}
```

This is an illustrative empty result, not proof every source was readable. Inspect diagnostics and each entry's harness, scope, type, source, and launch metadata.

## Scoping to a single harness

```bash
observal scan --inventory --harness claude-code --output json
```

For plain scan, even a harness filter is not a migration boundary: startup migrations may inspect other legacy configuration. Use inventory when no mutation/network access is acceptable.

## What to do next

If you only wanted local discovery, stop here. For permitted capture, inspect the applicable privacy/retention policy and preview a targeted change:

```bash
observal doctor patch --harness claude-code --dry-run --output json
```

Apply instrumentation only after reviewing that plan. Installed hooks and inventory entries are not evidence of successful delivery or native execution. Follow [session delivery verification](/core-concepts/session-tracking).

## Exit codes

| Code | Observed behavior |
| --- | --- |
| `0` | Inventory completes, including empty results. Plain scan JSON also returns an empty result successfully; successful ordinary discovery returns zero. |
| `1` | Unknown harness, or ordinary table-mode scan finds no harness configuration. Unknown-harness JSON diagnostics are currently plain text on stderr, not the shared categorized error object. |

Other startup/filesystem/dependency failures can use the shared [CLI error contract](/cli/index#exit-codes). Do not infer zero found resources is always an error across output modes.

## Related

[Doctor](/cli/doctor) · [Agent pull](/cli/pull) · [MCP session evidence](/use-cases/observe-mcp-traffic) · [Integration capabilities](/integrations/index)
