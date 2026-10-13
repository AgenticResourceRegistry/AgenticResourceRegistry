<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Recovery workflows

## Contents

- Reconcile missed sessions
- Manage CLI versions
- Explicit local fallback

## Reconcile missed sessions

Use only when telemetry status or the user identifies sessions missed by automatic delivery.

Preview first when scope is uncertain:

```bash
observal reconcile --harness claude-code --since 24 --dry-run --output json
```

Then run the smallest required scope:

```bash
observal reconcile --harness claude-code --since 24 --output json
observal reconcile --output json
```

Dry run does not drain the outbox, contact ingestion, or change cursors. Report discovered, queued, skipped, failed, and cursor state. Reconciliation does not install hooks.

## Manage CLI versions

Read current state first:

```bash
observal self status --output json
```

Upgrade, downgrade, and rollback are separate operations:

```bash
observal self upgrade --force --output json
observal self upgrade --version 2.6.0 --force --output json
observal self downgrade --list --output json
observal self downgrade --version 2.5.0 --force --output json
observal self rollback --force --output json
```

`-dev` versions are not on PyPI; `self upgrade --version X.Y.Z-dev.N` builds them from `git+https://github.com/Observal/Observal@main`. The 426 error and `observal auth login` print the exact command. If it fails, as a last resort install from source: `uv tool install --force git+https://github.com/Observal/Observal@main` (dev) or `@v<version>` (release tag), then confirm `observal --version` equals the server version.

Standalone binary changes require a published checksum because JSON mode does not accept unsigned downloads interactively. Verify installed version after mutation.

## Explicit local fallback

Use only after JSON returns either `error.category: unavailable` with exit code `9`, or `error.category: authentication` with operation `Load authenticated CLI configuration` and exit code `3`. The user confirms they still want a local-only Agent file before anything is written.

| Harness | User scope | Project scope |
| --- | --- | --- |
| Claude Code | `~/.claude/agents/<name>.md` | `.claude/agents/<name>.md` |
| Kiro | `~/.kiro/agents/<name>.json` | `.kiro/agents/<name>.json` |
| Cursor | `~/.cursor/rules/<name>.mdc` | `.cursor/rules/<name>.mdc` |
| VS Code | `~/.config/Code/User/agents/<name>.md` | `.vscode/agents/<name>.md` |
| Codex CLI | `~/.codex/agents/<name>.md` | `.codex/agents/<name>.md` |
| Copilot CLI | `~/.config/github-copilot/agents/<name>.md` | `.github/copilot/agents/<name>.md` |
| OpenCode | `~/.opencode/agents/<name>.md` | `.opencode/agents/<name>.md` |

Kiro JSON shape:

```json
{"name":"<name>","description":"<description>","prompt":"<prompt>","model":"<model>","mcpServers":{},"tools":["read"],"resources":["skill://~/.kiro/skills/*/SKILL.md"]}
```

Start with the minimum explicit Kiro tool allowlist. Add exact built-in or MCP tool names only when the user requests them. Set `tools` to `["*"]` only after separate confirmation that unrestricted built-in and MCP tool access is intended.

Markdown harness shape:

```markdown
---
name: <name>
description: <description>
---
<prompt>
```

Cursor uses the same frontmatter in an `.mdc` file. Preserve existing files unless the user explicitly authorizes replacement. Report the exact path and state that no Registry publication occurred. When service returns, use the normal `observal agent create` or authoring workflow.
