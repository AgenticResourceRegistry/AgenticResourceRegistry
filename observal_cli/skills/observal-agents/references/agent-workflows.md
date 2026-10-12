<!-- SPDX-FileCopyrightText: 2026 Observal Contributors -->
<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Agent workflows

## Contents

- Discover and inspect
- Pull and verify
- Direct create
- Fork and edit
- Local authoring
- Update in place
- Release a version
- Bulk create
- Lifecycle and collaboration
- Error decisions

## Discover and inspect

```bash
observal agent list --search 'incident resolution' --output json
observal agent list --namespace platform-tools --output json
observal agent list --team platform-tools --output json
observal agent my --output json
observal agent show NAMESPACE/AGENT_SLUG --output json
observal agent versions NAMESPACE/AGENT_SLUG --output json
```

Use `qualified_name` or UUID from JSON for later commands. Do not use displayed row numbers.

Before choosing a model, query models for every selected harness:

```bash
observal registry models --harness kiro --output json
observal registry models --harness claude-code --output json
```

Use an exact returned model name.

## Pull and verify

```bash
observal agent pull NAMESPACE/AGENT_SLUG --harness kiro --no-prompt --dir . --output json
observal agent pull NAMESPACE/AGENT_SLUG --harness claude-code --scope project --dry-run --no-prompt --output json
```

JSON pull requires `--no-prompt` and is appropriate only when no secret values are required. If required values are missing, it exits nonzero with `error.result.needs_input: true` before installing anything. The `--env` and `--header` options expose values in shell history and process arguments, so use them only for non-secret configuration.

For credentials or tokens, omit `--no-prompt` and JSON output, then enter values through the interactive prompts. This keeps values out of process arguments. Treat generated harness configuration as sensitive because the harness may store those values.

Pulls are pinned. The first pull in a project installs the latest approved version and records it in `observal.lock` in `--dir`; later pulls install that same version even after newer ones are approved. Only add `--upgrade` (latest approved) or `--version X` when the user asks to update or pick a version. Tell the user to commit `observal.lock` so teammates and CI install the same versions. For CI, add `--strict` (or set `OBSERVAL_STRICT=1`) so an install that does not match its lock fails instead of warning.

Inspect `files`, `warnings`, `setup_commands`, `reports_sessions`, `agent.version`, `agent.resolved_from`, `agent.latest_version`, and `lock` (`status`, `components`, `problems`). Report a newer `latest_version` as available rather than installing it. When `reports_sessions` is true after a successful pull, tell the user that installed session hooks may send prompts, tool calls, and tool output to the configured Observal server when this Agent is used. For a dry run, say hooks *would* be present if applied, not that they were installed. On a failed pull, inspect `error.result.reports_sessions`: if true, disclose that hooks are already on disk and may report sessions despite the failed installation. Then verify installation:

```bash
observal scan --harness kiro --output json
```

For Pi, use the exact local profile name returned by pull with the harness profile command.

## Direct create

Use for a complete one-call Agent without local component authoring:

```bash
observal agent create --name reviewer --description 'Reviews pull requests' --prompt 'Review changes for correctness and risk' --model claude-sonnet-4-6 --harness kiro --output json
```

Name, description, and prompt keep creation noninteractive. Use `--prompt-file` for long prompts. Verify with the returned UUID or `qualified_name`:

```bash
observal agent show NAMESPACE/REVIEWER --output json
```

## Fork and edit

Fork only when the user wants a new independently owned Agent, rather than a release of the source. Read the approved source with `agent show --output json`, choose a new name/target, and fork once. Never retry blindly after an uncertain mutation failure: check `agent my --output json` for the new draft first.

```bash
observal agent fork NAMESPACE/SOURCE --name my-copy --dir ./my-copy --output json
observal agent build --dir ./my-copy --output json
observal agent publish --update --dir ./my-copy --output json
observal agent publish --submit FORK_UUID --output json
observal agent show FORK_UUID --output json
```

Use the fork response's `id` as `FORK_UUID`. Edit `observal-agent.yaml` before `publish --update`; its `agent_id` directs the update to the draft (public search does not include drafts). `--version X.Y.Z` selects one approved source release, and `--new-version X.Y.Z` sets the new draft version. `--team HANDLE --visibility team` puts a public source's copy into an authorized private teamspace. A private source must stay within the same teamspace. Components retain their approved pins, without forking the components. Report `warnings` for re-pinned components and the actual review status. If `forked_from.available` is false, describe the original only as **Source unavailable**; never expose saved source references. A private or unpublished fork does not add to the public fork count.

## Local authoring

Use this workflow when the Agent needs component references, review before publication, or repeatable source files.

1. Scaffold:

```bash
observal agent init --dir ./my-agent --name reviewer --description 'Reviews pull requests' --prompt-file ./PROMPT.md --model claude-sonnet-4-6 --harness kiro --output json
```

2. Find components and add returned UUIDs:

```bash
observal registry mcp list --search 'github' --output json
observal registry skill list --search 'code review' --output json
observal agent add mcp COMPONENT_UUID --dir ./my-agent --output json
observal agent add skill COMPONENT_UUID --dir ./my-agent --output json
```

3. Validate, then publish:

```bash
observal agent build --dir ./my-agent --output json
observal agent publish --dir ./my-agent --output json
```

Use `--draft` to save without review and `--submit AGENT_UUID` to submit an existing draft. Team publication uses an explicit target:

```bash
observal agent publish --dir ./my-agent --team platform-tools --visibility team --output json
observal agent publish --dir ./my-agent --team platform-tools --visibility public --output json
```

## Update in place

Use only when the user wants to change the current listing without a reviewed version, and only while its latest version is a draft, pending, or rejected. An approved version is immutable; the update fails with a conflict that points to `agent release`. Use [Release a version](#release-a-version) instead.

1. Read current state with `agent show`.
2. Preserve required fields in `observal-agent.yaml`, including `model_config_json: {}` and `external_mcps: []`.
3. Build before mutation.
4. Publish update and verify.

```bash
observal agent build --dir ./my-agent --output json
observal agent publish --update --dir ./my-agent --output json
observal agent show NAMESPACE/AGENT_SLUG --output json
```

## Release a version

Use when the user asks for a patch, minor, major, release, or reviewed version.

```bash
observal agent release NAMESPACE/AGENT_SLUG --bump patch --dir ./my-agent --output json
observal agent versions NAMESPACE/AGENT_SLUG --output json
```

The YAML must include all required fields. Report the returned review status and version. A submitted release is not approved until review says so.

A release pins every component to an exact version and keeps the pins of the current release, so components do not change unless asked. To see what is behind, and to move components forward:

```bash
observal agent outdated NAMESPACE/AGENT_SLUG --output json
observal agent release NAMESPACE/AGENT_SLUG --bump minor --dir ./my-agent --refresh-components --output json
```

`--refresh-components` moves every component without a `version` in the YAML to its latest approved release. To pin one component to an exact release instead, set its `version` in the YAML, or add it with `observal agent add TYPE COMPONENT_UUID --version X.Y.Z --dir ./my-agent`. Only refresh when the user asks for newer component versions.

## Bulk create

Run dry run first, then execute the same prepared input:

```bash
observal agent bulk-create --from-file agents.json --dry-run --output json
observal agent bulk-create --from-file agents.json --yes --output json
```

A file contains 1-50 agents and duplicate canonical names are rejected before the request. Verify each returned item. `errors > 0` sets `partial: true` and exits with code `11`; skips alone remain successful.

## Lifecycle and collaboration

```bash
observal agent archive NAMESPACE/AGENT_SLUG --yes --output json
observal agent unarchive NAMESPACE/AGENT_SLUG --yes --output json
observal agent transfer-owner NAMESPACE/AGENT_SLUG @username --yes --output json
observal agent co-authors list NAMESPACE/AGENT_SLUG --output json
observal agent co-authors add NAMESPACE/AGENT_SLUG @username --output json
observal agent co-authors remove NAMESPACE/AGENT_SLUG USER_UUID --output json
```

Use user UUIDs returned by co-author list for removal. Verify ownership and lifecycle state with `agent show`.

## Error decisions

- 409 ambiguous name: re-list and use `qualified_name` or UUID.
- 409 existing Agent: choose update only for in-place change, release for a new version.
- Validation names a required YAML field: inspect `error.result.components` and `error.result.issues`, correct the source file, rebuild, and retry once.
- Unavailable or not configured: stop. Load `observal-advanced` only for an explicit fallback request.
