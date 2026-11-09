---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
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

`data.retention_days` is the separate configured ClickHouse TTL policy and defaults to 90 days. Application retention controls validate against that configured ceiling when enabled. **The setting value does not establish the actual table TTL.**

In the inspected source Compose path, `SKIP_DDL_ON_STARTUP=true` skips the runtime initializer that applies the policy. The fresh operator rehearsal's `session_events` table had no TTL clause despite the 90-day default. Other tables can have independent migration-defined TTLs. Verify the actual table definition with authorized database access before relying on automatic expiration or a privacy/storage guarantee.

| Value | Requested policy—not proof of application |
|-------|--------|
| `90` (default) | 90-day raw-session TTL when the policy is applied |
| `30` | Shorter requested retention window |
| `365` | Longer requested retention window |
| `0` | No requested raw-session TTL; does not automatically remove an existing TTL when policy application is skipped |

### Verify storage and scheduled retention

Using an authorized ClickHouse client with the correct endpoint and authentication, run this **read-only** query (replace the database name if your deployment uses another one):

```sql
SHOW CREATE TABLE observal.session_events;
```

Read the actual returned definition, not only `data.retention_days` in the settings UI:

- A **table-level TTL** describes expiration of rows according to its expression.
- A **column-level TTL**, such as one attached to `raw_line`, can expire that column's content without deleting the entire session record. It is not the same as whole-row retention.
- If the relevant TTL is absent, do not infer automatic 90-day expiration from a stored policy value. Other tables and derived records have their own policies.

A TTL clause establishes configured storage behavior, not deletion at an exact wall-clock deadline. ClickHouse cleanup/merges are asynchronous; inspect representative authorized records and the applied policy over an appropriate window. Do not issue an improvised `ALTER`, purge, or forced production cleanup just to make the check pass.

Separately inspect the scheduled policy through authorized settings reads:

```bash
observal admin settings --output json
```

Check `retention.enabled`, `retention.trace_days`, `retention.score_days`, and `retention.max_trace_count`. Then inspect the worker's configured schedule and its actual completion/failure/no-op evidence. For a source deployment, a bounded log read is:

```bash
docker compose -f docker/docker-compose.yml logs --since 24h --tail 200 observal-worker
```

Use your actual project/files/profile and protect the output. Look for `run_retention_purge`, completion statistics, failures, or messages explaining that a run was skipped. A successfully scheduled job can be a no-op when the policy is disabled or work is deferred; job success alone is not deletion evidence. The source worker schedules this task every six hours, but configured schedules are not proof it ran in your deployment.

Rehearse execution with non-sensitive fixtures in an isolated environment if needed. These read-only checks do not enable retention, delete data, or certify that every derived record has been removed.

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

The current documented cache settings are `data.cache_ttl_default` and `data.cache_ttl_dashboard`. Do not infer an additional configurable “OTEL Cache TTL” from a cache namespace or an older guide. Startup-bound cache decorators may require a restart; verify actual response behavior after changes.

## Deleting a user {#deleting-a-user}

Telemetry belongs to the instance once it has been sent. Deleting a user, from the admin users page or through SCIM, does **not** remove or change their sessions, component activity, snapshots, facets, or insight reports. That data keeps the original user ID and is removed only by the retention settings above or by **Purge Traces & Insights**.

Deletion removes the account itself. The user's row stays as an empty shell so that everything referencing the account keeps working: its email, name, username, avatar, password, and SSO subject are cleared, it can no longer log in or refresh a token, and it no longer appears in user lists, search, or SCIM. Listings, versions, reviews, and other records the user created stay in place. The account's name becomes **Deleted user**; where an author's username or email is shown, it appears as a non-identifying `deleted-…` placeholder. The account's group, team membership, work profile, recommendation feedback, and inbox rows are removed. The security event for the deletion keeps the address the account had.

If the same person signs in again through SSO later, a new account is created; the deleted one is not restored.

Once any user has been deleted, the database cannot be downgraded to a version from before this feature: earlier versions cannot lock deleted accounts out, so the downgrade stops with an error instead.
