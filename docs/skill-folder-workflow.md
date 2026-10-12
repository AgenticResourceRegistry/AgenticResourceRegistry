<!-- SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Complete skill folders: developer workflow

> In a fully rolled-out deployment, authors do not manage a feature switch:
> save a draft, explicitly submit it for review, then install the approved
> release. If this deployment has not enabled complete-folder delivery yet,
> authors can save drafts but cannot finish review or installation. Ask the
> operator to complete the [one-time rollout](skill-folder-rollout.md); do not
> turn the gate on as part of authoring.

A complete skill contains a UTF-8 `SKILL.md` at its root and optional scripts,
assets, and templates. The current limits are **128 extra files**, **2 MiB per
file**, and **4 MiB decoded bytes for the whole folder**. Browser uploads do not
retain POSIX executable bits: inspect and set executable switches before saving.
For large or nested projects, use the CLI so paths and executable modes are
captured directly. Symlinks, special files, hidden files and Git metadata are
not included automatically; inspect the reported exclusions before proceeding.

## Create, inspect, and review

```sh
observal registry skill submit --from-dir ./my-skill --draft
# Note the returned listing ID and exact draft version UUID.
observal registry skill replace-files LISTING --version-id DRAFT_UUID \
  --revision OBSERVED_REVISION --from-dir ./my-skill
observal registry skill submit --submit LISTING --version-id DRAFT_UUID
```

The Components browser has the equivalent folder editor. Save the exact version
and reopen it from **My submissions**, the listing's **Versions** tab, or its
**Edit** tab; the version number and short UUID identify which draft is being
edited. The owner can submit from the draft detail page or select an exact
version in **My submissions**. If folder review is disabled, the draft stays
editable and the submission control explains why it is unavailable. A reviewer
must inspect that exact candidate before approving it. Until approval, old
released bytes and their installations remain unchanged.

Reviewers can use the browser or the CLI. In the CLI, `review show` prints a
folder candidate's version UUID, revision and files, and `review approve` lists
the files (with added, removed, modified paths against a stored base) and binds
the decision to that revision:

```sh
observal admin review show NAMESPACE/SLUG
observal admin review approve NAMESPACE/SLUG --revision REVISION   # exactly what you inspected
observal admin review approve NAMESPACE/SLUG --yes                  # accept the files listed for this run
observal admin review approve NAMESPACE/SLUG --revision REVISION --ack-git-base   # Git -> folder conversion
observal admin review reject NAMESPACE/SLUG --reason 'Unsafe script'
```

Pass `--version-id UUID` when a listing has several pending versions.

To release a successor under the *same listing*:

```sh
observal registry skill fork NAMESPACE/SLUG --version 1.1.0 \
  --description 'New templates' --from-dir ./my-skill
# The command reports the saved version UUID; submit that exact UUID.
observal registry skill submit --submit NAMESPACE/SLUG --version-id DRAFT_UUID
```

The owner can also use **Create next version** on an approved skill's detail
page, edit the copied folder, and submit its exact draft. For a historically
invalid direct release or an approved Git release, use the distinct conversion:

```sh
observal registry skill import-folder NAMESPACE/SLUG --from-dir ./my-skill \
  --version 1.1.0 --description 'First complete registry folder'
```

The browser's **Import folder as next version** uses the same atomic endpoint.
Supply the *entire* directory yourself. Observal does **not** fetch private Git
contents for a purported file-by-file diff: reviewers see old Git source
metadata and the complete new candidate. The old Git release remains installable
until its successor passes review.

## Inspect and install an approved version

Choose the visible approved version in the browser. The install command pins
that exact version by default. **Follow latest** is an explicit, unpinned
alternative. **User (global)** installs for the current user's harness;
**Project** operates relative to the terminal's current project directory.
Only server-advertised, skill-capable harnesses are offered. The viewer shows
inert text and byte-verified binary downloads, never executes uploaded files.

```sh
observal registry skill show NAMESPACE/SLUG --version 1.1.0
observal registry skill export NAMESPACE/SLUG ./inspected-copy --version-id APPROVED_UUID
observal registry skill install NAMESPACE/SLUG --version 1.1.0 --harness pi --scope project
```

`show` lists paths, byte sizes and modes without dumping their content; `export`
refuses an existing destination. Project installs must be run from the intended
project root. Git-backed and historical resource-less skills keep their
existing install behavior while the folder-delivery gate is disabled.

## Verified managed update and recovery

An installation receipt records exact registry, listing, release, target,
owner, and sorted file hashes/modes in `~/.observal/lockfile.json`; it stores
no file content or credentials. The CLI checks every local file, including
unexpected files, before treating an existing folder as managed. Managed
folder installs, upgrades, backup restores, and pinned Agent folder pulls
currently require POSIX filesystem semantics (Linux/macOS or WSL on its Linux
filesystem); native Windows is not supported and fails closed. This restriction
does not apply to the existing Git and single-file workflows. A modified,
symlinked, differently owned or ambiguous destination is **not** overwritten.
Do not delete it or use `--force`; export the reviewed release, back up local
changes separately, and resolve the mismatch deliberately. A historical folder
without a receipt can be adopted only when its unambiguous machine lock entry
and *complete* reviewed old bundle match the tree exactly. Installing an older
Git or resource-less release into the same managed listing/scope is refused,
even when it uses a different folder name: that transition needs an explicit
manual migration rather than losing the receipt. `--check-upgrade` and
`--no-write` do not record downloads on a server that supports previews.

```sh
observal registry skill install NAMESPACE/SLUG --harness pi --scope project \
  --version 1.1.0 --check-upgrade
observal registry skill install NAMESPACE/SLUG --harness pi --scope project \
  --version 1.1.0 --upgrade
observal registry skill backups list
observal registry skill backups restore BACKUP_ID
observal registry skill backups prune BACKUP_ID
```

A first install needs no backup, so it works whatever filesystem the default
private `~/.observal/backups/skills/` is on. Replacing an existing folder moves
it into the backup root by rename, so choose an explicit `--backup-root DIR` on
the target filesystem if the default is on another one. `--check-upgrade`
reports `added`, `removed`, `modified` (content changed) and `mode_changed`
paths. The backup root must be outside all harness skill-discovery roots and ignored by
Git when inside a repository. Backups are retained until explicitly pruned;
restore refuses an edited active folder. A downgrade requires both an exact
older `--version` and explicit update intent. A process crash can briefly leave
an active path absent; the next managed invocation inspects its recovery marker
and restores a byte-verified prior copy or stops with its paths for manual
inspection. Do not run an old CLI against a managed folder during a mixed-
version rollout. Agent pulls remain pinned by the project `observal.lock` and
change versions only with an explicit `observal agent pull --upgrade` or
`--version`. A legacy Agent pull or config write cannot replace an existing
verified skill folder; a failed managed pull restores its previous machine
lock entry after the verified folders are rolled back. Delegation's disposable
worktrees are not managed installations.

Human UI review, historical migration rehearsal, and mixed-version rollout
verification are release gates **after** workflow testing. Completing these
instructions does not authorize enabling folder delivery or merging a PR.
