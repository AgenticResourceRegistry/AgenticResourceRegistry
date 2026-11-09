---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Pull your first agent"
description: "Find an agent, inspect its setup, pull it into Claude Code, and activate it."
---

Install an agent from your registry and use it in a coding session. This guide uses **Claude Code with project scope** as one concrete path. Other coding tools have different activation steps; use their [integration guides](/integrations/index).

## Before you start

- [Install the CLI](/getting-started/installation) and [sign in to your registry](/getting-started/onboarding).
- Install Claude Code and sign in to it separately.
- Choose a project directory where you are comfortable adding agent configuration. The example task below reads `README.md`, so choose a project that has one. Start with a disposable project if you are evaluating an unfamiliar agent.

An agent can install commands, hooks, and MCP servers that run with your permissions. Inspect what you are installing before you run it.

## Find and inspect an agent

```bash
observal agent list
```

Choose an agent available to your account. Use its qualified name, such as `your-team/reviewer`, rather than guessing a bare name.

For the commands below, replace this **illustrative name** with the one returned by your registry:

<Tabs>
  <Tab title="macOS / Linux">

```bash
AGENT='your-team/reviewer'
observal agent show "$AGENT" --output json
```

  </Tab>
  <Tab title="PowerShell">

```powershell
$AGENT = 'your-team/reviewer'
observal agent show "$AGENT" --output json
```

  </Tab>
</Tabs>

Both shells can use the quoted `"$AGENT"` argument in the pull commands below.

JSON output includes `prompt` and `component_links`; the default table is only a summary. Read the instructions and identify the components before installing. Choose an agent that supports `claude-code`. If there are no agents available, use the [empty-registry path](#no-agents-yet) below.

### Inspect the release you will install

A listing's details are not necessarily the version pinned in your project. List its releases:

```bash
observal agent versions "$AGENT"
```

Choose an approved version. If this project already has `observal.lock`, inspect its pinned version before deciding to replace it. Copy the agent's `id` from JSON output and the chosen version into these variables; the values below are placeholders:

<Tabs>
  <Tab title="macOS / Linux">

```bash
AGENT_ID='COPY_AGENT_UUID_FROM_SHOW'
VERSION='COPY_APPROVED_VERSION'
```

  </Tab>
  <Tab title="PowerShell">

```powershell
$AGENT_ID = 'COPY_AGENT_UUID_FROM_SHOW'
$VERSION = 'COPY_APPROVED_VERSION'
```

  </Tab>
</Tabs>

Read the exact release and its component pins:

```bash
observal api GET "/api/v1/agents/$AGENT_ID/versions/$VERSION" --output json
observal api GET "/api/v1/agents/$AGENT_ID/versions/$VERSION/lock" --output json
```

Check `prompt`, `supported_harnesses`, `external_mcps`, and `components` in the release detail. The lock identifies component versions and digests. A component name alone does not show its executable content: [inspect the referenced component versions](/guides/inspect-an-agent#inspect-pinned-components) before proceeding. If a pin is unavailable or cannot be inspected with your permissions, stop and ask the author or administrator.

<Note>
  Registry approval is a publication gate, not proof that every component is safe for your project. Review hook commands, MCP sources, and required permissions yourself.
</Note>

## Preview the installation

From your chosen project directory:

```bash
observal agent pull "$AGENT" --harness claude-code --scope project --version "$VERSION" --strict --dry-run
```

The explicit version keeps this preview and the next pull on the release you inspected. `--strict` refuses incomplete pins or integrity problems rather than installing with a warning. The preview shows the planned file paths, warnings, and any setup commands. It does not install the agent or record it as installed. It contacts the registry to generate the configuration and may ask for component settings or credentials.

For this path, expect an agent profile under `.claude/agents/`. The exact filename is printed by the CLI. Review the rest of the plan too: an agent may add skills, register MCP servers, and configure session hooks.

<Warning>
  Generated session hooks can send prompts, tool calls, and tool output to your registry when they run. A pull may also add the registry's delegation MCP server. These are real configuration changes, not just a downloaded prompt. Read the preview and confirm your team's data-handling policy before installing.
</Warning>

If required credentials are missing, supply them interactively when prompted. Do not put secrets in `--env` or `--header` command arguments: other processes and shell history may expose them. Values used to generate an installation are sent to the registry in the installation request.

## Pull the agent

Apply the same installation:

```bash
observal agent pull "$AGENT" --harness claude-code --scope project --version "$VERSION" --strict
```

Review every warning. A failed setup command or incomplete skill installation is not a successful install just because some files were written.

A successful project-scope pull writes the agent profile and `observal.lock`, which records the agent version and component pins. Commit that lockfile if teammates should install the same version. A later plain pull keeps the locked version; upgrades are explicit. See [version pinning](/cli/pull#pinned-versions).

## Activate it in Claude Code

Use the name of the generated agent file **without `.md`**. For example, if the CLI wrote `.claude/agents/reviewer.md`, start a new session in the same project with:

```bash
claude --agent reviewer
```

`reviewer` is illustrative. Use the exact generated name, including any namespace qualification the CLI used to avoid a collision.

Pulling the profile does not select it in an already-running session. Start a new session explicitly rather than assuming every conversation in the project uses the installed agent.

## Check it works

Inspect the local inventory:

```bash
observal scan --inventory --harness claude-code
```

The generated agent should appear in the inventory. `--inventory` is the read-only scan mode; plain `scan` can also run legacy configuration and lockfile migrations. An inventory entry does not prove which agent a running session is using.

In the explicitly activated session, give the agent a small task appropriate to its purpose. For a reviewer agent, you could ask:

```text
Read README.md and summarize the project in three bullets.
Do not edit files or run shell commands.
```

Confirm that Claude Code starts with the intended agent and that the task completes. Instructions are not a security sandbox; continue to review tool permissions and approval requests.

You do not need a trace or an insights report to complete this guide. Those become useful after you have session evidence and the relevant capture/reporting setup.

## No agents yet?

If you are joining a team, ask for an approved agent and any required teamspace membership. Agents pending review do not appear in the approved list.

For your own evaluation registry, create a minimal agent with no third-party components:

<Tabs>
  <Tab title="macOS / Linux">

```bash
observal agent create \
  --name first-reviewer \
  --description 'A small agent for trying the registry workflow' \
  --prompt 'Read README.md and summarize the project in three bullets. Do not edit files or run shell commands.' \
  --harness claude-code
```

  </Tab>
  <Tab title="PowerShell">

```powershell
observal agent create `
  --name first-reviewer `
  --description 'A small agent for trying the registry workflow' `
  --prompt 'Read README.md and summarize the project in three bullets. Do not edit files or run shell commands.' `
  --harness claude-code
```

  </Tab>
</Tabs>

Record the returned agent ID and review information. Follow [Review and publish a submission](/guides/review-and-publish) with an eligible reviewer. Approval and publication can be separate steps. Then list agents again and copy its actual qualified name.

Some deployments allow authors to install their own unapproved versions through `security.allow_draft_install`. Do not assume that setting is enabled. If the install is refused while the submission is pending, complete review rather than bypassing the policy. The default first-success path here uses a published agent.

## If something fails

| Problem | What to check |
| --- | --- |
| `Server component pin disagrees with the install lock.` | Stop and investigate the [known response-consistency limitation](/guides/version-components#known-install-response-limitation). This check runs outside strict mode; removing `--strict` or passing `--no-strict` does not resolve it. Do not silently move the selected pin to make the command finish. |
| Strict install refuses incomplete pins | Ask the author for a fully pinned, published release. Do not drop strict mode to complete this first-install guide. |
| Agent not found or not approved | Check the qualified name, teamspace access, and publication state. Your own pending agent may still be blocked by policy. |
| Ambiguous name | Use the complete `namespace/slug` returned by the registry. |
| Harness declaration does not match your tool | In v1.16.0 this is a manual checkpoint, not an automatic refusal. Choose a compatible declared agent and inspect component support before installation. |
| Missing environment variables or headers | Retry interactively and enter the required values at the prompts. |
| MCP setup fails | Check that `claude` is installed and inspect the printed setup failure. Files may be partially written; do not delete unrelated configuration. |
| Claude Code says it is not logged in | Authenticate Claude Code separately. Registry login supplies no model credentials. |
| Agent is installed but not active | Start a new session with `claude --agent` and the exact generated local name. |

## Next

- [Share the same agent setup with a teammate](/use-cases/share-agent-configs).
- [Fork and customize an agent](/use-cases/fork-and-customize).
- [Search for resources for your next task](/cli/discover).
- After using it, [read agent insights](/cli/ops#agent-insights) or [debug using session evidence](/use-cases/debug-agent-failures).
