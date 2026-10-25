---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Config files"
---

Every file Observal reads or writes on the client (`~/.observal/`) and in each harness's config directory.

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
| `keys/` | Server-side JWT keys (operators only; path controlled by `JWT_KEY_DIR`) | `0600` |

### `config.json` schema

```json
{
  "server_url": "https://observal.your-company.internal",
  "access_token": "ey...",
  "refresh_token": "ey...",
  "user_id": "f9f3...",
  "user_name": "Alice",
  "username": "alice",
  "timeout": 30,
  "update_check": true,
  "update_check_interval": 86400,
  "update_check_repo": ""
}
```

Authentication and identity fields are managed by `observal auth`. The `config set` command accepts only `server_url`, `timeout`, `update_check`, `update_check_interval`, and `update_check_repo`. Supported environment variables override persisted values for the current invocation; see [Environment variables](/reference/environment-variables).

### Durable session outbox

Python session exporters persist each observed batch in `telemetry_buffer.db` before network delivery. OpenCode and Pi use per-session files under their native outbox directories because their TypeScript runtimes cannot call the Python SQLite engine. All follow the same protocol: pending data survives process restarts and failed attempts, and the source line advances only when the server's contiguous checkpoint covers the complete batch. Observal does not silently evict unacknowledged records at capacity.

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

| Path | Purpose |
| --- | --- |
| `~/.claude/settings.json` | Hooks, MCP servers, telemetry config |
| `~/.claude/agents/<name>.json` | User-scoped sub-agent definitions |
| `.claude/agents/<name>.json` | Project-scoped sub-agent definitions |
| `.claude/skills/<skill>/` | Installed skills (SKILL.md + assets) |
| `AGENTS.md` / `CLAUDE.md` | Rules loaded into context |

### Kiro

| Path | Purpose |
| --- | --- |
| `.kiro/settings/mcp.json` | Project-level MCP servers with direct commands or URLs |
| `~/.kiro/settings/mcp.json` | Global MCP servers |
| `.kiro/agents/<name>.json` | Project-level agent config with telemetry hooks |
| `~/.kiro/agents/<name>.json` | Global agent config |
| `.kiro/steering/<name>.md` | Steering files (system instructions with YAML frontmatter for inclusion modes) |
| `.kiro/skills/` | Kiro skills (SKILL.md) |
| `.kiro/hooks/` | Standalone hook definitions |
| `AGENTS.md` | Rules loaded into context (compat with Claude Code) |

### Cursor

| Path | Purpose |
| --- | --- |
| `.cursor/mcp.json` | MCP servers with direct commands or URLs |
| `.cursor/rules/` | Cursor rules |
| `AGENTS.md` | Rules |

### VS Code

| Path | Purpose |
| --- | --- |
| `.vscode/mcp.json` | MCP servers with direct commands or URLs |
| `AGENTS.md` | Rules loaded into context |

### Codex CLI

| Path | Purpose |
| --- | --- |
| `AGENTS.md` | Rules (rules-only integration) |

### Pi

| Path | Purpose |
| --- | --- |
| `~/.pi/agent/extensions/observal.ts` | Bundled telemetry extension, installed locally when npm isn't configured |
| `~/.pi/agent/extensions/.observal-extension.json` | Manifest recording the CLI version the local extension was installed from |
| `~/.pi/agent/extensions/observal.ts.bak` | Copy of the previous extension, kept whenever Observal replaces or removes one |
| `~/.pi/agent/settings.json` | Pi's own config; a `packages` entry of `npm:observal-pi[@version]` here selects the npm installation mode instead |

Two mutually exclusive installation modes are supported:

- **npm**: `npm:observal-pi` is configured in `~/.pi/agent/settings.json` (with or without a pinned version, and whether or not Pi has downloaded it yet). Observal never installs `extensions/observal.ts` in this mode — `observal doctor` only reports when a pinned version is older than the installed CLI, with a `pi update npm:observal-pi` reminder. Pi loads a local `extensions/observal.ts` in addition to the npm package, so if one is left over from local mode every session is sent twice; `observal doctor patch` removes a local copy it recognizes as its own (keeping it as `observal.ts.bak`) and leaves a file Observal did not write alone.
- **local**: no npm package is configured. `observal auth login` and `observal doctor patch` install the extension bundled with the CLI directly to `extensions/observal.ts`, recording the installed CLI version in the adjacent manifest. A later CLI upgrade rewrites each of those files atomically — the two are written in sequence, not as a single atomic unit — and prints a reminder to restart Pi or run `/reload`; a newer local install than the current CLI is left alone. If the file still carries the current version but its contents have drifted from the bundled source — an edited working copy, say — doctor restores it and keeps the previous contents as `observal.ts.bak`.

Observal keeps a copy of the file whenever it rewrites or deletes one, named
`observal.ts.bak` (`.bak.1`, `.bak.2`, … if that name is taken). The copy does
not end in `.ts`, so Pi does not load it; delete it once you are satisfied with
the result.

There are two cases where no copy is made, because nothing is lost: adopting a
file whose contents already match the bundle, where only the manifest is
written, and `observal doctor cleanup` removing a tracked install that still
matches what Observal wrote. Every other refresh, restore, migration and
duplicate removal keeps one — a manifest records which CLI version wrote an
install, not that its bytes are still unedited.

### Migrating installs from before version tracking

CLI versions before the manifest existed wrote `extensions/observal.ts` with nothing beside it. Those installs are recognized by the doc header every Observal extension carries, and `observal auth login` or `observal doctor patch` migrates them: the existing file is copied to `observal.ts.bak` (`.bak.1`, `.bak.2`, … if that name is taken), the current extension is written in its place, and the manifest is created. The backup does not end in `.ts`, so Pi does not load it; delete it once you're satisfied with the upgrade.

If `extensions/observal.ts` exists without a matching manifest, doesn't match the bundled source, and carries no Observal header, Observal treats it as unmanaged and never overwrites or removes it — remove or move the file aside, then re-run `observal doctor patch --harness pi` to let Observal manage it.

## Safe writes

`observal doctor patch` and `observal doctor cleanup` write each managed configuration file atomically and preserve unrelated entries. Use `--dry-run --output json` to review planned harness changes before writing. Agent Pull reports every created or merged file in its result.

## File permissions

Client-side files under `~/.observal/` are created with mode `0600` (owner read/write only). This holds your access token, so don't loosen the permissions.

## Related

* [Environment variables](/reference/environment-variables) - supported runtime overrides
* [`observal config`](/cli/config), CLI surface for editing
