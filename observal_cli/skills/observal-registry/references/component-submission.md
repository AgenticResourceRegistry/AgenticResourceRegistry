<!-- SPDX-FileCopyrightText: 2026 Observal Contributors -->
<!-- SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Component submission

## Contents

- Common workflow
- Mixed bulk submission
- MCP server
- Skill
- Hook
- Prompt
- Sandbox
- Remote A2A agent
- Drafts and review status

Submit only content the user owns or is authorized to publish. Use `--team TEAM_HANDLE --visibility team` for private teamspace ownership, or `--visibility public` for reviewable public content.

## Common workflow

1. Run the leaf command's help and validate required fields.
2. Prefer a prepared file for structured or long content when supported.
3. Submit in JSON mode.
4. Capture UUID, `qualified_name`, version, and status.
5. Verify with `show` or the owner's list. Pending is not approved.

## Mixed bulk submission

Use one typed JSON file for up to 200 mixed components. Dry run performs local structural validation. Execution requires confirmation in JSON mode.

```bash
observal registry bulk submit --from-file components.json --dry-run --output json
observal registry bulk submit --from-file components.json --yes --output json
```

Each entry contains `type` plus the normal API submission fields. Supported types are `mcp`, `skill`, `hook`, `prompt`, and `sandbox`. Inspect `submitted`, `skipped`, `errors`, `partial`, and every result. Actual item errors set `partial: true` and exit with code `11`; conflicts counted as skips remain successful. Authentication and service failures stop the batch. After an uncertain failure, verify by UUID or `qualified_name` before rerunning.

## MCP server

`mcp submit` reads MCP JSON from standard input. It does not support `--from-file`.

```bash
printf '%s\n' '{"command":"npx","args":["-y","@example/mcp-server"]}' | observal registry mcp submit --name my-mcp --category developer-tools --yes --output json
observal registry mcp submit --git https://github.com/org/mcp-server --name my-mcp --category developer-tools --yes --output json
printf '%s\n' '{"command":"npx","args":["internal-mcp"]}' | observal registry mcp submit --name internal-mcp --category developer-tools --team platform-tools --visibility team --yes --output json
```

If the response includes setup instructions, report them as required follow-up. Do not invent wrappers or telemetry environment variables.

## Skill

Git delivery is preferred for public multi-file skills:

```bash
observal registry skill submit --skill-md ./SKILL.md --git-url https://github.com/org/repo --git-ref main --name my-skill --description 'What it does' --task-type general --output json
```

Registry-direct delivery stores the supplied SKILL.md and optional script:

```bash
observal registry skill submit --skill-md ./SKILL.md --delivery-mode registry_direct --name my-skill --description 'What it does' --task-type general --harness claude-code --output json
observal registry skill submit --skill-md ./SKILL.md --script ./run.sh --delivery-mode registry_direct --name my-skill --description 'What it does' --task-type general --output json
```

Choose one delivery mode deliberately. Verify installed paths and script metadata after approval.

## Hook

```bash
observal registry hook submit --name guard --description 'Guard prompts' --event UserPromptSubmit --handler-command './guard.sh' --execution-mode sync --timeout 10 --scope agent --harness claude-code --output json
observal registry hook submit --from-file hook.json --output json
```

Valid execution modes and limits are server-defined. If uncertain, read help before submission. Blocking behavior is fragile, so preserve the requested event, timeout, scope, and harness exactly.

## Prompt

```bash
observal registry prompt submit --name frontend-brief --description 'Frontend design brief' --category general --template 'Design {{component}} accessibly' --output json
observal registry prompt submit --from-file prompt.json --output json
```

Keep template variables intact and verify the stored template with `prompt show` or `prompt render`.

## Sandbox

```bash
observal registry sandbox submit --name node-runner --description 'Node sandbox' --runtime-type docker --image node:22-alpine --resource-limits '{"memory_mb":512}' --runtime-config '{}' --network-policy none --entrypoint node --harness claude-code --output json
observal registry sandbox submit --from-file sandbox.json --output json
```

Treat runtime, image, network policy, limits, entrypoint, and harness support as security-relevant fields. Do not weaken them silently.

## Remote A2A agent

A running service that publishes an A2A Agent Card (another team's agent, a deployed triage or data agent) is registered by URL, not submitted as a component. Registered agents are reviewed like components; once approved, agents can find them with discovery and hand them tasks.

```bash
observal registry a2a submit https://agents.acme.com --visibility private --output json          # /.well-known/agent-card.json
observal registry a2a submit https://agents.acme.com/a2a/card.json --visibility team --team platform --output json
observal registry a2a list --output json
observal discover inspect urn:air:agents.acme.com:a2a:incident-triage --output json                 # read the card and its obs:artifactDigest
observal registry a2a review urn:air:agents.acme.com:a2a:incident-triage --approve --digest sha256:... --output json   # reviewers only
observal registry a2a remove urn:air:agents.acme.com:a2a:incident-triage --yes --output json
```

The server fetches the card over https (internal hosts only when an administrator allowed them), and the reviewed card is pinned. Resubmitting a card whose content changed sends it back to review. Credentials are never stored in Observal.

## Drafts and review status

Submission commands support draft workflows where documented. Use local help for the exact leaf:

```bash
observal registry skill submit --skill-md ./SKILL.md --delivery-mode registry_direct --name my-skill --description 'What it does' --task-type general --draft --output json
observal registry skill submit --submit NAMESPACE/SLUG --message 'Address reviewer feedback' --output json
```

After submitting, use the returned `review_number` / `review_url` (or the Review #N link in human output) to open `observal review show N` and address threads. MCP, hook, prompt, sandbox, skill and agent draft resubmissions accept `--message` for the revision note. Report draft, pending, changes requested, approved, rejected, or archived accurately; a pending review is not a publication.
