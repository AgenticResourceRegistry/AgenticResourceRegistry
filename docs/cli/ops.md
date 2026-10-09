---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal ops"
---

Inspect current session telemetry, Registry usage, feedback, logs, and Agent insight reports.

All 11 supported workflows provide table and JSON output. Finite commands return one JSON document. Streaming commands return JSON Lines, one compact object per update.

The dead `ops metrics`, legacy `ops spans`, and synthetic `ops telemetry test` commands have been removed. Current trace detail comes from `ops traces --span` through the Sessions API.

## Commands

| Command | Purpose |
| --- | --- |
| `top` | Show the most-downloaded MCPs or Agents |
| `rate` | Submit Registry feedback |
| `rate-update` | Update the caller's feedback |
| `rate-delete` | Delete the caller's feedback |
| `feedback` | Show aggregate and individual feedback |
| `traces` | List current sessions or fetch their details |
| `telemetry status` | Check server event counts and local outbox health |
| `logs` | Follow local or remote development logs |
| `insights list` | List Agent insight reports |
| `insights show` | Show an insight report or section |
| `insights generate` | Generate an insight report |

## Top Registry items

```bash
observal ops top --type mcp --output json
observal ops top --type agent --output json
```

JSON returns the direct ranking array. MCP ranking is currently server-limited to five results; Agent ranking is currently server-limited to six.

## Feedback

Submit feedback:

```bash
observal ops rate alice/postgres --type mcp --stars 5 --comment 'Reliable' --output json
```

Update feedback:

```bash
observal ops rate-update alice/postgres --type mcp --stars 4 --output json
observal ops rate-update alice/postgres --type mcp --comment 'Updated review' --anonymous --output json
```

Delete feedback:

```bash
observal ops rate-delete alice/postgres --type mcp --yes --output json
```

View feedback:

```bash
observal ops feedback alice/postgres --type mcp --output json
```

Feedback types are `mcp`, `agent`, `skill`, `hook`, `prompt`, and `sandbox`. Stars accept 1 through 5 and comments accept at most 5,000 characters.

Rate and update return the direct feedback object. Delete currently returns an empty object. JSON deletion requires `--yes`; human mode prompts without it.

Feedback JSON combines aggregate and individual results:

```json
{
  "summary": {
    "average_rating": 4.5,
    "total_reviews": 2
  },
  "reviews": []
}
```

## Traces and current session details

`traces` uses the current REST session pipeline:

* Summary list: `GET /api/v1/sessions`
* Turn or span detail: `GET /api/v1/sessions/{session_id}`

```bash
observal ops traces --limit 20 --output json
observal ops traces --platform kiro --days 7 --output json
observal ops traces --turn --limit 5 --output json
observal ops traces --span --limit 3 --output json
```

`--platform` accepts a registered harness. `--days` accepts 1 through 365 and `--limit` accepts 1 through 200.

Default JSON returns the direct session summary array. Turn and span JSON fetch every selected session detail:

```json
{
  "view": "span",
  "items": [
    {
      "summary": {
        "session_id": "session-id"
      },
      "detail": {
        "events": []
      }
    }
  ]
}
```

`--turn` renders prompts and tool calls. `--span` includes full assistant and tool-result detail. Detail failures are surfaced rather than replaced with incomplete summaries.

## Telemetry status

```bash
observal ops telemetry status --output json
```

The server portion requires administrator access. The result combines recent server counts and local durable outbox state:

```json
{
  "server": {
    "status": "ok",
    "tool_call_events": 8,
    "agent_interaction_events": 5
  },
  "outbox": {
    "available": true,
    "pending": 0,
    "failed": 0,
    "total": 0,
    "bytes": 0
  }
}
```

A local outbox read failure remains visible as `available: false` with a safe error type. The removed synthetic test command is not part of the current JSONL session ingestion architecture.

## Logs

Read a finite local tail:

```bash
observal ops logs --level WARNING --lines 50 --no-follow --output json
```

Follow local logs:

```bash
observal ops logs --level INFO --output json
```

Follow remote server logs with administrator access:

```bash
observal ops logs --remote --level WARNING --output json
```

Valid levels are `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`. `--filter` performs case-insensitive text filtering. `--lines` must be zero or greater.

JSON mode emits JSON Lines. Local records have this shape:

```json
{"event":"log","source":"local","level":"ERROR","line":"raw log line"}
```

Remote records wrap the structured server event:

```json
{"event":"log","source":"remote","log":{"level":"ERROR","event":"request failed"}}
```

Malformed remote records, inaccessible files, HTTP errors, timeouts, and connection failures use categorized exits. Access tokens are never emitted. Log content itself may contain sensitive application data and must be handled accordingly.

## Agent insights

List reports:

```bash
observal ops insights list alice/reviewer --output json
```

Show a report or one section:

```bash
observal ops insights show alice/reviewer latest --output json
observal ops insights show alice/reviewer latest --section suggestions --output json
```

A section request returns `report_id`, `section`, and that section's `data` rather than the entire report.

Generate once:

```bash
observal ops insights generate alice/reviewer --period 14 --output json
```

Generate and wait with JSON Lines progress:

```bash
observal ops insights generate alice/reviewer --period 14 --wait --output json
```

```json
{"event":"queued","report":{"id":"report-id","status":"pending"}}
{"event":"progress","report":{"id":"report-id","status":"running","progress_percent":50}}
{"event":"progress","report":{"id":"report-id","status":"completed","progress_percent":100}}
```

Periods accept 1 through 365 days. `--version` and `--compare` require semantic versions. Invalid report rows, ambiguous prefixes, missing reports, unknown sections, unavailable providers, generation failures, and wait timeouts use categorized errors.

Report sections include:

* `at_a_glance`
* `what_they_work_on`
* `interaction_style`
* `usage_patterns`
* `what_works`
* `friction_analysis`
* `suggestions`
* `usage_cost_analysis`
* `version_comparison`
* `regression_detection`
* `on_the_horizon`
* `fun_ending`

## Component insights

Owners, co-authors and admins of an MCP, skill or hook can report on it with `--component TYPE NAMESPACE/SLUG`:

```bash
observal ops insights generate --component skill alice/review --period 14 --wait --output json
observal ops insights list --component skill alice/review --output json
observal ops insights show --component skill alice/review latest
```

Periods accept 1 through 90 days. Component reports count only sessions where the component was verified present. If there are none, generation returns 422 with the coverage instead of a zero-use report.

MCP reports count attributed calls. They can add model-written findings when insights are configured.

Skill reports are deterministic. They count present sessions, sessions with a confirmed load (the skill's file text entered the model's context), sessions with an explicit invocation, and sessions where the skill was offered. Each harness records a different subset:

| Harness | Offered | Confirmed load | Invocation |
| --- | --- | --- | --- |
| Pi | yes | model read of the skill's `SKILL.md` | not recorded |
| Claude Code | not recorded | `Skill` tool call with its linked expansion | `/name` command with its harness-written expansion |

"Not recorded" is unknown, never zero. If a cohort mixes harnesses, a count covers only the harnesses that record it, and the report says so. Skill evidence counts only for a verified install at the exact `SKILL.md` location the session names. The verifier assumes the default `~/.claude` and `~/.pi/agent` directories. Skills under a custom `CLAUDE_CONFIG_DIR` or `PI_CODING_AGENT_DIR` are not counted. A load or invocation shows the skill's instructions entered context. It does not show that the skill was followed or that it helped. Hook reports are deterministic and cover Claude Code and Pi. They count present sessions, sessions where the hook **could run**, and recorded runs: successful runs that printed output, failures, and runs that blocked an action. A recorded run must name the exact event and command of the verified install.

- **Silent runs.** Pi's extension records every run, so Pi reports count runs that succeeded without output as silent runs. Claude Code leaves no record of them: there, silent runs are unknown (`silent_runs` is null), recorded runs are a lower bound, and a hook with no recorded runs may still have run. In a cohort that mixes both, the silent count covers only Pi sessions and the report says so.
- **Agent hooks only run with their agent, and only interactively.** Hooks installed by `agent pull` live in the agent's frontmatter. They run only while that agent is active, and not at all in headless `claude -p` sessions. Those sessions are reported as "could not run" and kept out of the denominator rather than counted as no use. Hooks installed with `hook install` (in `settings.json`) run in every session.
- **Claude Code versions.** Hook records were proven on a tested range of Claude Code versions (currently 2.1.286). A session written by any other version is `version_unverified`: kept out of the denominator unless it recorded a run, and named in the report.
- A recorded run shows the hook executed. It does not show what it changed or that it helped.

- **Pi runs registry hooks in its Observal extension.** Only command hooks on `PreToolUse` and `PostToolUse` are installed, in user scope; a pull reports every other hook as not installed. A verified Pi hook can run in every session, interactive or headless, so every session that verifies it is in the denominator. Runs are read from receipts the extension writes, which any other extension in the same Pi process could also write. See [Pi: Registry hooks](../integrations/pi.md#registry-hooks).

## Exit codes

| Code | Meaning |
| --- | --- |
| 3 | Authentication required or failed |
| 4 | Telemetry, log, session, or insight permission denied |
| 5 | Registry item, report, session, or local log file not found |
| 6 | Ambiguous report or Registry reference |
| 7 | Invalid type, harness, section, period, version, comment, or missing confirmation |
| 8 | Rate limit reached |
| 9 | Server, provider, stream, outbox, or local log dependency unavailable |
| 10 | CLI and server version mismatch |

## Related

* [`observal inbox`](/cli/inbox): completed insight notifications
* [`observal agent`](/cli/agent): apply insight recommendations
* [`observal registry`](/cli/registry): inspect rated or recommended components
* [Debug Agent failures](/use-cases/debug-agent-failures)
