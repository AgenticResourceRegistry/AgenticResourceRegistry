---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Mutation retries and idempotency"
---

Agents must distinguish a rejected mutation from a mutation whose result is unknown.

## Automatic retry contract

The shared CLI client automatically retries authenticated `GET` requests on HTTP 429, 503, and 504 responses, honoring `Retry-After` when present. It attempts token refresh once after a 401 response.

`POST`, `PUT`, `PATCH`, and `DELETE` requests are sent once. The CLI does not retry transient mutation responses because the server may have applied the change before the connection failed.

## Agent workflow

1. Read current state and capture UUIDs, canonical names, versions, or status.
2. Send one mutation.
3. If the command succeeds, inspect the returned state and perform the smallest verification read when needed.
4. If validation, permission, or conflict is returned, correct that state rather than retrying unchanged.
5. If timeout, connection failure, 503, or 504 occurs after a mutation was sent, treat the result as unknown. Read current state before deciding whether to retry.

## Mutation families

| Mutation | Verification before retry |
| --- | --- |
| Create Agent or component | List or show by canonical `namespace/slug`; do not create again if it exists |
| Publish or release version | Read version history and review status |
| Update visibility, role, or settings | Read the specific resource and compare the desired field |
| Approve or reject a request | Read request status by UUID |
| Archive, restore, revoke, or delete | Show the resource or confirm not-found where deletion is expected |
| Install or pull | Inspect returned files and run `scan` for the target harness |
| Bulk submit | Re-run only after reviewing per-entry results; existing identities return conflicts and are reported as skipped |
| Server upgrade, rollback, or migration | Read service version, migration validation, and destination state before any retry |

## Conflict behavior

A conflict is deterministic information, not a transient failure. Common decisions include:

- Use `qualified_name` when a bare name is ambiguous.
- Use update only when the user requested an in-place change.
- Use release or version publish when the user requested a new version.
- Treat an already-created bulk component as skipped after verifying its identity.
- Do not bypass edit locks or ownership conflicts.

The `observal api` escape hatch follows the same retry rules because it uses the shared authenticated client.
