---
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Hook contracts and safety boundaries"
description: "Understand event, handler, timeout, and harness-specific failure semantics."
---

Use [Publish a hook](/registry-hook-helper) for the author/reviewer workflow. This reference explains why a hook's declaration is not a universal runtime contract.

## Concepts

| Property | What it declares |
| --- | --- |
| Event | Intended lifecycle point such as `PreToolUse`, `PostToolUse`, `Stop`, or `UserPromptSubmit` |
| Handler type | Local command/script or HTTP endpoint |
| Execution mode | Requested async/sync/blocking behavior |
| Handler config | Command/URL, timeout, and supported handler details |
| Scope | Intended agent/session/global context |

The adapter and native harness determine what is actually representable. Not every event, scope, handler, platform, or headless mode supports the same semantics. Use [integration guidance](/integrations/index) and its stated tested versions.

## CLI Commands

Existing command links remain available here; use the canonical registry reference for current v1.16.0 options rather than a duplicated recipe.

### Submit a hook

Use [hook submission options](/cli/registry#observal-registry-hook-submit) and the [author/reviewer workflow](/registry-hook-helper).

### List hooks

Use [hook listing and filters](/cli/registry#observal-registry-hook-list).

### Show hook details

Use [hook detail inspection](/cli/registry#observal-registry-hook-show); inspect executable content and exact versions before installation.

### Install a hook

Use [hook installation and raw preview](/cli/registry#observal-registry-hook-install), with the target harness's capabilities and ownership constraints.

### Edit a hook

Use [hook editing](/cli/registry#observal-registry-hook-edit); publish reviewed successors instead of assuming immutable releases can be overwritten.

## Timeouts

The current CLI validates caps of 30 seconds for blocking, 10 for sync, and 60 for async submission modes. These limits do not prove a future harness will enforce the same timeout or honor a veto. A timeout/error is not automatically a safe fail-closed outcome.

## Input and exit behavior

Many command hooks receive JSON on stdin, but exact fields and interpretation differ. Do not assume an `$INPUT` environment variable contains it. For tool-event handlers, validate the actual payload and missing/malformed cases without logging private arguments.

Exit `2`, stdout, stderr, and HTTP failures have event/harness-specific meanings. A pre-tool handler may block where supported; a post-tool hook cannot undo an already executed action. Stdout is not universally passed back as model context.

The prior generic “grep rm -rf” example is not a complete guard: shell syntax, missing input, alternate commands, paths, and tool identities require a real threat model and tests. Do not treat a short illustrative hook as protection for production secrets or destructive actions.

## Installation and ownership

Standalone hook install can write handlers and merge settings. Its `--raw` path does not write. An agent install can place hooks in a profile or a supported settings gate; scope metadata alone does not prove standalone settings apply only to that agent.

Malformed existing settings, local edits, source/script delivery, and unsupported handlers need explicit error/ownership handling. Inspect the output before overwriting anything. For Claude Code agent hooks, see [frontmatter/headless/settings placement](/cli/pull#agent-hooks-in-headless-sessions). Pi's supported event/type/platform matrix differs.

## Privacy and evidence

A handler can read tool data, change files, or transmit events to its HTTP endpoint. Approval, installed presence, and an execution receipt are distinct signals. Test with controlled data on the actual native harness before relying on blocking or attribution. See [insights interpretation](/guides/interpret-insights).

Exact authoring/install options are in [the registry reference](/cli/registry); use the [hook publishing guide](/registry-hook-helper) rather than duplicating a deployment script here.
