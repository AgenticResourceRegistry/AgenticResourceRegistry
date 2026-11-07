---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Supported coding tools"
description: "Choose a harness and understand what the registry can install for it."
---

Choose the coding tool you actually use, then follow its setup and activation instructions. Agentic Resource Registry calls these tools **harnesses**.

All ten registered harnesses have registry integration support. Two checks still matter: the agent version must support your chosen harness, and that harness must support the components the agent needs. Passing one check does not replace the other.

## Capability overview

This table follows the shared harness registry in the current source tree. It describes registered capabilities, not identical behavior or end-to-end certification on every tool version. Hook events, file scopes, MCP activation, and component-insight coverage have additional limits.

| Coding tool | `--harness` value | MCP servers | Skills | Hooks | Native prompt capability |
| --- | --- | --- | --- | --- | --- |
| [Claude Code](/integrations/claude-code) | `claude-code` | Yes | Yes | Yes | No |
| [Kiro](/integrations/kiro) | `kiro` | Yes | Yes | Yes | No |
| [Cursor](/integrations/cursor) | `cursor` | Yes | No | Yes | No |
| Codex | `codex` | Yes | Yes | Yes | No |
| [Copilot](/integrations/copilot) | `copilot` | Yes | Yes | Yes | Yes |
| Copilot CLI | `copilot-cli` | Yes | Yes | Yes | Yes |
| [OpenCode](/integrations/opencode) | `opencode` | Yes | Yes | Yes | No |
| Antigravity | `antigravity` | Yes | Yes | Yes | No |
| [Goose](/integrations/goose) | `goose` | Yes | Yes | Yes | No |
| [Pi](/integrations/pi) | `pi` | Yes | Yes | Yes | No |

A missing native prompt capability does not mean the tool cannot receive agent instructions. It means you must not assume standalone registry prompts install as a native prompt feature. Sandbox behavior is separate from this capability table.

### Setup-guide coverage

Registry support and completed documentation are separate. The [Claude Code first-agent walkthrough](/getting-started/quickstart) provides a concrete install/activation path. Existing integration pages describe configuration and limitations, but are not a promise that every current tool version has been verified end to end.

**Incomplete activation guides:** Codex, Copilot CLI, and Antigravity do not yet have dedicated install → activate → verify walkthroughs here. The Cursor page also needs a version-specific activation walkthrough. Do not substitute a generic pull command and assume the installed profile is active. These are documentation follow-ups, not missing registry adapters.

## Choose a scope

**Project scope** keeps the install associated with a project. **User scope** makes the profile or configuration available at the account level where the harness supports it.

Do not assume one default everywhere. Claude Code defaults to project scope; Kiro and Pi default to user scope. Some tools only support one scope, and component constraints can narrow the valid choices further. For example, Goose MCP configuration is user-level.

Pass an explicit scope when following a guide that supports one:

```bash
observal agent pull YOUR_NAMESPACE/YOUR_AGENT --harness claude-code --scope project --dry-run
```

Replace the resource name first. Read the preview before applying the install.

## Check activation and evidence separately

- Pull shows what was written and any setup commands or warnings.
- `scan --inventory` provides a read-only view of local configuration. Discovery depth varies by harness; absence from a scan is not always proof that a component is absent.
- Your coding tool determines how to select and activate the installed agent.
- Session delivery and component insights have their own version and verification requirements.

Do not read “unsupported evidence” as “zero usage,” or infer that every hook event works just because hooks appear in the table. Use the individual integration guide for limitations.

## Next

For a concrete example path, [pull and activate an agent in Claude Code](/getting-started/quickstart). For another tool, follow its integration guide rather than substituting a harness name into Claude Code's activation command.
