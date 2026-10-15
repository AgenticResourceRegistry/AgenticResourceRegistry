<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# observal registry

Publish and manage registry components. The registry has five component types: MCP servers, skills, hooks, prompts, and sandboxes.

## Subcommand structure

```text
observal registry <type> <action> [args]
```

| Type | Submit | List | My | Show | Install | Render | Edit | Fork |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mcp` | yes | yes | yes | yes | yes | no | yes | yes |
| `skill` | yes | yes | yes | yes | yes | no | yes | yes |
| `hook` | yes | yes | no | yes | yes | no | yes | yes |
| `prompt` | yes | yes | yes | yes | no | yes | yes | yes |
| `sandbox` | yes | yes | no | yes | no | no | yes | yes |

Every component type also supports archive, unarchive, ownership transfer, and co-author management. Registry also contains the `models`, `version`, `recommend`, and mixed `bulk` groups.

All registry references accept a UUID, canonical `namespace/slug`, a unique legacy bare name, a row number from the latest human list output for the same component type, or an `@alias`. Agents and scripts must use returned UUIDs or `qualified_name` values, never row numbers. If the same bare slug exists in multiple namespaces, qualify it, for example `alice/search` instead of `search`.

### Fork an approved component

```bash
observal registry skill fork-listing acme/review-skill --name my-review-skill --output json
observal registry mcp fork acme/github --version 1.2.0 --new-version 0.1.0 --output json
observal registry hook fork acme/audit --team platform --visibility team --output json
observal registry prompt fork acme/review --output json
observal registry sandbox fork acme/python --output json
```

The five `fork` commands accept `SOURCE [--name NAME] [--version BASE] [--new-version V] [--team HANDLE|UUID] [--visibility public|team] [--output table|json]`. Only currently approved sources and approved base versions are eligible. The fork is a separate editable **draft**, not a new release of the original; it has its own namespace, owner, reviews, and installation identity. Team-private sources can only be forked within their teamspace. JSON returns the direct server draft object. Edit using `observal registry <type> edit FORK_UUID ...` and submit through that type's draft workflow. `show` prints visible fork provenance and the count of public approved direct forks. If source access is lost, it displays **Source unavailable** rather than the saved source reference. A private or unpublished fork never increases the public count.

### Shared lifecycle and collaboration commands

```bash
observal registry skill archive alice/reviewer --yes --output json
observal registry skill unarchive alice/reviewer --yes --output json
observal registry skill transfer-owner alice/reviewer bob --yes --output json
observal registry skill co-authors list alice/reviewer --output json
observal registry skill co-authors add alice/reviewer bob@example.com --output json
observal registry skill co-authors remove alice/reviewer <user-uuid> --output json
```

Archive, restore, and ownership transfer require explicit confirmation in JSON mode. Their JSON output is the direct server result. Co-author list returns the standard list envelope; add and remove return the direct server result.

The namespace is the publisher's username or a teamspace handle. Usernames cannot change after the account owns a registry listing. Team members can browse approved private teamspace items in normal list results. Use `--team TEAM_HANDLE` to include public items plus that team's private items, or `--namespace TEAM_HANDLE` to restrict results to that namespace. Direct references use `team-handle/item-slug`. Nonmembers receive the same not-found response for private items as for unknown items.

### Teamspace visibility

Use the teamspace target and visibility options on submit commands:

```bash
observal registry skill submit --skill-md ./SKILL.md --team platform-tools --visibility public
observal registry skill submit --skill-md ./SKILL.md --team platform-tools --visibility team
observal registry skill list --team platform-tools --search 'frontend design' --harness claude-code --output json
observal registry skill list --namespace platform-tools --output json
observal registry skill show platform-tools/internal-skill --output json
```

`public` teamspace items are visible to all registry users. `team` items are visible only to team members and privileged reviewers. Team owners and reviewers can change visibility after publication. A team member's new submission still follows the normal review workflow.

---

## Mixed bulk submission

Submit up to 200 MCP, skill, hook, prompt, and sandbox entries from one JSON file:

```bash
observal registry bulk submit --from-file components.json --dry-run --output json
observal registry bulk submit --from-file components.json --yes --output json
```

The file is a bare array or an object with a `components` array. Each entry contains `type` plus the normal API submission fields:

```json
{
  "components": [
    {
      "type": "skill",
      "name": "review-helper",
      "version": "1.0.0",
      "description": "Reviews changes",
      "owner": "alice",
      "task_type": "code-review",
      "delivery_mode": "registry_direct",
      "skill_md_content": "---\nname: review-helper\ndescription: Reviews changes\n---\n"
    },
    {
      "type": "prompt",
      "name": "review-brief",
      "description": "Review prompt",
      "owner": "alice",
      "category": "general",
      "template": "Review {{change}}"
    }
  ]
}
```

Dry run validates file structure without contacting submission endpoints. Execution structurally validates every entry before the first mutation, submits entries in order, reports conflicts as skipped, and returns per-entry IDs, canonical names, review status, and safe errors. Results include `partial`; item errors set it to `true` and exit with code 11, while conflicts counted as skips remain successful. Authentication, permission, rate-limit, version, and service failures stop the batch. JSON execution requires `--yes`.

Re-running a partially completed file is safe only after inspecting results. Existing identities are skipped for component types that reject duplicates. Verify created items by returned UUID or `qualified_name`.

---

## MCP servers

MCP server registry commands for submitting, browsing, generating configuration, editing, and archiving MCP server listings.

### `observal registry mcp submit`

Submit an MCP server to the registry. By default, paste your server's JSON config (the same format you use in your harness). Use `--git` to analyze a git repository instead.

#### Synopsis

```bash
observal registry mcp submit [OPTIONS]
observal registry mcp submit --git <url> [OPTIONS]
```

#### Options

| Option | Short | Description |
| --- | --- | --- |
| `--git` | `-g` | Analyze a git repository instead of pasting config |
| `--name` | `-n` | Pre-fill server name (skip prompt) |
| `--category` | `-c` | Pre-fill category (skip prompt) |
| `--yes` | `-y` | Accept all defaults |
| `--draft` | | Save as draft instead of submitting for review |
| `--submit` | | Submit an existing draft for review (MCP ID) |
| `--output` | `-o` | Output format: `table` or `json` |

#### Default flow (JSON paste)

1. Prompts you to paste your MCP server JSON config.
2. Accepts multiple formats:
   - **harness config**: `{"mcpServers": {"name": {"command": "...", "args": [...], "env": {...}}}}`
   - **Bare config**: `{"command": "npx", "args": ["-y", "pkg"]}`
   - **SSE/HTTP**: `{"url": "http://...", "type": "sse", "headers": {...}}`
   - **server.json manifest**: `{"packages": [...], "remotes": [...]}`
3. Auto-detects environment variables from `$VAR` patterns and `env` keys.
4. Shows a config preview and prompts for metadata (name, description, category).
5. Submits to registry for review.

#### Git analysis flow (`--git`)

1. Shallow-clones the repo.
2. Detects the MCP framework (FastMCP, MCP SDK, TypeScript SDK, Go SDK).
3. Extracts server name, description, and exposed tools via AST.
4. Scans for required env vars (`os.environ`, `os.getenv`, `.env.example`, `server.json`).
5. Prompts for metadata confirmation.
6. Submits to registry for review.

#### Examples

```bash
# Paste config (default, recommended)
observal registry mcp submit

# Non-interactive with piped JSON
echo '{"command": "npx", "args": ["-y", "@example/mcp-server"]}' | observal registry mcp submit -y -n my-server -c developer-tools --output json

# Save as draft
observal registry mcp submit --draft

# Analyze a git repo
observal registry mcp submit --git https://github.com/MarkusPfundstein/mcp-obsidian

# Non-interactive git analysis
observal registry mcp submit --git https://github.com/sooperset/mcp-atlassian -y

# Submit an existing draft for review
observal registry mcp submit --submit my-server
```

#### Valid categories

`browser-automation`, `cloud-platforms`, `code-execution`, `communication`, `databases`, `developer-tools`, `devops`, `file-systems`, `finance`, `knowledge-memory`, `monitoring`, `multimedia`, `productivity`, `search`, `security`, `version-control`, `ai-ml`, `data-analytics`, `general`.

#### Valid transports

`stdio`, `sse`, `streamable-http`.

#### Valid frameworks

`python`, `docker`, `typescript`, `go`.

---

### `observal registry mcp list`

List approved MCP servers in the registry.

```bash
observal registry mcp list [--search TERM] [--category CAT] [--limit N] [--sort name|category|version] [--output table|json] [--interactive]
```

| Option | Short | Description |
| --- | --- | --- |
| `--search` | `-s` | Search by name or description |
| `--category` | `-c` | Filter by category |
| `--limit` | `-n` | Max results (default: 50) |
| `--sort` | | Sort by: `name`, `category`, `version` |
| `--output` | `-o` | Output format: `table`, `json` |
| `--interactive` | `-i` | Open a fuzzy-search picker |

```bash
observal registry mcp list --search github
observal registry mcp list --category ai-ml --output json
observal registry mcp list --interactive
observal registry mcp list --sort category --limit 10
```

---

### `observal registry mcp my`

List your own MCP servers across all statuses (draft, pending, approved, rejected).

```bash
observal registry mcp my [--output table|json]
```

```bash
observal registry mcp my
observal registry mcp my --output json
```

---

### `observal registry mcp show`

Show full details of an MCP server including validation results, env vars, and supported harnesses.

```bash
observal registry mcp show <id-or-name> [--output table|json]
```

```bash
observal registry mcp show my-server
observal registry mcp show 3
observal registry mcp show @fav --output json
```

---

### `observal registry mcp install`

Generate a harness config snippet for an MCP server. This command does not write harness configuration or record an installation. Interactive mode prompts for required environment variables and headers. JSON mode requires `--no-prompt` and rejects missing required values before config generation with `error.result.needs_input: true`.

```bash
observal registry mcp install <id-or-name> --harness <harness> [options]
```

| Option | Short | Description |
| --- | --- | --- |
| `--harness` | `-i` | Target harness (required) |
| `--version` | `-V` | Generate configuration from that exact version instead of the latest |
| `--env` | `-e` | Environment value as `KEY=VALUE`; repeatable |
| `--header` | | Header value as `KEY=VALUE`; repeatable |
| `--env-file` | | Read environment values from a file |
| `--no-prompt` | `-y` | Use supplied values without prompting; JSON fails if required values are missing |
| `--raw` | | Output only a bare config template; missing values remain explicit placeholders |
| `--output` | `-o` | Output the complete operation result as table or JSON |

```bash
observal registry mcp install my-server --harness claude-code
observal registry mcp install my-server --harness cursor --raw > .cursor/mcp.json
observal registry mcp install 2 --harness copilot
observal registry mcp install @db --harness kiro
```

---

### `observal registry mcp edit`

Edit an MCP server submission. For draft/pending/rejected listings, edits in place. For approved listings, publishes a new version with a semver bump.

```bash
observal registry mcp edit <id-or-name> [OPTIONS]
```

| Option | Short | Description |
| --- | --- | --- |
| `--from-file` | `-f` | Load updates from a JSON file |
| `--name` | `-n` | New listing name |
| `--description` | `-d` | New description |
| `--category` | `-c` | New category |
| `--version` | `-v` | New version string |
| `--git-url` | | New git URL |
| `--command` | | New command |
| `--url` | | New URL (SSE/HTTP) |
| `--bump` | | Version bump for approved listings: `patch`, `minor`, or `major` |
| `--changelog` | | Changelog for an approved-listing version |
| `--output` | `-o` | Output format: `table` or `json` |

Without flags, opens an interactive JSON paste prompt (same format as submit).

```bash
# Interactive JSON paste edit
observal registry mcp edit my-server

# Update specific fields
observal registry mcp edit my-server -d "New description" -c databases

# Load updates from a file
observal registry mcp edit my-server --from-file updates.json

# Bump version on an approved listing
observal registry mcp edit my-server --version 1.2.0
```

---

### `observal registry mcp transfer-owner`

Transfer ownership to another username. You stop being the owner immediately.

```bash
observal registry mcp transfer-owner my-server @alice -y
```

---

### `observal registry mcp sync`

Publish a new MCP version automatically when the listing's GitHub or GitLab repository changes. See [Sync an MCP server from GitHub or GitLab](../use-cases/mcp-repo-sync.md) for the full setup.

| Command | Description |
| --- | --- |
| `enable <id-or-name>` | Turn on sync, or change its triggers. Prints the webhook URL, and the secret on first enable |
| `status <id-or-name>` | Show the triggers, tracked branch, and last sync result |
| `run <id-or-name>` | Sync the tracked branch now |
| `rotate-secret <id-or-name>` | Issue a new webhook secret |
| `disable <id-or-name>` | Turn off sync. Published versions are kept |

| `enable` option | Short | Description |
| --- | --- | --- |
| `--push / --no-push` | | Publish a version on each push to the tracked branch (on by default) |
| `--release / --no-release` | | Publish a version when a release is published (off by default) |
| `--branch` | `-b` | Branch to track. Defaults to the repository's default branch |
| `--provider` | | `github` or `gitlab`. Detected from the repository URL; set it for a self-managed GitLab whose hostname does not contain `gitlab` |
| `--output` | `-o` | Output format: `table` or `json` |

On an existing setup, `enable` keeps any setting you do not pass.

```bash
# Push sync on the default branch
observal registry mcp sync enable alice/weather-mcp

# Releases only
observal registry mcp sync enable alice/weather-mcp --release --no-push

# Self-managed GitLab on its own hostname
observal registry mcp sync enable alice/weather-mcp --provider gitlab

# Check the last result
observal registry mcp sync status alice/weather-mcp --output json
```

---

## Skills

Skill registry commands. Skills are portable SKILL.md instruction packages that provide agents with task-specific guidance.

Valid task types: `code-review`, `code-generation`, `testing`, `documentation`, `debugging`, `refactoring`, `deployment`, `security-audit`, `performance`, `general`.

### `observal registry skill submit`

Submit a new skill for review. Provide `--git-url` to let the server fetch SKILL.md automatically, or use `--skill-md` to paste content directly.

```bash
observal registry skill submit [OPTIONS]
```

| Option | Short | Description |
| --- | --- | --- |
| `--from-file` | `-f` | Create from JSON file |
| `--skill-md` | | Path to SKILL.md (auto-fills fields from frontmatter) |
| `--git-url` | | Git repository URL |
| `--git-ref` | | Branch or tag (default: main) |
| `--draft` | | Save as draft instead of submitting for review |
| `--submit` | | Submit a draft for review (skill ID) |
| `--output` | `-o` | Output format: `table` or `json` |

```bash
observal registry skill submit --git-url https://github.com/org/repo
observal registry skill submit --from-file skill.json
observal registry skill submit --skill-md ./SKILL.md --git-url https://github.com/org/repo --name review --description "Review code" --task-type code-review --output json
observal registry skill submit --draft
observal registry skill submit --submit abc123
```

---

### `observal registry skill list`

List approved skills in the registry.

```bash
observal registry skill list [--task-type TYPE] [--target-agent AGENT] [--search TERM] [--output table|json]
```

| Option | Short | Description |
| --- | --- | --- |
| `--task-type` | `-t` | Filter by task type |
| `--target-agent` | | Filter by target agent |
| `--search` | `-s` | Search by name or description |
| `--output` | `-o` | Output format: `table`, `json` |

```bash
observal registry skill list
observal registry skill list --task-type code-review
observal registry skill list --target-agent claude-code --output json
observal registry skill list --search "refactor"
```

---

### `observal registry skill my`

List your own skills across all statuses (draft, pending, approved, rejected).

```bash
observal registry skill my [--output table|json]
```

```bash
observal registry skill my
observal registry skill my --output json
```

---

### `observal registry skill show`

Show detailed information about a skill, including validation status, task type, git source, and slash command.

```bash
observal registry skill show <id-or-name> [--output table|json]
```

```bash
observal registry skill show my-skill
observal registry skill show 1
observal registry skill show @refactor-skill --output json
```

---

### `observal registry skill install`

Install a skill by fetching the full skill directory from git. Clones the skill directory via sparse checkout and writes it to the appropriate harness skill path.

```bash
observal registry skill install <id-or-name> --harness <harness> [--scope user|project] [--raw] [--no-write]
```

| Option | Short | Description |
| --- | --- | --- |
| `--harness` | `-i` | Target harness (required) |
| `--scope` | `-s` | Install scope: `user` (global, default) or `project` |
| `--raw` | | Output raw JSON only |
| `--no-write` | | Generate config without writing skill files or lockfile state |
| `--version` | `-V` | Install one version instead of the latest |
| `--output` | `-o` | Output the operation result as table or JSON |

Scopes:
- `user` (default): writes to `~/.<harness>/skills/<name>/` globally.
- `project`: writes to `.agents/skills/<name>/` in the current directory, then symlinks into detected harness config directories.

JSON output does not disable installation. It returns whether files were written and the installed path. Raw and no-write modes do not record the skill as installed. An installed skill is recorded with its exact version, version id, and digest, with or without `--version`. A failed file write or lockfile update returns a categorized failure instead of reporting success.

```bash
observal registry skill install my-skill --harness claude-code
observal registry skill install @sk --harness kiro --scope project
observal registry skill install 2 --harness cursor --raw
observal registry skill install my-skill --harness antigravity --no-write
```

---

### `observal registry skill edit`

Edit a draft, pending, or rejected skill submission. Acquires an edit lock to prevent concurrent modifications.

```bash
observal registry skill edit <id-or-name> [OPTIONS]
```

| Option | Short | Description |
| --- | --- | --- |
| `--from-file` | `-f` | Load updates from JSON file |
| `--name` | `-n` | New listing name |
| `--description` | `-d` | New description |
| `--version` | `-v` | New version string |
| `--task-type` | `-t` | New task type |
| `--git-url` | | New git URL |
| `--git-ref` | | New git ref |
| `--output` | `-o` | Output format: `table` or `json` |

```bash
observal registry skill edit my-skill --description "Better desc"
observal registry skill edit abc123 --from-file updates.json
observal registry skill edit @sk --git-url https://github.com/org/new-repo
observal registry skill edit 2 --version 2.0.0 --task-type debugging
```

---

### `observal registry hook submit`

Submit a new hook for review. Supports inline script content via `--script`, or git-hosted hooks via `--source-url`.

```bash
observal registry hook submit [OPTIONS]
```

| Option | Short | Description |
| --- | --- | --- |
| `--from-file` | `-f` | Create from JSON file |
| `--draft` | | Save as draft instead of submitting for review |
| `--submit` | | Submit a draft for review (hook ID) |
| `--script` | | Path to hook script file (content stored in registry) |
| `--source-url` | | Git repo containing hook scripts |
| `--source-ref` | | Branch/tag to track (default: main) |
| `--source-path` | | Directory within repo containing hook files |
| `--requires` | | Install prerequisites (repeatable) |
| `--output` | `-o` | Output format: `table` or `json` |

```bash
observal registry hook submit
observal registry hook submit --script ./protect-files.sh
observal registry hook submit --source-url https://github.com/org/hooks --source-path hooks/guard/
observal registry hook submit --from-file hook.json
observal registry hook submit --draft
observal registry hook submit --submit abc123
```

---

### `observal registry hook list`

List approved hooks from the registry.

```bash
observal registry hook list [--event EVENT] [--search TERM] [--output table|json]
```

| Option | Short | Description |
| --- | --- | --- |
| `--event` | `-e` | Filter by event type |
| `--search` | `-s` | Search by name or description |
| `--output` | `-o` | Output format: `table`, `json` |

```bash
observal registry hook list
observal registry hook list --event Stop
observal registry hook list --search guard --output json
```

---

### `observal registry hook show`

Show detailed information for a single hook, including event type, handler config, and execution mode.

```bash
observal registry hook show <id-or-name> [--output table|json]
```

```bash
observal registry hook show my-hook
observal registry hook show 1
observal registry hook show @guard --output json
```

---

### `observal registry hook install`

Install a hook for a specific harness. Writes script files and merges hook config into the harness's settings. Existing hooks are preserved during merge.

```bash
observal registry hook install <id-or-name> --harness <harness> [--platform PLATFORM] [--raw] [--dir DIR] [--version VERSION]
```

| Option | Short | Description |
| --- | --- | --- |
| `--harness` | `-i` | Target harness (required) |
| `--platform` | `-p` | Platform: `win32`, `darwin`, `linux` |
| `--raw` | | Output raw JSON only (no file writes) |
| `--dir` | `-d` | Project directory for file writes (default: cwd) |
| `--version` | `-V` | Install one version instead of the latest |
| `--output` | `-o` | Output the complete installation result as table or JSON |

```bash
observal registry hook install my-hook --harness claude-code
observal registry hook install @guard --harness kiro --dir ./project
observal registry hook install my-hook --harness cursor --raw
observal registry hook install my-hook --harness claude-code --platform darwin
observal registry hook install my-hook --harness claude-code --output json
```

Hook installation validates every path before writing, refuses to replace malformed existing JSON, writes files atomically, and does not duplicate an existing event entry when repeated. The lockfile records the installed version, its version id and digest, and any version you requested.

---

### `observal registry hook edit`

Edit a draft, pending, or rejected hook submission. Acquires an edit lock to prevent concurrent modifications.

```bash
observal registry hook edit <id-or-name> [OPTIONS]
```

| Option | Short | Description |
| --- | --- | --- |
| `--from-file` | `-f` | Load updates from JSON file |
| `--name` | `-n` | New listing name |
| `--description` | `-d` | New description |
| `--version` | `-v` | New version string |
| `--event` | `-e` | New event type |
| `--output` | `-o` | Output format: `table` or `json` |

```bash
observal registry hook edit my-hook --description "Updated guard hook"
observal registry hook edit my-hook --event Stop --version 1.1.0
observal registry hook edit @guard --from-file updated-hook.json
observal registry hook edit 1 --name new-name
```

---

### `observal registry prompt submit`

Submit a new prompt template for review. You can submit interactively, from a JSON file, or from a raw template file.

```bash
observal registry prompt submit [OPTIONS]
```

| Option | Short | Description |
| --- | --- | --- |
| `--from-file` | `-f` | Create from JSON file, or read template from a text file |
| `--draft` | | Save as draft instead of submitting for review |
| `--submit` | | Submit a draft for review (prompt ID) |
| `--output` | `-o` | Output format: `table` or `json` |

If `--from-file` points to a non-JSON file, its content is used as the template and you are prompted for metadata interactively.

```bash
observal registry prompt submit
observal registry prompt submit --from-file prompt.json
observal registry prompt submit --from-file template.md
observal registry prompt submit --draft
observal registry prompt submit --submit abc123
```

---

### `observal registry prompt list`

List approved prompts in the registry.

```bash
observal registry prompt list [--category CAT] [--search TERM] [--output table|json]
```

| Option | Short | Description |
| --- | --- | --- |
| `--category` | `-c` | Filter by category |
| `--search` | `-s` | Search by name or description |
| `--output` | `-o` | Output format: `table`, `json` |

```bash
observal registry prompt list
observal registry prompt list --category code-review
observal registry prompt list --search "refactor" --output json
```

---

### `observal registry prompt my`

List your own prompts across all statuses (draft, pending, approved, rejected).

```bash
observal registry prompt my [--output table|json]
```

```bash
observal registry prompt my
observal registry prompt my --output json
```

---

### `observal registry prompt show`

Show detailed information about a prompt, including the template content.

```bash
observal registry prompt show <id-or-name> [--output table|json]
```

```bash
observal registry prompt show my-prompt
observal registry prompt show 1
observal registry prompt show @refactor-prompt --output json
```

---

### `observal registry prompt render`

Render a prompt template with variable substitution. Sends key=value pairs to the server, which substitutes them into the template and returns the rendered output.

```bash
observal registry prompt render <id-or-name> --var key=value [--var key2=value2 ...]
```

| Option | Short | Description |
| --- | --- | --- |
| `--var` | `-v` | Variable as `key=value` (repeatable) |
| `--output` | `-o` | Output format: `table` or `json` |

```bash
observal registry prompt render my-prompt --var lang=python
observal registry prompt render @tpl --var file=main.py --var task=refactor
```

---

### `observal registry prompt edit`

Edit a draft, pending, or rejected prompt submission. Acquires an edit lock to prevent concurrent modifications.

```bash
observal registry prompt edit <id-or-name> [OPTIONS]
```

| Option | Short | Description |
| --- | --- | --- |
| `--from-file` | `-f` | Load updates from JSON file |
| `--name` | `-n` | New listing name |
| `--description` | `-d` | New description |
| `--version` | `-v` | New version string |
| `--category` | `-c` | New category |
| `--template` | `-t` | New template text |
| `--output` | `-o` | Output format: `table` or `json` |

```bash
observal registry prompt edit my-prompt --description "Updated desc"
observal registry prompt edit abc123 --from-file updates.json
observal registry prompt edit @tpl --template "New template: {{ var }}"
observal registry prompt edit 2 --version 2.0.0 --category debugging
```

---

### `observal registry sandbox submit`

Submit a new sandbox environment for review.

```bash
observal registry sandbox submit [OPTIONS]
```

| Option | Short | Description |
| --- | --- | --- |
| `--from-file` | `-f` | Create from JSON file |
| `--draft` | | Save as draft instead of submitting for review |
| `--submit` | | Submit a draft for review (sandbox ID) |
| `--output` | `-o` | Output format: `table` or `json` |

```bash
observal registry sandbox submit
observal registry sandbox submit --from-file sandbox.json
observal registry sandbox submit --draft
observal registry sandbox submit --submit abc123
```

---

### `observal registry sandbox list`

List approved sandboxes in the registry.

```bash
observal registry sandbox list [--runtime TYPE] [--search TERM] [--output table|json]
```

| Option | Short | Description |
| --- | --- | --- |
| `--runtime` | `-r` | Filter by runtime type |
| `--search` | `-s` | Search by name or description |
| `--output` | `-o` | Output format: `table`, `json` |

```bash
observal registry sandbox list
observal registry sandbox list --runtime docker
observal registry sandbox list --search "node" --output json
```

---

### `observal registry sandbox show`

Show detailed information about a sandbox, including runtime type, container image, and resource limits.

```bash
observal registry sandbox show <id-or-name> [--output table|json]
```

```bash
observal registry sandbox show my-sandbox
observal registry sandbox show 1
observal registry sandbox show @dev-env --output json
```

---

Sandboxes are attached to agents by UUID and are installed when the agent is pulled. There is no standalone Sandbox install command.

```bash
observal agent add sandbox <sandbox-uuid>
observal agent build
```

---

### `observal registry sandbox edit`

Edit a draft, pending, or rejected sandbox submission. Acquires an edit lock to prevent concurrent modifications.

```bash
observal registry sandbox edit <id-or-name> [OPTIONS]
```

| Option | Short | Description |
| --- | --- | --- |
| `--from-file` | `-f` | Load updates from JSON file |
| `--name` | `-n` | New listing name |
| `--description` | `-d` | New description |
| `--version` | `-v` | New version string |
| `--runtime-type` | `-r` | New runtime type |
| `--image` | `-i` | New container image |
| `--output` | `-o` | Output format: `table` or `json` |

```bash
observal registry sandbox edit my-sandbox --image node:20-alpine
observal registry sandbox edit abc123 --from-file updates.json
observal registry sandbox edit @env --runtime-type docker --version 2.0.0 --output json
```

---

## Component versions

Use `observal registry version publish` and `observal registry version list` for all five component types. Publication supports direct JSON results; history supports explicit pagination.

See [`observal registry version`](component.md) for the complete contract.

## Personalized recommendations

Use `observal registry recommend` to rank visible components against the signed-in user's sessions and to dismiss or mark recommendations as installed.

See [`observal registry recommend`](recommend.md) for the JSON schema and feedback actions.
