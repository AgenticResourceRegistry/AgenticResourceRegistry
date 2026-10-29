---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Releasing"
---

Observal develops on `main` and publishes only from protected `release/X.Y` branches. Each minor line has one branch for alpha, beta, release candidates, stable, and subsequent patches. There is no `develop` branch, per-channel branch, or SLA database.

```text
main          A---B---C---D---E------> next features
                      \       \
release/1.14           C---RC---E'---stable---patch
                               ^
                         selected backport
```

Never merge a release branch into `main`, rebase it onto `main`, force-push it, delete its published history, or replace a published tag. Ordinary fixes land on `main` first and are cherry-picked with provenance into each affected release line. The provenance check verifies the source exists on main, not semantic equivalence: required reviewers must inspect the actual backport diff, especially after conflict resolution. Conflict resolution is reviewed on the destination branch.

## Prerequisites

- Git, authenticated GitHub CLI, and `uv`.
- `upstream` points to the canonical repository, `origin` to your fork. Use `--upstream origin --fork origin` when both are the canonical clone.
- A clean local branch that exactly matches its canonical remote branch.
- Release branch rules and publishing environments configured as described below.

The existing maintainer tool is `tools/release.py`, not a command in the distributed Observal CLI. `make release` invokes it; `make release ARGS="..."` passes options through.

## Cut a minor line

From an up-to-date `main`:

```bash
uv run python tools/release.py --cut 1.14
```

This creates `release/1.14` at the exact current canonical `main` commit. The new minor line must be above every existing release tag; maintain older lines in place. It does not publish anything. It also opens a PR on `main` (`chore: start 1.15 development`) advancing the version files to the next minor dev version (`1.15.0-dev.0`), so `main` never looks older than the line it feeds. That commit is metadata: it is never backported. An existing line cannot be overwritten, including if another maintainer cuts the same line concurrently.

```bash
git fetch upstream
git switch --track upstream/release/1.14
```

Continue normal feature PRs against `main`. The release line receives selected backports and release metadata only.

## Prepare and preview

On the release branch:

```bash
make release-preview
make release
# Or specify the stage:
make release ARGS="--channel beta"
make release ARGS="--channel rc"
make release ARGS="--channel stable"
# Non-interactive preparation with default public-note selection:
make release ARGS="--channel rc --yes"
# Fully flag-driven, with note overrides (repeatable) and an explicit version:
make release ARGS="--channel stable --version 1.14.0 --yes --include-pr 1641 --exclude-pr 1700 --highlight-pr 1650 --breaking-pr 1651 --title-pr '1652=Clearer title' --category-pr 1653=Fixes"
```

Every prompt has a flag equivalent. `--include-pr`, `--exclude-pr`, `--highlight-pr`, `--breaking-pr`, `--title-pr PR=TITLE`, and `--category-pr PR=CATEGORY` adjust public notes; unknown PR numbers or categories fail. Non-interactive runs need no terminal.

All code on the branch ships. The interactive picker curates public notes, not the code boundary. It can edit note titles, categories, highlights, and breaking-change markers. Database migrations must appear in the public notes. Existing changelog history is preserved.

The tool creates `prepare/vX.Y.Z[-channel.N]`, updates package versions and lockfiles, writes `.release.toml`, release notes and a changelog section, and opens a PR against `release/X.Y`. The manifest records the branch, exact source parent, previous reachable release tag, and included PRs.

Rebase-merge the single metadata commit after CI passes. If the destination branch advances, regenerate the release PR from the new tip; merely rebasing stale metadata is rejected. Failed preparation preserves its worktree under `.worktrees/` for diagnosis. Remove or rename that local preparation branch/worktree explicitly before preparing the same version again.

## Channels and versions

| Stage | Example | Docker alias | GitHub |
| --- | --- | --- | --- |
| Alpha | `1.14.0-alpha.1` | `alpha` | Prerelease |
| Beta | `1.14.0-beta.1` | `beta` | Prerelease |
| Release candidate | `1.14.0-rc.1` | `next` | Prerelease |
| Newest stable | `1.14.0` | `latest` | Stable, promoted after verification |
| Older stable line | `1.13.2` | `lts-1.13` | Stable, never promoted to latest |

Versions advance within their branch's minor line. Repeating a channel increments its serial. Alpha to beta to RC to stable is allowed, as is skipping a stage; going backward within the same version core is rejected. After stable, preparation starts the next patch. `--version` accepts an explicit version subject to the same checks. Starting a new minor requires a new branch.

Python distributes the equivalent PEP 440 versions (`1.14.0a1`, `1.14.0b1`, `1.14.0rc1`). PyPI has no dist-tags. Exact-version pins are the mechanism for maintenance consumers. Helm publishes exact OCI chart versions and uses the matching application image version; it has no `latest` or `lts` alias.

Older prereleases use line-specific aliases such as `next-1.13` or `beta-1.13` when a newer release exists in the same channel. They cannot move the shared prerelease alias backward.

Only the numerically newest stable version across all lines can take over stable discovery. Patching an older line must not change Docker `latest`, GitHub latest, or the hosted deployment. Consumers selecting a prerelease or maintenance line should pin an exact version when reproducibility matters.

## Backport a fix

After a PR is rebase-merged into canonical `main`, return to an up-to-date local `main`:

```bash
uv run python tools/release.py --backport 123 --to release/1.14
```

The tool identifies the mainline commits from the merged PR, verifies their messages against the original PR, creates `backport/1.14/123`, cherry-picks with `-x`, and opens a destination PR with `Backport-of`, target, and original commit hashes. Squash or merge-commit histories that cannot be identified safely fail rather than guessing.

Resolve conflicts in the preserved worktree. Do not omit a failed commit or silently skip an empty cherry-pick. Review the backport, run destination CI, and merge before preparing the next patch or candidate. Repeat separately for other affected lines. Do not merge a whole newer release branch into an older one.

```bash
uv run python tools/release.py --status
```

On `main`, status lists canonical release lines. On a release branch, it shows the latest reachable tag and unreleased commits. GitHub's open and merged backport PRs are the tracking record; no SLA state or label database is required.

## Pipeline and recovery

A release-branch push is inspected for a single release metadata commit. Branch creation and ordinary backports do not publish. Builds use the exact validated commit from the push, never a moving branch tip or an unmerged PR head. Manual recovery must select the same `release/X.Y` branch in Actions and supply the full release commit SHA.

1. Validate branch ancestry, parent, version progression, allowed metadata files, package versions, notes, and tag identity.
2. Build CLI binaries, container digests, and the server archive.
3. Require the production environment approval.
4. Create a keyless signed tag tied to the exact release branch, then publish PyPI, Docker, and Helm artifacts.
5. Publish GitHub assets and verify the registries and installed CLI.
6. For the newest stable release only, promote GitHub latest and invoke the EC2 workflow. EC2 checks out the validated tagged commit, not `main`.

The release workflow is globally serialized across lines with GitHub's `queue: max` setting, which keeps up to 100 pending runs rather than replacing an earlier pending release. Publishing jobs recompute alias eligibility from current tags, including on failed-job retries. Signing and attestation verification use `refs/heads/release/X.Y`, not `main` or a wildcard. See [release verification](/security/release-verification).

An existing tag is accepted only if it points to the exact validated commit and verifies with the expected signature. A later published version on the same line makes an older preparation ineligible for republication. Use failed-job reruns for partial failures and never replace immutable package versions or tags. Correct released code with a new patch.

## One-time rollout checklist

These are production administration actions. Do not perform them implicitly during local development or a sandbox test.

1. Merge the rewritten tooling and workflows into `main` before cutting a line. The first new line should be a future minor, for example `release/1.14`; do not fabricate history for old detached release tags.
2. Install `.github/release-ruleset.json` through the GitHub repository rulesets API or UI. It requires PRs, rebase-only merging, linear history, reviews, and existing main CI checks plus `release-policy`, and blocks deletion and force-push. Adjust the external CLA check only if the actual integration has changed. Required checks must run on the chosen event; path-filtered Terraform/E2E checks are intentionally not unconditional required contexts.
3. Keep existing `main` protection and immutable tag rules. The release GitHub App must retain permission to create signed tags under the existing tag rules. Do not grant ordinary contributors a release bypass.
4. Add the `release/*` **branch** pattern to the `production` environment. Its pre-migration policy permits only `main` and `v*` tags, which would block this pipeline. Restrict the `pypi` publishing environment to release branches as appropriate. Preserve reviewers and existing approval requirements.
5. Check PyPI trusted-publisher configuration for the repository, workflow filename, and environment. Any provider policy explicitly tied to `main` must be updated to allow the intended release branches, without broadening unrelated credentials.
6. Review the first release's notes carefully. Historical curated-cutoff tags are not ancestors of `main`; discovery now uses the latest reachable tag, not their old manifest cutoff. This can include previously shipped changes in the first new-line comparison. Curate those public notes once rather than keeping a legacy resolver.
7. Rehearse beta, RC, stable, and a maintenance patch in the disposable sandbox. Verify protections, signing, packaged tooling, and latest selection there. Then authorize a real prerelease to validate production registry credentials and all build platforms.

Terraform remains a separate validation workflow. It runs for relevant PRs/pushes on `main` and release branches, plus merge groups. Release preparation and publication never run Terraform plan/apply, change state, or provision cloud resources. Existing `image_tag` inputs remain intact; pin the desired published version for managed deployments.

No rollout should be called complete until the production rules, environment restrictions, trusted publishers, and a real prerelease have been checked. Sandbox success does not verify production secrets, cloud deployments, or package registry permissions.
