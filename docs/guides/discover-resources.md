---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Discover resources with ARD"
description: "Find resources for a task and distinguish relevance from trust and availability."
---

Use discovery when you know the task but not which agent or component to choose. Agentic Resource Discovery (ARD) gives clients structured resource descriptions so they can search, inspect, and decide what to use.

## Before you start

Sign in to the registry you want to search. Results follow your account's discovery scope and teamspace access; do not assume a search spans every public registry or every person's public listing.

## Search for a task

```bash
observal discover search 'review authentication changes' --type agent --harness claude-code --output json
```

Expect a `results` array, which may be empty. The harness filter requires an explicit support declaration, so resources with unknown support may be absent. Try fewer keywords or omit the filter to investigate an empty result.

Read these signals separately:

- **Relevance:** how the resource matched the query, not whether it is safe.
- **Approval:** the published/review status.
- **Harness support:** the publisher's compatibility declaration.
- **Availability:** whether it can be used now, after installation, or not in this harness.

## Inspect a result

Copy its actual `identifier` into this command:

```bash
observal discover inspect RESOURCE_IDENTIFIER --output json
```

Read the description, representative tasks, versioned artifact reference, and digest. An identifier is not a credential, but the returned content may be private to your team.

## Choose the next action

```bash
observal discover use RESOURCE_IDENTIFIER --harness claude-code --output json
```

For skills and prompts, `use` can fetch and print text for a coding assistant's context. Printing that text in a terminal does not automatically send it to an already-running assistant.

For agents, MCP servers, and hooks, `use` returns an installation next step; it does not silently rewrite configuration. Inspect the exact resource and follow the installation workflow. A remote A2A agent is a service to delegate to, not a local profile to pull.

## Check the outcome

Discovery succeeds when you can identify a suitable accessible resource and understand how it becomes usable. An empty search is not a tool failure, and a high score is not an approval or safety verdict.

Use [Pull your first agent](/getting-started/quickstart) for installation, [Delegate work](/guides/delegate-work) for another agent's service or headless task, and the [discovery reference](/cli/discover) for exact fields and scope rules. The [ARD architecture decision](/adr/0001-agentic-resource-discovery) is supporting protocol background, not required reading for a first search.
