---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Debug agent failures from session evidence"
---

AI failures rarely produce one reliable error code. Agentic Resource Registry preserves the session transcript so you can inspect what the user asked, which tools ran, what results the harness recorded, and how the agent responded.

## 1. Find the session

Open `/traces` in the web UI or list recent sessions:

```bash
observal ops traces --limit 50
```

Filter by harness, agent, user, model, or time range. Select the session that matches the reported failure.

## 2. Inspect the event sequence

Expand the session to review prompts, assistant responses, tool calls, tool results, lifecycle events, and subagent activity in source order. The CLI can unfold the same session structure:

```bash
observal ops traces --turn --limit 10
```

Look for repeated tool calls, error results, missing inputs, unexpected model responses, or a stop event that occurred before the expected work completed.

## 3. Inspect recorded tool detail

The detail available for a tool call depends on the harness transcript. When present, Agentic Resource Registry displays the tool name, input, result, and error indicators. Agentic Resource Registry does not intercept MCP transport traffic or invent payload fields that the harness did not record.

## 4. Common patterns

| Session pattern | Likely cause |
| --- | --- |
| Same tool called repeatedly with identical arguments | The agent is stuck in a retry loop or is not consuming the result |
| Tool result records an error and the assistant continues | Prompt or recovery policy did not handle the failure |
| Tool call has no matching result | Harness shutdown, interrupted process, or incomplete transcript delivery |
| Session stops immediately after a prompt | Hook, model, permission, or harness configuration issue |
| Expected MCP call never appears | The model did not select the tool, or the harness transcript omits that detail |

## 5. Recover incomplete delivery

If the local harness still contains the session source, run:

```bash
observal reconcile
```

Reconciliation resumes from the acknowledged checkpoint and replays missing source records idempotently.
