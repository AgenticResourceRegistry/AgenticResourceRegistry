---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal agent pull"
---

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
observal agent pull alice/reviewer --harness claude-code --hooks=settings --dry-run
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
| `--hooks` | Claude Code only: `frontmatter` (default) or `settings`. See [Agent hooks in headless sessions](#agent-hooks-in-headless-sessions) |
| `--on-unknown` | With `--hooks=settings`: `skip` (default) or `run` when Claude Code's hook input is unrecognized |
| `--force-hooks` | Replace or remove Observal-owned agent hooks in `settings.json` that were edited locally |
| `--output`, `-o` | Table or JSON output |

Unknown harnesses, unsupported scopes, malformed assignments, unused harness model overrides, unsupported model or tool options, invalid versions, and `--upgrade` combined with `--version` fail locally with validation exit code 7. A project-scope pull of an Agent with MCP servers also fails this way for Goose, which only reads a user-level MCP config: pull with `--scope user` instead.

## Pinned versions

Pulls are pinned at two levels.

**Components.** Every Agent version pins each of its MCP servers, skills, hooks, prompts, and sandboxes to one exact component version, identified by its version id and a content digest. Pull always installs those pinned versions. A component that ships a newer version never changes what an existing Agent version installs; the Agent's author releases a new Agent version to move it (see [`observal agent release --refresh-components`](/cli/agent#release-and-versions)).

**The Agent version.** Pull chooses the Agent version in this order:

1. `--version X`: exactly that version.
2. `--upgrade`: the latest approved version.
3. `observal.lock` in `--dir`: the version the project locked.
4. `~/.observal/lockfile.json`: the version this machine already installed for this harness and directory.
5. Otherwise, a first install: the latest approved version.

A plain pull therefore keeps an existing install on its version after newer versions are approved, and says when one is available. Only `--upgrade` or `--version` moves it.

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

## Agent hooks in headless sessions

Claude Code runs hooks defined in an Agent's file only in interactive sessions. In headless runs (`claude -p --agent …`, including Observal delegation) they don't run. `--hooks=settings` moves the Agent's command hooks into `.claude/settings.local.json` (or `~/.claude/settings.json` for user scope), each wrapped by `observal_cli.hook_gate`. The gate runs a hook only while that Agent is active, in interactive and headless sessions, and does nothing in other sessions. Project scope uses the local settings file, which Claude Code keeps out of version control, because each wrapped command holds this machine's interpreter path.

```bash
observal agent pull alice/reviewer --harness claude-code --hooks=settings --dry-run   # review the plan
observal agent pull alice/reviewer --harness claude-code --hooks=settings             # apply it
observal agent pull alice/reviewer --harness claude-code --hooks=frontmatter          # move the hooks back
```

* **Opt-in and remembered.** The default stays `frontmatter`. The choice is recorded per Agent in the local lockfile, so a later plain pull keeps it; `--hooks=frontmatter` removes the Observal-owned groups and writes the hooks back into the Agent file.
* **Where it is offered.** POSIX only. Its hook behaviour was recorded on Claude Code 2.1.286 (the tested range). On any other version the pull still succeeds but warns, and `observal doctor` warns after an untested upgrade. A warning is not proof the gate still behaves. Component Insights reports hook evidence from sessions written by an untested version as `version_unverified`: excluded from the denominator and shown as unknown, never as zero, until that version is recorded and added to the tested range.
* **What the gate decides.** Hook input naming this Agent runs the hook. Another Agent, or a plain session (no `agent_type`), skips it silently. Unrecognized input skips it with a short diagnostic unless `--on-unknown run` was chosen, which then runs it in every session. No policy keeps both Agent isolation and blocking on unrecognized input, so decide explicitly for a hook that guards an action.
* **Cost and timeouts.** Each gated hook starts a Python interpreter on every matching event, even when its Agent is not active (about 40 ms on an Apple M1). The dry run shows the measured cost on your machine. Timeouts are not changed; the gate's startup counts against them.
* **Ownership.** Each group carries `"_observal": {"kind": "agent-hook", "agent", "component_id", "digest"}`. A pull adds, keeps or removes only the groups owned by the Agent being pulled. Your own groups, other Agents' groups and Observal's telemetry hooks are never touched, and `observal doctor patch` and `cleanup` leave Agent groups alone. A group you edited (any change: command, matcher, timeout or option) is left exactly as you made it and keeps running; the rest of the pull continues and warns about it. It is not updated, and Component Insights reports it as drifted and does not count it. `--force-hooks` replaces it, and deleting its `_observal` key makes it yours.
* **Atomic.** `settings.json` is replaced atomically and then the lockfile is written. A `settings.json` that is not a JSON object, or whose `hooks` section is malformed, is never edited: the opt-in fails with conflict exit code 6 and nothing is written.
* **What stays in the Agent file.** Observal's session telemetry hooks, and HTTP hooks (which have no command to gate and so still run only interactively). Scripts that read the Agent file's `hooks:` section will no longer find the moved hooks.
* **Component Insights.** A gated hook is verified only when its owned group is present exactly once and unedited and the Agent file no longer carries the original command. Its sessions count as "could run" whenever its Agent was active, headless included. A subagent's own transcript doesn't record which Agent ran, so Observal reads it from the parent session's record of the spawn: a gated hook counts as "could run" in a subagent session of its Agent and "could not run" in another Agent's. Until the parent session is uploaded, or when it doesn't name the Agent unambiguously, the session is reported as unknown rather than as "could not run". If the Python interpreter the gate was written with moves (a CLI reinstall), the hook shows as drifted until you pull again; `observal doctor` reports this.

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
9. With `--hooks=settings` (or a restore with `--hooks=frontmatter`), atomically write the reconciled `settings.json`. The settings plan is validated before step 7, so an invalid settings file refuses the pull before installation files are written. The **whole pull is not one atomic transaction**: later setup or lockfile failures can leave partial state (reported under `error.result`). Locally edited Observal groups are left as they are, with a warning.
10. Record the installed Agent and component versions in the Registry-scoped lockfile and, for project-scope installs, in `observal.lock`.
11. Refresh the local layer snapshot and active-Agent state.

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

When agent hooks are placed in, or removed from, `settings.json`, the result also carries `agent_hooks`: `placement`, `on_unknown`, `settings_file`, the `added`, `removed`, `kept` and `conflicts` groups, `changed`, each gated hook (`name`, `event`, `component_id`, `command`, `timeout`, `can_block`), and `gate_startup_ms` (measured in dry runs only).

`agent.version` is the version that was installed and `agent.resolved_from` says why: `requested`, `upgrade`, `project-lock`, `installed`, or `latest`. `lock.status` is `locked`, `partial`, or `unlocked`; each component's `source` is `lock`, `version` (matched by its recorded version string), or `fallback-latest`. `lock.problems` lists what strict mode would refuse. `project_lock` is null for user-scope installs and dry runs. `reports_sessions` is true when written hook files contain Observal session push commands, including hooks retained during a merge. These hooks can report prompts, tool calls, and tool output to the configured server when they run, whether or not `observal doctor patch` was run. It does not guarantee successful delivery. For Pi, it is also true when the Observal Pi extension is installed, because the extension reports every Pi session. On a failed pull, check `error.result.reports_sessions` when available: a hook file may already be on disk and active even when this pull wrote no files and the Agent was not recorded as installed.

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
| 7 | Invalid harness, scope, version, path, assignment, or option combination, including `--hooks=settings` on Windows |
| 8 | Rate limit reached |
| 9 | Server, filesystem, skill source, lockfile, or setup command unavailable |
| 10 | CLI and server version mismatch, including a strict pull against a server without component locks, or `--hooks=settings` against a server that cannot place hooks |

## Related

* [`observal agent`](/cli/agent): create and publish Agents
* [`observal scan`](/cli/scan): inspect installed harness content
* [`observal outdated`](/cli/outdated): compare installed Agent versions
* [`observal agent outdated`](/cli/agent#check-component-pins): see which components an Agent version pins behind their latest release
* [`observal doctor`](/cli/doctor): verify hooks and local installation state
