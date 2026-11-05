---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Data & Retention Settings"
---

Control deployment-wide telemetry retention and how aggressively expensive API responses are cached.

## Purge Traces and Insights

The **Purge Traces & Insights** danger-zone action permanently deletes telemetry and generated insight data for the deployment.

It removes:

- ClickHouse session events and session aggregates for project `default`.
- Data derived from those sessions: capability actions, and component activity and publication markers.
- Agent and component insight reports, and insight caches/facets.

Each ClickHouse delete waits until it has completed. The action reports success only if every delete completed; otherwise it returns an error naming the tables that failed. Insight reports are still removed in that case, and running the purge again is safe.

Layer snapshots, their component mappings, and session checkpoints are kept. Snapshots and mappings describe installed configuration rather than session content. Clients remember which snapshots they have already uploaded and do not upload them again, so deleting snapshots or mappings would leave later sessions on the same configuration without verified component attribution until a scheduled backfill rebuilt them.

It does **not** delete registry agents, versions, skills, hooks, prompts, users, reviews, or audit/security logs.

Use this only when you intentionally need a clean telemetry slate, for example before handing over a demo instance or after importing accidental/private trace data. The action cannot be undone from Agentic Resource Registry; take database backups first if you may need the data later.

<a id="application-retention"></a>

## Application retention policy

The admin retention controls store deployment-wide policy values in the following settings:

| Setting | Effect |
|---------|--------|
| `retention.enabled` | Enables scheduled application-level purging |
| `retention.trace_days` | Deletes session events older than this many days |
| `retention.score_days` | Deletes completed and stale insight reports older than this many days |
| `retention.max_trace_count` | Limits the number of retained sessions |

The policy values are independent of registry ownership. Leave a threshold empty when that limit is not needed. If `retention.score_days` is empty, the purge uses twice `retention.trace_days`, with a 30-day minimum.

When `retention.trace_days` or `retention.max_trace_count` is set, every scheduled run also removes data derived from sessions whose source events are gone: session summaries, component activity and publication markers, capability actions older than the cutoff, and scoped Insights facet caches. Matching uses the full project, user, harness, and session identity, so another user's session with the same ID is not affected. Layer snapshots and their component mappings are always kept: they describe configuration, not session content, and a sender that has already uploaded a hash never uploads it again. Component and agent reports continue to follow `retention.score_days`.

This derived cleanup runs on every run, not only on runs that delete source events, so a cleanup step that failed earlier is retried even after a count-limited deployment drops back under its limit. On a run that deletes no source events, recent capability actions are protected by the older of the oldest retained session event and a 7-day grace period. If a source delete fails or the source cannot be read, derived cleanup is skipped for that run.

Two legacy Insights tables that current versions no longer write are cleaned conservatively:

- `insight_session_meta` has no project, user, or harness identity. A row is removed only if it was computed before the cutoff and its session ID has no retained session event under any project, user, or harness.
- `insight_meta_cache` rows whose period starts before the cutoff, or whose period cannot be parsed, are removed. They are caches and are recomputed if needed.


<a id="data-retention"></a>

## ClickHouse TTL

`data.retention_days` remains the separate ClickHouse TTL setting. It controls automatic expiry of raw session content and defaults to 90 days. Application retention values cannot exceed this ceiling when it is enabled.

| Value | Effect |
|-------|--------|
| `90` (default) | Keep raw telemetry for 90 days |
| `30` | Short TTL for privacy-sensitive deployments |
| `365` | Long TTL for annual analysis |
| `0` | Keep raw telemetry indefinitely, not recommended unless storage is actively managed |

**When to lower:** The deployment has strict data minimization rules, or ClickHouse storage is growing too quickly.

**When to raise:** You need longer trend windows for audits, investigations, or longitudinal agent performance analysis.

## Default Cache TTL

Default cache duration, in seconds, for ordinary API responses.

| Value | Effect |
|-------|--------|
| `30` (default) | Good balance between freshness and database load |
| `5` | Very fresh data, higher database pressure |
| `120` | Lower database load, more stale list/detail pages |

## Dashboard Cache TTL

Cache duration, in seconds, for expensive dashboard aggregation queries.

| Value | Effect |
|-------|--------|
| `60` (default) | Dashboards feel fresh without hammering ClickHouse |
| `15` | Near-live dashboard updates, higher query load |
| `300` | Lower query load for large deployments, charts can lag by several minutes |

## OTEL Cache TTL

Cache duration, in seconds, for trace and session list endpoints.

| Value | Effect |
|-------|--------|
| `15` (default) | Keeps trace lists responsive while preserving near-live monitoring |
| `5` | Useful during active debugging |
| `60` | Better for large deployments where trace lists are expensive |

## Deleting a user {#deleting-a-user}

Telemetry belongs to the instance once it has been sent. Deleting a user, from the admin users page or through SCIM, does **not** remove or change their sessions, component activity, snapshots, facets, or insight reports. That data keeps the original user ID and is removed only by the retention settings above or by **Purge Traces & Insights**.

Deletion removes the account itself. The user's row stays as an empty shell so that everything referencing the account keeps working: its email, name, username, avatar, password, and SSO subject are cleared, it can no longer log in or refresh a token, and it no longer appears in user lists, search, or SCIM. Listings, versions, reviews, and other records the user created stay in place. The account's name becomes **Deleted user**; where an author's username or email is shown, it appears as a non-identifying `deleted-…` placeholder. The account's group, team membership, work profile, recommendation feedback, and inbox rows are removed. The security event for the deletion keeps the address the account had.

If the same person signs in again through SSO later, a new account is created; the deleted one is not restored.

Once any user has been deleted, the database cannot be downgraded to a version from before this feature: earlier versions cannot lock deleted accounts out, so the downgrade stops with an error instead.
