---
# SPDX-FileCopyrightText: 2026 AgenticResourceRegistry contributors
# SPDX-License-Identifier: Apache-2.0
title: "observal registry mcp"
---

Submit, browse, install, and manage MCP server listings in the registry. See [Publish an MCP configuration](/registry-mcp-helper) for the owner-to-reviewer-to-consumer path and authoring guidance.

## Commands

| Command | Description |
| --- | --- |
| `submit` | Submit a new MCP server or save a draft |
| `list` | List approved visible MCP servers |
| `my` | List your MCP servers across all statuses |
| `show` | Show one MCP server including env vars and validation results |
| `fork` | Copy an approved MCP server release into an independent draft |
| `install` | Generate or apply a harness config snippet for an MCP server |
| `edit` | Edit a draft, pending, rejected, or approved MCP server |
| `sync` | Publish new versions automatically from a GitHub or GitLab repo |
| `transfer-owner` | Transfer ownership |
| `co-authors` | List, add, or remove co-authors |

All structured commands support table and JSON output.

## Submit

The submit command accepts a JSON config you paste interactively, piped input, or a git repository analyzed with `--git`. All three paths end at the same review queue.

### Input formats

The JSON paste flow accepts four config shapes:

| Format | Example snippet |
| --- | --- |
| Harness config | `{"mcpServers": {"name": {"command": "...", "args": [...], "env": {...}}}}` |
| Bare config | `{"command": "npx", "args": ["-y", "pkg"]}` |
| SSE/HTTP | `{"url": "http://...", "type": "sse", "headers": {...}}` |
| `server.json` manifest | `{"packages": [...], "remotes": [...]}` |

Environment variables are detected automatically from `$VAR` patterns and `env` keys.

### server.json

A `server.json` manifest bundles multiple transports and package entries in one file. The registry reads `packages` for stdio-based servers and `remotes` for SSE/HTTP entries. When you submit via `--git`, the tool scans for a `server.json` in the repository root alongside `.env.example` and inline `os.environ`/`os.getenv` calls.

### Drafts

Pass `--draft` to save without submitting for review. Resume later with `--submit <id-or-name>`.

```bash
observal registry mcp submit --draft
observal registry mcp submit --submit my-server
```

### Git analysis

`--git` shallow-clones the repository, detects the MCP framework (FastMCP, MCP SDK, TypeScript SDK, Go SDK), extracts the server name, description, and exposed tools via AST, and scans for required env vars. It then prompts for metadata confirmation before submitting.

```bash
observal registry mcp submit --git https://github.com/MarkusPfundstein/mcp-obsidian

# Non-interactive git analysis
observal registry mcp submit --git https://github.com/sooperset/mcp-atlassian -y
```

### Full examples

```bash
# Paste config (default, recommended)
observal registry mcp submit

# Non-interactive with piped JSON
echo '{"command": "npx", "args": ["-y", "@example/mcp-server"]}' | \
  observal registry mcp submit -y -n my-server -c developer-tools --output json

# Analyze a git repo and save a draft
observal registry mcp submit --git https://github.com/example/mcp-tool --draft

# Submit an existing draft for review
observal registry mcp submit --submit my-server --output json
```

### Options

| Option | Short | Description |
| --- | --- | --- |
| `--git` | `-g` | Analyze a git repository instead of pasting config |
| `--name` | `-n` | Pre-fill server name (skip prompt) |
| `--category` | `-c` | Pre-fill category (skip prompt) |
| `--yes` | `-y` | Accept all defaults |
| `--draft` | | Save as draft instead of submitting for review |
| `--submit` | | Submit an existing draft for review (MCP ID) |
| `--output` | `-o` | Output format: `table` or `json` |

### Valid categories

`browser-automation`, `cloud-platforms`, `code-execution`, `communication`, `databases`, `developer-tools`, `devops`, `file-systems`, `finance`, `knowledge-memory`, `monitoring`, `multimedia`, `productivity`, `search`, `security`, `version-control`, `ai-ml`, `data-analytics`, `general`.

### Valid transports

`stdio`, `sse`, `streamable-http`. The older `http` spelling is accepted and stored as `streamable-http`.

### Valid frameworks

`python`, `docker`, `typescript`, `go`.

## List and show

```bash
observal registry mcp list --search github
observal registry mcp list --category ai-ml --output json
observal registry mcp list --interactive
observal registry mcp list --sort category --limit 10
observal registry mcp my --output json
observal registry mcp show my-server
observal registry mcp show 3
observal registry mcp show @fav --output json
```

Row numbers are scoped to the latest MCP list. Empty lists clear previous MCP row references. `show` includes validation results, detected env vars, and the list of supported harnesses.

## Fork

```bash
observal registry mcp fork acme/github --version 1.2.0 --new-version 0.1.0 --output json
observal registry mcp edit FORK_UUID --description 'Customized GitHub server' --output json
observal registry mcp submit --submit FORK_UUID --output json
```

Only approved source listings and versions can be forked. The fork is an independent draft with its own namespace, owner, reviews, and installation identity. Team-private sources can only be forked within their teamspace. Use the returned UUID to edit and submit without affecting the source.

## Install

Generate a harness config snippet or apply it directly with `--apply`.

```bash
observal registry mcp install my-server --harness claude-code
observal registry mcp install my-server --harness cursor --raw
observal registry mcp install 2 --harness copilot
observal registry mcp install @db --harness kiro
```

| Option | Short | Description |
| --- | --- | --- |
| `--harness` | `-i` | Target harness (required) |
| `--version` | `-V` | Use a specific version instead of the latest |
| `--env` | `-e` | Environment value as `KEY=VALUE`; repeatable |
| `--header` | | Header value as `KEY=VALUE`; repeatable |
| `--env-file` | | Read environment values from a file |
| `--no-prompt` | `-y` | Use supplied values without prompting |
| `--raw` | | Emit only the bare config template; missing values become explicit placeholders |
| `--apply` | | Opt in to Claude Code setup and local installation tracking |
| `--dir` | `-d` | Explicit target directory for `--apply` |
| `--scope` | | Explicit project/user scope for `--apply` |
| `--output` | `-o` | Output format: `table` or `json` |

Without `--apply`, the command never writes harness configuration or records a local installation. JSON mode requires `--no-prompt` and fails early with `error.result.needs_input: true` when required env values are missing. Do not redirect raw output over an existing config as a blind merge. Credential flags are visible in process arguments and history; prefer interactive or file-based input for secrets.

## Edit

```bash
observal registry mcp edit my-server
observal registry mcp edit my-server -d "New description" -c databases
observal registry mcp edit my-server --from-file updates.json
observal registry mcp edit my-server --version 1.2.0
```

For draft, pending, and rejected listings, edits in place. For approved listings, publishes a new version with a semver bump. Without flags, opens an interactive JSON paste prompt using the same format as submit.

## Sync

Publish a new version automatically whenever the linked GitHub or GitLab repository changes. See [Sync an MCP server from GitHub or GitLab](/use-cases/mcp-repo-sync) for the full setup.

| Subcommand | Description |
| --- | --- |
| `enable <id-or-name>` | Turn on sync, or update its triggers. Prints the webhook URL and secret on first enable |
| `status <id-or-name>` | Show triggers, tracked branch, and last sync result |
| `run <id-or-name>` | Sync the tracked branch immediately |
| `rotate-secret <id-or-name>` | Issue a new webhook secret |
| `disable <id-or-name>` | Turn off sync; published versions are kept |

```bash
observal registry mcp sync enable alice/weather-mcp
observal registry mcp sync enable alice/weather-mcp --release --no-push
observal registry mcp sync enable alice/weather-mcp --provider gitlab
observal registry mcp sync status alice/weather-mcp --output json
```

## Other commands

```bash
# Transfer ownership
observal registry mcp transfer-owner my-server @alice -y

# Archive and restore
observal registry mcp archive my-server --yes --output json
observal registry mcp unarchive my-server --yes --output json

# Co-author management
observal registry mcp co-authors list my-server --output json
observal registry mcp co-authors add my-server bob@example.com --output json
observal registry mcp co-authors remove my-server <user-uuid> --output json
```

Archive, restore, and ownership transfer require explicit confirmation in JSON mode. Co-author list returns the standard list envelope; add and remove return the direct server result.

## Related

* [`observal registry`](/cli/registry): complete registry reference
* [Publish an MCP configuration](/registry-mcp-helper): authoring guide and checkpoint walkthrough
* [Sync an MCP server from GitHub or GitLab](/use-cases/mcp-repo-sync): webhook setup and trigger options
* [`observal agent`](/cli/agent): attach MCP servers to agents
