---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Publish a skill"
description: "Choose a delivery mode, review the actual instructions/files, and install an approved version."
---

A skill is reusable instructions with optional supporting files. Keep its purpose specific and the main `SKILL.md` short; link reference files when needed. Do not assume a vendor-specific shell expansion or invocation syntax is portable to every harness.

## Choose the delivery path

| Path | Use when | Important boundary |
| --- | --- | --- |
| Small registry-direct skill | Instructions are self-contained and no repository fetch is needed | Submitted inline content is not proof a model loaded/followed it. |
| Git-backed skill | Reviewed source is maintained in a repository | Source/ref/path and consumer Git access must work; moving branches are not an immutable review guarantee. |
| Complete registry folder | Scripts, references, templates, assets, or file modes must be versioned together | Exact candidate review, compatible clients, and the deployment's folder rollout gate apply. |

## Create a portable minimal skill

For example, `SKILL.md`:

```markdown
---
name: review-checklist
description: Summarize a requested file and identify questions before making changes.
---

Read the requested file and return three concise bullets.
If no target was supplied, ask for one. Do not modify files just to gather context.
```

These are instructions, not a security boundary. No embedded `!git diff` command or provider-specific invocation is required for this example.

## Save a simple direct draft

```bash
observal registry skill submit --skill-md ./SKILL.md --delivery-mode registry_direct --name review-checklist --version 1.0.0 --description 'A small review checklist' --task-type code-review --harness claude-code --draft --output json
```

Record the listing UUID and returned version metadata. Inspect JSON and the actual content:

```bash
observal registry skill show YOUR_NAMESPACE/review-checklist --output json
```

Do not choose a harness that lacks the registry skills capability. For scripts or multiple files, use the folder path below rather than pretending a single-file submission captures the entire tree.

## Save a Git-backed draft

Use a repository you own or are authorized to publish. Replace the URL, reviewed commit/ref, and directory with the actual source; `--skill-path` identifies the directory containing `SKILL.md`:

```bash
observal registry skill submit --git-url https://github.com/YOUR_ORG/your-skills.git --git-ref REVIEWED_REF --skill-path skills/review-checklist --delivery-mode git_fetch --name review-checklist --version 1.0.0 --description 'A Git-backed review checklist' --task-type code-review --harness claude-code --draft --output json
```

Inspect the returned listing with `skill show YOUR_NAMESPACE/review-checklist --output json` and review the instructions and supporting files at that exact source/ref/path. Then use `skill submit --submit LISTING_UUID` and the review/publication checkpoint below. The [submission reference](/cli/skill#submit) documents the source/ref/path flags and links the folder layout.

Submission validation reads `SKILL.md`; it is not a full audit or captured immutable bundle of the repository. A moving branch—and a tag that can be retargeted—is not an immutable review guarantee. Prefer a reviewed immutable commit where the source/consumer path supports it, and verify the fetched revision/content. Changing a source ref requires deliberate version review rather than assuming an existing agent pin tracks it safely. The server's source fetch and consumer's Git access are separate prerequisites; saving a draft does not prove either succeeds. Do not embed credentials in the repository URL. For exact registry-managed file/mode review and delivery guarantees, use the gated folder workflow instead.

## Complete folders have a separate submission checkpoint

```bash
observal registry skill submit --from-dir ./my-skill --name my-skill --description 'Complete task resources' --draft --output json
observal registry skill submit --submit LISTING_UUID --version-id DRAFT_VERSION_UUID --output json
```

Replace the returned IDs. The CLI observes the selected manifest revision before submission. Inspect every included file, mode, and exclusion. Do not acknowledge sensitive/excluded paths just to silence a warning.

Saving is permitted while folder rollout is incomplete; submission/delivery may be refused. Ask the operator to complete the [approved rollout](/skill-folder-rollout)—do not enable a deployment gate as part of creating a skill. Use [the exact folder workflow](/skill-folder-workflow) for successor versions, managed updates, backups, and recovery. Native Windows managed-folder operations are not currently supported; POSIX/WSL requirements matter.

## Review, publish, and install the selected release

For the simple draft, submit through `skill submit --submit LISTING_UUID`; for a folder, retain the exact version-ID path. Follow [review/publication](/guides/review-and-publish), then pin an approved release:

```bash
observal registry skill install YOUR_NAMESPACE/review-checklist --harness claude-code --scope project --version COMPONENT_VERSION --no-write --output json
observal registry skill install YOUR_NAMESPACE/review-checklist --harness claude-code --scope project --version COMPONENT_VERSION --output json
```

Read the no-write result before installing. Inspect `installed_path`, write status, warnings, and receipts; versioned content may be delivered as a managed bundle. A preview is not proof that an apply will pass every filesystem check. Symlinks in the destination or backup path can be refused; use an inspected canonical, non-symlink location rather than weakening that guard. Do not overwrite edited/unowned skill directories or erase receipts to force success.

A file on disk is not proof of availability, confirmed load, or usefulness in a model session. Activate it using the chosen [harness guide](/integrations/index), give it a bounded appropriate task, and inspect the available evidence. [Skill insights](/guides/interpret-insights) distinguish those meanings.

## Next

[Pin the skill in an agent](/guides/components#add-an-exact-component-version-to-an-agent), or use [versioning](/guides/version-components) and the [skill reference](/cli/skill) to maintain it.
