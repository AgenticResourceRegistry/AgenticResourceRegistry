---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Interpret agent and component insights"
description: "Use reports to ask better questions without mistaking missing evidence for zero use."
---

Use insights after an agent has been used and the registry has relevant session evidence. They help you investigate patterns and decide what to improve; they are not required to browse or install resources.

## Before you start

You need permission to view the agent or component's report. Session capture and delivery must be configured according to your team's data policy. Generating narrative agent reports also depends on the operator's report-provider configuration and can incur provider cost.

## Read an existing agent report

Replace the qualified name:

```bash
observal ops insights list YOUR_NAMESPACE/YOUR_AGENT --output json
observal ops insights show YOUR_NAMESPACE/YOUR_AGENT latest --output json
```

Check the report's period, agent version, status, and evidence volume before interpreting its findings. Read the summary, friction, and suggestions as hypotheses grounded in the included sessions, not a universal score of the agent's quality.

If the list is empty, there may be no completed report. Confirm capture, delivery, access, and provider setup before requesting generation. Generating a report does not repair missing evidence.

## Start component analysis with coverage

MCP, skill, and hook evidence has a separate deterministic activity view. Owners, co-authors, and authorized administrators can inspect it:

```bash
observal ops insights activity --component skill YOUR_NAMESPACE/YOUR_SKILL --period 14 --output json
```

Look at coverage and limitations before counts:

- **Verified presence:** the snapshot and install evidence are consistent for that component.
- **Observed use:** the supported parser found the relevant evidence in delivered sessions.
- **Unsupported/unverified/missing evidence:** unknown, not measured zero.

A skill load says instructions entered context; it does not prove they were followed or useful. A recorded hook run or MCP call is not an effectiveness score. Component attribution is currently supported for Claude Code and Pi, with version- and evidence-specific limits.

The client supplies snapshots and transcripts. These observations are not independent, fraud-resistant attestation and must not be used as proof of safety or as trustworthy ranking signals.

## Choose a next step

Use an agent report to frame a specific investigation: repeated errors, unclear instructions, a component mismatch, or a change between versions. Check the relevant session evidence when needed and permitted, then [fork or revise the agent](/use-cases/fork-and-customize) and test the change.

If a suggestion references an existing registry component, inspect its qualified name and version before installing it. Narrative suggestions without a verified resource reference are not ready-to-install components.

See the [insights command reference](/cli/ops#agent-insights) for report generation, sections, comparison options, and component evidence contracts. Operators configure report providers separately from a developer's model-provider login.
