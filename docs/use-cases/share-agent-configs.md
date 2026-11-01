---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Share agent configs across harnesses"
---

Your team has a reviewer agent that works great in Claude Code. Now someone wants to use it in Kiro. Then Cursor. Then they want to tweak the skills. Copy-pasting config snippets across tools doesn't scale.

Agentic Resource Registry's registry gives you one agent definition that installs cleanly into every supported harness.

## The shape of an agent

Every agent is a YAML file that bundles:

* MCP servers it needs
* Skills to load
* Hooks to wire into the session lifecycle
* Prompts (with variables)
* Sandboxes for code execution

When someone runs `observal agent pull <agent>`, Agentic Resource Registry templates that YAML into the right files for their harness: `~/.claude/agents/*.json`, `.kiro/agents/*.json`, `.cursor/mcp.json`, and so on.

## Publish an agent

### Option A - the interactive wizard

```bash
observal agent create
```

Step-by-step prompts: name, description, which MCP servers, which skills, which hooks. Results in a registry entry you can share by ID.

### Option B - the YAML workflow (recommended for teams)

```bash
observal agent init                  # scaffold observal-agent.yaml
observal agent add mcp github-mcp    # add components
observal agent add skill code-review-skill
observal agent add hook pretooluse-logger

observal agent build                 # validate (dry-run)
observal agent publish               # submit to registry
```

The YAML workflow is PR-reviewable. The file lives in your repo; changes flow through your normal review process.

## Install an agent into any harness

Browse what exists:

```bash
observal agent list
observal agent list --search review
observal agent show <agent-id>
```

Install with one command, pick the harness:

```bash
observal agent pull <agent-id> --harness claude-code
observal agent pull <agent-id> --harness kiro
observal agent pull <agent-id> --harness cursor
observal agent pull <agent-id> --harness vscode
observal agent pull <agent-id> --harness codex
```

The CLI prompts for any environment variables the MCP servers declare as required (GitHub tokens, API keys). These are stored in your harness config, not uploaded to Agentic Resource Registry.

### Control what gets installed

```bash
# Preview without writing anything
observal agent pull <agent-id> --harness claude-code --dry-run

# Install into a specific directory
observal agent pull <agent-id> --harness claude-code --dir ./my-project

# Claude Code only: scope (project-local vs user-global)
observal agent pull <agent-id> --harness claude-code --scope project
observal agent pull <agent-id> --harness claude-code --scope user

# Claude Code only: sub-agent model
observal agent pull <agent-id> --harness claude-code --model sonnet

# Claude Code only: tool allowlist
observal agent pull <agent-id> --harness claude-code --tools Read,Write,Bash
```

## What portability actually means

The harness feature matrix (defined in `packages/observal-shared/observal_shared/harness_registry.py`) controls what each harness supports. If an agent uses skills and the target harness doesn't have skills, the installer:

* Installs the compatible parts cleanly
* Warns about the unsupported parts
* Exits non-zero if the agent *requires* something the harness cannot provide

Useful when onboarding a new machine or swapping between "work setup" and "personal setup."

## Next

→ [Run a team-wide agent registry](/use-cases/team-registry): once publishing is routine, you need governance.
