---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Hooks specification"
---

The schema Observal uses for hook definitions -- both the registry hook type (`observal registry hook`) and hooks wired into harness configs by `observal agent pull` / `observal doctor patch`.

Current version: `HOOKS_SPEC_VERSION = "5"` (see `observal_cli/hooks_spec.py`).

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

## Metadata marker

Observal writes a `_observal` key into hook matcher groups so subsequent runs of `doctor patch` / `pull` can find and update only Observal-managed hooks without stomping on user-authored ones.

```json
{
  "_observal": {
    "version": "5",
    "source": "observal-scan",
    "installed_at": "2026-04-21T14:30:55Z"
  },
  "PreToolUse": [ ... ]
}
```

Older installs (pre-metadata) are detected with a fallback heuristic.

## Claude Code: native HTTP hook example

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "_observal": { "version": "5", "source": "observal-pull" },
        "matcher": "*",
        "type": "http",
        "url": "http://localhost/api/v1/telemetry/hooks",
        "method": "POST",
        "headers": {
          "Authorization": "Bearer ${OBSERVAL_API_KEY}",
          "Content-Type": "application/json"
        }
      }
    ]
  }
}
```

## Kiro: shell-command hook example

Kiro doesn't support native HTTP hooks, so Observal uses `curl`:

```json
{
  "name": "my-agent",
  "hooks": {
    "agentSpawn":       "curl -s -X POST http://localhost/api/v1/telemetry/hooks -H 'Authorization: Bearer $OBSERVAL_API_KEY' -H 'Content-Type: application/json' -d @-",
    "userPromptSubmit": "curl -s -X POST http://localhost/api/v1/telemetry/hooks ...",
    "preToolUse":       "curl -s -X POST http://localhost/api/v1/telemetry/hooks ...",
    "postToolUse":      "curl -s -X POST http://localhost/api/v1/telemetry/hooks ...",
    "stop":             "curl -s -X POST http://localhost/api/v1/telemetry/hooks ..."
  }
}
```

## Event name mapping (Claude Code ↔ Kiro)

Kiro uses camelCase / lowercase event names; Claude Code uses PascalCase. Observal maps between them.

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

* `observal_cli/hooks_spec.py`: version, metadata marker, spec shape
* `observal_cli/constants.py`: valid events, handler types, execution modes, scopes
* `observal-server/schemas/constants.py`: server-side mirror

A sync test (`tests/test_constants_sync.py`) ensures CLI and server stay in lockstep.

## Related

* [`observal registry hook`](/cli/registry)
* [Session tracking and reconciliation](/core-concepts/session-tracking)
