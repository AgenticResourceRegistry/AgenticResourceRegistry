<!-- SPDX-FileCopyrightText: 2026 Gokulkrishnan <gokulkri247@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# `observal registry prompt`

Manage reusable prompt templates in the Registry. Prompts can be submitted, listed, rendered, edited, archived, restored, transferred, and shared with co-authors. Standalone prompt installation has been removed; attach prompts to agents instead.

## Commands

| Command | Description |
| --- | --- |
| `submit` | Submit a prompt or save a draft |
| `list` | List approved visible prompts |
| `my` | List your prompts across all statuses |
| `show` | Show one prompt, visible source provenance, and public fork count |
| `fork` | Copy an approved prompt release into an independent draft |
| `render` | Render a prompt with variables |
| `edit` | Edit a draft, pending, or rejected prompt |
| `archive` | Archive an approved prompt |
| `unarchive` | Restore an archived prompt |
| `transfer-owner` | Transfer ownership |
| `co-authors` | List, add, or remove co-authors |

All structured commands support table and JSON output.

## Submit

```bash
observal registry prompt submit \
  --name review \
  --description "Review code" \
  --category code-review \
  --template "Review {{code}}" \
  --output json

observal registry prompt submit --from-file prompt.json --output json
observal registry prompt submit --submit acme/review --output json
```

JSON mode never prompts. A plain template file requires explicit name, description, and category options. A JSON file may contain the complete payload.

Valid categories are `system-prompt`, `code-review`, `code-generation`, `testing`, `documentation`, `debugging`, and `general`.

## List and show

```bash
observal registry prompt list --category code-review --output json
observal registry prompt my --output json
observal registry prompt show acme/review --output json
```

Row numbers are scoped to the latest Prompt list. Empty Prompt lists clear previous Prompt row references. Human output escapes template text so bracketed content is rendered literally.

## Fork

```bash
observal registry prompt fork acme/review --name review-custom --output json
observal registry prompt edit FORK_UUID --template 'Review {{code}} and tests' --output json
observal registry prompt submit --submit FORK_UUID --output json
```

Only approved source listings and versions can be forked. A fork has its own namespace, review status, and template; editing it does not change the original. Public fork counts exclude drafts and private teamspace forks. A source that the viewer cannot access is shown only as **Source unavailable**.

## Render

```bash
observal registry prompt render acme/review --var code=main.py --output json
```

Every variable must use `key=value` syntax with a non-empty key. JSON returns the direct server result. Human output prints the rendered prompt as literal text.

## Edit

```bash
observal registry prompt edit acme/review --description "Updated" --output json
observal registry prompt edit acme/review --from-file updates.json --output json
```

Invalid categories, versions, files, and edit-lock conflicts preserve their shared error category and stable exit code.

## Agent usage

Prompts are agent components rather than standalone harness installations:

```bash
observal agent add prompt <prompt-uuid>
observal agent build
```

## Related

* [`observal registry`](registry.md): complete Registry reference
* [`observal agent`](agent.md): compose and publish agents
