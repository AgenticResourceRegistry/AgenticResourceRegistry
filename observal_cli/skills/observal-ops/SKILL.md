---
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-License-Identifier: Apache-2.0
name: observal-ops
command: observal
description: "Inspects Observal traces, sessions, rankings, feedback, telemetry health, logs, and Agent insight reports. Use when the user wants operational evidence, current activity, telemetry diagnosis, ratings, report generation, regression analysis, or recommendations grounded in Agent usage."
version: 2.4.0
owner: observal
---

# Observing Observal

## Execution contract

1. Execute commands with a 60 second timeout. Use a longer timeout only for report generation or an intentional stream.
2. **Use machine output by default:** add `--output json` whenever supported. Parse list envelopes, finite objects, and JSON Lines separately.
3. Run `--help` before acting when a path or flag is uncertain.
4. Start with the narrowest read that answers the question. Do not generate a new report when a completed report already suffices.
5. Ground every conclusion in returned fields, report period, and sample size. Distinguish missing data from healthy data.
6. Verify rating mutations and report generation results.
7. Never repeat secrets or sensitive trace and log content unless the user explicitly requests the specific data and is authorized.
8. After an uncertain rating or report-generation failure, read current state before retrying.

## Choose the workflow

| User intent | Read |
| --- | --- |
| Sessions, traces, rankings, ratings, telemetry, or logs | [Operational workflows](references/operational-workflows.md) |
| Agent health, friction, costs, versions, regressions, or suggestions | [Insight reports](references/insight-reports.md) |
| MCP, skill and hook component presence, observed calls, skill loads or hook runs, and attribution coverage | [Insight reports](references/insight-reports.md) |

Read the selected reference completely before executing.

## Analysis rules

- A zero exit status from telemetry diagnosis can still contain issues or warnings. Read the JSON health fields.
- No events is not automatically a hook problem. Check authentication, server reachability, local outbox, and hook state in that order.
- Quote report evidence accurately and say when session count is thin. For component reports use `--component mcp|skill|hook NAMESPACE/SLUG` with `insights list|show|generate`; coverage gaps are unknown, never proof of non-use. A skill load or invocation shows its instructions entered context, never that the skill helped; a recorded hook run is a lower bound and never shows what the hook changed.
- Only `component_ref` proves an insight suggestion maps to a Registry component. Never reconstruct a component identity from prose.
- Lead with reuse suggestions before create-new suggestions.
- For logs and traces, summarize the minimum sensitive content needed to answer the question.

## Completion

Report the time range, filters, counts, health state, strongest evidence, uncertainty, and concrete next action. Do not present popularity fallback or sparse reports as personalized certainty.
