<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Recovery workflows

## Contents

- Reconcile missed sessions
- Manage CLI versions
- Explicit local fallback

## Reconcile missed sessions

Use when telemetry status or the user identifies sessions missed by automatic delivery.
Kiro V3/IDE finalization is a separate, deliberate reconciliation step: its hook owner
terminates detached children at shutdown. After the chat exits, finish recorded Stop
intents from an independent terminal:

```bash
observal reconcile --harness kiro --since 24 --output json
```

If Kiro was launched with a relocated runtime root, the independent terminal must use
**the same `KIRO_HOME` value** (and the same Observal authentication home). For example:

```bash
KIRO_HOME=/absolute/path/used/by/kiro observal reconcile --harness kiro --since 24 --output json
```

The bare command does not inherit environment variables from another terminal and may
otherwise discover the wrong runtime root. This manual V3/IDE workflow still needs human
approval; it is not automatic background finalization.

Kiro hooks capture records and persist Stop intent, but do not claim automatic V3 final
completion. Reconcile sends late records and credits and finalizes only the stopped source
group. Quiet/permission-paused sessions without a matching Stop remain non-final. A resumed
prompt cancels old authority, including queued final semantics and waiting V2 workers.
Queued records and credits remain deliverable without finalizing the resumed session. Verify the local finalized cursor and emptied intent/outbox;
a scheduled child or successful capture is not finalization evidence.

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

`-dev` versions are not on PyPI; `self upgrade --version X.Y.Z-dev.N` builds them from `git+https://github.com/AgenticResourceRegistry/AgenticResourceRegistry@main`. Without `--version`, a CLI logged in to a server that runs a `-dev` build targets that build instead of the latest release, because no release would pass the version gate. If the server cannot be reached, upgrade falls back to GitHub releases and says so. The 426 error and `observal auth login` print the exact command. If it fails, as a last resort install from source: `uv tool install --force git+https://github.com/AgenticResourceRegistry/AgenticResourceRegistry@main` (dev) or `@v<version>` (release tag), then confirm `observal --version` equals the server version.

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
