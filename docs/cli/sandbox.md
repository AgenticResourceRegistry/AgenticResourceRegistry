---
# SPDX-FileCopyrightText: 2026 Sanket Patel <sanket@the-algo.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal registry sandbox"
---

Submit, browse, fork, and manage versioned sandbox environments in the Registry. A sandbox declares a containerized execution runtime for agent tasks; the Registry stores and distributes its configuration and does not deploy a hosted execution service. See the [sandbox runtime reference](/sandboxes) for what the registered metadata actually enforces and [Publish a sandbox configuration](/registry-sandbox-helper) for the owner-to-reviewer-to-consumer workflow.

## Commands

| Command | Description |
| --- | --- |
| `submit` | Submit a sandbox or save a draft |
| `list` | List approved visible sandboxes |
| `show` | Show one sandbox, visible source provenance, and public fork count |
| `fork` | Copy an approved sandbox release into an independent draft |
| `edit` | Edit a draft, pending, or rejected sandbox |
| `archive` | Archive an approved sandbox |
| `unarchive` | Restore an archived sandbox |
| `transfer-owner` | Transfer ownership |
| `co-authors` | List, add, or remove co-authors |

Every command that returns structured data supports table and JSON output. Archive, restore, and ownership transfer require the confirmation bypass in JSON mode.

## Submit

Submit a new sandbox environment for review, save it as a draft first, or submit an existing draft later. The no-flag form is interactive and cannot run in JSON mode.

```bash
observal registry sandbox submit \
  --name component-echo \
  --version 1.0.0 \
  --description "A bounded Docker echo example" \
  --runtime-type docker \
  --image alpine:3 \
  --entrypoint "echo registry-component-check" \
  --network-policy none \
  --resource-limits '{"timeout":10,"memory_mb":128,"cpu_count":1}' \
  --harness claude-code \
  --draft --output json

observal registry sandbox submit --from-file sandbox.json --output json
observal registry sandbox submit --submit SANDBOX_UUID --message "Second revision" --output json
```

Name, description, runtime type, and image are required. Firecracker sandboxes also require `--runtime-config` with `config_path`, or with both `kernel_image_path` and `rootfs_path`. `--version` defaults to `1.0.0` and must be a valid version string; a `--from-file` payload must carry its own `version` — the default applies only to flag and interactive submissions, and `--version` does not merge into the file. `--resource-limits` and `--runtime-config` take JSON objects. `--harness` is repeatable and every value must be a registered harness. `--source-url`, `--source-ref`, and `--sandbox-path` record source provenance for review; they do not build an image or fetch artifacts.

Valid runtime types are `docker`, `lxc`, `firecracker`, and `wasm`. Valid network policies are `none`, `host`, `bridge`, and `restricted` (the `restricted` Docker policy currently falls back to no-network mode, not a general allowlist profile).

`--draft` and `--submit` are mutually exclusive. `--submit` sends a saved draft for review, or as a new revision of an existing listing, and `--message` attaches a revision note. `--team HANDLE --visibility team` publishes into a teamspace; `--visibility team` requires `--team`. JSON mode never prompts; supply all required inputs explicitly. Only submit sandboxes you created or represent.

## List and show

```bash
observal registry sandbox list --runtime docker --output json
observal registry sandbox list --search "node" --output json
observal registry sandbox list --namespace alice --output json
observal registry sandbox list --team platform --output json
observal registry sandbox show alice/dev-env --output json
```

`list` shows approved sandboxes only and accepts `--runtime`, `--search`, `--namespace`, and `--team` filters. Row numbers are scoped to the latest Sandbox list. Empty lists clear previous Sandbox row references.

`show` accepts a UUID, name, row number, or `@alias` and displays runtime type, container image, status, timestamps, and visible fork provenance. The table omits resource limits and runtime config; use `--output json` to inspect them in the returned server object. A source the viewer cannot access is shown only as **Source unavailable**.

## Fork

```bash
observal registry sandbox fork alice/dev-env --name my-dev-env --output json
observal registry sandbox fork alice/dev-env --version 1.2.0 --new-version 0.1.0 --team platform --visibility team --output json
observal registry sandbox edit FORK_UUID --description 'Customized runtime' --output json
observal registry sandbox submit --submit FORK_UUID --output json
```

Only approved source listings and approved base versions can be forked. The fork is an independent draft with its own namespace, reviews, and identity; editing it does not change the original. Public fork counts exclude drafts and private teamspace forks.

## Edit

```bash
observal registry sandbox edit alice/dev-env --image node:20-alpine --output json
observal registry sandbox edit alice/dev-env --from-file updates.json --output json
```

Edit a draft, pending, or rejected submission using individual field options or a JSON update file. Acquiring the edit lock protects against concurrent modifications. Runtime type, network policy, and version changes are validated against the same allowed values as `submit`. An edit on a submission under review acts as the author's resubmission.

## Archive, unarchive, and transfer ownership

```bash
observal registry sandbox archive alice/dev-env --yes --output json
observal registry sandbox unarchive alice/dev-env --yes --output json
observal registry sandbox transfer-owner alice/dev-env bob --yes --output json
```

Table mode asks for confirmation first; JSON mode never prompts and requires `--yes`. The new owner's username may optionally begin with `@`.

## Co-authors

Co-authors can edit and submit the same sandbox:

```bash
observal registry sandbox co-authors list alice/dev-env --output json
observal registry sandbox co-authors add alice/dev-env dev@example.com --output json
observal registry sandbox co-authors add alice/dev-env @dev --output json
observal registry sandbox co-authors remove alice/dev-env 550e8400-e29b-41d4-a716-446655440000 --output json
```

Remove requires the user UUID returned by list.

## Agent usage

There is no standalone sandbox install command. Sandboxes are attached to agents by UUID and are delivered when the agent is pulled, which adds an `observal-sandbox` MCP entry exposing the sandbox tools. `agent add` edits the local YAML and `agent build` validates it; publish the result for review — `agent release` version-bumps an approved agent — then pull the release that contains the sandbox:

```bash
observal agent add sandbox <sandbox-uuid>
observal agent build
observal agent publish --output json
observal agent pull alice/my-agent --output json
```

## Related

* [`observal registry`](/cli/registry): complete Registry reference
* [`observal agent`](/cli/agent): attach sandboxes to agents
* [Sandbox runtime reference](/sandboxes): what registered sandbox metadata enforces
