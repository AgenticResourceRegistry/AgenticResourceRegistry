---
# SPDX-FileCopyrightText: 2026 Faatih <22oo1cso41faatih@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Cursor"
---

Cursor is a first-class Agentic Resource Registry harness integration. Agentic Resource Registry can install Cursor agents,
configure MCP servers, and collect Cursor session telemetry.

---

## Overview

Cursor agent profiles are Markdown files. Project agents live in `.cursor/agents/`.
User agents live in `~/.cursor/agents/`.

Cursor supports project and user installation scopes. MCP servers, hooks,
and agent profiles are managed under the `.cursor` configuration directory.

---

## Supported capabilities

| Capability      | Support                                                     |
| --------------- | ----------------------------------------------------------- |
| Agent profiles  | Project and user scope                                      |
| Hook bridge     | Supported                                                   |
| MCP servers     | `.cursor/mcp.json` and `~/.cursor/mcp.json`                 |
| Registry skills | Not supported by the Cursor adapter capability contract |
| Session parsing | Built-in Cursor session parser                              |
| Default scope   | Project                                                     |

---

## Setup

### 1. Install the CLI

Follow [Install the CLI](/getting-started/installation) and the [documentation version guidance](/reference/documentation-version). Authenticate Cursor separately with an account that can use its models; registry login does not provide those credentials.

### 2. Connect to your registry

Replace the example URL with your registry's URL:

```bash
observal auth login --server https://registry.example.com --no-setup
```

The CLI saves credentials in `~/.observal/config.json`. `--no-setup` skips post-login harness setup. Without it, login can install bundled skills and session instrumentation in detected coding tools. See [Join your team's registry](/getting-started/onboarding) for sign-in and setup details.

### 3. Inspect, preview, and pull an exact release

Choose a qualified `namespace/slug` and an approved version declaring Cursor support. [Inspect that exact agent release and its pinned components](/guides/inspect-an-agent) before installing. The registry's Cursor adapter does not support skill installation; check that the agent does not require an unsupported capability.

From the target project, replace the resource and version placeholders:

```bash
observal agent pull YOUR_NAMESPACE/YOUR_AGENT --harness cursor --scope project --version AGENT_VERSION --strict --dry-run
```

Read every generated path, setup action, and warning. The preview can include `.cursor/hooks.json`, MCP configuration, and commands that report session content to the registry. Missing component credentials should be entered interactively, not placed in shell arguments. Skipping login setup does not prevent a pull from installing its own hooks.

If the plan is appropriate, apply the same version and scope:

```bash
observal agent pull YOUR_NAMESPACE/YOUR_AGENT --harness cursor --scope project --version AGENT_VERSION --strict
```

The project agent profile is written under `.cursor/agents/`; use the exact generated name for activation. A successful project install records the selected release in `observal.lock`. Strict mode checks pins and integrity, not whether the submitted code is safe.

For a user-scope install, use `--scope user` in both the preview and pull after the same inspection. Review the planned home paths and permissions first; user profiles use `~/.cursor/agents/`.

### 4. Invoke the installed subagent

Cursor uses these profiles as **custom subagents**, not as an automatic replacement for the parent session. Restart Agent in the same workspace after pulling, then request the exact generated local name:

```text
/LOCAL_NAME read README.md and summarize it in three bullets.
Do not edit files or run shell commands. Wait for the subagent's result.
```

Replace `LOCAL_NAME` with the installed profile's name. Natural-language selection such as “Use the LOCAL_NAME subagent” is also documented by Cursor. Inspect the task card or child activity to confirm the requested profile ran; a parent answer alone is not sufficient.

For the CLI, start an interactive planning session from the workspace:

```bash
cursor-agent --mode plan
```

Then give it the explicit subagent request above. The checked CLI help (`2026.10.22-27dcf5f`) has no `--agent` selection flag; do not borrow `claude --agent` syntax. Keep normal trust and permission checks enabled. Parent plan mode and a no-edit prompt do not independently constrain a remote MCP server.

### 5. Verify configuration and the task

```bash
observal scan --inventory --harness cursor
```

Confirm the installed profile and review the generated files, then verify the subagent's task completes under the intended permissions. Inventory confirms local configuration, not native selection or delivered session evidence.

The format and invocation steps follow [Cursor's custom-subagent documentation](https://cursor.com/docs/subagents). No model-authenticated Cursor child task was completed in this docs batch; validate the profile on the actual tool version your team runs.

---

## Config paths

| Purpose       | Project scope                    | User scope                         |
| ------------- | -------------------------------- | ---------------------------------- |
| Agent profile | `.cursor/agents/{name}.md`       | `~/.cursor/agents/{name}.md`       |
| MCP config    | `.cursor/mcp.json`               | `~/.cursor/mcp.json`               |
| Hook config   | `.cursor/hooks.json`             | `~/.cursor/hooks.json`             |

Cursor MCP configs use the `mcpServers` key.

---

## Hook spec

### Event map

| Agentic Resource Registry event     | Cursor event         |
| ------------------ | -------------------- |
| `PreToolUse`       | `preToolUse`         |
| `PostToolUse`      | `postToolUse`        |
| `Stop`             | `sessionEnd`         |
| `SessionStart`     | `sessionStart`       |
| `UserPromptSubmit` | `beforeSubmitPrompt` |
| `SubagentStop`     | `subagentStop`       |

---

## Session delivery and parsing

Cursor keeps its built-in `cursor` parser. Its adapter resolves the transcript path supplied by Cursor, with project-directory discovery as a fallback, and discovers separately stored subagent transcripts.

Cursor hook timeouts require a split delivery path: the hook synchronously writes complete transcript records and Stop-event token usage into the durable SQLite outbox, then a detached shared worker performs network delivery. The local byte/line cursor is not advanced by the hook or worker launch; it advances only after the server acknowledges a contiguous checkpoint. A delayed finalizer captures records written after Cursor's Stop event, restores stale local state from the server checkpoint, and performs the final hash audit. Audit mismatches rewind and replay the affected range.

---

## Caveats

**Default scope is project.** Project profiles use `.cursor/agents/`; user profiles use `~/.cursor/agents/`. Keep the same explicit release and scope between inspection, preview, and pull.

**Configuration lives under `.cursor`.** Agent profiles, MCP configuration,
and hooks are stored in the `.cursor` directory for project
installs and under `~/.cursor` for user installs.

**Registry skill installation is not supported by this adapter.** Cursor itself documents native skills, but the registry's Cursor adapter does not advertise its `skills` capability. Paths in shared metadata do not establish a usable registry install. Choose an agent compatible with the adapter's actual capabilities.

**Runtime verification remains version-specific.** This page now describes explicit native subagent invocation, but it does not certify a completed model-authenticated task on every Cursor version. Confirm the child activity and result, not just the generated files.

**Auto model sentinel configuration is not available.**
