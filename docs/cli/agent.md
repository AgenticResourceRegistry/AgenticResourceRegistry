<!-- SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# `observal agent`

Create, compose, publish, install, and govern agents. An agent bundles MCP servers, skills, hooks, prompts, and sandboxes into one versioned Registry object.

Canonical identities use `namespace/slug`. Commands also accept UUIDs, unambiguous bare names, aliases, and row numbers from the latest Agent list.

## Commands

| Command | Purpose |
| --- | --- |
| `create` | Create from flags, JSON, or an interactive wizard |
| `bulk-create` | Validate or create multiple agents from JSON |
| `list` | List approved visible agents |
| `my` | List agents owned or co-authored by the user |
| `show` | Show an agent and its composition, including visible fork provenance and public fork count |
| `fork` | Copy an approved agent release into an independent draft |
| `install` | Generate an installation config without writing files |
| `pull` | Generate and write a complete harness installation |
| `archive` | Archive an agent |
| `delete` | Compatibility alias for `archive` |
| `unarchive` | Restore an archived agent |
| `init` | Create a local `observal-agent.yaml` |
| `add` | Add a Registry component UUID to local YAML |
| `build` | Validate local composition and target scope |
| `publish` | Create, update, save, or submit an agent |
| `release` | Publish a reviewed version bump |
| `versions` | List version history |
| `outdated` | Show which pinned components have newer approved releases |
| `transfer-owner` | Transfer ownership |
| `co-authors` | List, add, or remove co-authors |

Every leaf command supports `--output table|json`. JSON success output contains no Rich text, prompt, banner, or spinner. JSON failures leave stdout empty and write one categorized object to stderr.

## Create

Use complete flags for automation:

```bash
observal agent create \
  --name reviewer \
  --description 'Reviews changes' \
  --prompt 'Review carefully.' \
  --model claude-sonnet-4 \
  --harness kiro \
  --output json
```

Other modes:

```bash
observal agent create --from-file agent.json --output json
observal agent create
```

The no-flag form is interactive and cannot run in JSON mode. Flag mode requires `--name` and either `--prompt` or `--prompt-file`. Versions must be semantic versions and every repeated `--harness` must be registered.

`--team HANDLE --visibility team` creates a private teamspace agent. `--visibility team` requires `--team`.

JSON returns the direct server Agent object.

## Bulk create

Input may be an array or an object containing an `agents` array:

```json
{
  "agents": [
    {
      "name": "reviewer",
      "version": "1.0.0",
      "owner": "alice",
      "prompt": "Review carefully.",
      "model_name": "claude-sonnet-4",
      "components": []
    }
  ]
}
```

```bash
observal agent bulk-create --from-file agents.json --dry-run --output json
observal agent bulk-create --from-file agents.json --yes --output json
```

Input contains 1-50 agents. The CLI rejects malformed fields and duplicate canonical names before HTTP, and the server applies the same duplicate rules. JSON mode requires either `--dry-run` or `--yes`. Results include per-agent statuses, summary counts, and `partial`. Item errors set `partial: true` and exit with code 11; skips alone remain successful.

## List, my, and show

```bash
observal agent list --search 'incident response' --output json
observal agent list --namespace alice --page 2 --limit 20 --output json
observal agent list --team platform --output json
observal agent my --output json
observal agent show alice/reviewer --output json
```

`list` JSON is paginated:

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 50
}
```

The most recent `list` or `my` result is cached for row-number references. Empty results clear stale Agent rows. `--interactive` is human-only and cannot be combined with JSON.

`my` returns the standard `items`, `total`, `page`, and `page_size` envelope, including pending, approved, rejected, and archived agents. Empty results use `items: []`.

`show` returns the direct Agent object, including component links and success criteria.

## Fork an approved agent

```bash
observal agent fork acme/pr-reviewer --name my-reviewer --output json
observal agent fork acme/pr-reviewer --version 2.0.0 --new-version 0.1.0 --team payments --visibility team --output json
observal agent fork acme/pr-reviewer --dir ./my-reviewer --output json
```

`SOURCE` accepts a UUID, `namespace/slug`, an alias, or a cached row number. Only a currently approved listing and an approved source version can be forked. The default is the newest stable approved version. A fork begins as **your draft** in a personal or permitted team namespace; it keeps approved component references, not copies of the components. A private source may be forked only into the same private teamspace. A name collision returns conflict rather than changing an existing agent. A disabled fork setting returns permission denied; per-user rate limiting returns exit code 8.

`--dir` creates a new `observal-agent.yaml` containing the fork ID and editable fields, including exact component pins, `model_config_json`, and `external_mcps`. It refuses to overwrite an existing file *before* sending the fork request. JSON returns the direct draft object, plus `yaml_path` when provided. To edit this fork without creating a second listing:

```bash
observal agent build --dir ./my-reviewer --output json
observal agent publish --update --dir ./my-reviewer --output json
observal agent publish --submit AGENT_UUID --output json
```

`--update` uses the scaffold's `agent_id` to target the draft exactly, even though drafts do not appear in the public search. The submit command uses the ID returned by fork. A draft requires review before appearing in public fork counts or lists. `show` displays **Source unavailable** instead of saved source identity when access to the original has been lost; never infer provenance from a cached reference.

## Generate installation config

`install` asks the server to generate config but does not write it:

```bash
observal agent install alice/reviewer --harness kiro --output json
```

JSON returns the complete server installation result. Legacy `--raw` prints only `config_snippet`; new automation should use the shared output option.

Use [`observal agent pull`](pull.md) to write and track the generated installation.

## Archive, delete, and restore

```bash
observal agent archive alice/reviewer --yes --output json
observal agent delete alice/reviewer --yes --output json
observal agent unarchive alice/reviewer --yes --output json
```

`delete` is an alias for the same reversible archive operation. JSON mode never prompts and requires `--yes`. JSON returns the direct updated Agent object.

## Local authoring workflow

### Initialize

```bash
observal agent init \
  --dir ./reviewer \
  --name reviewer \
  --description 'Reviews changes' \
  --prompt-file ./PROMPT.md \
  --model claude-sonnet-4 \
  --harness kiro \
  --output json
```

The no-flag form is interactive. JSON mode requires complete flags and refuses to overwrite an existing definition. Writes are atomic.

#### From a working session

```bash
observal agent init --from-capabilities --name pr-review-flow --dir ./pr-review-flow --output json
observal agent init --from-capabilities --since 3d --name data-helper --output json
```

`--from-capabilities` pre-fills `components` and `supported_harnesses` from the
resources recorded in the capability lock for the current directory (see
[`observal discover`](discover.md)): everything loaded with `discover use` and
every component installed here in the last 24 hours (`--since` accepts `45m`,
`2h`, `3d`, `1w`). In JSON mode a default description and prompt are filled in
when omitted. Agents cannot nest other agents, so a pulled agent is reported
under `from_capabilities.skipped_agents` rather than added. Exit code `5` when
nothing was used in the window.

JSON returns:

```json
{
  "path": "/work/reviewer/observal-agent.yaml",
  "agent": {
    "name": "reviewer",
    "version": "1.0.0"
  }
}
```

### Add components

Find a component in Registry JSON, then copy its UUID:

```bash
observal registry skill list --search review --output json
observal agent add skill 22222222-2222-2222-2222-222222222222 --dir ./reviewer --output json
```

Valid types are `mcp`, `skill`, `hook`, `prompt`, and `sandbox`. Duplicate type and UUID pairs return conflict exit code 6. Invalid types or IDs return validation exit code 7.

Pin an exact component release with `--version`:

```bash
observal agent add mcp 33333333-3333-3333-3333-333333333333 --version 1.4.2 --dir ./reviewer --output json
```

This writes `version: 1.4.2` on the component entry. Without a version, the Agent keeps the version its current release pins, or pins the latest approved release when it first gains the component. Versions are matched exactly; `latest` and ranges are not accepted. A release pins approved versions only; a draft may also pin a component release still in review, but only one you can read (your own component, or as an admin or reviewer).

JSON returns the YAML path and added component.

### Build

```bash
observal agent build --dir ./reviewer --output json
observal agent build --dir ./reviewer --team platform --visibility team --output json
```

Build verifies every component, checks that any pinned component `version` exists, and validates whether private components are visible to the target owner. A successful JSON result contains `valid`, `agent`, `components`, and `issues`. Invalid composition exits with code 7 and leaves JSON stdout empty; the stderr error includes the same repairable data under `error.result`.

### Publish

```bash
observal agent publish --dir ./reviewer --output json
observal agent publish --dir ./reviewer --draft --output json
observal agent publish --submit alice/reviewer --output json
observal agent publish --dir ./reviewer --update --bump minor --output json
```

`--draft` and `--submit` are mutually exclusive. Scope changes cannot be combined with `--update`; use ownership transfer or a separate visibility operation. Non-interactive updates may use `--bump patch|minor|major`.

`--update` edits the latest version in place, so it only works while that version is a draft, pending, or rejected. An approved version is immutable: installs and locks depend on it. The server answers conflict exit code 6 and points to `observal agent release`, which publishes a new version for review.

JSON returns the direct created, saved, submitted, or updated Agent object.

## Release and versions

```bash
observal agent release alice/reviewer --bump patch --dir ./reviewer --output json
observal agent versions alice/reviewer --page 1 --page-size 50 --output json
```

Release obtains the server's semantic-version suggestion, submits the complete YAML snapshot, then updates local YAML atomically only after the server accepts the release. A failed server request leaves the local version unchanged.

Every release pins each component to an exact version, and those pins are what every pull of the release installs. A release keeps the pins of the Agent's current release, so components never move unless the author asks:

* give a component a `version` in the YAML to pin that release;
* pass `--refresh-components` to move every other component to its latest approved release.

```bash
observal agent release alice/reviewer --bump minor --dir ./reviewer --refresh-components --output json
```

Approval freezes the release's lock. The review gate checks that each pinned component version is approved.

## Check component pins

```bash
observal agent outdated alice/reviewer
observal agent outdated alice/reviewer --version 1.2.0 --output json
```

Lists each component the Agent version pins with its pinned version, its latest approved release, and a status: `current`, `outdated`, or `unlocked` (Agent versions released before pinning). Archived components are marked. Defaults to the latest Agent version.

JSON returns `agent_id`, `qualified_name`, `version`, `components`, and a `summary` with `total`, `outdated`, `unlocked`, and `archived` counts. Consumers keep installing the pins; this is the author's cue to release a refreshed version.

Versions JSON returns the direct paginated server object. Page size is 1 through 100.

## Transfer ownership

```bash
observal agent transfer-owner alice/reviewer bob --yes --output json
```

The username may optionally begin with `@`. JSON mode requires `--yes` and returns the direct server result.

## Co-authors

Co-authors can edit and publish the same Agent:

```bash
observal agent co-authors list alice/reviewer --output json
observal agent co-authors add alice/reviewer dev@example.com --output json
observal agent co-authors add alice/reviewer @dev --output json
observal agent co-authors remove alice/reviewer 550e8400-e29b-41d4-a716-446655440000 --output json
```

List returns the standard list envelope. Add returns the added user. Remove requires the user UUID returned by list and returns the direct deletion result.

## Pull

```bash
observal agent pull alice/reviewer --harness kiro --no-prompt --output json
```

Pull writes harness files, records the exact Agent and component versions in `observal.lock` and the local lockfile, and reports every file and setup action. Later pulls keep the locked version until `--upgrade` or `--version`. See the [Pull reference](pull.md) for pinning, strict mode, path, secret, merge, dry-run, and JSON behavior.

## Exit codes

Common Agent failures use:

| Code | Meaning |
| --- | --- |
| 3 | Authentication required or failed |
| 4 | Permission denied |
| 5 | Agent, component, or local definition not found |
| 6 | Existing definition, duplicate component, or unsafe config merge conflict |
| 7 | Invalid name, version, harness, component, scope, or command combination |
| 8 | Server rate limit |
| 9 | Server, filesystem, lockfile, generated config, or setup dependency unavailable |
| 10 | CLI and server version mismatch |
| 11 | Bulk operation returned one or more item errors |

## Related

* [`observal agent pull`](pull.md): install into a harness
* [`observal registry`](registry.md): manage Agent components
* [`observal registry models`](models.md): inspect exact harness model IDs
