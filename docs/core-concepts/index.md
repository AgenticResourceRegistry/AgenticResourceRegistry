---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Core concepts"
description: "Understand agents, components, names, versions, approval, and activation."
---

You only need a few product-specific concepts to find, install, and share an agent. Exact file formats and protocol details belong in the linked references.

## Agents and components

An **agent** is a versioned setup for a coding tool: instructions plus the resources it needs for a task. It is not a hosted model or a model-provider account.

An agent can include five kinds of **component**:

| Component | What it contributes |
| --- | --- |
| MCP server | Tools exposed through the [Model Context Protocol](https://modelcontextprotocol.io/). |
| Skill | Instructions and supporting files the coding tool can load when needed. |
| Hook | A command or supported handler tied to a lifecycle event, such as a tool call. |
| Prompt | A reusable prompt template, optionally with variables. |
| Sandbox | Execution configuration for supported agent-generated code workflows. |

You can [publish components independently](/guides/components) and reuse them across agents. An agent does not have to include every component type. Sandboxes do not make every agent or delegation isolated from your machine; their actual runtime and trust boundaries matter. See [sandbox behavior](/sandboxes).

## Registry and harness

The **registry** is the server where resources are stored, reviewed, versioned, and made available according to permissions.

A **harness** is the coding tool that runs an agent, such as Claude Code, Kiro, or Pi. The CLI adapts the agent's setup into that tool's configuration. Capabilities and activation differ between tools; a registry listing does not guarantee identical behavior everywhere. See [supported coding tools](/integrations/index).

## Names and namespaces

A qualified name identifies a resource as `namespace/slug`, for example `platform/reviewer`.

- The **namespace** identifies a personal or teamspace publishing identity.
- The **slug** identifies the resource within that namespace.
- The display name is a human-readable label; it is not the canonical identifier.

Use qualified names when sharing commands. A bare name such as `reviewer` works only when it resolves unambiguously. UUIDs are also accepted by relevant commands and remain stable identifiers behind renamed or transferred listings.

Your coding tool may need a different **local name**. Pull prints the generated profile path and can qualify its filename when two namespaces use the same slug. Use that exact local name for activation.

## Versions and locks

Agents and components have versions. Each agent release pins component versions so publishing a newer component does not silently change an existing agent release.

Project-scope pulls write `observal.lock`. Commit it when your team should install the same agent version in that project. Later plain pulls keep the locked version; use `--upgrade` or `--version` to move it deliberately. User-scope installs are tracked on the machine, not through a project's `observal.lock`.

Older agent releases may lack complete component pins. Their installs warn about fallback behavior. Strict mode refuses problems such as missing pins or digest mismatches rather than silently accepting them. See [the exact pinning and strict-mode contract](/cli/pull#pinned-versions).

## Access, review, and publication

Visibility and review answer different questions:

- **Access:** may this account see or act on the resource?
- **Review:** has the submitted version received the required approvals and passed its publication gate?
- **Publication:** is that version available through the approved resource workflow?

Public, teamspace, and author access are not interchangeable. Review policies determine required approvals and publication behavior. Approval is not a security audit or proof of effectiveness.

An author may be able to inspect a pending submission without being able to install it. Installing your own unapproved agent depends on `security.allow_draft_install`; do not assume it is enabled. See [review and publication](/guides/review-and-publish).

## Installed is not active

**Installed** means the CLI wrote the configuration and recorded the install. **Active** means the coding tool loaded and selected it.

For example, a Claude Code project profile is selected with `claude --agent LOCAL_NAME` in a new session. Pi user profiles have their own switching workflow. An agent installed in a directory does not automatically own every session in that directory.

[Pull your first agent](/getting-started/quickstart) shows the full install-and-activate path.

## Discovery, delegation, and insights

**Discovery (ARD)** helps a caller find and inspect resources for a task. Search relevance is distinct from approval, compatibility, and whether a resource is usable now. [Discover resources](/guides/discover-resources).

**Delegation (A2A)** hands a task to another agent. A local registry agent runs through a supported harness; a remote A2A agent is an existing service described by an Agent Card. Delegated work has permissions and isolation limits of its own. [Delegate work](/guides/delegate-work).

**Insights** help interpret agent usage and component evidence. Agent reports and deterministic component evidence have different meanings. A verified install is not proof a component was used, and a recorded use is not proof it helped. [Interpret insights](/guides/interpret-insights).

Sessions and traces are the underlying evidence where supported and delivered. For delivery details, use [session tracking and reconciliation](/core-concepts/session-tracking).

## Authentication

Sign in through the CLI or browser flow your registry supports. Authenticated API requests use JWT bearer tokens; there is no `X-API-Key` authentication path. Whether self-registration is enabled is a separate administrator setting, not a consequence of having email/password login.

For server identity configuration and SSO-only deployments, see [authentication and SSO](/self-hosting/authentication).
