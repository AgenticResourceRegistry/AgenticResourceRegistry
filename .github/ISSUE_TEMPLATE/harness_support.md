---
name: Detailed Harness Request
about: Submit a researched harness integration issue using the full implementation template
title: "Add harness support: "
labels: enhancement, harness-support
assignees: ""
---

<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

## Background

[Describe the harness, where it runs, and why Observal should support it. Link to the official product page.]

## Before you pick this up

Only take this issue if you are willing to use the harness as a daily driver for a while, properly test the integration, help maintain support for the runtime, and write proper documentation for it.

## Implementation guide

Follow [docs/adding-a-harness.md](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/docs/adding-a-harness.md) for the full file checklist and step-by-step process. Start with Step 1, Research the harness, using the official documentation to determine config paths, MCP format, hook support, and session log locations.

## Scope

[Describe the expected harness support, including MCP servers, skills, prompts or guidelines, hooks, custom agents, and session parsing. Call out unknowns that require research.]

> **Note:** PRs without screenshots showing traces and installed Observal components working in the harness will be automatically closed.

## Relevant docs found so far

[Link the official documentation for configuration, MCP servers, skills, hooks, custom agents, and session storage.]

| Feature | Path or behavior |
|---------|------------------|
| Project MCP config | [Path] |
| User MCP config | [Path] |
| MCP key | [Key] |
| Project skills | [Path] |
| User skills | [Path] |
| Project guidelines | [Path] |
| User guidelines | [Path] |
| Project agents | [Path] |
| User agents | [Path] |
| Hook config | [Path] |
| Session storage | [Path and format] |

## File checklist

See [docs/adding-a-harness.md, File Checklist](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/docs/adding-a-harness.md#file-checklist) for the full list.

[List any harness-specific files or changes discovered during research.]

## Acceptance criteria

- [ ] `observal agent pull <agent> --harness <name>` writes correct config and the agent is usable in the harness.
- [ ] `observal scan --harness <name>` discovers installed components.
- [ ] MCP servers are installable and functional within the harness.
- [ ] Skills are installable and functional within the harness.
- [ ] Guidelines are written to a documented harness-compatible location.
- [ ] Custom agents are scanned or generated when supported by the adapter.
- [ ] Hooks fire correctly on supported lifecycle events, or unsupported hooks are documented clearly.
- [ ] Sessions are traced with a working session parser, or the PR documents why session parsing is not possible.
- [ ] `observal doctor` reports the harness status.
- [ ] Registry synchronization tests pass.
- [ ] Harness adapter tests pass.

## Reference

- Implementation guide: [docs/adding-a-harness.md](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/docs/adding-a-harness.md)
- Official documentation: [Link]
- Source repository: [Link]
- First-class adapters for reference: `observal_cli/harness/claude_code.py`, `observal_cli/harness/kiro.py`

## Implementation checklist for harness support

Please include these updates in the same PR:

- `observal_cli/cmd_doctor.py`: add diagnose, patch, and cleanup coverage for the harness
- `observal_cli/layer.py`: update `HARNESS_LAYER_CONFIGS` and managed-file attribution paths
- `README.md`: add the harness to the supported harness list
- `docs/adding-a-harness.md`: keep checklist requirements aligned when needed
