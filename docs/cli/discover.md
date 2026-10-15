<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# `observal discover`

Find approved registry resources for the task at hand and use them in the current session, without building an Agent first.

Discovery searches every kind at once (agents, MCP servers, skills, hooks, prompts, sandboxes), ranks by relevance, and tells you separately whether each result is approved, whether it works in your harness, and whether it can be used right now or only after a restart. For signed-in users, results are scoped to personal listings they own and listings in every teamspace they currently belong to (including public teamspace listings); other people's public listings are not returned. Global reviewers and admins retain their review/admin scope. Anonymous public discovery is unchanged when the public registry is enabled. The bundled `observal` skill tells coding assistants to run this before reinventing something or declaring a capability unavailable.

## Search

```bash
observal discover search review a pull request for authentication bugs
observal discover search 'query postgres' --type mcp --output json
observal discover search generate tests --harness pi
observal discover search release notes --include-unapproved
```

Pass the task as one shell-escaped argument (single-quote it and escape any embedded `'` as `'\''`). Several bare words are still joined into one query, but the text comes from the user and must never be spliced into a shell command unquoted.

| Option | Description |
| --- | --- |
| `--type`, `-t` | Restrict to `agent`, `mcp`, `skill`, `hook`, `prompt`, or `sandbox` |
| `--harness`, `-i` | Only resources that list this harness as supported |
| `--limit`, `-n` | Number of results, 1 through 25 (default 5) |
| `--include-unapproved` | Also return your own pending or rejected submissions |
| `--output`, `-o` | Table or JSON output |

JSON returns `query`, `harness`, `count`, and `results[]`. Important result fields:

| Field | Meaning |
| --- | --- |
| `identifier` | Permanent `urn:air:<domain>:<kind>:<uuid>` used by `inspect` and `use` |
| `score` | Relevance to the query, 0 to 100. Never approval or trust. |
| `matchedOn` | The query words that matched |
| `obs:kind` | `agent`, `mcp`, `skill`, `hook`, `prompt`, `sandbox` |
| `obs:nativeRef` | `namespace/slug@version`, usable with the registry and agent commands |
| `obs:approval` | `approved`, `pending`, `rejected`, `archived`, `draft` |
| `obs:availability` | `now`, `next-session`, `explicit-install`, `not-approved`, `archived`, `unsupported-in-harness` |
| `obs:supportedHarnesses` | Harnesses the publisher declared; empty means unrestricted |
| `obs:artifactDigest` | SHA-256 of the exact artifact bytes |

An empty `results` array is a successful answer: nothing matched. Try fewer or different words before concluding the resource does not exist.

## Inspect

```bash
observal discover inspect urn:air:observal.acme.com:skill:5f2c... --output json
```

Returns the complete entry, including `representativeQueries` (what the resource is good for), `capabilities`, the versioned artifact `url`, and its digest. The predecessor `urn:ai:` prefix is accepted.

## Use

```bash
observal discover use urn:air:observal.acme.com:skill:5f2c...
observal discover use urn:air:observal.acme.com:mcp:9a1b... --harness kiro --output json
```

| Option | Description |
| --- | --- |
| `--harness`, `-i` | Harness this session runs in. Detected from `OBSERVAL_HARNESS` or the only installed harness when omitted. |
| `--max-chars` | Cap on printed content (default 32000); longer artifacts are truncated with a pointer to the full URL |
| `--yes`, `-y` | Load a resource that is not approved (your own drafts) |
| `--output`, `-o` | Table or JSON output |

What happens depends on the kind:

- **Skills and prompts** are text. The exact approved version is fetched from its permanent URL and printed (JSON: `content`, `activated: true`, `mode: context`). The use is recorded in the capability lock.
- **MCP servers, agents, sandboxes, and hooks** change your setup. Nothing is written; the response carries `next_step`, the existing install command (`observal registry mcp install …`, `observal agent pull …`), which confirms before touching harness config and takes effect after a restart. JSON: `activated: false`, `mode: next-session`.

Unapproved resources are refused without `--yes`. A resource whose `obs:supportedHarnesses` excludes the detected harness is refused; pass `--harness` to override the detection.

## The capability lock

Every activation appends one line to `~/.observal/capability_lock.jsonl`: timestamp, harness, working directory, identifier, kind, version, digest, and mode. `agent pull` and the component install commands append too. Session upload (`session_push` hooks and `observal reconcile`) attaches the lines that fall inside a session's harness, directory, and time window as `capabilities_used`, which is how Observal knows which resources a session relied on. Nothing secret is stored.

Entries older than 30 days are pruned. Related: [`observal agent init --from-capabilities`](agent.md) turns the resources used in a directory into a draft Agent.

## Exit codes

Follows the shared CLI error contract: `4` permission (unapproved resource without `--yes`), `5` not found, `7` validation (unknown type, harness, or identifier).
