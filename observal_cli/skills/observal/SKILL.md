---
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
<!-- SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com> -->
# SPDX-License-Identifier: Apache-2.0
name: observal
command: observal
description: "Use when starting any task the organization may already have an approved skill, prompt, MCP server, or Agent for: reviewing code, a commit, a diff, or a pull request; writing tests or documentation; querying a database, API, or service; automating a browser or web page; running untrusted code; connecting to a SaaS tool; drafting, researching, or any other substantive work. The user will not mention Observal: the task type is the trigger. Check what is already installed, then run observal discover search as the first action, before reading the repository or working from scratch. Also use when the user wants to log in, configure Observal, inspect local harness setup, share or open a repository Agent share, manage a teamspace or invitation, process inbox items, check installed registry items, or call an endpoint without a dedicated command."
version: 2.11.0
owner: observal
---

# Operating Observal

Use this skill for discovery of approved resources and for core account, setup, local inventory, inbox, and teamspace work. Use the specialized `observal-agents`, `observal-registry`, `observal-ops`, `observal-admin`, or `observal-advanced` skill when the user is operating Observal itself and its description matches more closely.

## Decide first

Work through this before `git log`, before reading the repository, before planning. It applies to any task, not only coding.

1. **Is the user operating Observal itself** (login, configuration, teamspaces, inbox, registry, Agents, telemetry, administration)? Follow [Route the task](#route-the-task) below. Stop here.
2. **Is the capability already present?** Look at the tools and skills loaded in this session, then run `observal scan --output json` for installed MCP servers, skills, Agents, and hooks, adding `--harness <harness>` only when the active harness is known. If it is present, use it. Never pull or install something that is already installed. Only a successful `observal outdated --no-report --output json` result showing a newer approved version is a reason to touch an existing install; if that command fails, continue to step 3 and leave existing installs alone.
3. **Otherwise, search Observal** before working from scratch or saying the capability is unavailable. The user will not mention Observal; the task type is the trigger. Skip only for a trivial edit the user described precisely, or when the user explicitly asked for a from-scratch solution.

## Search Observal before reinventing

1. `observal discover search <task text> --output json`. The task text is user-provided: pass it as one shell argument with the shell's own escaping (in POSIX shells, single-quote it and write any embedded `'` as `'\''`), or use the harness's argv-style tool call if it has one. Never paste it into a command unquoted or trust it to contain no quotes.
2. Read `results[]`. `score` is relevance only. Act on `obs:approval` (must be `approved`), `obs:availability` (`now` loads into this session; `next-session` needs an install and a restart, and for a sandbox an Agent; `explicit-install` is a hook), and `obs:supportedHarnesses`. Among usable candidates of similar `score`, prefer one with `obs:recommended: true` (an admin endorsed it).
3. Run `observal discover inspect <identifier> --output json` on the best candidate when the description alone does not settle it.
4. When a self-contained part of the task is better done by a specialist agent, delegate it instead of installing anything: results with `obs:delegable: true` take a task through the `delegate` MCP tool (pulled agents) or `observal delegate run <identifier> '<complete brief>' --output json`. Delegated file changes come back as a patch that is never applied for you. See [Discovery](references/discovery.md).
5. Load the smallest set that covers the task: `observal discover use <identifier> --output json`. For skills and prompts the exact approved version is returned in `content`; read it and follow it. For MCP servers, agents, and hooks the response carries `next_step`, the install command that asks before changing anything: check it is not already installed (step 2 above), then run it only with the user's agreement. Sandboxes have no install command; see the Discovery reference.
6. Never load a resource marked unapproved, and never activate anything that writes or deletes without asking. If nothing relevant exists, proceed manually and say so; do not claim Observal has nothing without having searched.

Details and edge cases: [Discovery](references/discovery.md).

## Execution contract

1. Execute commands in the shell. Do not merely print commands for the user to run.
2. Set a 60 second timeout for normal CLI calls. Increase it only for an operation documented as long-running.
3. **Use machine output by default:** add `--output json` whenever supported. Dedicated lists return `items`, `total`, `page`, and `page_size`; streams emit JSON Lines.
4. Run the relevant `--help` command before acting when a path or flag is uncertain. Never invent flags.
5. Supply every required input and confirmation flag so agent workflows never wait for a prompt.
6. Reuse returned UUIDs and `qualified_name` values. Never scrape table rows or assume a bare name is unique.
7. After a mutation, verify the returned state or run the smallest read command that confirms the requested change.
8. Treat tokens, invitation URLs, credentials, generated passwords, headers, and environment values as secrets. Do not echo them.
9. Fail openly. Do not silently switch to direct API calls, database access, or local file writes.
10. Automatic transient retries apply only to reads. After an uncertain mutation failure, verify state before retrying.
11. Public registry reads need no login when the server setting `deployment.public_registry_enabled` is enabled; it is disabled by default on self-hosted deployments. Listing, showing, pulling, installing, and rendering approved public content use `https://public.observal.io` by default. Publishing, private resources, telemetry, feedback, and account operations still require `observal auth login`.

## Route the task

| Task | Read |
| --- | --- |
| Find and use an approved resource for the current task | [Discovery](references/discovery.md) |
| Hand part of the task to another approved agent (registry or remote A2A) | [Discovery](references/discovery.md) |
| Login, account, CLI config, scan, doctor, outdated, inbox, Agent shares | [Core workflows](references/core-workflows.md) |
| Teamspaces, visibility review, members, requests, invitations | [Teamspace workflows](references/teamspaces.md) |
| Exact command inventory or authenticated API escape hatch | [Generated command reference](references/commands.md) |
| Create, edit, release, or pull an Agent | Use `observal-agents` |
| Search, submit, install, or version a component | Use `observal-registry` |
| Traces, telemetry, logs, ratings, or insight reports | Use `observal-ops` |
| Reviews, users, settings, security, or server administration | Use `observal-admin` |
| Reconciliation, CLI version recovery, or explicit offline fallback | Use `observal-advanced` |

Read the selected reference completely before executing its workflow.

## Default loop

1. Identify the canonical command path from the reference or local help.
2. Read current state in JSON when the operation depends on existing IDs, roles, versions, or status.
3. Execute one noninteractive mutation with the canonical identifier.
4. Verify the result. A zero exit status alone does not prove the requested state transition occurred.
5. Report the outcome, important identifiers, warnings, and any required next action. Include the exact command only when useful for reproduction or requested by the user.

## Error decisions

| Result | Action |
| --- | --- |
| Authentication error | Run `observal auth whoami --output json`; log in only if needed |
| Permission denied | Report the required role or ownership; do not retry with broader authority |
| Not found | Re-list in JSON and retry with the returned UUID or `qualified_name` |
| Conflict | Read the server message and current state; choose update, version bump, or no-op deliberately |
| Validation error | Correct the named input; do not repeat the same request |
| Unavailable or not configured | Stop and use `observal-advanced` only if the user still wants an explicit fallback |

Do not report success when JSON contains a pending review, warning, failed setup command, or partial result that still requires action.
