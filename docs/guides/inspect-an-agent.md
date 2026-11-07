---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Inspect an agent before installing"
description: "Read the exact agent release and the component versions it will install."
---

Inspect an agent when you need to decide whether its instructions, tools, and executable components are appropriate for your project. A description or approval badge is not a complete safety review.

## Before you start

You need access to the registry and the agent. Use `observal auth whoami` to confirm your account. This guide uses read operations; installation comes afterward.

## Read the full listing

```bash
observal agent show YOUR_NAMESPACE/YOUR_AGENT --output json
observal agent versions YOUR_NAMESPACE/YOUR_AGENT
```

Replace the qualified name. JSON shows the listing's `prompt`, `component_links`, `external_mcps`, and harness declarations. The human-readable table does not show the full prompt and all component types.

Choose a published, approved release. A plain pull can resolve an older project or machine pin, so do not assume the listing's current version is what you will install. For an existing project, check `observal.lock` first.

## Read that release

Copy its UUID and version into the paths below:

```bash
observal api GET /api/v1/agents/AGENT_UUID/versions/AGENT_VERSION --output json
observal api GET /api/v1/agents/AGENT_UUID/versions/AGENT_VERSION/lock --output json
```

Read the release's instructions and model settings. Check the requested harness and required capabilities. Inspect `external_mcps` as well as registry-linked components: an external tool connection is not covered just because the agent also has reviewed components.

The release detail lists `components`; the lock records their exact versions, version IDs, and digests. Pin information establishes what to inspect, not whether the content is trustworthy.

## Inspect pinned components

For each component, use its UUID and exact version from the release lock. Select the route matching its type:

| Type | Exact-version endpoint |
| --- | --- |
| MCP | `/api/v1/mcps/COMPONENT_UUID/versions/COMPONENT_VERSION` |
| Skill | `/api/v1/skills/COMPONENT_UUID/versions/COMPONENT_VERSION` |
| Hook | `/api/v1/hooks/COMPONENT_UUID/versions/COMPONENT_VERSION` |
| Prompt | `/api/v1/prompts/COMPONENT_UUID/versions/COMPONENT_VERSION` |
| Sandbox | `/api/v1/sandboxes/COMPONENT_UUID/versions/COMPONENT_VERSION` |

For example, to inspect a pinned hook:

```bash
observal api GET /api/v1/hooks/COMPONENT_UUID/versions/COMPONENT_VERSION --output json
```

Do not substitute the component listing's newest release. Review:

- **MCPs:** command and arguments or remote endpoint, repository/source, credentials requested, and the permissions its tools need.
- **Skills:** instructions, scripts, and supporting files; read all files that the tool may load or execute.
- **Hooks:** event, handler or command, timeout, and whether it can block or change a tool call.
- **Prompts:** template instructions and required variables.
- **Sandboxes:** image/source, runtime configuration, mounts, network access, and documented isolation limits.

Version responses may reference a source rather than contain every file. Follow the exact source/version information and inspect it too. If you cannot access a pinned payload, a version is missing, or a legacy release has no pin, do not treat the check as passed.

## Preview the same release

Use the exact version in both preview and installation:

```bash
observal agent pull YOUR_NAMESPACE/YOUR_AGENT --harness claude-code --scope project --version AGENT_VERSION --strict --dry-run
```

The preview is the last configuration checkpoint: check every path, setup action, session hook, and delegation MCP entry. It is not a second independent code audit. Strict mode checks the install's integrity and pins; it does not certify that an executable is safe.

## Next

Continue with [Pull your first agent](/getting-started/quickstart). If the release is unsuitable, [fork and customize it](/use-cases/fork-and-customize) or ask its author for a revised release rather than silently overriding checks.
