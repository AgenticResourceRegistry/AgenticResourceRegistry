---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Work through Inbox"
description: "Follow review decisions and update notices without confusing feed state with resource state."
---

Inbox is your own registry work feed: review requests and decisions, team activity, update notices, completed insights, and system notices. It is not a team-wide message viewer or a queue that automatically executes actions.

## Before you start

Sign in to the intended registry/account. Items depend on emitted events and your current access to their subjects. No session capture is needed to receive ordinary review or team activity. An empty feed is successful, not a reason to seed data or broaden permissions.

## Find work that needs attention

```bash
observal inbox list --state open --action-required --output json
observal inbox count --facets --facet-state open --output json
observal inbox show ITEM_UUID --output json
```

Copy an item UUID from `items`; do not use a review number as an Inbox ID. Read the subject, version, body, action URL/command, and history. `show` does not mark it read and never executes the displayed command.

Confirm the action belongs to the intended registry resource, project, version, and harness. Stored suggestions are navigation/action hints—not trusted shell instructions or authorization to upgrade. Review requests may become stale when another reviewer decides the candidate.

## Take the resource action, then organize the item

For a review request, open the selected review, inspect its exact candidate and files, then use the [review/publication workflow](/guides/review-and-publish). For a team request, use the [owner membership workflow](/use-cases/teamspaces#membership). For an insight, read the report and its evidence limitations.

Use Inbox transitions separately:

| Transition | Changes | Does not do |
| --- | --- | --- |
| `read` / `unread` | Whether the item has been seen | Decide or publish a review |
| `done` | Move the item to Done | Complete the underlying approval, membership, or upgrade action |
| `dismiss` | Move it to Dismissed without taking the suggested action | Reject a resource or delete it |
| `reopen` | Return a resolved item to Open | Reopen a decided review or restore access |

```bash
observal inbox read ITEM_UUID --output json
observal inbox done ITEM_UUID --output json
```

Mark Done after the actual work is complete, not as a shortcut to approve it. Review decisions can resolve other reviewers' request copies automatically; inspect the current review before acting on an old notice. A hidden/deleted subject can make its item unavailable even when you still have its UUID.

## Evaluate an update notice

```bash
observal outdated --no-report --output json
observal inbox list --state open --kind update_available --output json
```

`outdated` normally reports findings to Inbox best-effort; `--no-report` suppresses that write, not the registry read. A successor component or agent does not silently move existing project/agent pins. Inspect the proposed release and apply an intentional upgrade through the relevant [version workflow](/guides/version-components).

Do not run a displayed upgrade command without checking scope, selected release, required values, and the preview. Keep [the known pin-response failure](/guides/version-components#known-install-response-limitation) separate from a stale-notification problem; removing strict mode is not a workaround.

## Mark a filtered set read

First list exactly the set you intend to affect:

```bash
observal inbox list --state open --kind update_available --output json
observal inbox read-all --state open --kind update_available --yes --output json
```

Read-all changes only matching unread items; it does not resolve them or install updates. JSON mode requires `--yes`. Counts of `unread` and `action_required` describe open accessible items, not every historical item.

## If something fails

| Symptom | What to check |
| --- | --- |
| No open action items | Remove the action/unread filter, inspect Done/Dismissed, and confirm this account/registry. |
| Item not found | Confirm its UUID and current subject access; do not switch to an administrator to bypass privacy. |
| Done did not approve or upgrade anything | Perform the separate authorized resource action. Reopen the notice if useful. |
| Update notice repeats | Inspect its subject/version and reporting result; notification delivery is not an upgrade. |
| Suggested action is stale | Inspect the current review/release and use its supported command, not an old hint. |
| `--kind` rejects a kind shown in an item | The current CLI filter does not accept every server-returned kind, including `review_ready`. Remove that filter and use subject/search filters; see [accepted filter kinds](/cli/inbox#list-items). |

## Next

Use the [Inbox command reference](/cli/inbox) for event kinds, filters, paging, and all transitions. Return to [team sharing](/use-cases/share-agent-configs) or [review/publication](/guides/review-and-publish).
