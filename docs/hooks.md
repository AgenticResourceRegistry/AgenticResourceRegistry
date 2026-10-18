---
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Hooks"
---

Hooks are event-driven commands that fire at specific points in the AI agent lifecycle. They let you guard, log, or extend agent behavior without modifying the agent itself.

## Concepts

A hook has four core properties:

| Property | Description |
|----------|-------------|
| **Event** | When it fires: `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, `UserPromptSubmit`, `Notification`, `SubagentStop` |
| **Handler Type** | `command` (runs a shell command) or `http` (POSTs to a URL) |
| **Execution Mode** | `blocking` (can veto), `sync` (waits for result), `async` (fire-and-forget) |
| **Handler Config** | The command/URL + timeout |

Hooks can be:
- **Inline commands**: `ruff check`, `eslint --fix`
- **Script-based**: a shell/python script stored in the registry and written to disk on install
- **Package-based**: references an installed package (`python -m my_org.hooks.guard`)

## CLI Commands

### Submit a hook

```bash
# Interactive (prompts for fields)
observal registry hook submit

# From JSON file
observal registry hook submit --from-file hook.json

# With a script file (content stored in registry)
observal registry hook submit --script ./my-hook.sh

# As a draft (not submitted for review)
observal registry hook submit --draft

# Submit an existing draft for review
observal registry hook submit --submit <hook-id>

# With git source tracking
observal registry hook submit --source-url https://github.com/org/hooks --source-ref main

# With install prerequisites
observal registry hook submit --requires "pip install jq"
```

### List hooks

```bash
# All approved hooks
observal registry hook list

# Filter by event
observal registry hook list --event PreToolUse

# Search
observal registry hook list --search "lint"

# JSON output
observal registry hook list --output json
```

### Show hook details

```bash
observal registry hook show <name-or-id>
observal registry hook show <name-or-id> --output json
```

### Install a hook

```bash
# Install for Claude Code (writes to .claude/settings.json + script file)
observal registry hook install <name> --harness claude-code

# Install for Cursor
observal registry hook install <name> --harness cursor

# Install for Kiro
observal registry hook install <name> --harness kiro

# Install into a specific directory
observal registry hook install <name> --harness claude-code --dir /path/to/project

# Raw server response, no file writes
observal registry hook install <name> --harness claude-code --raw

# Install files and return a JSON operation result
observal registry hook install <name> --harness claude-code --output json
```

Installation validates paths before writing, refuses to overwrite malformed existing JSON, writes each file atomically, and deduplicates existing event entries.

### Edit a hook

```bash
observal registry hook edit <name-or-id> --name "new-name" --output json
observal registry hook edit <name-or-id> --description "updated" --output json
observal registry hook edit <name-or-id> --from-file updates.json --output json
```


## Examples

### Example 1: Inline command hook (block dangerous shell commands)

```json
{
  "name": "block-rm-rf",
  "version": "1.0.0",
  "description": "Blocks rm -rf commands",
  "owner": "your-name",
  "event": "PreToolUse",
  "handler_type": "command",
  "handler_config": {"command": "echo $INPUT | grep -q 'rm -rf' && exit 2 || exit 0", "timeout": 5},
  "execution_mode": "blocking"
}
```

```bash
observal registry hook submit --from-file block-rm-rf.json
observal registry hook install block-rm-rf --harness claude-code
```

### Example 2: Script-based hook (protect sensitive files)

Create the script:

```bash
#!/bin/bash
# protect-files.sh - blocks edits to .env and secret files
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
if [[ "$FILE_PATH" == *.env* ]] || [[ "$FILE_PATH" == *secret* ]]; then
    echo "BLOCKED: $FILE_PATH is a sensitive file"
    exit 2
fi
exit 0
```

Submit with the script:

```bash
observal registry hook submit --script ./protect-files.sh
# Prompts: name, event (PreToolUse), timeout (5), execution_mode (blocking)
```

Install (writes both the config AND the script file:

```bash
observal registry hook install protect-files --harness claude-code
# Creates: .claude/settings.json (hook config pointing to script)
# Creates: .claude/hooks/protect-files.sh (the script, chmod +x)
```

### Example 3: Audit logging hook (async, doesn't block)

```bash
#!/bin/bash
# log-tools.sh - logs every tool call
INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // "unknown"')
TARGET=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.command // "n/a"')
echo "$(date +%H:%M:%S) [$TOOL] $TARGET" >> /tmp/tool-audit.txt
exit 0
```

```bash
observal registry hook submit --script ./log-tools.sh
# Event: PreToolUse, Execution mode: async, Timeout: 3
```

### Example 4: Hook as an agent component

When a hook is added as an agent component, it auto-installs when the agent is pulled:

```bash
# Create agent with hook component
cat > agent.json << 'EOF'
{
  "name": "safe-coder",
  "version": "1.0.0",
  "owner": "your-name",
  "model_name": "claude-sonnet-4-20250514",
  "description": "Coding agent with file protection",
  "prompt": "You are a careful coding assistant.",
  "components": [
    {"component_type": "hook", "component_id": "<hook-id>"}
  ]
}
EOF
observal agent create --from-file agent.json

# Pull the agent - hook is auto-installed
observal agent pull safe-coder --harness claude-code
# Writes: ~/.claude/agents/safe-coder.md (with hook in frontmatter)
# Writes: .claude/hooks/protect-files.sh (script file)
```

## Multi-harness Support

The registry maps canonical event names to each harness's format:

| Observal Event | Claude Code | Cursor | Kiro | Codex CLI |
|----------------|-------------|--------|------|-----------|
| `PreToolUse` | `PreToolUse` | `preToolUse` | `preToolUse` | `pre_tool_use` |
| `PostToolUse` | `PostToolUse` | `postToolUse` | `postToolUse` | `post_tool_use` |
| `Stop` | `Stop` | `sessionEnd` | `stop` | `session_stop` |
| `SessionStart` | `SessionStart` | `sessionStart` | `agentSpawn` | - |
| `UserPromptSubmit` | `UserPromptSubmit` | `beforeSubmitPrompt` | `userPromptSubmit` | `user_prompt_submit` |

Install generates the correct format automatically:

```bash
# Same hook, different harnesses
observal registry hook install my-hook --harness claude-code  # → .claude/settings.json
observal registry hook install my-hook --harness cursor       # → .cursor/hooks.json
observal registry hook install my-hook --harness kiro         # → ~/.kiro/agents/my-hook.json
```

## Timeout Enforcement

Hooks have maximum timeout limits enforced at submit time:

| Execution Mode | Max Timeout | Why |
|----------------|-------------|-----|
| `blocking` | 30s | Freezes the harness until completion |
| `sync` | 10s | harness waits for return value |
| `async` | 60s | Prevents zombie processes |

Submitting a hook that exceeds these limits returns HTTP 422:

```
Timeout 60s exceeds maximum 30s for blocking hooks.
Either reduce the timeout or change execution_mode (async max: 60s).
```

## Hook Script Input Format

Hook scripts receive JSON on stdin describing the event:

```json
{
  "tool_name": "Write",
  "tool_input": {
    "file_path": "/path/to/file.ts",
    "content": "..."
  },
  "session_id": "abc123"
}
```

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Allow (proceed normally) |
| `2` | Block (veto the tool call - blocking mode only) |
| Other | Error (logged, tool call proceeds) |

Stdout from the script is passed back to the agent as context.
