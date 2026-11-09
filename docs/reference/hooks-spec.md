---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Hooks specification"
description: "Accepted request fields, requested semantics, and verified-runtime boundaries for registry hooks."
---

This page describes the registry's accepted hook request shape. It is **not** a universal native-harness execution specification. Use [Publish a hook](/registry-hook-helper) for the authoring journey and [hook contracts](/hooks) for input, failure, ownership, and safety boundaries.

## Where hooks live

Registry definitions and native configuration are different surfaces. A registry listing carries versioned request metadata; a consumer adapter determines actual profile/settings placement. Managed session-delivery hooks installed by `doctor patch` are separate instrumentation, described below.

## Registry hook payload shape

A command-hook submission contains the handler inside `handler_config`, and target declarations use `supported_harnesses`:

```json
{
  "name": "review-reminder",
  "version": "1.0.0",
  "description": "Print a reminder before proceeding; not a security blocker",
  "owner": "example-author",
  "event": "UserPromptSubmit",
  "handler_type": "command",
  "handler_config": {
    "command": "printf '%s\\n' 'Review the changed files.'",
    "timeout": 5
  },
  "execution_mode": "sync",
  "scope": "agent",
  "supported_harnesses": [
    "claude-code"
  ]
}
```

Replace the identity and handler for your actual resource. This is a complete illustrative `HookSubmitRequest`, not executable protection or proof the handler runs. Request metadata such as `version` and `owner` is required by the submission schema; authority still comes from the authenticated caller and target policy.

The CLI's `--handler-command`, `--handler-url`, and repeated `--harness` options construct these fields. A top-level API `command` or `harness` list is not the correct submission shape. Installation is a different request: `HookInstallRequest` has a single `harness`, optional selected `version`, platform, and local-name information. Do not interchange the two schemas.

## Events

The current accepted registry vocabulary is `SessionStart`, `Stop`, `SubagentStop`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, and `Notification`.

Acceptance of a name means it is valid registry metadata—not that every harness/platform/mode emits it or supports its handler. In particular, native Stop semantics and subagent support differ. Check the relevant [integration guide](/integrations/index) and adapter before assigning runtime meaning to the event.

## Event name mapping (Claude Code ↔ Kiro)

Mapping depends on the adapter and native format revision, including legacy Kiro inline events versus standalone hooks. Inspect the registry's event maps and the [Kiro guide](/integrations/kiro) instead of copying an old cross-harness table. A valid registry event is not a promise that an equivalent native event exists.

## Handler types

| Accepted type | Request data | What still needs verification |
| --- | --- | --- |
| `command` | `handler_config` command and supported timeout/details; optional stored script metadata | Executable/file availability, native input, permissions, and script delivery support |
| `http` | `handler_config` endpoint and supported details | Native HTTP-handler support, authentication, data policy, and failure behavior |

A source path, script body, or endpoint in a request is not proof that a consumer can execute or reach it. Do not assume templating, method/header/body support, or HTTP behavior beyond the target adapter's contract.

## Execution modes

| Accepted mode | Requested intent—not universal enforcement |
| --- | --- |
| `async` | Non-blocking/background handling where representable |
| `sync` | Waiting for a handler where supported |
| `blocking` | Blocking behavior only where the native event/handler supports it |

The CLI currently validates timeout caps of 60 seconds for async, 10 for sync, and 30 for blocking submissions. These validation limits do not certify runtime timeout, veto, fail-open/fail-closed, stdout-context, or exit-code semantics. See [input and exit behavior](/hooks#input-and-exit-behavior).

## Scopes

| Accepted scope | Requested intent—not an automatic isolation boundary |
| --- | --- |
| `agent` | Associate with an agent; generated placement/gating must establish which events actually run it |
| `session` | Session-oriented configuration where the native format supports it |
| `global` | Shared configuration subject to event/filter/permission and native placement rules |

`scope: agent` does not guarantee a standalone settings installation applies only to one agent. Inspect the generated profile/settings and verified gating behavior. For Claude Code's opt-in command gates, use the [headless/settings placement contract](/cli/pull#agent-hooks-in-headless-sessions). Pi and other harnesses have different supported matrices.

## Session delivery hooks

Registry components are reviewed/versioned resources. Instrumentation installed by `doctor patch` is a separate mechanism that reads local transcripts and can send session content to the registry. Do not treat both as the same request schema or infer that global capture belongs only to the selected agent.

Managed instrumentation uses installation-specific launcher and spec metadata. Do not copy a "current spec version" number from an example; inspect the installed plan. Use a targeted preview, such as `observal doctor patch --harness claude-code --dry-run`, and the [session-delivery/integration documentation](/core-concepts/session-tracking) before changing capture.

## Source of truth

- `observal-server/schemas/hook.py`: submission, draft, update, and install request models.
- CLI/server constants: accepted metadata values and synchronization tests.
- `packages/observal-shared/observal_shared/harness_registry.py` and adapters: capability/event/path declarations.
- `observal_cli/harness_specs/` and the Pi extension: managed instrumentation details, not a substitute for native execution proof.

Check the selected code/tool revision and actual behavior. A valid payload, published component, or installed file does not establish event execution, isolation, observed use, or effectiveness.

## Related

[Publish a hook](/registry-hook-helper) · [Hook contract reference](/hooks) · [Integration support](/integrations/index) · [Review/publication](/guides/review-and-publish)
