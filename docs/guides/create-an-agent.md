---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Create and publish an agent"
description: "Turn a working set of instructions and components into a reviewed registry release."
---

Create an agent when a setup is useful enough to share or maintain as a versioned resource. Start with clear instructions; add components only when the task needs them.

## Before you start

Sign in to your registry. You need permission to submit in the intended personal or teamspace namespace. Choose the harnesses you will support and verify that the components work in them.

## Create a small first submission

For a minimal example, run this command in a shell that supports backslash continuations:

```bash
observal agent create \
  --name first-reviewer \
  --description 'Summarizes a project before reviewing changes' \
  --prompt 'Read README.md and summarize the project in three bullets. Do not edit files or run shell commands.' \
  --harness claude-code
```

For the PowerShell variant, see the [empty-registry walkthrough](/getting-started/quickstart#no-agents-yet). The description and prompt are examples, not claims that the agent has been tested for your project.

Record its ID, qualified name, version, and review information. Creating a submission is not the same as publishing a release.

## Compose an agent in a project

For a PR-reviewable definition, use `observal agent init` to create `observal-agent.yaml`. Follow [component authoring and reuse](/guides/components) to choose/create reviewed resources. Find components in your registry and add them by their returned UUIDs:

```bash
observal agent init --dir ./my-agent
observal registry skill list --output json
observal agent add skill COMPONENT_UUID --version COMPONENT_VERSION --dir ./my-agent
observal agent build --dir ./my-agent --output json
```

Replace the UUID and version before running `add`. The init command is interactive unless you supply the complete flags. Review the YAML and build result. In v1.16.0, build validates declared harness names and component references, versions, and access/composition scope—not whether the components work in those harnesses. Inspect each pinned release and verify compatibility manually before publication. Build does not execute the agent or prove effectiveness.

```bash
observal agent publish --dir ./my-agent --output json
```

This submits the definition. Save its returned identifiers and follow [Review and publish](/guides/review-and-publish). Avoid running both the minimal create example and the YAML publish workflow with the same name unless you intentionally handle the existing submission.

## Test the published release

As a user with normal installation permissions, [inspect, preview, pull, and activate it](/getting-started/quickstart). Check the exact published version, component pins, and a small real task before telling teammates it is ready.

## Maintain it

Approved releases are not edited in place. Release a new agent version when instructions or component pins change. Existing project locks keep users on their selected version until they upgrade deliberately.

For exact authoring flags, version releases, and pin refresh behavior, see the [agent reference](/cli/agent#local-authoring-workflow). To adapt someone else's setup without changing the original, [fork it](/use-cases/fork-and-customize).
