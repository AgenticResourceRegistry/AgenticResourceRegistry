---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal scan"
---

Discover MCP servers, hooks, and telemetry configuration across your harness configs. `scan` is **read-only** -- it shows what you have without modifying any files.

To install session telemetry hooks, use [`observal doctor patch`](/cli/doctor). MCP commands and URLs are never rewritten.

## Synopsis

```bash
observal scan [--harness <harness>] [--inventory] [--output table|json]
```

Use `--inventory` for **local-only**, bounded harness evidence. It returns a versioned JSON document with `inventory` and `diagnostics`, or a table showing the harness, scope, component type, name, and privacy-safe source path. URLs have userinfo, fragments, and **all query parameters** removed; suspicious credentials in the URL path make a launch incomplete. Token values, raw prompts, and command arguments are not printed. Unrecognized or unsafe launches have `launch: null` and a diagnostic, not a guessed Registry identity. An empty inventory succeeds with an empty list. It never contacts Observal, writes a lockfile, or submits a component. Package-manager installations alone are not scanned or treated as configured capabilities. Component names and source filenames are user-supplied, so review the output before sharing it externally.

To find approved resources for a task, use [`observal discover`](/cli/discover). To submit something you found locally, **you** choose to run the corresponding `observal registry <type> submit --draft` command; inventory never starts that workflow.

## Options

| Option | Description |
| --- | --- |
| `--harness <harness>` | Scope to one harness: `cursor`, `kiro`, `claude-code`, `codex`, `copilot`, `copilot-cli`, `opencode`, `antigravity`, `goose`, `pi` |
| `--inventory` | Bounded, redacted **local-only** evidence; no Registry lookup. |
| `--output table\|json` | Render a table or machine-readable JSON. |

If you run `observal scan` with no flags, it auto-detects every installed harness and scans each in turn.

## What it does

1. Finds MCP config files:
   * Claude Code: `~/.claude/settings.json`
   * Kiro: `.kiro/settings/mcp.json` (project) or `~/.kiro/settings/mcp.json` (home)
   * Cursor: `.cursor/mcp.json`
   * Copilot: `.vscode/mcp.json`
   * Antigravity: `.agents/mcp_config.json` or `~/.gemini/antigravity-cli/mcp_config.json`
   * Goose: `~/.config/goose/config.yaml` (the `extensions` key)
   * Copilot CLI: `.mcp.json` (project) or `~/.copilot/mcp-config.json` (home)
2. Lists every MCP server found and its direct command or URL.
3. Reports installed session telemetry hooks.

No files are written and no registration happens. **Default table-mode scan may query the Registry when authenticated** to show unregistered names; use `--inventory` for a guaranteed local-only scan.

## Example

```bash
observal scan
```

Output:

```
Claude Code (~/.claude/settings.json)
  filesystem        npx @modelcontextprotocol/server-filesystem   not wrapped
  github            npx @modelcontextprotocol/server-github       not wrapped

Kiro (.kiro/settings/mcp.json)
  mcp-obsidian      mcp-obsidian                                  not wrapped

2 harness(s) found, 3 MCP server(s) total, 0 wrapped.
```

## Scoping to a single harness

```bash
observal scan --harness claude-code
```

## What to do next

Once you see what's installed, instrument it:

```bash
# Install session telemetry hooks across all harnesses
observal doctor patch --all-harnesses

# Or target a specific harness
observal doctor patch --harness kiro

# Preview changes without writing anything
observal doctor patch --all-harnesses --dry-run
```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Inventory completed (even when empty); default scan found a harness config |
| 1 | Unknown harness; default scan found no harness configs |

## Related

* [`observal doctor patch`](/cli/doctor): instrument your harnesses (hooks, shims)
* [`observal agent pull`](/cli/pull): install a full agent (also wires up MCP servers)
* [`observal doctor`](/cli/doctor): diagnose instrumentation end-to-end
* [Use Cases -- Observe MCP traffic](/use-cases/observe-mcp-traffic): narrative walkthrough
