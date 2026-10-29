---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal doctor"
---

Diagnose local Observal state, repair managed telemetry instrumentation, remove managed instrumentation, and work with redacted support bundles.

All five workflows support `--output table|json`. JSON mode never prompts or emits human banners.

## Commands

| Command | Purpose |
| --- | --- |
| `doctor` | Diagnose configuration, Registry metadata, skills, and harness telemetry |
| `doctor patch` | Install or update Observal-managed telemetry instrumentation |
| `doctor cleanup` | Remove Observal-managed telemetry instrumentation |
| `doctor support bundle` | Generate a redacted diagnostic archive |
| `doctor support inspect` | Inspect a diagnostic archive without extracting it |

## Diagnose

```bash
observal doctor --output json
```

Doctor checks:

* Local authentication configuration and server health
* Registry lockfile metadata against the active Registry
* Managed hooks, plugins, or extensions for all registered harnesses
* UUID-attributed Kiro Agent hooks
* Claude Code Agents pulled with `--hooks=settings`: a warning when Claude Code is outside the tested version range, or when the Python interpreter their gated hooks name no longer exists (pull those Agents again)
* Bundled Observal skill installation

JSON diagnosis exits zero when the checks ran successfully. Health is reported through `healthy`, `issues`, and `warnings`:

```json
{
  "healthy": false,
  "issues": ["Cannot reach the configured server"],
  "warnings": ["Cursor session push hooks are not installed"],
  "lockfile_changes": [],
  "skill_missing": [],
  "fix_attempted": false,
  "patch": null
}
```

Human mode retains health-check behavior: unresolved issues exit nonzero. Warnings alone remain successful.

Apply fixable warnings and canonical lockfile metadata without prompting:

```bash
observal doctor --yes --output json
```

Installed version pins are not changed. Only Observal-managed telemetry entries are updated.

## Patch

Preview every registered harness:

```bash
observal doctor patch --all-harnesses --dry-run --output json
```

Patch selected harnesses:

```bash
observal doctor patch --harness claude-code --harness kiro --output json
```

Exactly one target mode is required: `--all-harnesses` or one or more `--harness` options. JSON returns one result per harness:

```json
{
  "action": "patch",
  "dry_run": false,
  "changed": true,
  "targets": [
    {"harness": "kiro", "changed": true}
  ]
}
```

Patch is idempotent. It preserves unrelated hooks and configuration. Configuration writes are atomic. Invalid or unreadable harness files fail loudly rather than being replaced.

For Pi, patch installs the bundled TypeScript extension at `~/.pi/agent/extensions/observal.ts` and records the CLI version it came from in an adjacent `.observal-extension.json`. A local file Observal did not write is never touched. MCP commands and remote URLs are never wrapped or rewritten.

## Cleanup

Preview cleanup:

```bash
observal doctor cleanup --dry-run --output json
```

Remove instrumentation from one harness:

```bash
observal doctor cleanup --harness goose --yes --output json
```

Remove instrumentation from all registered harnesses except selected entries:

```bash
observal doctor cleanup --exclude kiro --yes --output json
```

Cleanup removes only Observal-managed hooks, plugins, extensions, and legacy telemetry settings. User-owned hooks remain, and so do an Agent's gated hooks placed by `agent pull --hooks=settings` (move those back with `--hooks=frontmatter`). Human mode confirms before writing unless `--yes` is present. JSON cleanup requires `--yes` unless it is a dry run.

Unknown harnesses, conflicting selections, malformed configuration, and write failures are surfaced before success is reported.

## Support bundles

```bash
observal doctor support bundle --output json
observal doctor support inspect ./observal-support.tar.gz --output json
```

See [Support bundles](/cli/support) for archive contents, redaction, offline behavior, and inspection limits.

## Exit codes

| Code | Meaning |
| --- | --- |
| 3 | Authentication is required for patching |
| 5 | Support bundle or requested bundle file not found |
| 6 | Output archive already exists |
| 7 | Invalid harness selection, malformed harness file, invalid bundle, or missing confirmation |
| 9 | Server, collector, or filesystem unavailable |

## Related

* [`observal scan`](/cli/scan): read-only harness inventory
* [`observal agent pull`](/cli/pull): install a complete Agent
* [Session tracking](/core-concepts/session-tracking): telemetry delivery architecture
