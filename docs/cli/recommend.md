<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# `observal registry recommend`

List Registry components ranked for the signed-in user and record recommendation feedback.

## List

The direct and explicit list forms are equivalent:

```bash
observal registry recommend --output json
observal registry recommend list --limit 12 --type mcp --refresh --output json
```

| Option | Description |
| --- | --- |
| `--limit`, `-n` | Number of recommendations, from 1 through 24 |
| `--type`, `-t` | Restrict to MCP, Skill, Hook, Prompt, or Sandbox |
| `--refresh` | Recompute the work profile instead of using its cache |
| `--output`, `-o` | Table or JSON output |

JSON returns the direct server object. Important fields include:

| Field | Meaning |
| --- | --- |
| `personalized` | Whether ranking used the user's sessions |
| `profile_sessions` | Session count behind the profile |
| `topics` | Dominant detected work topics |
| `items` | Recommended visible components |
| `items[].reason` | Server-provided explanation |
| `items[].matched_on` | Terms from the user's own work profile |

An empty `items` array is a successful result. It means either no visible components remain or everything applicable has already been installed or dismissed.

When `personalized` is false, results are popularity-based and must not be described as tailored.

## Dismiss

```bash
observal registry recommend dismiss skill acme/reviewer --output json
observal registry recommend dismiss mcp acme/postgres --action not_relevant --output json
observal registry recommend dismiss hook acme/guard --action installed --output json
```

Valid actions are `dismissed`, `not_relevant`, and `installed`.

JSON returns:

```json
{
  "component_type": "mcp",
  "component_id": "498c17ac-1234-4567-89ab-cdef01234567",
  "action": "installed"
}
```

Dismissals are scoped to the signed-in user. The CLI cannot inspect another user's recommendation profile.

## Exit codes

Invalid component types and actions use validation exit code 7. Authentication, permission, rate-limit, unavailable-service, and version-mismatch failures preserve the shared CLI exit contract. JSON failures leave stdout empty and write one error object to stderr.

## Related

* [`observal registry`](registry.md): browse components directly
* [`observal outdated`](outdated.md): check installed versions
