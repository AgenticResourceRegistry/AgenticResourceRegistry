---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Publish a hook"
description: "Review a lifecycle handler and verify its actual behavior on the target harness."
---

Use a hook for a deterministic handler at a supported lifecycle event. A hook can execute code or send data, so its event, input, permissions, and failure behavior need a targeted test—not only an approval badge.

## Before you start

Choose the actual harness/version and event. Know whether the handler is a command or HTTP endpoint, what it receives, and where its files/binaries exist. Scope and execution-mode metadata do not prove the runtime enforces an agent-only boundary or blocks an action.

For a first example, use a reminder rather than an untested security guard. The POSIX command below prints text; it does **not** prevent destructive actions:

```bash
observal registry hook submit --name review-reminder --version 1.0.0 --description 'Remind the user to inspect changes; not a security blocker' --event UserPromptSubmit --handler-type command --handler-command "printf '%s\n' 'Review the changed files.'" --execution-mode sync --timeout 5 --scope agent --harness claude-code --draft --output json
```

No secret input is required. Scripts can instead be submitted through `--script`; inspect the stored content, filename, dependencies, and whether your target actually supports script delivery.

## Inspect and submit

```bash
observal registry hook show YOUR_NAMESPACE/review-reminder --output json
observal registry hook submit --submit LISTING_UUID --output json
```

Use the returned identity. Check command/URL, event, scope, mode, timeout, source, and prerequisites. Review the selected version and files through [publication](/guides/review-and-publish). A source URL is provenance, not a guarantee that files are built or present on every machine.

## Preview the actual placement

For a standalone no-write response:

```bash
observal registry hook install YOUR_NAMESPACE/review-reminder --harness claude-code --version COMPONENT_VERSION --raw
```

For agent context, [pin it in an agent](/guides/components#add-an-exact-component-version-to-an-agent) and inspect the complete agent's strict pull preview. Compare generated settings/profile paths with the intended scope. Standalone settings placement is not automatically the same as an agent-owned gated hook.

The ordinary install command can write files/merge settings; JSON output does not make it read-only. There is no universal component `--dry-run` flag: use the supported raw preview or an agent preview.

## Verify the event and failure modes

Trigger a small controlled event in an isolated target harness. Confirm the handler actually runs, receives the expected input, times out as intended, and returns the documented result. Test failure/blocking behavior separately before relying on it for safety.

Input fields, exit-code semantics, stdout context, supported HTTP handlers, platform behavior, and headless execution differ across harnesses. Do not assume `$INPUT` is populated or that `exit 2` always vetoes a tool call. A post-tool handler cannot undo the action that already happened.

See the [hook contract reference](/hooks) and the specific [integration guide](/integrations/index). HTTP handlers can disclose event data to their endpoint; local logging can retain sensitive arguments. Review the data policy before enabling either.

## Next

[Publish a new reviewed hook version](/guides/version-components), then release a new agent version when its pins should move. Do not silently change an approved handler or treat install/presence evidence as proof it protected a session.
