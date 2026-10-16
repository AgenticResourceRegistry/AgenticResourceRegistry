<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# `observal agent pull`

Install a complete Agent into a harness. Pull resolves the Agent version to install, asks the server for harness-native config built from the exact component versions that Agent version pinned, merges generated files safely, installs bundled skills and hooks, runs required harness setup, and records exact installed state in `observal.lock` and the local lockfile.

## Synopsis

```bash
observal agent pull <agent-reference> --harness <harness> [OPTIONS]
```

Agent references may be UUIDs, canonical `namespace/slug`, unambiguous bare names, aliases, or row numbers from the latest Agent list.

## Examples

```bash
observal agent pull alice/reviewer --harness kiro --no-prompt --output json
observal agent pull alice/reviewer --harness claude-code --scope project --dry-run --no-prompt --output json
observal agent pull alice/reviewer --harness pi --version 1.2.3 --no-prompt --output json
observal agent pull alice/reviewer --harness cursor --no-prompt --upgrade
observal agent pull alice/reviewer --harness cursor --no-prompt --strict
```

## Options

| Option | Description |
| --- | --- |
| `--harness`, `-i` | Required target: `cursor`, `kiro`, `claude-code`, `codex`, `copilot`, `copilot-cli`, `opencode`, `antigravity`, `goose`, or `pi` |
| `--dir`, `-d` | Project directory used to resolve generated paths |
| `--dry-run`, `-n` | Return planned files and setup commands without changing disk or installation metadata |
| `--scope` | `project` or `user`, only for harnesses that support explicit scope |
| `--model` | Model ID, or `harness=model`; repeatable |
| `--tools` | Claude Code tool allowlist |
| `--refresh-models` | Refresh the model catalog before an interactive model picker |
| `--no-prompt`, `-y` | Disable environment, header, scope, and model prompts |
| `--env`, `-e` | MCP environment value in `NAME=VALUE` form; repeatable |
| `--header`, `-H` | MCP header in `NAME=VALUE` form; repeatable |
| `--version`, `-V` | Install this exact Agent version and lock it |
| `--upgrade` | Install the latest approved Agent version instead of the locked one, and lock it |
| `--strict` / `--no-strict` | Refuse an install that does not match its lock; defaults to `OBSERVAL_STRICT` |
| `--output`, `-o` | Table or JSON output |

Unknown harnesses, unsupported scopes, malformed assignments, unused harness model overrides, unsupported model or tool options, invalid versions, and `--upgrade` combined with `--version` fail locally with validation exit code 7.

## Pinned versions

Pulls are pinned at two levels.

**Components.** Every Agent version pins each of its MCP servers, skills, hooks, prompts, and sandboxes to one exact component version, identified by its version id and a content digest. Pull always installs those pinned versions. A component that ships a newer version never changes what an existing Agent version installs; the Agent's author releases a new Agent version to move it (see [`observal agent release --refresh-components`](agent.md#release-and-versions)).

**The Agent version.** Pull chooses the Agent version in this order:

1. `--version X`: exactly that version.
2. `--upgrade`: the latest approved version.
3. `observal.lock` in `--dir`: the version the project locked.
4. `~/.observal/lockfile.json`: the version this machine already installed for this harness and directory.
5. Otherwise, a first install: the latest approved version.

A plain pull therefore keeps an existing install on its version after newer versions are approved, and says when one is available. Only `--upgrade` or `--version` moves it. For **user-scoped** installs, an explicit `--version` also records your version pin in local installed state; automatic startup updates will not override it. An explicit `--upgrade` clears that pin. Older user installs with no recorded pin intent stay notice-only at startup until explicitly re-pulled.

### `observal.lock`

Project-scope pulls write `observal.lock` to `--dir`. Commit it: teammates and CI pulling the same Agent in that project install the same Agent version, and so the same component versions, until someone runs `--upgrade` or `--version` and commits the change.

```json
{
  "lock_version": 1,
  "agents": {
    "alice/reviewer": {
      "id": "11111111-1111-1111-1111-111111111111",
      "version": "1.2.3",
      "lock_digest": "sha256:…",
      "components": [
        {"type": "mcp", "qualified_name": "acme/github", "version": "1.4.2", "digest": "sha256:…"}
      ]
    }
  }
}
```

Entries are keyed by `namespace/slug` and carry the Agent's registry `id`, so an Agent that was renamed or transferred keeps its locked version; the next pull rewrites the entry under the new name.

User-scope installs are not tied to a project and neither read nor write `observal.lock`. `--dry-run` never writes it. A malformed or unsupported `observal.lock` fails with validation exit code 7 before anything is installed. If `observal.lock` or the local lockfile names an Agent version this server does not have, pull fails with not-found exit code 5 and says which lock pinned it; `--upgrade` or `--version` moves past it and rewrites the lock.

### Strict mode

Without strict mode, pull installs and warns when:

* a component of an Agent version released before pinning has no lock, so its latest version is installed;
* a pinned component version no longer matches the digest recorded when the Agent version was locked;
* a pinned component version is not approved;
* the Agent version no longer matches the lock digest recorded in `observal.lock`.

`--strict`, or `OBSERVAL_STRICT=1` for CI, turns each of these into a conflict (exit code 6) before any file is written. The flag wins over the environment variable, so `--no-strict` is the escape hatch in a strict pipeline. A server that predates component locks cannot check any of this, so a strict pull against it fails with version-mismatch exit code 10 instead of installing unchecked.

JSON mode cannot prompt and requires `--no-prompt`. Missing required component values fail before config generation with `error.result.needs_input: true` and a list of names and component labels.

## Secrets

Pull discovers required MCP environment variables and headers from the Agent's components. Interactive mode prompts for missing values. Non-interactive mode uses matching `--env` and `--header` assignments and stops before installation when any required value is missing. Optional values may remain unset.

Values are sent only in the installation request and generated config. They are not included in JSON results, success messages, traces, or error details.

Prefer environment expansion or secure shell input so secrets do not remain in shell history.

## File safety

Generated relative paths are confined to `--dir`. Home paths are allowed only for an explicit user-scope installation supported by that harness. Absolute paths, parent traversal, and symlink escapes are rejected before installation tracking is updated.

Pull behavior by file type:

* JSON MCP and hook sections merge into existing objects.
* YAML sections merge only when the existing top level and target section are mappings.
* TOML managed tables are replaced idempotently while unrelated tables remain.
* Generated text, prompt, Agent, and hook config files use atomic replacement.
* Malformed or structurally incompatible existing config is never overwritten. The command exits with conflict code 6 and leaves it untouched.

No `OTEL_*` or harness telemetry environment variables are generated. Session telemetry continues through Observal-managed hooks and reconciliation.

## Installation sequence

Pull performs these steps:

1. Validate harness, scope, model, tool, assignment, version, and output combinations.
2. Resolve the canonical Agent, collect install options, and choose the Agent version (see [Pinned versions](#pinned-versions)).
3. Load MCP environment and header requirements from the pinned component versions.
4. Check installed component version conflicts.
5. Request the harness-specific installation config and its lock; refuse here in strict mode.
6. Resolve and validate every generated path.
7. Write or preview files and install bundled skills.
8. Run required harness MCP registration commands.
9. Record the installed Agent and component versions in the Registry-scoped lockfile and, for project-scope installs, in `observal.lock`.
10. Refresh the local layer snapshot and active-Agent state.
11. On a successful explicit install, attempt to record hashes of the written files and the complete lock digest in a local ownership baseline. This does not grant auto-update consent; missing, shared, modified or unverifiable files remain ineligible. Baselines created before digest binding (v1) require a new explicit pull. A baseline failure is reported as a warning, not as a failed manual pull. In the Pi startup pilot, the same installer first verifies an existing baseline and narrow file plan, then refreshes the baseline only after a successful write. An automatic Pi update saves pre-write owned bytes. A failed update restores them only when the old lock/baseline and planned file bytes can still be verified; otherwise it keeps the backup and pending notice for manual inspection.

Failed skill installation or MCP setup prevents installation metadata from being recorded. Setup commands have a 60-second timeout; a timeout is reported as a setup failure with exit code 9. A lockfile write failure is also reported as exit code 9 instead of claiming success. Failures after filesystem changes include safe partial state under `error.result`, including the stage, written file statuses, setup executable status, and tracking state. Setup arguments and secret values are omitted. A layer-snapshot failure is returned as a visible warning because the generated harness installation remains usable.

## JSON result

Successful JSON output has this shape:

```json
{
  "agent": {
    "id": "11111111-1111-1111-1111-111111111111",
    "qualified_name": "alice/reviewer",
    "version": "1.2.3",
    "latest_version": "1.3.0",
    "resolved_from": "project-lock",
    "local_name": "reviewer"
  },
  "project_lock": "/work/project/observal.lock",
  "lock": {
    "status": "locked",
    "digest": "sha256:…",
    "components": [
      {
        "type": "mcp",
        "name": "GitHub",
        "id": "22222222-2222-2222-2222-222222222222",
        "version": "1.4.2",
        "version_id": "33333333-3333-3333-3333-333333333333",
        "digest": "sha256:…",
        "qualified_name": "acme/github",
        "source": "lock"
      }
    ],
    "problems": []
  },
  "harness": "kiro",
  "scope": "project",
  "dry_run": false,
  "target_directory": "/work/project",
  "files": [
    {
      "path": "/work/project/.kiro/agents/reviewer.json",
      "status": "created"
    }
  ],
  "warnings": [],
  "setup_commands": [],
  "reports_sessions": true
}
```

File statuses include `created`, `updated`, `merged`, `installed`, `cloned`, `would write`, and `would clone`.

`agent.version` is the version that was installed and `agent.resolved_from` says why: `requested`, `upgrade`, `project-lock`, `installed`, or `latest`. `lock.status` is `locked`, `partial`, or `unlocked`; each component's `source` is `lock`, `version` (matched by its recorded version string), or `fallback-latest`. `lock.problems` lists what strict mode would refuse. `project_lock` is null for user-scope installs and dry runs. `reports_sessions` is true when written hook files contain Observal session push commands, including hooks retained during a merge. These hooks can report prompts, tool calls, and tool output to the configured server when they run, whether or not `observal doctor patch` was run. It does not guarantee successful delivery. On a failed pull, check `error.result.reports_sessions` when available: a hook file may already be on disk and active even when this pull wrote no files and the Agent was not recorded as installed.

Dry-run returns the same shape with `dry_run: true`, planned statuses, and `would_run` setup actions. In dry-run, `reports_sessions` predicts whether session hooks **would** be present after applying the plan; it does not mean the preview installed them. Dry-run does not write files, execute setup commands, update the lockfile or `observal.lock`, persist an active Agent, or emit a pull audit event.

## Human output

Human mode lists every created, updated, merged, installed, cloned, or planned path. Component version conflicts, server warnings, snapshot warnings, and setup commands are printed explicitly. When session push hooks are configured, a telemetry line names the server they can report to.

## Exit codes

| Code | Meaning |
| --- | --- |
| 3 | Authentication required or failed |
| 4 | Agent or component access denied |
| 5 | Agent or component not found |
| 6 | Existing config cannot be merged safely, or a strict install does not match its lock |
| 7 | Invalid harness, scope, version, path, assignment, or option combination |
| 8 | Rate limit reached |
| 9 | Server, filesystem, skill source, lockfile, or setup command unavailable |
| 10 | CLI and server version mismatch, including a strict pull against a server without component locks |

## Related

* [`observal agent`](agent.md): create and publish Agents
* [`observal scan`](scan.md): inspect installed harness content
* [`observal outdated`](outdated.md): compare installed Agent versions
* [`observal agent outdated`](agent.md#check-component-pins): see which components an Agent version pins behind their latest release
* [`observal doctor`](doctor.md): verify hooks and local installation state
