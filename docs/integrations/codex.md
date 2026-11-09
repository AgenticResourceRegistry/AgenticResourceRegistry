---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Use a registry agent in Codex"
description: "Install a Codex custom subagent, invoke it explicitly, and verify the selected role."
---

Codex loads registry agents as **custom subagent definitions**. Pulling one does not replace the main Codex session's instructions or select a named CLI configuration profile.

## Before you start

Install Codex and authenticate it separately from the registry. Check `codex --version` and `codex login status`. This guide follows the registry's [v1.16.0 documentation scope](/reference/documentation-version); the local CLI help checked for this batch was Codex `0.163.0`.

Choose an accessible approved agent release that supports `codex`, then [inspect its exact instructions and component pins](/guides/inspect-an-agent). Use a disposable project with a `README.md` for an unfamiliar setup.

## Preview and pull

Replace the qualified name and version:

```bash
observal agent pull YOUR_NAMESPACE/YOUR_AGENT --harness codex --scope project --version AGENT_VERSION --strict --dry-run
observal agent pull YOUR_NAMESPACE/YOUR_AGENT --harness codex --scope project --version AGENT_VERSION --strict
```

Review generated paths, commands, warnings, and requested credentials before applying the preview. The agent definition belongs in `.codex/agents/LOCAL_NAME.toml`. Read its `name`, `description`, and `developer_instructions` fields. Use the actual `name` value when asking Codex to run the subagent.

The parent session's MCP configuration is written under `.codex/config.toml`; registry skills use `.agents/skills/`. Subagents inherit the parent's provider, permissions, and MCP tools. Putting an MCP definition or sandbox setting inside a custom agent file does not create an independent permission boundary.

User-scope installs use `~/.codex/agents/` and `~/.codex/config.toml`. If you set a custom `CODEX_HOME`, verify the paths Codex actually loads rather than assuming default-home output is active.

## Invoke the custom subagent

Start a new Codex session from the project:

```bash
codex --sandbox read-only
```

If Codex asks whether to trust the project, inspect its configuration first. Do not bypass trust prompts just to make a first test run.

Give the parent an explicit task, replacing `LOCAL_NAME` with the definition's `name`:

```text
Use the LOCAL_NAME custom subagent to read README.md and summarize it in three bullets.
Wait for its result. Do not edit files or run state-changing commands.
```

Inspect the subagent activity and use `/subagents` to follow the child thread. Confirm that Codex selected the requested custom role, not the default worker or explorer.

<Note>
  `codex --profile` selects a Codex configuration profile. It is not the activation command for a pulled `.codex/agents/*.toml` file. Likewise, `codex agents` lists agent sessions; it is not proof that a custom role has run.
</Note>

## Verify the result

The child should complete the requested task, and the parent should return its result. Verify the selected role and its instructions using the child thread, not only the main answer's wording.

```bash
observal scan --inventory --harness codex
```

Inventory is a local configuration check, not proof of a child execution or session attribution. A plain parent session in the same folder is not automatically attributed to the pulled agent.

The parent sandbox constrains local execution; it does not independently sandbox remote MCP servers. Review their permissions and approve only the actions the task needs.

## Session delivery

Codex session delivery uses the user-level hooks installed by `observal doctor patch --harness codex`, not an assumption that every pull writes a telemetry hooks file. Preview that optional setup first and confirm your team's data-handling policy. Component-insight attribution is not currently supported for Codex.

## If it does not run

- **Custom role not found:** check the TOML `name`, project directory, trust status, and supported custom-agent format on your installed Codex version.
- **Wrong profile selected:** do not use `--profile` as a substitute for custom-subagent invocation.
- **Permission request denied:** inspect the requested action. Instructions alone do not grant permission, and non-interactive runs cannot obtain a new approval.
- **Model authentication fails:** authenticate Codex separately; registry login supplies no provider credentials.

The generated format and invocation workflow were checked against the registry implementation and [OpenAI's custom-subagent documentation](https://developers.openai.com/codex/agent-configuration/subagents). A successful model-authenticated Codex child task has not been completed in this docs batch; CLI configuration/context checks are not a substitute for it.

## v1.16.0 attribution boundary

This release predates later Codex rollout-identity/recovery attribution fixes. A recovered session or an agent name in a prompt is not independent evidence of the original custom-role identity. Keep native activation/recovery checks bounded and do not claim the newer recovery provenance contract for this tag.
