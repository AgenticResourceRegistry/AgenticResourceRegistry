---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Antigravity: check compatibility before activation"
description: "Inspect the registry's generated Antigravity profile and confirm your CLI can load its format."
---

The registry has an Antigravity adapter, but a registered adapter does not prove that its generated profile loads in every external CLI release.

<Warning>
  Activation compatibility is unresolved for the current vendor-documented format. This registry revision emits `agent.json` profiles; current Antigravity documentation describes `agent.md` profiles and a different global location. No Antigravity CLI was available for a live check in this docs batch. Do not treat this page as a verified first-agent walkthrough.
</Warning>

## Before you start

Check the Antigravity CLI you actually use (`agy --version` and `agy --help`), its native agent locations, and your model authentication. Sign in to the registry separately.

The current vendor [Agents command guide](https://antigravity.google/docs/cli/commands/agents.md) describes project profiles at `.agents/agents/NAME/agent.md` and global profiles under `~/.gemini/config/agents/NAME/agent.md`.

That differs from the registry's current output. This discrepancy requires a runtime compatibility check or an adapter update; documentation alone cannot establish that legacy JSON remains supported.

## Inspect the registry output

[Inspect the exact agent release](/guides/inspect-an-agent), then preview an explicit project scope:

```bash
observal agent pull YOUR_NAMESPACE/YOUR_AGENT --harness antigravity --scope project --version AGENT_VERSION --strict --dry-run
```

Replace the qualified name and version. The registry currently plans these locations:

| Scope | Generated agent profile | Generated MCP configuration |
| --- | --- | --- |
| Project | `.agents/agents/LOCAL_NAME/agent.json` | `.agents/mcp_config.json` |
| User (default) | `~/.gemini/antigravity-cli/agents/LOCAL_NAME/agent.json` | `~/.gemini/antigravity-cli/mcp_config.json` |

The JSON profile carries `name`, `description`, `system_prompt`, tool/MCP configuration, and model settings. These are the adapter's generated fields, not a claim that your current CLI will load them.

## Establish compatibility before pulling

Compare the preview with your CLI's accepted schema and agent locations. If the CLI does not load the generated JSON format, stop and use a verified integration or have the Antigravity adapter updated and tested.

Do not just rename `agent.json` to `agent.md`: changing an extension does not translate instructions, tool permissions, model settings, MCP connections, or lock/attribution records. v1.16.0 has no `--allow-unsupported-harness` flag; declarations or successful generation do not resolve this format mismatch.

If a compatible installed version is established, apply the same scoped release, restart the CLI, and confirm its Agent Manager actually lists the profile before running a task.

## Native selection checkpoint

Current vendor documentation describes `/agents` in the TUI and `agy --agent LOCAL_NAME` for explicit session selection. Select a profile that the runtime actually discovered, then give it a small read-only task and inspect the selected agent and result.

A generated file, local registry inventory entry, or instruction text copied into a prompt is not proof of native agent activation. Successful execution and session evidence remain required before this integration is presented as a verified path for that CLI version.

See [Antigravity's agent selection documentation](https://antigravity.google/docs/cli/commands/agents.md) and [CLI reference](https://antigravity.google/docs/cli/reference/) for the native side. The registry's [documentation version page](/reference/documentation-version) describes the source revision being evaluated.
