---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Use a registry agent in Copilot CLI"
description: "Pull a project agent, select it in GitHub Copilot CLI, and verify a small task."
---

Use `--harness copilot-cli` for the terminal application. VS Code Copilot is a separate registry integration, identified by `copilot`.

## Before you start

Install GitHub Copilot CLI and sign in with an account that can use it. Check `copilot --version` and the authentication instructions for that release. Registry login and GitHub/model authentication are separate.

Choose an approved release declaring `copilot-cli` support. [Inspect its instructions and pinned components](/guides/inspect-an-agent), and review any hooks or MCP servers before installing. This guide uses a project with `README.md` for the first task.

The native `--agent` command was checked against Copilot CLI `1.0.107` help and [GitHub's custom-agent guide](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli). Successful model execution is not yet verified in this documentation batch.

## Preview and pull

Replace the resource name and exact release:

```bash
observal agent pull YOUR_NAMESPACE/YOUR_AGENT --harness copilot-cli --version AGENT_VERSION --strict --dry-run
observal agent pull YOUR_NAMESPACE/YOUR_AGENT --harness copilot-cli --version AGENT_VERSION --strict
```

The registry adapter currently uses fixed project scope for Copilot CLI. Omit `--scope`: this CLI revision rejects an explicit scope for the harness, even `--scope project`. Its agent profile is `.github/agents/LOCAL_NAME.agent.md`; project MCP configuration uses `.mcp.json`. It can also write hooks, skills, and prompt files. Read the preview instead of assuming the pull is only an instruction download.

In particular, `.github/hooks/observal.json` can contain session-push hooks. Those can report session content to the configured registry. Confirm that behavior is appropriate for the project before pulling.

## Select the installed agent

Restart Copilot CLI from the same project directory:

```bash
copilot
```

Enter `/agent`, select the installed profile, and submit a small task. Use the exact local ID derived from its filename without `.agent.md`. Personal profiles with the same ID can take precedence, so check the selected profile rather than relying on an ambiguous display name.

For explicit command-line selection:

```bash
copilot --agent LOCAL_NAME --prompt "Read README.md and summarize it in three bullets. Do not edit files or run shell commands."
```

Replace `LOCAL_NAME` with the actual installed ID. Keep normal permission checks enabled. Do not add `--allow-all-tools` or broad permissions merely to finish the example.

## Check the outcome

Confirm the CLI selected your custom agent and that its task completed. Review any tool requests before approving them. A prompt that says “do not edit” is not a sandbox or a tool-permission policy.

```bash
observal scan --inventory --harness copilot-cli
```

The agent should be discoverable in the local inventory. That confirms the file is installed, not that the runtime selected it or that telemetry was delivered.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Agent missing from `/agent` | Current project, generated filename, frontmatter, and whether the CLI was restarted. |
| A different profile wins | Personal/project ID collisions and the CLI's displayed selection. |
| Authentication or entitlement failure | Copilot CLI's GitHub account and model access, not the registry token. |
| Tool action denied in prompt mode | Non-interactive execution cannot ask for missing permission. Review and authorize only the specific action you intend. |
| Install worked but no insights appeared | Capture, delivery, access, and report setup are separate. Component-insight attribution is not currently supported for this harness. |

For exact custom-agent selection and precedence rules, use [GitHub's Copilot CLI documentation](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli). Follow the registry's [version guidance](/reference/documentation-version) when your installed releases differ from this development-checked guide.
