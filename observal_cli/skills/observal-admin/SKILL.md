---
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0
name: observal-admin
command: observal
description: "Administers Observal users, settings, diagnostics, review queues, security events, audit logs, SAML, SCIM, the local Observal server, its upgrades and rollback, and its own PostgreSQL and ClickHouse schema migrations. Use when the user needs privileged governance, submission decisions, identity configuration, security investigation, or Observal server operations. Not for querying a database or any work outside Observal administration."
version: 2.2.1
owner: observal
---

# Administering Observal

Core administration requires an admin role. Review actions also work for authorized global reviewers and teamspace owners or reviewers.

## Execution contract

1. Execute commands with a 60 second timeout, except documented long-running server and migration operations.
2. **Use machine output by default:** add `--output json` whenever supported. Parse list results from `items` and pagination fields.
3. Run `--help` before acting when a path, role requirement, confirmation flag, or destination option is uncertain.
4. Read current state before privileged mutations. Use the smallest required authority.
5. Supply `--force` or another documented confirmation flag for noninteractive destructive operations.
6. Verify review decisions, role changes, identity settings, server upgrades, rollback, and imports.
7. Never repeat generated passwords, SCIM tokens, certificates, submitted headers, database URLs, environment values, or sensitive audit content.
8. Fail openly. Do not bypass the CLI through direct database changes or hand-written migration SQL.
9. Never blindly retry privileged mutations. Read resulting state after an uncertain failure.

## Choose the workflow

| User intent | Read |
| --- | --- |
| Users, settings, diagnostics, reviews, security, audit, SAML, or SCIM | [Governance and identity](references/governance-and-identity.md) |
| Local services, versions, upgrades, rollback, reset, or data migration | [Server operations](references/server-operations.md) |

Read the selected reference completely before executing.

## Safety rules

- Permission denial is a result, not a reason to escalate automatically. Report the required role.
- Review only the returned UUID requested by the user. Never act on table position or an unrelated queue item.
- Treat one-time password and token responses as secrets from the moment they are returned.
- Export and import destinations must be explicit. Validate an archive before import.
- Use the project's migration commands. Never replace them with ad hoc SQL.
- A server command is successful only when the final status confirms the requested service or version state.

## Completion

Report the affected resource by safe identifier, resulting state, request ID for failures, and any required follow-up. Redact secret values even when the command returned them successfully.
