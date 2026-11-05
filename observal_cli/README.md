<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Agentic Resource Registry CLI

Command-line interface for the Agentic Resource Registry platform. Authenticate with a server, manage registry components, configure harnesses, and collect telemetry.

## Install

The CLI is packaged as a Python project. From the repo root:

```bash
uv pip install -e .
```

This installs three entry points:

| Command                | Purpose                                  |
| ---------------------- | ---------------------------------------- |
| `observal`             | Main CLI                                 |
| `observal-sandbox-run` | Sandbox execution runner                 |
| `observal-sandbox-mcp` | MCP interface for configured sandboxes   |

## Quick Start

```bash
observal auth login                  # connect to your Agentic Resource Registry server
observal scan                        # discover what's installed across your harnesses (read-only)
observal doctor patch --all-harnesses        # install session telemetry hooks
observal agent pull my-agent --harness cursor  # fetch agent config for Cursor
observal doctor                      # check harness compatibility
```

## Commands

### Authentication

```
observal auth login       # connect to server (initializes admin on first run)
observal auth logout      # clear saved credentials
observal auth whoami      # show current user
observal auth status      # check connectivity and buffer status
```

### Agent Workflow

```
observal agent init              # scaffold observal-agent.yaml
observal agent add mcp <id>      # add a component to the agent definition
observal agent build             # validate the definition
observal agent publish           # push to the server
observal agent list              # list active agents
observal agent show <id>         # show agent details
observal agent install <id> --harness <harness>  # get harness config snippet
```

### Component Registry

Each component type (mcp, skill, hook, prompt, sandbox) shares the same subcommand pattern:

```
observal registry <type> submit      # submit for review
observal registry <type> list        # list approved items
observal registry <type> show <id>   # show details
observal registry <type> install <id> --harness <harness>  # get harness config
```

Hooks have an extra `sync` subcommand. Prompts have an extra `render` subcommand for variable substitution.

### Operations

```
observal ops telemetry status --output json       # check telemetry flow
observal ops top --type agent --output json        # most-downloaded agents
observal ops traces --output json                   # current session summaries
observal ops traces --span --output json            # current session details
observal ops insights list <agent> --output json    # agent insight reports
```

### Administration

```
observal admin diagnostics --output json             # server health
observal admin users --output json                   # user management
observal admin security-events --output json         # security events
observal admin audit-log --output json               # compliance audit events
observal review list --output json                   # pending submissions
```

### Utilities

```
observal agent pull <agent> --harness <harness>             # write agent config to harness files
observal scan [--harness <harness>]                          # discover what's installed (read-only)
observal reconcile --dry-run --output json                    # preview recoverable sessions
observal doctor patch --all-harnesses --output json         # install hooks for every harness
observal doctor patch --harness <harness> --output json      # install hooks for a specific harness
observal doctor --output json                        # diagnose harness/Agentic Resource Registry issues
observal doctor support bundle --output json         # create a redacted support archive
observal config show                                 # show current config
```

## Supported harnesses

| harness / Tool                  | Support Level                |
| --------------------------- | ---------------------------- |
| Claude Code                 | Fully supported              |
| Kiro CLI                    | Supported (next most tested) |
| Cursor, VS Code             | Untested                     |

The `--harness` flag controls which config format is generated. Each harness has its own config paths and JSON structure.

## Config Files

All CLI state lives in `~/.observal/`:

| File                     | Contents                                  |
| ------------------------ | ----------------------------------------- |
| `config.json`            | Server URL, tokens, user ID               |
| `aliases.json`           | User-defined name-to-UUID aliases         |
| `last_results.json`      | Cached list results for numeric shorthand |

## Telemetry

Hook scripts in `observal_cli/hooks/` locate local harness session transcripts and deliver new JSONL records to the session ingest endpoint. `observal reconcile` recovers records that were not delivered during the original hook run. MCP commands and remote URLs are left unchanged.

## Directory Layout

```
observal_cli/
├── main.py                  # Root app, command registration
├── config.py                # Config file I/O
├── client.py                # HTTP client with auth and token refresh
├── constants.py             # Valid harnesses, categories, component types
├── render.py                # Rich output formatting
├── analyzer.py              # Repo analysis for MCP submission
├── settings_reconciler.py   # Non-destructive Claude Code settings merge
├── cmd_auth.py              # Auth commands
├── cmd_agent.py             # Agent commands
├── cmd_mcp.py               # MCP commands
├── cmd_skill.py             # Skill commands
├── cmd_hook.py              # Hook commands
├── cmd_prompt.py            # Prompt commands
├── cmd_sandbox.py           # Sandbox commands
├── cmd_pull.py              # Pull command
├── cmd_scan.py              # Scan command
├── cmd_doctor.py            # Doctor command
├── cmd_ops.py               # Operations commands
├── cmd_profile.py           # Profile swapping
├── sandbox_runner.py        # observal-sandbox-run entrypoint
└── hooks/                   # Telemetry hook scripts
```
