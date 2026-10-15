<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Discovery

## Contents

- When to search
- Check what is already installed
- Search
- Inspect
- Use
- Delegate to another agent
- Save a working session as an Agent
- Interpreting fields

## When to search

Search whenever the task could plausibly be covered by something the organization has already approved: reviews, test generation, documentation, querying a system, running code safely, connecting to a service, drafting or research work, anything substantive. This is not limited to coding. Search once for each need. If it returns no results, retry once with fewer or different words, then act on the final result. Do not search again for the same need in the same session.

Do not search for trivial edits the user described precisely, or when the user explicitly asked for a from-scratch solution.

## Check what is already installed

Before searching, and again before running any `next_step` install command, confirm the capability is not already present:

```bash
observal scan --output json                        # add --harness <harness> when the active harness is known
observal outdated --no-report --output json
```

`scan` is read-only and lists the MCP servers, skills, Agents, and hooks installed for every registered harness, or for one harness with `--harness`; combine it with the tools and skills already loaded in the current session. Pass `--no-report` to `outdated` so an automatic check does not write to the user's inbox. If the resource is already there, use it directly and do not pull or install it again. A component that a user relies on every day must not be re-pulled on every session. Only a successful `outdated` result reporting a newer approved version is a reason to touch an existing install, and even then ask first. If `outdated` fails (authentication, server unavailable, rate limit), treat it as nonfatal: continue to discovery and leave existing installs alone.

## Search

```bash
observal discover search 'review a pull request for authentication bugs' --output json
observal discover search 'query postgres' --type mcp --output json
observal discover search 'generate playwright tests' --harness pi --output json
observal discover search 'release notes' --include-unapproved --output json   # your own drafts too
```

Pass the task as one shell-escaped argument (single-quote it and escape any embedded `'` as `'\''`); the words are user text, not shell syntax. `--type` accepts `agent`, `mcp`, `skill`, `hook`, `prompt`, `sandbox`. `--harness` restricts to resources that list that harness. Results are `results[]`, ranked by `score` (relevance only, 0-100), each with `matchedOn` explaining the match. An empty `results` is a successful answer: nothing matched.

Signed-in discovery covers resources in your personal namespace and every teamspace you currently belong to; it does not search other people's public namespaces. A resource elsewhere may still be installable by reference. Admins and global reviewers retain their broader access, and anonymous public discovery is controlled by the deployment's public registry switch. Retry once with fewer or different words before concluding nothing exists within your scope.

## Inspect

```bash
observal discover inspect urn:air:observal.acme.com:skill:5f2c... --output json
```

Returns the complete entry: `description`, `capabilities`, `representativeQueries` (what it is good for), `obs:supportedHarnesses`, `obs:lifecycle`, `obs:activatable`, `url` (the exact versioned artifact), `obs:artifactDigest`.

## Use

```bash
observal discover use urn:air:observal.acme.com:skill:5f2c... --output json
observal discover use urn:air:observal.acme.com:mcp:9a1b... --harness kiro --output json
```

- Skills and prompts (`obs:availability: now`): the response has `activated: true`, `mode: context`, and `content` holding the exact approved version. Read `content` and follow it for the current task. `truncated: true` means the artifact was cut at `--max-chars`; fetch `artifact_url` only if the remainder is needed.
- MCP servers, agents, sandboxes (`next-session`) and hooks (`explicit-install`): the response has `activated: false` and `next_step`, the existing install command. It confirms before writing harness config and takes effect after a restart. Run it only with the user's agreement, then tell the user a restart is needed.
- `--harness` is detected from `OBSERVAL_HARNESS` or the single installed harness; pass it explicitly when several harnesses are installed.
- Unapproved resources are refused unless `--yes` is given; use that only for the user's own drafts and say so.

Every `use` is recorded in `~/.observal/capability_lock.jsonl` so the session shows which resources it relied on. Nothing secret is written there.

## Delegate to another agent

When a self-contained part of the task needs a specialist (a security review, a service team's own agent, test generation), hand it to an approved agent instead of installing it. Results with `obs:delegable: true` qualify: registry Agents (run headless in a supported harness) and remote A2A agents (`obs:availability: delegate`). Agents pulled from Observal have the same operations as MCP tools (`find_agents`, `delegate`, `get_task`, `cancel_task`); prefer those when they are loaded.

```bash
observal delegate find 'review this branch for authentication bugs' --output json
observal delegate run urn:air:observal.acme.com:agent:5f2c... 'Review src/auth on this branch for token leaks. Report file:line findings.' --output json
observal delegate status <task-id> --wait 120 --output json
observal delegate reply <task-id> 'Use the eu-west region' --output json   # remote agent asked for input
observal delegate cancel <task-id> --output json
```

- Write the message as a complete brief. The delegated agent does not see this conversation.
- A registry Agent works in a throwaway copy of the repository. Its file changes come back as a `changes.patch` artifact and `metadata.observal.patchPath`; they are never applied for you. Review the patch, tell the user what it changes, and `git apply` it only when it is right.
- The response is an A2A Task. `status.state` is `TASK_STATE_COMPLETED`, `TASK_STATE_FAILED`, `TASK_STATE_CANCELED`, `TASK_STATE_REJECTED`, `TASK_STATE_INPUT_REQUIRED` (answer with `reply`), or still `TASK_STATE_WORKING` (poll with `status --wait`).
- Delegation is refused past two levels, past three tasks started by one delegated agent, for an agent already in the chain, and for unapproved agents. Do not work around a refusal; do that part yourself.
- Remote agents that need credentials read them from `OBSERVAL_A2A_TOKEN_<HOST>`, named after the agent's endpoint host (`agents.acme.com` is `OBSERVAL_A2A_TOKEN_AGENTS_ACME_COM`). Never put a token in the message.

## Save a working session as an Agent

When a combination of resources worked, offer to save it:

```bash
observal agent init --from-capabilities --name pr-review-flow --dir ./pr-review-flow --output json
```

This pre-fills `components` from everything used in the current directory in the last 24 hours (`--since 2h`, `--since 3d` to change). Agents cannot nest other agents; a pulled agent is reported under `skipped_agents`. Continue with the `observal-agents` skill to review, build, and publish.

## Interpreting fields

| Field | Meaning |
| --- | --- |
| `score` | Relevance to the query only. Not approval, not trust. |
| `obs:approval` / `obs:lifecycle` | `approved`, `pending`, `rejected`, `archived`, `draft`. Only `approved` loads without `--yes`. |
| `obs:availability` | `now`, `next-session`, `explicit-install`, `delegate`, `not-approved`, `archived`, `unsupported-in-harness`. |
| `obs:delegable` | The entry can take a delegated task now (approved agent with a headless harness, or approved remote A2A agent). |
| `obs:recommended` | An admin marked it as recommended. Editorial, never part of `score`; it only breaks ties between equal scores. Prefer it among usable candidates of similar relevance. `find_agents` returns it as `recommended`. |
| `obs:provider` | Remote A2A agents only: the organization named on the agent card, as the card states it (not verified). |
| `obs:supportedHarnesses` | Harnesses the publisher declared. Empty means unrestricted. |
| `obs:nativeRef` | `namespace/slug@version`, usable with `observal registry ... show` and `observal agent pull`. |
| `obs:artifactDigest` | SHA-256 of the exact artifact bytes; recorded in the capability lock. |
| `matchedOn` | The query words that matched, for explaining the choice to the user. |
