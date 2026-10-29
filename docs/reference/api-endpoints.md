---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "API endpoints"
---

REST and GraphQL surface of the Agentic Resource Registry server. Unless noted, endpoints require authentication via Bearer token or API key (`Authorization: Bearer <token>` or `X-API-Key: <key>`).

Base path: `/api/v1`.

## Auth

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/auth/bootstrap` | Auto-create admin on fresh server (localhost only) |
| `POST` | `/auth/register` | Self-registration (email + password; `deployment.sso_only=false` only) |
| `POST` | `/auth/login` | Login with API key or email + password |
| `POST` | `/auth/exchange` | Exchange one-time OAuth code for credentials |
| `GET` | `/auth/whoami` | Current user info |
| `POST` | `/auth/token` | Exchange credentials for JWT access + refresh tokens |
| `POST` | `/auth/token/refresh` | Rotate refresh token for new access token |
| `POST` | `/auth/token/revoke` | Revoke a refresh token |
| `GET` | `/auth/oauth/login` | Initiate OAuth SSO flow |
| `GET` | `/auth/oauth/callback` | OAuth callback handler |

## Registry

Per type: `mcps`, `agents`, `skills`, `hooks`, `prompts`, `sandboxes`.

All `{id}` parameters accept a UUID or a name.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/{type}` | Submit / create |
| `GET` | `/{type}` | List approved items |
| `GET` | `/{type}/{id}` | Get details |
| `POST` | `/{type}/{id}/install` | Get harness config snippet |
| `DELETE` | `/{type}/{id}` | Delete |
| `GET` | `/{type}/{id}/metrics` | Metrics |
| `POST` | `/agents/{id}/pull` | Pull agent (installs all components) |

`POST /{type}/{id}/install` accepts `version` for agents, MCP servers, skills, and hooks. The response reports the `version` that was installed; component installs also return its `version_id` and content `digest`.

### MCP webhook sync

Owner routes manage GitHub or GitLab webhook sync for one MCP listing. See [Sync an MCP server from GitHub or GitLab](/use-cases/mcp-repo-sync).

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/mcps/{id}/webhook-sync` | Sync settings and last result. `enabled` is false when sync is off |
| `PUT` | `/mcps/{id}/webhook-sync` | Turn on or update sync: `sync_on_push`, `sync_on_release`, `branch`, `provider` (`github` or `gitlab`; detected from the repository URL when omitted on first enable). Returns `secret` only on first enable |
| `POST` | `/mcps/{id}/webhook-sync/rotate-secret` | Issue a new secret, returned once |
| `POST` | `/mcps/{id}/webhook-sync/run` | Queue a sync of the tracked branch (`202`) |
| `DELETE` | `/mcps/{id}/webhook-sync` | Turn off sync |
| `POST` | `/webhooks/github/mcp/{sync_id}` | GitHub delivery receiver. No user token: authenticated by `X-Hub-Signature-256`. Returns `202` with `status` `queued` or `ignored` |
| `POST` | `/webhooks/gitlab/mcp/{sync_id}` | GitLab delivery receiver. No user token: authenticated by `X-Gitlab-Token`. Returns `202` with `status` `queued` or `ignored` |

### Registry forks

Only authenticated users can fork an approved listing and an approved base release. The result is an independently owned **draft**; no source content or counts are changed. For component endpoints, `{type}` is one of `mcps`, `skills`, `hooks`, `prompts`, or `sandboxes`.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/agents/{id}/fork` or `/{type}/{id}/fork` | Create a draft fork; optional body fields: `name`, `version`, `new_version`, `team_id`, `visibility` |
| `GET` | `/agents/{id}/forks` or `/{type}/{id}/forks` | Paginated, public approved direct forks only; `limit` and `offset` query parameters |
| `GET` | `/agents/{id}/fork-diff?version=…` or `/{type}/{id}/fork-diff?version=…` | Unified `diff` and `unchanged` relative to the fork's approved base; defaults to the latest fork version visible to the caller |

Diff access follows normal fork-version permissions. If the upstream is no longer visible or its approved base is gone, comparison returns `404`, never a saved source reference. Component diffs omit secret environment/header values and use a digest marker for install content excluded from public version responses. [Fork and customize](/use-cases/fork-and-customize) describes the workflow.

### Agent versions and locks

Each agent version pins exact component versions. Installs generate every component from its pinned version. Agent versions released before pinning can have unlocked components: those install at their latest approved release, are reported with `source: fallback-latest` in the install `lock`, and are refused by a `strict` install.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/agents/{id}/versions` | Version history |
| `POST` | `/agents/{id}/versions` | Release a version for review. Components keep the current release's pins unless a component names a `version` or `refresh_components` is true |
| `GET` | `/agents/{id}/versions/{version}/lock` | The version's lock: exact component versions, version ids, and digests. Frozen once approved |
| `GET` | `/agents/{id}/versions/{version}/outdated` | For each pin, the latest approved release and whether the pin is behind |
| `PUT` | `/agents/{id}` | Edits version-owned fields only while the latest version is a draft, pending, or rejected; otherwise `409` |

A component reference's `version` must name an approved (or archived) release. Drafts may also pin a release that is not approved yet, but only a caller who may read it (its owner, co-authors, admins, and reviewers); to anyone else it does not exist, as on the component version routes.

`POST /agents/{id}/install` also accepts `strict`. Its response includes `version` (the agent version installed) and `lock`: `status` (`locked`, `partial`, or `unlocked`), `digest`, `components` with each one's `source`, and `problems`. With `strict: true`, any problem is a `409` and nothing is generated.

### Scan

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/scan` | Bulk register items from harness config scan |

### Review

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/review` | List pending submissions |
| `GET` | `/review/{id}` | Submission details |
| `POST` | `/review/{id}/approve` | Approve |
| `POST` | `/review/{id}/reject` | Reject |

## Discovery (ARD)

Agentic Resource Discovery endpoints. Authentication is optional: anonymous
callers see public, approved resources only when `deployment.public_registry_enabled`
is on; authenticated callers see what the registry's visibility rules
already grant them (public, their teams', and their own drafts). Errors use the
ARD envelope `{"errorCode": "INVALID_ARGUMENT", "message": "..."}`. See
[ADR 0001](/adr/0001-agentic-resource-discovery).

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/.well-known/ard.json` | Manifest of the registry entry plus this deployment's public approved resources (not imported remote agents) |
| `GET` | `/.well-known/ai-catalog.json` | Predecessor path, same document |
| `POST` | `/api/v1/ard/search` | ARD Search. `score` is relevance only; `obs:approval`, `obs:availability`, `obs:supportedHarnesses`, `obs:delegable` carry the rest; remote A2A results add `obs:provider` from the card |
| `GET` | `/api/v1/ard/agents` | ARD List: deterministic browse with `filter`, `orderBy`, `pageSize`, `pageToken` |
| `POST` | `/api/v1/ard/explore` | ARD Explore: not implemented (`501`) |
| `GET` | `/api/v1/ard/entries/{identifier}` | One complete entry by `urn:air:` identifier (Agentic Resource Registry-specific) |
| `GET` | `/api/v1/artifacts/{kind}/{uuid}/{version}` | Permanent versioned artifact; `Digest` and `X-Artifact-Digest` headers |

Search request body follows the spec: `{"query": {"text": "...", "filter": {...}}, "federation": "none", "pageSize": 5}`.
Supported filter terms: `type`, `tags`, `capabilities`, `publisher`, `version`,
`obs:kind`, `obs:supportedHarnesses`, `obs:lifecycle`, `obs:activatable`,
`obs:forksOf`. The last filter takes one upstream `urn:air:` identifier and
returns only currently **public approved** direct forks. Search, List, entry,
and manifest serialization add `obs:forkedFrom` only while both fork and source
are currently public and approved; private lineage is never projected from a
saved reference.

### Remote A2A agents

Remote agents are registered by Agent Card URL and reviewed before anyone can
delegate to them. They then appear in search as `type:
application/a2a-agent-card+json` with the reviewed card pinned in the entry
(`obs:agentCard`, `obs:a2aInterface`). Clients call the agent directly; the
server does not proxy A2A traffic. See [ADR 0002](/adr/0002-a2a-delegation).

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/v1/ard/imports` | Remote agents the caller can see, including their own and (reviewers) pending ones |
| `POST` | `/api/v1/ard/imports/a2a` | Register or refresh a card: `{"cardUrl": "...", "visibility": "private\|team\|public", "teamId": "..."}`. `201` new, `200` refresh; a changed card returns to `pending` |
| `POST` | `/api/v1/ard/imports/{identifier}/review` | Reviewers in scope (team owners and reviewers for team agents, global reviewers for public, admins for private): `{"action": "approve\|reject", "reason": "...", "digest": "sha256:..."}` (`digest` of the reviewed card required to approve, 409 when the card changed since; reason required to reject) |
| `DELETE` | `/api/v1/ard/imports/{identifier}` | Owner or admin: remove from discovery |

Cards must be served over https from a public address. Internal hosts are
allowed individually with the `discovery.a2a_private_hosts` setting. The
`discovery.delegation_enabled` setting (default on) adds the `observal-agents`
MCP server to every pulled Agent.

## Telemetry

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/telemetry/events` | Legacy event ingestion |
| `GET` | `/telemetry/status` | Data flow status |
| `POST` | `/ingest/session` | Idempotently ingest indexed session source records and return the highest contiguous acknowledgement |
| `GET` | `/ingest/session/checkpoint` | Get the caller's contiguous line/byte checkpoint for a harness session |

Session record identity is scoped by project, user, harness, session ID, and source line index. Retrying the same content at the same index is safe; different content at an already acknowledged index returns `409`.

Final session requests can include `session_hash` and `hashed_line_count`. The response includes `integrity_ok`, `server_hash`, and `repair_from_line`. A failed audit rewinds the durable checkpoint to the first affected range; the exporter then replays that range idempotently. Hashing and canonical manifest scans occur only for final/audit requests, not incremental ingest.

## Telemetry hooks

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/telemetry/hooks` | Ingest lifecycle hook events (used by Kiro shell hooks) |

## Alerts

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/alerts` | List alert rules |
| `POST` | `/alerts` | Create alert rule. Non-admins may only target an agent they own or co-author |
| `PATCH` | `/alerts/{id}` | Update alert rule |
| `DELETE` | `/alerts/{id}` | Delete alert rule |
| `GET` | `/alerts/{id}/history` | Alert firing history |
## Feedback

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/feedback` | Submit rating |
| `GET` | `/feedback/{type}/{id}` | Get feedback |
| `GET` | `/feedback/summary/{id}` | Rating summary |

## Admin

Requires `admin` or `super_admin` role.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/admin/settings` | List settings |
| `PUT` | `/admin/settings/{key}` | Set a value |
| `GET` | `/admin/users` | List users |
| `POST` | `/admin/users` | Create user |
| `PUT` | `/admin/users/{id}/role` | Change role |
| `PUT` | `/admin/users/{id}/password` | Reset user password (admin) |
| `GET` | `/admin/weights` | Get dimension weights |
| `PUT` | `/admin/weights` | Set dimension weights |
| `GET` | `/admin/canaries/{agent_id}/reports` | Canary detection reports |

## GraphQL

Single endpoint, query + subscription via WebSocket.

| Path | Description |
| --- | --- |
| `/api/v1/graphql` | Session update subscriptions |

Subscriptions use `graphql-ws` protocol.

## Health

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Readiness - checks DB + ClickHouse |
| `GET` | `/healthz` | Liveness - is the API process alive |

## Rate limiting

Auth endpoints are subject to `RATE_LIMIT_AUTH` and `RATE_LIMIT_AUTH_STRICT`. Non-auth endpoints are not rate-limited by default; put a reverse proxy or API gateway in front if you need it.

## Request size limits

`MAX_REQUEST_SIZE_MB` (default `10`) caps body size on all endpoints. Large telemetry batches may need tuning.

## Related

* [Authentication and SSO](/self-hosting/authentication)
* [Hooks specification](/reference/hooks-spec)
