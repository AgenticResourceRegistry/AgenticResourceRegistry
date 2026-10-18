---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal admin"
---

Manage server settings, users, security policy, SSO, audit data, and the submission review queue.

All 24 workflows support `--output table|json`. JSON mode never prompts or emits progress banners. Destructive JSON commands require `--force` or `--yes`.

Core administration requires the `admin` role. Review commands are also available to global reviewers and authorized teamspace owners or reviewers. Some role changes require `super_admin`.

## Commands

| Command | Purpose |
| --- | --- |
| `settings` | List dynamic server settings |
| `set` | Create or update a dynamic setting |
| `users` | List users |
| `create-user` | Create a password-auth user |
| `reset-password` | Reset or generate a user password |
| `delete-user` | Permanently delete a user |
| `set-role` | Change a user role |
| `diagnostics` | Show database, key, and runtime health |
| `trace-privacy` | Show trace-redaction policy |
| `trace-privacy-set` | Change trace-redaction policy |
| `cache-clear` | Clear server caches |
| `saml-config` | Show redacted SAML configuration |
| `saml-config-set` | Create or replace SAML configuration |
| `saml-config-delete` | Delete SAML configuration |
| `scim-tokens` | List SCIM token metadata |
| `scim-token-create` | Create a one-time SCIM bearer token |
| `scim-token-revoke` | Revoke a SCIM token |
| `security-events` | Query security events |
| `audit-log` | Query compliance audit events |
| `audit-log-export` | Export audit events as CSV or JSON |
| `review list` | List pending submissions |
| `review show` | Show one submission |
| `review approve` | Approve a component, Agent, or bundle |
| `review reject` | Reject a component, Agent, or bundle |

## Settings

```bash
observal admin settings --output json
observal admin set review.require_approval true --output json
```

`settings` returns the direct settings array. Sensitive values are redacted by the server. `set` returns the server setting object and never echoes the supplied value in human output.

A positional setting value may be retained by shell history. Prefer deployment secret files for sensitive settings that support external management.

## Users and roles

List users:

```bash
observal admin users --output json
```

Create a user and let the server generate a password:

```bash
observal admin create-user alice@example.com 'Alice Smith' --role user --output json
```

Supported roles are `super_admin`, `admin`, `reviewer`, and `user`.

Create with a chosen password only when shell-history exposure is acceptable:

```bash
observal admin create-user alice@example.com 'Alice Smith' --password 'chosen-password' --output json
```

Reset interactively in human mode:

```bash
observal admin reset-password alice@example.com
```

Generate a password without prompting:

```bash
observal admin reset-password alice@example.com --generate --output json
```

JSON reset requires `--generate`. Created and generated passwords are returned once. Treat the entire result as a secret and do not paste it into logs, issues, or chat.

Change a role:

```bash
observal admin set-role alice@example.com reviewer --output json
```

Delete a user:

```bash
observal admin delete-user alice@example.com --force --output json
```

Human deletion prompts unless `--force` or `--yes` is present. JSON deletion requires confirmation through one of those flags.

## Diagnostics and policy

```bash
observal admin diagnostics --output json
observal admin trace-privacy --output json
observal admin trace-privacy-set true --output json
observal admin cache-clear --output json
```

`diagnostics` returns the direct health object. Trace privacy responses return `trace_privacy`. Cache clear returns the number of cleared entries.

## SAML

Show redacted configuration:

```bash
observal admin saml-config --output json
```

Create or replace configuration:

```bash
observal admin saml-config-set \
  --idp-entity-id 'https://idp.example.com/entity' \
  --idp-sso-url 'https://idp.example.com/sso' \
  --idp-x509-cert "$(cat idp-cert.pem)" \
  --active \
  --output json
```

Every update requires the IdP entity ID, SSO URL, and X.509 certificate. Optional flags include `--idp-slo-url`, `--sp-entity-id`, `--jit|--no-jit`, and `--active|--inactive`. Certificate and private-key material is never returned by configuration reads.

Delete configuration:

```bash
observal admin saml-config-delete --force --output json
```

JSON deletion requires `--force` or `--yes`.

## SCIM tokens

```bash
observal admin scim-tokens --output json
observal admin scim-token-create --description 'Okta' --output json
observal admin scim-token-revoke 11111111-1111-1111-1111-111111111111 --force --output json
```

List results contain metadata and token prefixes only. Creation returns the plaintext bearer token once. Treat that result as a secret. Revocation requires a complete UUID and prompts in human mode unless forced.

## Security events

```bash
observal admin security-events --limit 50 --offset 0 --output json
observal admin security-events --type auth.login.failure --severity critical --output json
observal admin security-events --actor alice@example.com --output json
```

Severity accepts `info`, `warning`, or `critical`. Limit accepts 1 through 1,000 and offset accepts zero or greater. JSON returns the server envelope with `events` and `total`.

## Audit log

Query events:

```bash
observal admin audit-log --limit 100 --offset 0 --output json
observal admin audit-log --actor alice@example.com --resource-type agent --output json
observal admin audit-log --source cli --outcome success --output json
observal admin audit-log --start-date 2026-08-01 --end-date 2026-08-31 --output json
```

Available filters are action, actor, resource type, sensitivity, outcome, source, start date, and end date. Source accepts `server` or `cli`. Limit accepts 1 through 500.

Print CSV to stdout:

```bash
observal admin audit-log-export
```

Write CSV atomically:

```bash
observal admin audit-log-export --file audit.csv
```

Print JSON:

```bash
observal admin audit-log-export --output json
```

Write JSON atomically:

```bash
observal admin audit-log-export --output json --file audit.json
```

Existing files prompt in human mode. JSON mode fails with a conflict unless `--force` or `--yes` is provided. Audit exports can contain sensitive administrative data.

## Review queue

Reviews are revision-based and use the `observal review` command group. Content is visible only to participants and authorized reviewers.

```bash
observal review list --output json
observal review list --type mcp --output json
observal review list --team-id 11111111-1111-1111-1111-111111111111 --output json
observal review show 123 --output json
observal review files 123 --output json
observal review diff 123 --file SKILL.md --output json
observal review checks 123 --output json
```

Review data can include submitted configuration. Handle it as potentially sensitive. Credentials are redacted in snapshots.

Submit a verdict and publish. Approval never publishes by itself unless the effective policy sets automatic publishing:

```bash
observal review approve 123 --output json
observal review request-changes 123 --body 'Please document the required variables' --output json
observal review publish 123 --output json
```

Review policy sets required approvals (0 to 5 per type), self-approval, and automatic publishing. A teamspace may set one policy for team-visible items and another for its public namespace; neither can be looser than the organization policy for public items. Zero approvals with automatic publishing is the "auto approve" setting.

```bash
observal review policy show --output json
observal review policy set required_approvals.mcp 2 --output json
```

Use `observal review --help` for threads, comments, reviewer requests, dismissal, and watch/mute.

### Automated checks (experimental)

Experimental and off by default. A super admin or admin turns it on under Settings, Experimental (`experimental.review_checks`). While off, the checks tab, queue labels, settings and endpoints below are hidden, and automated checks neither run nor block publishing; the built-in secret, schema and skill folder checks still apply.

Each revision runs built-in checks (secret scan, required fields, skill folder validity) plus configurable automated checks on the worker, within about 15 seconds of submission:

| Check | Applies to | Reads or contacts |
| --- | --- | --- |
| Harness config generation | agent | nothing external |
| Script syntax and risky patterns | hook, skill | nothing external; never executes scripts |
| Prompt template variables | prompt | nothing external |
| High-entropy secret heuristic | all | nothing external |
| Sandbox image pinned by digest | sandbox | nothing external |
| Git source resolves | mcp, skill, hook, sandbox | the Git host |
| Remote MCP server responds | mcp | the MCP URL (anonymous, public addresses only) |
| Sandbox image exists | sandbox | the image registry (anonymous) |
| Known-vulnerable dependencies | mcp, skill, hook, sandbox | Git host and osv.dev (package names and versions) |

Checks that contact external hosts, other than the Git source check, are off by default. Every check is advisory by default; an admin or team owner can mark one as blocking. A blocking check that is queued, running, failed or errored holds publication, including automatic publication. A check that could not run reports `skipped` or `error`, never `pass`.

Settings are scoped. Admins edit the organization settings (used for public items) with the gear button on the review queue, or `PUT /api/v1/admin/review-checks`. Team owners and admins edit their teamspace's settings (used for team-visible items) on the teamspace Settings tab, or `PUT /api/v1/teams/{id}/review-checks`. Changes apply to new revisions; `POST /api/v1/reviews/{number}/checks/rerun` re-queues an open review under the current settings. These checks are static and network lookups: they do not prove a component is safe or that it works.

## Exit codes

| Code | Meaning |
| --- | --- |
| 3 | Authentication required or failed |
| 4 | Administrator or reviewer permission denied |
| 5 | User, token, setting, review, or configuration not found |
| 6 | Ambiguous reference or existing export conflict |
| 7 | Invalid role, filter, UUID, SAML input, reason, or missing non-interactive confirmation |
| 8 | Rate limit reached |
| 9 | Server, database, ClickHouse, Redis, or filesystem unavailable |
| 10 | CLI and server version mismatch |

## Related

* [`observal auth`](/cli/auth): inspect the active account and role
* [`observal inbox`](/cli/inbox): review and security notifications
* [`observal ops`](/cli/ops): sessions, telemetry, logs, and insights
