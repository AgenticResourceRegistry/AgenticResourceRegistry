---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-FileCopyrightText: 2026 Meghana Inti <meghanainti149@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Config files"
description: "Distinguish local tracking, native profiles, capture buffers, and safe file handling."
---

Important CLI state and selected native configuration paths for this documented revision—not a complete inventory of every harness file. The [integration guides](/integrations/index) describe activation and compatibility; inspect the actual pull/doctor plan before changing files.

## Client-side: `~/.observal/`

| File | Purpose | Permissions |
| --- | --- | --- |
| `config.json` | CLI config (server URL, access token, user info, timeout) | `0600` |
| `aliases.json` | User-defined shortcuts (`@my-mcp` → UUID) | `0600` |
| `last_results.json` | Latest typed list output, enabling row-number references | `0600` |
| `telemetry_buffer.db` | Durable SQLite outbox for Python session exporters awaiting contiguous server acknowledgement | `0600` |
| `opencode_session_outbox/` | Per-session durable OpenCode plugin batches and acknowledged line state | `0600` files |
| `pi_session_outbox/` | Durable pending Pi extension batches | `0600` files |
| `sync_state.json` | Acknowledged byte/line cursors for file-backed exporters | owner read/write |
| `lockfile.json` | Local installation tracking, partitioned by active registry and harness | owner-only writes |
| `capability_lock.jsonl` | Recorded resource activation modes, versions, directories, and times | private metadata; not proof of native execution |

### `config.json` schema

```json
{
  "server_url": "https://registry.example.com",
  "access_token": "<redacted>",
  "refresh_token": "<redacted>",
  "user_id": "f9f3...",
  "user_name": "Alice",
  "username": "alice",
  "timeout": 30,
  "update_check": true,
  "update_check_interval": 86400,
  "update_check_repo": ""
}
```

This is an illustrative redacted stored shape, not credentials to copy. Authentication and identity fields are managed by `observal auth`. The `config set` command accepts only `server_url`, `timeout`, `update_check`, `update_check_interval`, and `update_check_repo`. Supported environment variables override persisted values for the current invocation; see [Environment variables](/reference/environment-variables).

A project's `observal.lock` separately selects the agent release; it is not a substitute for local installation tracking. Do not commit token-bearing config, generated credentials, raw session buffers, or private local metadata. Operator JWT keys live at the configured server `JWT_KEY_DIR` and belong in the [operator recovery plan](/self-hosting/backup-and-restore), not a portable client bundle.

### Durable session outbox

Python session exporters persist each observed batch in `telemetry_buffer.db` before network delivery. OpenCode and Pi use per-session files under their native outbox directories because their TypeScript runtimes cannot call the Python SQLite engine. All follow the same protocol: pending data survives process restarts and failed attempts, and the source line advances only when the server's contiguous checkpoint covers the complete batch. Agentic Resource Registry does not silently evict unacknowledged records at capacity.

`sync_state.json` is a cache of acknowledged local positions, not the authority for delivered history. If it is missing, corrupt, or stale, recovery validates and restores positions from the authenticated server checkpoint. Finalized sessions also send a SHA-256 audit manifest; hashing is not performed on ordinary incremental uploads.

Use `observal ops telemetry status` to inspect pending batch count, disk use, oldest pending time, and last successful acknowledgement.

### `aliases.json` schema

```json
{
  "my-mcp": "498c17ac-1234-4567-89ab-cdef01234567",
  "reviewer": "alice/reviewer"
}
```

Use anywhere that accepts a compatible reference by prefixing the alias with `@`.

### `last_results.json` schema

```json
{
  "item_type": "skill",
  "ids": ["498c17ac-1234-4567-89ab-cdef01234567"],
  "names": {
    "reviewer": "498c17ac-1234-4567-89ab-cdef01234567"
  }
}
```

Each list invocation replaces this cache, including an empty result. Numeric row references are valid only for the component type that produced the latest list.

## harness-side

### Claude Code

| Path/surface | Purpose |
| --- | --- |
| `.claude/agents/<local-name>.md` / `~/.claude/agents/<local-name>.md` | Project/user agent Markdown with YAML frontmatter |
| `.claude/skills/<skill>/` / `~/.claude/skills/<skill>/` | Skill instructions/resources where supported |
| `~/.claude/settings.json` | Managed user session hooks; opt-in user agent gates |
| `.claude/settings.local.json` | Opt-in project agent gates; not the default agent-frontmatter placement |
| Native `claude mcp add` registration | MCP setup/reset commands returned by pull; inspect the native scope/result instead of assuming MCPs are stored in `settings.json` |
| `CLAUDE.md` and supported guidance files | Native project guidance, not registry agent activation |

See [Claude Code](/integrations/claude-code) and the [agent hook placement contract](/cli/pull#agent-hooks-in-headless-sessions). A generated profile or hook declaration is not proof it is selected or runs.

### Kiro

| Path | Purpose |
| --- | --- |
| `.kiro/settings/mcp.json` / `~/.kiro/settings/mcp.json` | Project/user MCP configuration |
| `.kiro/agents/<local-name>.json` / `~/.kiro/agents/<local-name>.json` | Project/user agent profiles |
| `.kiro/steering/` and `.kiro/skills/` | Supported native instructions/skills; not equivalent to profile selection |
| `.kiro/hooks/` | Standalone hooks for the supported native format/version |

Legacy inline hooks and standalone hooks differ. Check [Kiro](/integrations/kiro) for the chosen tool revision rather than copying an old event map.

### Cursor

| Path | Purpose |
| --- | --- |
| `.cursor/agents/<local-name>.md` / `~/.cursor/agents/<local-name>.md` | Custom subagent profiles |
| `.cursor/mcp.json` / `~/.cursor/mcp.json` | Project/user MCP configuration |
| `.cursor/rules/` | Native guidance |
| `.cursor/hooks.json` / `~/.cursor/hooks.json` | Supported hook configuration |

[Cursor](/integrations/cursor) needs explicit custom-subagent activation. Cursor lacks the registry skills capability; directory patterns in shared metadata do not override capability gating.

### VS Code

The registry identifier is `copilot`, not `vscode`.

| Path | Purpose |
| --- | --- |
| `.github/agents/<local-name>.agent.md` | Project custom-agent profile |
| `.vscode/mcp.json` | MCP configuration using the native `servers` key |

Use [VS Code Copilot](/integrations/copilot), not the terminal adapter's assumptions.

### Codex CLI

| Path | Purpose |
| --- | --- |
| `.codex/agents/<local-name>.toml` / `~/.codex/agents/<local-name>.toml` | Custom-subagent definitions |
| `.codex/config.toml` / `~/.codex/config.toml` | MCP configuration using `mcp_servers` |
| `AGENTS.md` and native overrides | Guidance; not a substitute for selecting the custom subagent |

[Codex](/integrations/codex) is not a rules-only integration. Its profile and `--profile` option are different concepts; generation does not prove native custom-role activation.

### Copilot CLI

| Path | Purpose |
| --- | --- |
| `.github/agents/<local-name>.agent.md` | Project agent profile |
| `.mcp.json` | Project MCP configuration using `mcpServers` |

The registry adapter uses fixed project scope and rejects explicit `--scope`, even `project`. Native personal MCP settings may also exist. Use [Copilot CLI](/integrations/copilot-cli) for activation and personal-profile precedence.

### Pi

User agent pulls isolate their content under `~/.pi/agent/agents/<local-name>/`; project pulls use `.pi/agents/<local-name>/`. These can include `AGENTS.md`, `mcp.json`, skills, and `observal-hooks.json`. The [Pi integration](/integrations/pi) explains user-profile switching and project-profile limits; base registry paths alone are not the final isolated profile paths.

| Path | Purpose |
| --- | --- |
| `~/.pi/agent/extensions/observal.ts` | Bundled telemetry extension |
| `~/.pi/agent/extensions/.observal-extension.json` | Manifest recording the CLI version the local extension was installed from |
| `~/.pi/agent/extensions/observal.ts.bak` | Copy of the previous extension, kept whenever Agentic Resource Registry replaces or removes one |

`observal auth login` and `observal doctor patch` install the extension bundled with the CLI directly to `extensions/observal.ts`, recording the installed CLI version in the adjacent manifest. A later CLI upgrade rewrites each of those files atomically (the two are written in sequence, not as a single atomic unit) and prints a reminder to restart Pi or run `/reload`; a newer local install than the current CLI is left alone. If the file still carries the current version but its contents have drifted from the bundled source, such as an edited working copy, doctor restores it and keeps the previous contents as `observal.ts.bak`.

Agentic Resource Registry keeps a copy of the file whenever it rewrites or deletes one, named
`observal.ts.bak` (`.bak.1`, `.bak.2`, … if that name is taken). The copy does
not end in `.ts`, so Pi does not load it; delete it once you are satisfied with
the result.

There are two cases where no copy is made, because nothing is lost: adopting a
file whose contents already match the bundle, where only the manifest is
written, and `observal doctor cleanup` removing a tracked install that still
matches what Agentic Resource Registry wrote. Every other refresh, restore, migration and
duplicate removal keeps one — a manifest records which CLI version wrote an
install, not that its bytes are still unedited.

### Migrating installs from before version tracking

CLI versions before the manifest existed wrote `extensions/observal.ts` with nothing beside it. Those installs are recognized by the doc header every Agentic Resource Registry extension carries, and `observal auth login` or `observal doctor patch` migrates them: the existing file is copied to `observal.ts.bak` (`.bak.1`, `.bak.2`, … if that name is taken), the current extension is written in its place, and the manifest is created. The backup does not end in `.ts`, so Pi does not load it; delete it once you're satisfied with the upgrade.

If `extensions/observal.ts` exists without a matching manifest, doesn't match the bundled source, and carries no Observal header (`Observal session telemetry extension for Pi`), Agentic Resource Registry treats it as unmanaged and never overwrites or removes it — remove or move the file aside, then re-run `observal doctor patch --harness pi` to let Agentic Resource Registry manage it.

## Safe writes

`observal doctor patch` and `observal doctor cleanup` write each managed configuration file atomically and preserve unrelated entries. Use `--dry-run --output json` to review planned harness changes before writing. Agent Pull reports every created or merged file in its result.

## File permissions

The CLI's private JSON writers and session buffer apply owner-only permissions on supported POSIX filesystems. Do not infer one mode for every directory, native harness file, backup, or platform. Verify actual access permissions; generated native configuration and session buffers can contain credentials or sensitive content. Do not loosen access merely to make setup pass.

## Related

* [Environment variables](/reference/environment-variables) - supported runtime overrides
* [`observal config`](/cli/config), CLI surface for editing
