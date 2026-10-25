---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal api"
---

Call an authenticated JSON endpoint when no dedicated high-level command exists.

## Examples

```bash
observal api GET /api/v1/teams --output json
observal api GET /api/v1/agents --param tag=review --param tag=security --output json
observal api POST /api/v1/teams --from-file team.json --output json
cat team.json | observal api POST /api/v1/teams --output json
```

Methods are `GET`, `POST`, `PUT`, `PATCH`, and `DELETE`. Paths must be canonical relative `/api/v1/` paths; the leading slash is optional, so `api/v1/teams` works too. Full URLs, encoded or plain traversal segments, fragments, and inline query strings are rejected. Use repeatable `--param KEY=VALUE` options for query parameters; repeated keys preserve their original order.

Git Bash on Windows rewrites an argument that starts with `/` into a Windows path, so `/api/v1/teams` reaches the CLI as `C:/Program Files/Git/api/v1/teams`. The CLI recognizes this, calls `/api/v1/teams`, and prints a note on standard error. Writing `api/v1/teams` without the leading slash avoids the rewrite.

The command uses the configured bearer token. It does not accept arbitrary authorization headers, so it cannot forward credentials to another host.

## Request bodies

`POST`, `PUT`, `PATCH`, and `DELETE` accept any single JSON value from `--from-file` or standard input: objects, arrays, strings, numbers, booleans, and explicit `null`. A file takes precedence when both are present. `GET` rejects every request body, including `null`.

## Output

Table mode renders arbitrary objects as field and value rows. JSON mode preserves the endpoint response exactly, including top-level arrays. This raw behavior is intentional and is the exception to dedicated list commands, which use the standard list envelope.

## Errors and retries

The command uses the shared categorized error contract and preserves server request IDs. Automatic transient retries apply only to `GET`. After an uncertain mutation failure, read endpoint state before retrying. See [Mutation retries and idempotency](/cli/idempotency).

Prefer dedicated commands when they exist because they provide stronger validation, safer confirmation, and domain-specific output.
