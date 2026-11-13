---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Meghana Inti <meghanainti149@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Publish an MCP configuration"
description: "Register a server you maintain, review its configuration, and reuse a published version."
---

Publish an MCP component when agents need tools from a server you own or represent. The registry distributes its configuration; it does not automatically deploy, authenticate, or run the server for every developer.

## Before you start

Have a working server/configuration, the intended transport and source/license, and a list of non-secret setup and install-time inputs. Test the actual server separately before claiming its tools work. Examples below are POSIX shell commands.

## Prepare configuration without secrets

For an owned stdio server, an illustrative `mcp.json` is:

```json
{
  "mcpServers": {
    "repo-tools": {
      "command": "YOUR_OWNED_MCP_EXECUTABLE",
      "args": ["YOUR_NON_SECRET_ARGUMENT"]
    }
  }
}
```

Replace the executable/arguments with your real reviewed configuration. For a remote server, use its correct URL/transport and input declarations. Do not put actual tokens, passwords, or private connection strings in a submitted config.

The CLI detects some environment/header placeholders and dependency names. That is best-effort parsing, not proof every required input was found. Inspect the returned input declarations and setup instructions yourself.

## Save and inspect a draft

```bash
observal registry mcp submit --name repo-tools --category developer-tools --draft --yes --output json < mcp.json
```

`mcp submit` reads JSON from standard input; it does **not** have a `--from-file` option. `--yes` accepts JSON-derived metadata defaults, so inspect the result rather than assuming you completed every metadata field. This flow currently starts at version `0.1.0`; use the returned version, not a guessed universal default.

```bash
observal registry mcp show YOUR_NAMESPACE/repo-tools --output json
```

Use the returned qualified name/UUID. Check description, command/args or URL/transport, input names, setup instructions, and declared harnesses. Broad declarations or “analysis complete” are not runtime compatibility proof. A Git source can help analysis/provenance, but source detection does not automatically build an image or guarantee tool discovery.

## Submit and publish

```bash
observal registry mcp submit --submit LISTING_UUID --output json
```

Follow the returned review and [publication gate](/guides/review-and-publish). Direct configuration can skip repository analysis; manual review and any required validation still matter. Do not override a blocked gate to make the example finish.

## Reuse the approved release

For a generated standalone snippet:

```bash
observal registry mcp install YOUR_NAMESPACE/repo-tools --harness claude-code --version COMPONENT_VERSION --no-prompt --output json
```

Replace the name/version. Without `--apply`, this prints configuration rather than applying/tracking it. Missing required inputs can still refuse generation; do not place credentials in command arguments to recover.

Alternatively [add its exact UUID/version to an agent](/guides/components#add-an-exact-component-version-to-an-agent), then inspect that agent's pull preview. Claude Code standalone `--apply` is a separate opt-in write/setup path; review its directory, scope, and merge behavior before applying it. Never redirect raw output over an existing MCP file as a blind merge.

## Verify use, not only submission

Confirm the executable or endpoint exists in the consumer environment, supply credentials through approved input handling, activate the coding tool's configuration, and invoke a small safe tool. A snippet, install audit, or published listing alone does not prove a connection/tool call succeeded.

Exact options are in the [registry reference](/cli/registry). Use [component versioning](/guides/version-components) for updates and [integration guides](/integrations/index) for activation requirements.
