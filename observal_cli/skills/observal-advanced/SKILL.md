---
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
name: observal-advanced
command: observal
description: "Recovers Observal session ingestion, manages CLI upgrades, downgrades and rollback, and performs explicit local Agent fallback when the server is unavailable. Use when the user asks to reconcile missed sessions, repair CLI version state, or continue locally after a confirmed connection or configuration failure."
version: 2.2.0
owner: observal
---

# Recovering Observal

## Execution contract

1. Execute commands with a 60 second timeout unless the operation documents a longer wait.
2. **Use machine output by default:** add `--output json` whenever supported. Parse list results from `items` and pagination fields.
3. Run `--help` before acting when a path or flag is uncertain.
4. Use dry run before reconciliation when scope or session volume is uncertain.
5. Supply documented force flags so version operations never prompt.
6. Verify cursor, outbox, installed version, checksum, and rollback state after recovery operations.
7. Fail openly. Never hide an unavailable server behind automatic local writes.
8. Never retry reconciliation, version changes, or fallback writes without checking resulting state.

Read [Recovery workflows](references/recovery-workflows.md) completely before executing.

## Decision rules

- Healthy telemetry does not need routine reconciliation.
- Reconcile repairs missed local session delivery. It does not replace hook or extension installation.
- Upgrade, downgrade, and rollback are distinct requests. Do not substitute one for another.
- Local fallback is allowed only after JSON reports `error.category: unavailable` with exit code `9`, or `error.category: authentication` with operation `Load authenticated CLI configuration` and exit code `3`. The user must still request local files.
- Local fallback creates harness-native Agent files. It does not publish Registry state and must be reported as local-only.
- Never invent telemetry environment variables or wrappers.

## Completion

Report sessions discovered, queued, skipped, or failed for reconciliation; old and new versions for CLI changes; or exact local paths for fallback. Include unresolved warnings and the command needed once the server is reachable.
