---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal registry models"
---

Display the model catalog packaged for registered harnesses.

## Synopsis

```bash
observal registry models [--harness <name>] [--output table|json]
observal registry models list [--harness <name>] [--output table|json]
```

The direct and explicit list forms are equivalent.

## Options

| Option | Description |
| --- | --- |
| `--harness <name>` | Filter to one registered harness |
| `--output table` | Render a human-readable table; default |
| `--output json` | Emit the complete catalog object |

## JSON schema

```json
{
  "models": [
    {
      "harness": "pi",
      "model_id": "anthropic/claude-sonnet-4-6",
      "kind": "exact",
      "display_name": "Claude Sonnet 4.6"
    }
  ],
  "source": "harness-registry",
  "degraded": false
}
```

An empty catalog keeps the same object with an empty `models` array.

## Data source

The command reads harness model JSON files packaged under `observal_shared/harness_models/`. It does not contact the Observal server.

An unknown harness is a usage error with exit code 2. JSON errors are written to stderr while stdout remains empty.

## Examples

```bash
observal registry models
observal registry models --harness pi --output json
observal registry models list --harness claude-code --output json
```
