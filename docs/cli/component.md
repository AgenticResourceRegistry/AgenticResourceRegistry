---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal registry version"
---

Publish and inspect versioned releases for MCP servers, skills, hooks, prompts, and sandboxes.

## Commands

| Command | Description |
| --- | --- |
| `publish` | Publish a new component version for review |
| `list` | List paginated version history |

Valid component types are `mcp`, `skill`, `hook`, `prompt`, and `sandbox`.

## Publish

```bash
observal registry version publish mcp acme/server \
  --version 2.0.0 \
  --description "New authentication flow" \
  --changelog "Breaking change" \
  --output json
```

| Option | Description |
| --- | --- |
| `--version`, `-v` | Version to publish |
| `--description`, `-d` | Required release description |
| `--changelog` | Optional release notes |
| `--harness` | Supported harness; repeatable |
| `--extra` | Type-specific metadata as a JSON object |
| `--output`, `-o` | Table or JSON output |

Human mode fetches version suggestions and prompts when a version is omitted. JSON mode never prompts and therefore requires an explicit version. Suggestion failures retain their original authentication, rate-limit, unavailable, or version-mismatch category.

The extra value must be a JSON object. Versions and harnesses are validated before publication.

## List

```bash
observal registry version list mcp acme/server --output json
observal registry version list hook acme/guard --page 2 --page-size 100 --output json
```

| Option | Description |
| --- | --- |
| `--page` | Page number, starting at 1 |
| `--page-size` | Items per page, from 1 through 200 |
| `--output`, `-o` | Table or JSON output |

JSON returns the direct paginated server object with `items`, `total`, `page`, and `page_size`. Empty pages preserve that shape. Human output shows version, review status, relative release date, and publisher.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Publication or listing completed |
| `2` | Invalid command syntax or pagination bounds |
| `3` | Authentication required or failed |
| `4` | Permission denied |
| `5` | Component not found |
| `6` | Version or state conflict |
| `7` | Invalid component type, version, harness, or metadata |
| `8` | Rate limit reached |
| `9` | Registry unavailable |
| `10` | CLI and server version mismatch |

## Related

* [`observal registry`](/cli/registry): component management
* [`observal outdated`](/cli/outdated): compare installed and latest versions
