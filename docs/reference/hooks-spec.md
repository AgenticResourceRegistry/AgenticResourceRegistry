---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Hooks specification"
---

The schema Observal uses for hook definitions -- both the registry hook type (`observal registry hook`) and hooks wired into harness configs by `observal agent pull` / `observal doctor patch`.

Claude Code session hooks currently use `HOOKS_SPEC_VERSION = "13"` in `observal_cli/harness_specs/claude_code_hooks_spec.py`. Other harnesses have their own hook specs.

## Where hooks live

Two distinct things share the name "hook":

1. **Registry hooks**: packaged, versioned hook definitions in the Observal registry. Install them via `observal registry hook install`.
2. **harness hooks**: entries in `~/.claude/settings.json`, `.kiro/agents/<name>.json`, etc. These are written by `observal agent pull` and `observal doctor patch`.

Both use the same event vocabulary.

## Events

| Event | When it fires |
| --- | --- |
| `SessionStart` | New harness session begins |
| `Stop` | Session ends |
| `SubagentStop` | Sub-agent session ends (Claude Code only) |
| `UserPromptSubmit` | User submits a prompt |
| `PreToolUse` | Before a tool call |
| `PostToolUse` | After a tool call (with result) |
| `Notification` | harness surfaces a notification |

Source: `observal_cli/constants.py:VALID_HOOK_EVENTS`.

## Handler types

| Type | Payload | Used by |
| --- | --- | --- |
| `command` | Shell command with templated args | Kiro (shell hooks only), Claude Code (local scripts) |
| `http` | URL + method + headers + body | Claude Code (native HTTP hooks) |

## Execution modes

| Mode | Semantics |
| --- | --- |
| `async` | Fire and forget - harness doesn't wait |
| `sync` | harness waits for handler to return before continuing |
| `blocking` | Handler can veto the event (e.g. block a tool call) |

Source: `observal_cli/constants.py:VALID_HOOK_EXECUTION_MODES`.

## Scopes

| Scope | Effect |
| --- | --- |
| `agent` | Applies only to one agent |
| `session` | Applies for the duration of a session |
| `global` | Applies across everything |

## Session delivery hooks

Telemetry is delivered by the session push hook, which reads the local harness transcript and sends session records to `/api/v1/ingest/session`. It uses the CLI configuration in `~/.observal/config.json`; it does not require a `curl` wrapper, a telemetry environment variable, or an API key header.

For Claude Code, `observal doctor patch --harness claude-code` writes **two** Observal-managed command hooks into `~/.claude/settings.json`: `UserPromptSubmit` and `Stop`. Each runs the installed Python interpreter with `-m observal_cli.hooks.session_push --harness claude-code`. Later patches recognize Observal's own hooks by their command, so user-authored hooks are left alone.

For Kiro, the current IDE/CLI reads `.kiro/hooks/observal.json` with `UserPromptSubmit` and `Stop` triggers. Legacy CLI 2.x (without the IDE) uses inline `userPromptSubmit` and `stop` hooks on its agent profile. Both use `-m observal_cli.hooks.session_push --harness kiro`, not `curl`. See `observal_cli/harness_specs/kiro_hooks_spec.py` for the exact formats.

Other harnesses have their own capability-specific specs. Use `observal doctor` to inspect the current installation and `observal doctor patch --all-harnesses` to instrument detected harnesses.

## Event name mapping (Claude Code ↔ Kiro)

Legacy Kiro CLI 2.x inline hooks use camelCase / lowercase event names; current standalone Kiro hooks use PascalCase triggers, like Claude Code. Observal maps the legacy names as follows:

| Claude Code | Kiro |
| --- | --- |
| `SessionStart` | `agentSpawn` |
| `Stop` | `stop` |
| `SubagentStop` | *(no equivalent)* |
| `UserPromptSubmit` | `userPromptSubmit` |
| `PreToolUse` | `preToolUse` |
| `PostToolUse` | `postToolUse` |
| `Notification` | *(no equivalent)* |

## Registry hook payload shape

When submitting a hook to the registry (`observal registry hook submit`):

```json
{
  "name": "pretooluse-logger",
  "description": "Logs every tool call to a local file",
  "event": "PreToolUse",
  "handler_type": "command",
  "command": "echo \"$TOOL_NAME $(date)\" >> ~/.observal/tool-log.txt",
  "execution_mode": "async",
  "scope": "agent",
  "harness": ["claude-code", "kiro"]
}
```

Each field is validated server-side against the lists in `observal_cli/constants.py` (mirrored from `observal-server/schemas/constants.py`).

## Source of truth

* `observal_cli/harness_specs/`: per-harness session hook specifications and versions
* `observal_cli/constants.py`: valid events, handler types, execution modes, scopes
* `observal-server/schemas/constants.py`: server-side mirror

A sync test (`tests/test_constants_sync.py`) ensures CLI and server stay in lockstep.

## Related

* [`observal registry hook`](/cli/registry)
* [Session tracking and reconciliation](/core-concepts/session-tracking)
