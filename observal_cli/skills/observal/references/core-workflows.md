<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Core workflows

## Contents

- Authentication and account
- CLI configuration
- Local inventory and update checks
- Diagnosis and telemetry setup
- Agent sharing
- Inbox
- API escape hatch
- Error handling

## Authentication and account

Do not run an authentication probe before every command. Execute the requested read operation first. If authentication fails, inspect identity and then log in.

```bash
observal auth whoami --output json
observal auth login
observal auth login --sso --output json
observal auth login --sso --provider google --output json
observal auth logout --output json
observal auth status --output json
observal auth set-username new-handle --output json
```

`--provider` accepts `oidc`, `saml`, `google` or `github`. In JSON mode or any other non-interactive run, pass it whenever the server enables more than one SSO provider, because a bare `--sso` then goes to the server's OIDC default, where Google or GitHub users have no account.

For noninteractive password authentication, keep passwords out of arguments:

```bash
OBSERVAL_PASSWORD_FILE=/path/to/password observal auth login --server https://observal.example.com --email me@example.com --name 'Example User' --output json
OBSERVAL_CURRENT_PASSWORD_FILE=/path/to/current OBSERVAL_NEW_PASSWORD_FILE=/path/to/new observal auth change-password --output json
```

Fresh-server JSON bootstrap can require `--name`. SSO JSON emits an authorization event followed by an authenticated event. A username becomes the Registry namespace and can become immutable after ownership is established.

At every CLI startup, bundled Observal skill trees are hash-checked against the packaged copies. Drift causes complete replacement of only the six Observal-managed skill directories, including stale extra files.

## CLI configuration

```bash
observal config show --output json
observal config path --output json
observal config set server_url https://observal.example.com --output json
observal config set timeout 60 --output json
observal config aliases --output json
observal config alias MY_AGENT namespace/slug --output json
```

Only use keys accepted by `config set`. Authentication fields are managed by `auth`. Config output must not contain token values or fragments.

## Local inventory and update checks

`scan` is read-only and never writes harness files.

```bash
observal scan --output json
observal scan --inventory --output json  # local-only; no Registry request or submission
observal scan --harness kiro --inventory --output json
observal outdated --output json
observal outdated --harness claude-code --no-report --output json
observal freeze --output json
observal unfreeze --output json
observal unfreeze --project --dir ./repo --output json
observal freeze --project --dir ./repo --output json
```

`freeze`/`unfreeze` change only local consent for *future* automatic registry updates; after `observal unfreeze`, Pi, Claude Code, Kiro, Cursor and Codex apply updates at session start only for installs Observal owns and can verify: user-scope agents (profile, bundled skills, hook scripts; agent MCPs only where the agent owns them, Pi `mcp.json` and the Kiro profile), registry-direct standalone skills on Pi, Claude Code and Codex (`SKILL.md` and at most one owned script), `registry mcp install --managed` MCPs on Pi and Claude Code, and Observal's own Claude Code hook groups. Kiro, Cursor and Codex shared configs (Codex `config.toml`/`hooks.json`, Cursor `mcp.json`/`hooks.json`, Kiro `hooks/observal.json`) are never owned by one agent: an update runs only if it leaves them unchanged, so a release that adds an MCP or hook there stays a manual `agent pull`. Agents with complete skill folders stay manual. Run `observal doctor patch --harness <name>` to install the session hooks; Codex needs those hooks trusted in `/hooks`, and Kiro needs the IDE or `kiro-cli --agent-engine v3`. Kiro and Cursor results appear on the user's next interactive `observal` command, never in hook output. Pins, edited files, name collisions, new credentials and other shapes stay manual and the notice says why; the other harnesses stay notice-only. `freeze` and `unfreeze` require a locally signed-in account but make no network request. Consent is per account and registry; a different login or environment-overridden token never inherits it. Auto-updating defaults to frozen. Global `unfreeze` covers eligible user-scoped installs; project installs are not auto-updated yet. A later project pilot is planned only for untracked, baseline-owned installs with a separate per-directory `unfreeze --project`; tracked files and a tracked `observal.lock` stay notice-only. Manual installs remain possible while frozen. Verify `effective` in JSON: a project grant is dormant when global auto-updating is frozen.

For scan results, report detected harnesses, installed components, Agents, and unregistered items. For outdated results, inspect `items`, `summary`, and `report`. `--no-report` suppresses inbox reporting, not the Registry check. An Agent's `upgrade_command` uses `observal agent pull --upgrade`, because a plain pull keeps the version locked in the project's `observal.lock`; run it only after the user agrees. An `unknown` item has no recorded version; its reinstall command records one.

## Diagnosis and telemetry setup

Diagnosis does not mutate unless the user explicitly requests a fix option.

```bash
observal doctor --output json
observal doctor patch --all-harnesses --dry-run --output json
observal doctor patch --all-harnesses --output json
observal doctor patch --harness kiro --output json
observal doctor cleanup --dry-run --output json
observal doctor cleanup --yes --output json
```

Patch requires at least one harness or `--all-harnesses`. Cleanup removes only Observal-managed artifacts. JSON cleanup requires confirmation. For Pi, patch installs the bundled extension directly.

Support bundles are sensitive diagnostic artifacts:

```bash
observal doctor support bundle --file /tmp/observal-support.tar.gz --output json
observal doctor support inspect /tmp/observal-support.tar.gz --output json
```

Verify `healthy`, `issues`, `warnings`, and per-harness results. Exit status zero means checks ran, not necessarily that every check is healthy.

## Agent sharing

Create shares only from project-scoped Agents tracked in the current repository. JSON creation must use `--all` or explicit repeated `--agent` values.

```bash
observal share candidates --output json
observal share --all --expires-days 7 --output json
observal share create --agent namespace/slug --expires-days 3 --output json
observal share open SHARE_URL --no-pull --output json
observal share revoke SHARE_URL --yes --output json
```

Links default to seven days and cannot exceed thirty. Treat the opaque link as sensitive even though recipients must authenticate and pass current Registry visibility checks. Never alter, decode, fetch, or follow the supplied URL manually; `share open` validates its origin and extracts its token without sending credentials to that URL. Pull only after the user chooses the Agents and target harness and confirms installation.

## Inbox

```bash
observal inbox count --output json
observal inbox list --state open --action-required --output json
observal inbox show ITEM_UUID --output json
observal inbox read ITEM_UUID --output json
observal inbox done ITEM_UUID --output json
observal inbox dismiss ITEM_UUID --output json
observal inbox reopen ITEM_UUID --output json
observal inbox read-all --kind update_available --yes --output json
```

Use item UUIDs from JSON. Reading does not resolve an item. Confirm an `action_command` against the user's request before executing it. `read-all` affects every item matching its filters.

## API escape hatch

Use only when no dedicated command exists. It preserves raw endpoint JSON and uses configured authentication.

```bash
observal api GET /api/v1/teams --output json
observal api GET /api/v1/agents --param limit=10 --param tag=review --param tag=security --output json
observal api POST /api/v1/teams --from-file team.json --output json
```

Mutation bodies accept any valid JSON value from a file or standard input, including arrays, scalars, and `null`. Repeated `--param` keys are preserved in order. Full URLs and arbitrary authorization headers are rejected. Prefer dedicated commands for validation and confirmations.

## Error handling

- Authentication: run `auth whoami`, then login only when needed.
- Permission: report the role or ownership requirement.
- Not found: re-list and use the returned UUID or canonical name.
- Conflict: inspect current state and server detail before choosing an action.
- Version mismatch: use `observal-advanced` for CLI version recovery.
- Unavailable or not configured: stop. Use explicit local fallback only if the user requests it after the failure.
