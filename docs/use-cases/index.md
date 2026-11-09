---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Registry workflows"
description: "Choose a workflow for finding, sharing, publishing, and improving agent resources."
---

Start with the resource or task you need. Agentic Resource Registry gives you a shared, versioned source of agent configurations; you do not need to collect traces before using the registry.

| If you need to... | Start here |
| --- | --- |
| Find and run an available agent | [Pull your first agent](/getting-started/quickstart) |
| Understand what an agent will install | [Inspect an agent](/guides/inspect-an-agent) |
| Publish a reusable setup | [Create and publish an agent](/guides/create-an-agent) |
| Approve a submitted release | [Review and publish](/guides/review-and-publish) |
| Join a shared namespace | [Work in a teamspace](/use-cases/teamspaces) |
| Follow reviews, decisions, and updates | [Work through Inbox](/guides/manage-inbox) |
| Give teammates the same configuration | [Share agent configurations](/use-cases/share-agent-configs) |
| Adapt an existing resource | [Fork and customize](/use-cases/fork-and-customize) |
| Find resources for a particular task | [Discover resources with ARD](/guides/discover-resources) |
| Hand part of a task to another agent | [Delegate work with A2A](/guides/delegate-work) |
| Understand how an agent is being used | [Interpret insights](/guides/interpret-insights) |

## One setup, different coding tools

An author publishes an agent's instructions and components. A teammate inspects a release, previews the configuration, and pulls it into a [supported coding tool](/integrations/index).

```mermaid
flowchart LR
    author[Create and publish] --> inspect[Inspect a release]
    inspect --> install[Preview and pull]
    install --> activate[Activate and use]
    activate --> share[Share or improve]
```

Portability is capability-dependent. An agent does not work identically in every harness, and not every component type is available everywhere. Match the release's declarations to the tool you use.

## Work with a team

Use [teamspaces](/use-cases/teamspaces) to organize access and publishing identities. [Review and publication](/guides/review-and-publish) control which submitted releases become available; a review decision and a resource's visibility are separate concerns.

For deployment and organization-wide operations, use [Run a team registry](/use-cases/team-registry) and [Self-hosting](/self-hosting/index).

## Use session evidence when it helps

After using agents, captured sessions can help you [debug a failure](/use-cases/debug-agent-failures), [examine MCP activity](/use-cases/observe-mcp-traffic), or [interpret insights](/guides/interpret-insights). Capture requires configuration and permission to handle the resulting data; it is not automatically low-risk or complete evidence.

Choose that path when you have a question the evidence can answer—not as a mandatory first step toward sharing an agent.
