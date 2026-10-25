---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Hook helper"
---

Use hook components when a harness should run deterministic checks or side effects around agent lifecycle events.

## What to fill in

| Field | What it means | Example |
|-------|---------------|---------|
| Name | Registry slug for the hook | `block-rm` |
| Event | Lifecycle event that triggers it | `PreToolUse` |
| Handler type | `command` for local scripts, `http` for webhooks | `command` |
| Handler command | Command or script filename | `./hooks/block-rm.sh` |
| Handler URL | HTTP endpoint for an HTTP hook | `https://hooks.example.com/pre-tool-use` |
| Execution mode | `blocking`, `sync`, or `async` | `blocking` |
| Timeout | Seconds before canceling | `10` |
| Scope | `agent`, `session`, or `global` | `agent` |
| Source URL | Optional repo for review and provenance | `https://github.com/acme/agent-hooks` |
| Source path | Directory containing hook files | `hooks/security` |

Use blocking only when the hook must stop unsafe behavior. Use async for logging, telemetry, and slow background work.

## Command hook example

```json
{
  "name": "block-rm",
  "version": "1.0.0",
  "description": "Block destructive shell commands before they run",
  "event": "PreToolUse",
  "handler_type": "command",
  "handler_config": {
    "command": "./hooks/block-rm.sh",
    "timeout": 10
  },
  "execution_mode": "blocking",
  "scope": "agent"
}
```

## HTTP hook example

```json
{
  "name": "audit-bash",
  "version": "1.0.0",
  "description": "Send Bash tool calls to an audit endpoint",
  "event": "PreToolUse",
  "handler_type": "http",
  "handler_config": {
    "url": "https://hooks.example.com/pre-tool-use",
    "timeout": 10
  },
  "execution_mode": "sync",
  "scope": "session"
}
```

## CLI example

Submit a hook from a JSON file like the examples above:

```bash
observal registry hook submit --from-file hook.json
```

## Sources

- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
