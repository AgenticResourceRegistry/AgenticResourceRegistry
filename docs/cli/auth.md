---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal auth"
---

Authentication and account management.

## Commands

| Command | Description |
| --- | --- |
| `observal auth login` | Authenticate with credentials or browser SSO |
| `observal auth logout` | Sign out every session for this account (CLI, hooks, web) and clear local credentials |
| `observal auth whoami` | Show the authenticated user |
| `observal auth status` | Check authenticated server and local outbox health |
| `observal auth change-password` | Change the current user's password; signs out every other session, this CLI stays signed in |
| `observal auth set-username` | Set or update the registry namespace username |

## Login

```bash
observal auth login
observal auth login --server https://observal.example.com --email alice
observal auth login --sso
observal auth login --sso --provider google
```

Login accepts `server`, `email`, `password`, `name`, `sso`, `saml`, `provider`, `output`, and `no-setup` options. Prefer `OBSERVAL_PASSWORD` or `OBSERVAL_PASSWORD_FILE` over the password option so the secret does not enter shell history or process arguments.

Human login always asks for the server URL unless `--server` is supplied; leave the prompt blank to use `http://localhost`. On a fresh server, provide email, name, and a password to create the first administrator. JSON mode never prompts, uses the configured server or local default, and requires complete credential inputs.

Successful human login first installs the Claude Code session hooks when Claude Code is already present (a `~/.claude` directory or a `claude` binary on `PATH`), then synchronizes the bundled skills, creates the initial layer snapshot, and runs doctor. A Claude Code settings file that is not valid JSON is left untouched with a note to run `observal doctor patch --harness claude-code`. Select `no-setup` to skip the snapshot and doctor. JSON mode skips those post-login steps.

`provider` picks the browser SSO provider: `oidc`, `saml`, `google` or `github`. It implies `sso`, and the CLI rejects a provider the server has not enabled, or `saml` combined with a different `provider`. A bare `sso` uses the only enabled provider, or asks which one when the server enables several. JSON mode and non-interactive input never ask, so a bare `sso` there falls back to the server default (OIDC, then SAML); pass `provider` to choose.

Every CLI invocation also computes a SHA-256 hash for each installed Observal-managed skill tree. A mismatched tree is replaced completely from the packaged bundle, including references and scripts, so local edits and stale extra files do not survive. Skill directories outside the six bundled Observal names are untouched.

Credential JSON login emits one safe object and never includes tokens or passwords:

```bash
OBSERVAL_PASSWORD_FILE=/run/secrets/observal-password \
  observal auth login \
  --server https://observal.example.com \
  --email alice \
  --output json
```

Browser SSO in JSON mode is a JSON Lines stream. The first event contains the verification URL and user code. The final event confirms authentication:

```bash
observal auth login --sso --output json
```

## Logout

```bash
observal auth logout
observal auth logout --output json
```

Logout always removes local tokens when the local configuration is readable. Remote revocation is best effort and is reported separately in JSON. Use `observal doctor cleanup` to remove Observal-managed harness hooks.

A logout from any device, including the web UI, also signs out the hooks on every other machine. When a hook's token refresh is rejected, the hooks stop retrying until the next `observal auth login`, and every other `observal` command prints a notice that sessions are not being sent.

## Current user

```bash
observal auth whoami
observal auth whoami --output json
```

The JSON form returns the server user object directly.

## Status

```bash
observal auth status
observal auth status --output json
```

Status reports the server URL, authentication state, health latency, and local telemetry outbox. It returns exit code 3 when authentication is absent and exit code 9 when the configured server is unreachable.

## Change password

```bash
observal auth change-password
```

Both modes read `OBSERVAL_CURRENT_PASSWORD` and `OBSERVAL_NEW_PASSWORD`, including their corresponding `_FILE` forms. Human mode prompts for missing values; JSON mode requires both values and never prompts.

```bash
OBSERVAL_CURRENT_PASSWORD_FILE=/run/secrets/current-password \
OBSERVAL_NEW_PASSWORD_FILE=/run/secrets/new-password \
  observal auth change-password --output json
```

Passwords require at least 12 characters, one uppercase letter, one number, and one special character.

## Set username

```bash
observal auth set-username alice
observal auth set-username alice --output json
```

The username is the user's registry namespace. It must follow the namespace rules shown by command help. The server prevents changes that conflict with another namespace or published registry ownership.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `OBSERVAL_SERVER_URL` | Default server URL |
| `OBSERVAL_ACCESS_TOKEN` | Pre-authenticate commands without login |
| `OBSERVAL_PASSWORD` | Credential or bootstrap login password |
| `OBSERVAL_CURRENT_PASSWORD` | Current password for JSON password changes |
| `OBSERVAL_NEW_PASSWORD` | New password for JSON password changes and mandatory login changes |
| `OBSERVAL_TIMEOUT` | Authenticated client timeout |

Password and token variables support a corresponding `_FILE` form. Do not set a direct value and its file form together.

Full list: [Environment variables](/reference/environment-variables).

## Related

* [`observal config`](/cli/config): local configuration
* [Self-hosting authentication](/self-hosting/authentication): server authentication and SSO
