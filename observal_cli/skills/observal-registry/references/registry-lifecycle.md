<!-- SPDX-FileCopyrightText: 2026 Observal Contributors -->
<!-- SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Registry lifecycle

## Contents

- Read owned state
- Fork an approved component
- Edit
- Publish a version
- Sync an MCP from GitHub or GitLab
- Archive and restore
- Transfer ownership
- Co-authors
- Error decisions

## Read owned state

Read status, version, ownership, and optimistic-lock fields before mutation.

```bash
observal registry mcp my --output json
observal registry skill my --output json
observal registry prompt my --output json
observal registry mcp show NAMESPACE/SLUG --output json
```

Use returned UUIDs or `qualified_name` values in later commands.

## Fork an approved component

When the user wants an independently owned copy rather than a new version of the original, inspect its approved status and fork once. Only approved source versions may be selected. Provide a new name and target explicitly when requested; use canonical names or UUIDs rather than bare names.

```bash
observal registry mcp fork NAMESPACE/SLUG --name my-mcp --output json
observal registry skill fork-listing NAMESPACE/SLUG --version 1.2.0 --new-version 0.1.0 --output json
observal registry hook fork NAMESPACE/SLUG --team platform --visibility team --output json
observal registry prompt fork NAMESPACE/SLUG --name my-prompt --output json
observal registry sandbox fork NAMESPACE/SLUG --name my-sandbox --output json
```

All five commands (`registry skill fork-listing` for skills, since `registry skill fork` creates a new version of the same listing) return the new independent draft object with an `id`, `qualified_name`, `status`, and visibility-filtered `forked_from`. Never display or infer a source identity when `forked_from.available` is false. A private source must remain in its own private teamspace. A draft and a team-private fork do not increase public fork counts. After creation, edit using the new UUID (`observal registry TYPE edit FORK_UUID ... --output json`) and submit using the relevant type's draft submission command; verify the resulting review status. If a request's outcome is uncertain, inspect `registry TYPE my --output json` (or show by UUID where `my` is unavailable) before any retry.

## Edit

Draft, pending, and rejected items edit in place. Approved listings can enter a version flow.

```bash
observal registry mcp edit NAMESPACE/SLUG --from-file updates.json --output json
observal registry mcp edit NAMESPACE/SLUG --name new-name --description 'New description' --output json
observal registry skill edit NAMESPACE/SLUG --from-file updates.json --output json
observal registry hook edit NAMESPACE/SLUG --version 1.2.0 --event Stop --output json
observal registry prompt edit NAMESPACE/SLUG --template 'New template body' --output json
observal registry sandbox edit NAMESPACE/SLUG --image python:3.12-slim --output json
```

Verify the returned status and version. On an edit-lock conflict, do not overwrite blindly. Wait or ask the current editor to release it.

## Publish a version

Always supply an explicit semantic version in agent workflows so no prompt appears.

```bash
observal registry version publish mcp NAMESPACE/SLUG --version 1.2.0 --description 'What changed' --output json
observal registry version publish skill NAMESPACE/SLUG --version 0.3.0 --description 'New tasks' --output json
observal registry version publish hook NAMESPACE/SLUG --version 1.0.1 --description 'Bug fix' --output json
observal registry version publish prompt NAMESPACE/SLUG --version 2.0.0 --description 'Rewrite' --output json
observal registry version publish sandbox NAMESPACE/SLUG --version 1.1.0 --description 'New image' --extra '{"runtime_type":"docker","image":"python:3.12-slim"}' --output json
observal registry version list mcp NAMESPACE/SLUG --output json
```

Report review status separately from version creation.

## Skill folder version management

Skills with extra files (scripts, templates, assets) use folder-based authoring.

### Save a folder draft

```bash
observal registry skill submit --from-dir ./my-skill --name my-skill --description 'My skill' --output json
```

The response contains `listing_id`, `version_id` and `revision`, not a reviewed or installable release. Hidden/excluded paths are reported; machine-readable submissions refuse omissions and likely-sensitive paths unless the human has inspected them and explicitly adds `--allow-excluded` and/or `--allow-sensitive`. Do not use the flag to bypass an uncertain directory. Resource-bearing review and delivery refuse while the rollout setting remains off. After review is separately enabled, submit that **exact** saved version:

```bash
observal registry skill submit --submit LISTING_ID --version-id VERSION_UUID --output json
```

### Fork the current approved direct release

```bash
observal registry skill fork NAMESPACE/SLUG --version 1.1.0 --description 'New resources' --from-dir ./my-skill --output json
```

This creates an **editable new version** of the same listing; the approved files never change. Omit `--from-dir` to start from the reviewed folder, or provide the complete new folder to replace the draft's files. The response names the exact draft UUID and revision. If upload fails after creation, resume that UUID instead of blindly forking again. A Git-backed or nonconforming historical direct release cannot be cloned with `fork`. Explicitly import one complete local folder into the **same listing** instead:

```bash
observal registry skill import-folder NAMESPACE/SLUG --from-dir ./my-skill --version 1.1.0 --description 'Conformant folder' --output json
```

This does not fetch the old Git files or overwrite its approved release. A reviewer must inspect the full candidate and acknowledge that the old Git file tree cannot be compared. Resource-bearing review stays disabled until the rollout gate is deliberately enabled.

### Replace all files in a version

```bash
observal registry skill replace-files NAMESPACE/SLUG --version-id UUID --from-dir ./my-skill --revision REVISION --output json
```

This replaces the **entire** draft tree. Inspect the local snapshot first: excluded paths require explicit `--allow-excluded` (they may be deleted remotely) and likely-sensitive files require `--allow-sensitive` in machine mode. No unacknowledged replacement request is sent.

### Withdraw a pending version

```bash
observal registry skill withdraw NAMESPACE/SLUG --version-id UUID --revision REVISION --output json
```

### Rebase a draft on current approved

```bash
observal registry skill rebase NAMESPACE/SLUG --version-id DRAFT_UUID --revision DRAFT_REV \
    --current-version-id APPROVED_UUID --current-revision APPROVED_REV --new-version 1.2.0 --output json
```

### Export a version to local directory

```bash
observal registry skill export NAMESPACE/SLUG ./local-dir --output json
observal registry skill export NAMESPACE/SLUG ./local-dir --version-id UUID --output json
```

Export requires a **new** destination directory, downloads and verifies every declared file before writing, and preserves binary attachments. Without `--version-id` it selects a cleared approved release, never an unreviewed draft.

Always use `--output json` for machine-readable responses. Verify revision fields match before mutations.

## Sync an MCP from GitHub or GitLab

An MCP listing with a git URL can publish versions automatically from GitHub or GitLab pushes, releases, or both. Synced versions skip review, so confirm with the user before enabling it.

```bash
observal registry mcp sync enable NAMESPACE/SLUG --output json
observal registry mcp sync enable NAMESPACE/SLUG --release --no-push --output json
observal registry mcp sync enable NAMESPACE/SLUG --provider gitlab --output json   # self-managed GitLab hostname
observal registry mcp sync status NAMESPACE/SLUG --output json
observal registry mcp sync run NAMESPACE/SLUG --output json
observal registry mcp sync disable NAMESPACE/SLUG --yes --output json
```

The first `enable` returns `provider`, `webhook_url` and a one-time `secret`. Tell the user to add them under Settings > Webhooks: on GitHub as the Payload URL and Secret with content type `application/json`, on GitLab as the URL and Secret token with Push and/or Releases events. Never echo the secret into files or logs.

## Archive and restore

```bash
observal registry mcp archive NAMESPACE/SLUG --yes --output json
observal registry skill archive NAMESPACE/SLUG --yes --output json
observal registry hook archive NAMESPACE/SLUG --yes --output json
observal registry prompt archive NAMESPACE/SLUG --yes --output json
observal registry sandbox archive NAMESPACE/SLUG --yes --output json
observal registry mcp unarchive NAMESPACE/SLUG --yes --output json
observal registry skill unarchive NAMESPACE/SLUG --yes --output json
```

Verify archived or restored state with the corresponding `show` command.

## Transfer ownership

```bash
observal registry mcp transfer-owner NAMESPACE/SLUG @username --yes --output json
observal registry skill transfer-owner NAMESPACE/SLUG @username --yes --output json
```

Ownership transfer changes who controls future edits and versions. Verify owner in the returned item.

## Co-authors

Co-authors can edit and publish. Add by email or username, remove by user UUID returned from list.

```bash
observal registry mcp co-authors list NAMESPACE/SLUG --output json
observal registry skill co-authors add NAMESPACE/SLUG @username --output json
observal registry hook co-authors remove NAMESPACE/SLUG USER_UUID --output json
```

## Error decisions

- Ambiguous name: use returned `qualified_name` or UUID.
- Edit lock: wait or coordinate, never force an overwrite.
- Conflict on approved item: inspect whether the server expects an edit or version command.
- Validation: correct only the named payload field and retry.
- Permission: report ownership or co-author requirement without escalating.
