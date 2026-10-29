---
# SPDX-FileCopyrightText: 2026 SK Akram <skcodewizard786@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Create agents with the Agentic Resource Registry skill"
---

We recommend creating agents from your coding harness using the bundled Agentic Resource Registry skill. It gives your coding assistant instructions for using the Agentic Resource Registry CLI to find registry components and create agents from your request.

## Set up your harness

Install the Agentic Resource Registry CLI following the Installation guide in the wiki, then sign in to your Agentic Resource Registry server:

```bash
observal auth login
```

The normal CLI login setup installs the bundled Agentic Resource Registry skills for detected, supported harnesses. If you skip setup, complete it before trying the skill. Restart your harness after installation so it can discover the skills.

## Ask for an agent

In your harness, try:

> Use the Agentic Resource Registry skill to create an agent that uses the GitHub MCP.

Describe the agent's purpose, instructions, and any components or publication requirements you want. Your assistant can use the CLI to inspect available components and submit the agent. Review its proposed definition and the resulting agent in the registry; the server's permissions and review requirements still apply.

## Use the manual Builder

The web Agent Builder remains available for creating and editing agents manually. The recommendation does not block either flow. You can dismiss the banner; that preference is saved in this browser and persists across visits.
