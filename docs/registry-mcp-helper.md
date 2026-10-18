---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "MCP helper"
---

Use MCP components when an agent needs tools from a local process or remote MCP endpoint.

## What to fill in

| Field | What it means | Example |
|-------|---------------|---------|
| Name | Registry slug for this MCP server | `filesystem-tools` |
| Category | Registry category for browsing | `file-systems` |
| Command | Local executable for stdio MCP servers | `npx` or `uvx` |
| Args | Argument array passed to the command | `["-y", "@modelcontextprotocol/server-filesystem", "/repo"]` |
| URL | Remote SSE or streamable HTTP endpoint | `https://mcp.example.com/sse` |
| Environment variables | Secrets users fill in at install time | `GITHUB_PERSONAL_ACCESS_TOKEN` |
| Setup instructions | Local prep required before the server works | `Run docker build -t acme/mcp:latest .` |

Use either command plus args for stdio, or URL for remote MCP. Do not put secret values in the registry. Use environment variable names and let installers provide values.

## Examples

### Filesystem server

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/dev/project"]
    }
  }
}
```

### Git server

```json
{
  "mcpServers": {
    "git": {
      "command": "uvx",
      "args": ["mcp-server-git", "--repository", "/home/dev/project"]
    }
  }
}
```

### Postgres server

```json
{
  "mcpServers": {
    "postgres": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/mydb"]
    }
  }
}
```

## CLI example

Paste a configuration like the examples above into the submit command:

```bash
observal registry mcp submit
```

## Sources

- [Model Context Protocol reference servers](https://github.com/modelcontextprotocol/servers)
