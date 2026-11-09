---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Create and reuse components"
description: "Choose a reusable resource, publish a reviewed version, and pin it in an agent."
---

A component is a reusable part of an agent's setup. Share it independently when several agents need the same tools, instructions, lifecycle behavior, template, or execution environment.

| If you need... | Component | Authoring guide |
| --- | --- | --- |
| Tools exposed by a local process or remote endpoint | MCP server | [Publish an MCP configuration](/registry-mcp-helper) |
| Instructions and optional supporting files | Skill | [Publish a skill](/registry-skill-helper) |
| A handler around a supported lifecycle event | Hook | [Publish a hook](/registry-hook-helper) |
| A parameterized text template | Prompt | [Publish and render a prompt](/guides/publish-a-prompt) |
| A declared local execution runtime/artifact | Sandbox | [Publish a sandbox](/registry-sandbox-helper) |

An agent bundles components; another agent is not a component type. You can use a supported standalone component operation when you do not need an entire agent, but not every type has an install command.

## Before you publish

Sign in to the intended registry and confirm your identity. Submit resources you own or represent. Check the source, license, declared harnesses, prerequisites, and any secrets or permissions the resource needs.

Use returned UUIDs or `namespace/slug` identities. Examples use illustrative names and POSIX shell syntax; replace placeholders before running. Do not add a literal example UUID or missing executable to a production agent.

## Find an existing resource first

```bash
observal registry skill list --search review --output json
observal registry skill show YOUR_NAMESPACE/YOUR_SKILL --output json
```

Choose the appropriate type for other resources. Inspect the exact release, not only its description. If it fits your task, reuse or fork it instead of submitting an indistinguishable duplicate.

## Save, submit, review, publish

The type-specific guide shows how to create a draft. Saving it is not submission. `registry TYPE submit --submit ID` submits the chosen candidate; skill folders additionally require an exact draft version UUID and observed revision.

Follow [Review and publish](/guides/review-and-publish). A reviewer must inspect the actual instructions/configuration and files. Approval may still need a separate publication step. Some author-owned resources are accessible before approval, but ownership is not a reason to present them as reviewed releases.

## Add an exact component version to an agent

Find the approved component's UUID and version, then use them in a local definition:

```bash
observal agent init --dir ./my-agent
observal agent add skill COMPONENT_UUID --version COMPONENT_VERSION --dir ./my-agent
observal agent build --dir ./my-agent --output json
```

The init command is interactive unless complete metadata flags are supplied. For other component types, replace `skill` with `mcp`, `hook`, `prompt`, or `sandbox`. In v1.16.0, build validates declared harness names and component references, versions, and access/composition scope. It does not check that each component supports every declared harness. Inspect the exact component releases and verify native compatibility manually before publishing; a valid build is not execution or effectiveness proof.

Publish/review the agent definition, then [inspect and pull its exact release](/getting-started/quickstart). Compare all planned resources with the component versions you inspected. A compatibility warning or failed setup is not success just because a profile was written.


Updating an agent's component pins does not fix an older release's inconsistent install response; see the [known pin-response limitation](/guides/version-components#known-install-response-limitation).

## Know what changes your machine

| Operation | What it means |
| --- | --- |
| MCP install without `--apply` | Generates configuration; not automatically an applied/tracked harness install. |
| Skill install | Can write a skill/folder; use its no-write/check-upgrade modes for the applicable preview. |
| Hook install | Can write handlers and merge settings; `--raw` is the no-write response path. |
| Prompt render | Substitutes text using supplied variables; does not ask a model to execute the prompt. |
| Sandbox | No standalone registry install; an agent install exposes it through the sandbox MCP runtime. |

Do not assume the agent pull preview flags exist on every standalone component command. Use that command's current help. Registry/harness sign-in, installed state, activation, observed use, and verified effectiveness are distinct.

## Maintain a shared component

[Publish a new component version](/guides/version-components) instead of silently changing an approved release. Existing agent releases retain their pins; an author must release a new agent version to adopt a new component version. Project locks then keep consumers on the chosen agent release until an explicit upgrade.

For installation/evidence questions, use [integration support](/integrations/index) and [insights interpretation](/guides/interpret-insights), not download counts or a successful submission as proof of usage.
