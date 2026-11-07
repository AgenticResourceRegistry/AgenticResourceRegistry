---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Share agent configurations"
description: "Give teammates the same versioned setup without copying coding-tool configuration."
---

Share a registry agent when teammates need the same instructions and resources. They pull a reviewed version into their own coding tool instead of copying your local configuration or credentials.

## Before you start

You need a published agent that your teammate can access. Confirm it supports their harness and that its components are compatible with that tool. Private teamspace resources require the appropriate membership.

If you are creating the agent, use the [local authoring workflow](/cli/agent#local-authoring-workflow). `agent add` takes component UUIDs: look up each resource rather than inventing names in the YAML. Build validates the definition; publishing and review make it available according to your registry's policy.

## Share the qualified name

Send the agent's `namespace/slug`, its intended use, and the harnesses you have actually checked. For example, `platform/reviewer` is a clearer reference than `reviewer`, which may exist in more than one namespace.

Your teammate can inspect it:

```bash
observal agent show platform/reviewer --output json
```

The name above is illustrative; replace it with your agent's actual qualified name. JSON includes the prompt and component links; the default table does not. Follow [Inspect an agent](/guides/inspect-an-agent) to review the exact release and its pinned components, rather than assuming current listing details match a project lock.

## Preview and pull

For Claude Code, from the target project:

```bash
observal agent pull platform/reviewer --harness claude-code --scope project --dry-run
observal agent pull platform/reviewer --harness claude-code --scope project
```

The CLI writes a Markdown agent profile with YAML frontmatter under `.claude/agents/`, installs compatible resources, and runs required setup such as MCP registration. Activate it using the exact generated local name. [First-agent walkthrough](/getting-started/quickstart).

For Kiro or Cursor, choose the appropriate `--harness` value and follow that tool's [integration instructions](/integrations/index). Do not assume configuration formats, defaults, or activation commands are the same. `vscode` is not a current harness identifier; VS Code Copilot uses `copilot`.

## Share versions, not secrets

Project-scope installs record the agent version in `observal.lock`. Commit that lockfile when teammates should pull the same version. Publishing newer component versions does not update an existing agent's pins by itself; the author releases a new agent version to change them.

Do not share local token files or generated credentials. Each teammate supplies the component settings and credentials required by their installation. These values are sent to the registry when it generates install configuration; enter secrets interactively instead of placing them in shell arguments.

A pull can install session hooks and a delegation MCP entry as well as your explicitly selected components. Review the preview with your teammate, especially where session data can be reported to the server.

## If a teammate cannot install it

Check these in order:

1. The qualified name and their registry connection.
2. Their teamspace access and the agent version's publication state.
3. The version's declared harness support and required component capabilities.
4. The preview warnings, required credentials, and setup-command failures.
5. The tool-specific activation step after installation.

Do not override unsupported-harness checks just to make the command finish. A partial install may not provide the behavior your team depends on.

## Next

[Manage teamspaces](/use-cases/teamspaces), [fork an agent for a different workflow](/use-cases/fork-and-customize), or [check installed versions](/cli/outdated).
