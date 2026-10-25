---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal inbox"
---

View and update the signed-in user's work and event feed. Inbox items cover reviews, decisions, teamspace activity, update notices, completed insights, and system notices.

The Inbox is self-only. There is no option for reading another user's items.

## List items

The direct and explicit list forms are equivalent:

```bash
observal inbox --output json
observal inbox list --state open --action-required --output json
observal inbox list --subject-type mcp --search postgres --sort oldest --output json
```

| Option | Description |
| --- | --- |
| `--state`, `-s` | Filter by `open`, `done`, or `dismissed` |
| `--kind`, `-k` | Filter by event kind |
| `--action-required` / `--no-action-required` | Filter by whether the item requires action |
| `--unread` / `--read` | Filter by read state |
| `--subject-type` | Filter by the related object type, such as `agent`, `mcp`, `skill`, `team`, or `insight_report` |
| `--search`, `-q` | Search titles, bodies, namespaces, and slugs |
| `--sort` | Sort by `newest` or `oldest` |
| `--page`, `-p` | Select a page, starting at 1 |
| `--page-size` | Return 1 through 100 items |
| `--output`, `-o` | Table or JSON output |

Supported kinds are:

* `review_requested`
* `review_approved`
* `review_rejected`
* `review_comment`
* `change_requested`
* `team_join_requested`
* `team_join_decided`
* `team_created_pending`
* `ownership_transfer`
* `update_available`
* `insight_ready`
* `system_notice`

Submitting a component or Agent for review sends a `review_requested` item to every reviewer who can act on it. Requesting a specific reviewer sends one more `review_requested` item to that reviewer only. Requested reviewers are then told when another reviewer approves and when the review is published. The author is told about approvals, change requests, and publication. When a review is decided, the other reviewers' copies move to Done and stop counting toward `unread`.

JSON returns the direct paginated server object:

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 25
}
```

An empty `items` array is successful. Continue with `--page N` while the returned page range is below `total`.

## Count items

```bash
observal inbox count --output json
observal inbox count --facets --facet-state open --output json
```

The basic result includes `unread`, `action_required`, `open`, `done`, and `dismissed`. `unread` and `action_required` count only open items, so `unread` matches the unread items the default Open list shows. `--facets` also returns `unread_by_state`, the `scope_total`, `scope_unread`, and `scope_action_required` counts for one folder, and `by_kind` and `by_subject_type`. `--facet-state` picks that folder (`open`, `done`, `dismissed`, or `all`) and requires `--facets`.

## Show an item

```bash
observal inbox show 11111111-1111-1111-1111-111111111111 --output json
```

The detail result includes the item's body, related subject, exact action URL or command, and append-only history. The CLI displays an action command but never runs it automatically.

## Update one item

Every mutation supports table and JSON output:

```bash
observal inbox read 11111111-1111-1111-1111-111111111111 --output json
observal inbox unread 11111111-1111-1111-1111-111111111111 --output json
observal inbox done 11111111-1111-1111-1111-111111111111 --output json
observal inbox dismiss 11111111-1111-1111-1111-111111111111 --output json
observal inbox reopen 11111111-1111-1111-1111-111111111111 --output json
```

Read state and lifecycle state are separate:

* `read` and `unread` change only whether the item has been seen.
* `done` resolves completed work.
* `dismiss` resolves the item without acting on it.
* `reopen` returns a done or dismissed item to `open`.

JSON returns the updated item directly.

## Mark a filtered set read

```bash
observal inbox read-all --kind update_available
observal inbox read-all --state open --subject-type mcp --search postgres --yes --output json
```

`read-all` affects only unread items matching the supplied state, kind, action-required, subject-type, and search filters. Human mode asks for confirmation unless `--yes` is present. JSON mode never prompts and therefore requires `--yes`.

JSON returns:

```json
{
  "updated": 3
}
```

## Exit codes

Invalid states, kinds, sort orders, filter lengths, item IDs, and non-interactive confirmation use validation exit code 7. Authentication, permission, not-found, rate-limit, unavailable-service, and version failures preserve the shared CLI exit contract.

In JSON mode, stdout contains only the successful result. Failures leave stdout empty and write one categorized error object to stderr.

## Related

* [`observal outdated`](/cli/outdated): report installed updates to Inbox
* [`observal registry`](/cli/registry): inspect Registry subjects referenced by Inbox items
