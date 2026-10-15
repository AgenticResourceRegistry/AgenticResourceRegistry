<!-- SPDX-FileCopyrightText: 2026 Nithin-Bhargav-07 <gaddamnithinbhargav@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# `observal registry skill`

Submit, browse, review, install, and safely update versioned skill folders, as well as Git-backed and historical single-file skills. See the [complete skill folder workflow](../skill-folder-workflow.md) for the owner-to-reviewer-to-consumer journey and rollout requirements.

## Commands

| Command | Description |
| --- | --- |
| `submit` | Submit a Git-backed or registry-direct skill |
| `list` | List approved visible skills |
| `my` | List your skills across all statuses |
| `show` | Show one skill, visible source provenance, and public fork count |
| `fork-listing` | Copy an approved skill release into a separate, independently owned draft |
| `install` | Write a skill into a supported harness |
| `edit` | Edit a draft, pending, or rejected skill |
| `fork` | Create a new draft from the exact reviewed direct release |
| `import-folder` | Import a complete local folder as a successor to a reviewed Git or historical direct release |
| `replace-files` | Replace a draft's complete file tree, binding to its version UUID and observed revision |
| `withdraw` / `rebase` | Withdraw a pending version or rebase a saved draft against a newer reviewed version |
| `export` | Export an exact version to a new local directory |
| `backups list` / `restore` / `prune` | Inspect, restore, or explicitly prune verified managed-folder backups |
| `archive` | Archive an approved skill |
| `unarchive` | Restore an archived skill |
| `transfer-owner` | Transfer ownership |
| `co-authors` | List, add, or remove co-authors |

Every command that returns structured data supports table and JSON output. Archive, restore, and ownership transfer require the confirmation bypass in JSON mode.

## Submit

Git-backed skills require a Git URL. Registry-direct skills can store `SKILL.md`, optional script resources, or a complete bounded folder with scripts, templates, binary assets and executable modes. Folder drafts are versioned; saving a draft does **not** submit it for review. Use an explicit `fork` or `import-folder` to create a successor to an approved release; never use `submit --from-dir` to silently overwrite one.

```bash
observal registry skill submit \
  --skill-md ./SKILL.md \
  --git-url https://github.com/acme/review-skill \
  --name review-skill \
  --description "Review code" \
  --task-type code-review \
  --output json

observal registry skill submit \
  --skill-md ./SKILL.md \
  --delivery-mode registry_direct \
  --name review-skill \
  --description "Review code" \
  --task-type code-review \
  --output json
```

For a new complete folder, use `observal registry skill submit --from-dir ./my-skill --name my-skill --description "My skill"`. To advance an approved listing, use `fork` (reviewed direct base) or `import-folder` (reviewed Git/historical direct base), then `observal registry skill replace-files my-skill --version-id UUID --from-dir ./my-skill --revision OBSERVED_REVISION` as needed. The [folder workflow](../skill-folder-workflow.md) covers exact-base selection, sensitive/excluded file acknowledgments, review, and immutable releases. JSON mode never prompts; supply all required inputs explicitly.

Valid task types are `code-review`, `code-generation`, `testing`, `documentation`, `debugging`, `refactoring`, `deployment`, `security-audit`, `performance`, and `general`.

## List and show

```bash
observal registry skill list --task-type code-review --output json
observal registry skill list --harness claude-code --output json
observal registry skill my --output json
observal registry skill show acme/review-skill --output json
```

Row numbers are scoped to the latest Skill list. Empty lists clear previous Skill row references.

## Fork

```bash
observal registry skill fork-listing acme/review-skill --name my-review-skill --output json
observal registry skill fork-listing acme/review-skill --team platform --visibility team --output json
observal registry skill edit FORK_UUID --description 'Customized review' --output json
observal registry skill submit --submit FORK_UUID --output json
```

The source listing and selected version must be approved; the copy begins as an independent draft. Inline `registry_direct` content is copied, but its validation flag resets for the normal submission path. Use the returned UUID to edit and submit without affecting the source. Only publicly approved forks appear in fork counts; private provenance is never exposed to viewers who lack source access.

## Install

```bash
observal registry skill install acme/review-skill --harness claude-code --scope user --output json
observal registry skill install acme/review-skill --harness pi --scope project --output json
observal registry skill install acme/review-skill --harness pi --no-write --output json
observal registry skill install acme/review-skill --harness pi --raw
```

JSON output performs the installation unless no-write is selected. It reports `write_performed` and `installed_path`. Raw mode emits only the generated config and performs no write. Lockfile state is recorded only after the skill content is written successfully. A complete folder install requires the server's default-off delivery gate, a compatible client, and a POSIX filesystem (Linux/macOS or WSL on its Linux filesystem). Native Windows managed folder installation, upgrades, backup restoration, and pinned Agent folder pulls are not supported; they fail closed rather than attempting an unsafe partial swap. The other CLI workflows and existing Git/single-file installations are not subject to this folder-swap restriction. Pin an approved `--version` for reproducible installs; omitting it follows the latest release. A managed folder is never replaced silently: first use `--check-upgrade` for a no-write preview, then `--upgrade` to verify every installed byte and mode, retain a private backup and update its receipt. `--backup-root DIR` must be private, ignored by Git when inside a worktree, outside skill discovery roots, and on the target filesystem. Cross-mode Git/single-file installs cannot erase existing folder receipts.

The command fails if the harness lacks skill support, the source cannot be installed, the destination is unowned or locally edited, or installed state cannot be recorded. See [verified updates and recovery](../skill-folder-workflow.md#verified-managed-update-and-recovery) before restoring a backup.

## Edit

```bash
observal registry skill edit acme/review-skill --description "Updated" --output json
observal registry skill edit acme/review-skill --from-file updates.json --output json
```

Edit-lock conflicts preserve conflict exit code 6. Invalid fields and files use the shared validation, not-found, permission, and unavailable categories.

## Related

* [`observal registry`](registry.md): complete registry reference
* [`observal agent`](agent.md): attach skills to agents
