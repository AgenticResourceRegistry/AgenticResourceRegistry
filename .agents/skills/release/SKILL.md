---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
name: release
description: Cut and maintain Agentic Resource Registry release branches, prepare alpha, beta, RC, stable, and patch releases, backport merged main PRs, inspect release status, verify published artifacts, and recover failed release operations. Use for Agentic Resource Registry maintainer release tasks and release-process changes, not for installing or upgrading an existing Agentic Resource Registry deployment.
license: Apache-2.0
metadata:
  repository: AgenticResourceRegistry/AgenticResourceRegistry
  policy: docs/self-hosting/releasing.md
---

# Agentic Resource Registry release operations

This is a repository-local maintainer skill, not a bundled end-user skill or an `observal` CLI command. Read the [release guide](../../../docs/self-hosting/releasing.md) completely before acting. Use the existing [release tool](../../../tools/release.py); do not implement a second release pipeline.

Resolve these links from the skill directory. Run commands from the Agentic Resource Registry checkout root. Examples use `release/1.14` and PR `123`; substitute the explicitly requested line and PR, never treat examples as authorization.

## Safety and authority

- Obtain approval before remote branch creation, pushes, PR creation, merges, workflow retries, or publishing, unless the current request explicitly authorizes that action. Preparing a PR does not authorize merging it or approving a production environment.
- Explain before merging a preparation PR that it triggers publication, subject to the workflow approval gate. The newest verified stable release also triggers hosted deployment.
- Never publish from `main`, merge a release branch back into `main`, rebase a release line onto `main`, force-push published history, or move an existing tag.
- `main` carries the next minor `X.Y.0-dev.0` version, never a published release version. Release versions, changelog, and `.release.toml` live only on release branches.
- Land ordinary fixes on `main` first. Each affected release line gets its own reviewed backport PR with `cherry-pick -x` provenance.
- Never bypass failed checks, reviews, signing, or checksum verification. Never expose tokens, environment files, GitHub App keys, or OIDC material.
- Production rulesets, environment restrictions, trusted publishers, deployment changes, and Terraform apply require separate authorization. This skill does not change infrastructure or provision resources.
- Do not run `uv publish`, `gh release create`, or `git tag` as a substitute for the release workflow.

## Inspect first

Read repository instructions, the release guide, and the [release workflow](../../../.github/workflows/release.yml). Confirm Git, GitHub CLI, and `uv` are available:

```bash
git status --short --branch
git remote
gh auth status
uv --version
uv run python tools/release.py --help
```

Verify remote destinations without printing embedded credentials. By default, `upstream` is `AgenticResourceRegistry/AgenticResourceRegistry` and `origin` is the maintainer fork. For a canonical-only clone, append `--upstream origin --fork origin` where appropriate. Confirm the destination before any write; a sandbox is not the production repository.

Cutting and backporting require a clean local `main` matching canonical `main`. Preparation requires a clean local `release/X.Y` matching that canonical release branch. Do not stash, reset, discard changes, or switch a dirty worktree automatically.

For the first release under this process, verify the guide's one-time rollout checklist. The checked-in ruleset template is not proof that protections are installed. Confirm `release/*` is permitted in the production environment and publishing identities remain valid. Stop and report missing configuration instead of changing it implicitly.

## Choose the operation

### Inspect status

```bash
uv run python tools/release.py --status
```

On `main`, this lists canonical release lines. On a release branch, it shows the latest reachable tag and unreleased commits. Status fetches remote Git metadata but does not publish.

### Cut a new minor line

After approval, update local `main` by fast-forward only, then run:

```bash
uv run python tools/release.py --cut 1.14
```

The tool creates `release/1.14` at exact canonical `main`. It rejects existing or older series and does not publish a release. It then opens a PR on `main` titled `chore: start 1.15 development`, advancing the version files and lockfiles to `1.15.0-dev.0` so `main` never looks older than the line it feeds. That PR needs the same approval as any `main` PR, is never backported, and the release branch is never merged into `main` to achieve it. If the bump PR step fails after the branch is created, the line still exists; rerun nothing blindly, inspect the preserved `.worktrees/main-1.15.0-dev.0` and open the PR from it. Verify the resulting remote SHA and branch protection. Fetch and check out that line before preparing a release. Maintain old lines in place rather than recutting them from current `main`.

### Backport a fix

From current canonical `main`, after the source PR has been rebase-merged:

```bash
uv run python tools/release.py --backport 123 --to release/1.14
```

The tool creates `backport/1.14/123` in an isolated worktree, identifies the merged mainline commits, cherry-picks them with `-x`, and opens a PR against `release/1.14`.

Review `Backport-of`, destination, original SHAs, DCO trailers, the actual diff, and line compatibility. PR linkage matters because rebase merges change commit hashes. Compare patch IDs when useful, but matching provenance alone does not prove semantic equivalence. Required reviewers must examine conflict adaptations.

For several affected lines, backport independently from newest to oldest. A backport is complete only when its destination PR is merged and required checks pass. Never copy an entire newer branch into an older one.

### Prepare or promote a release

On the exact, current release branch, preview the requested channel first:

```bash
uv run python tools/release.py --preview --channel beta
```

After approval, prepare interactively or specify the requested stage:

```bash
uv run python tools/release.py
uv run python tools/release.py --channel alpha
uv run python tools/release.py --channel beta
uv run python tools/release.py --channel rc
uv run python tools/release.py --channel stable
```

These are alternatives, not a batch of commands to execute. Use `--version` only for an approved explicit version within the line. Use `--yes` only when the channel and default public-note selection have been approved; it creates and pushes a PR without prompting. Adjust public notes non-interactively with repeatable `--include-pr N`, `--exclude-pr N`, and `--highlight-pr N`, `--breaking-pr N`, `--title-pr N=TITLE`, and `--category-pr N=CATEGORY` (database migration PRs must be included). Never combine preparation flags with `--cut`, `--backport`, or `--status`.

All code on the release branch ships. The picker curates public notes, not a code cutoff. Include database migrations in the notes. Do not manually change generated versions, lockfiles, or the release manifest to evade validation.

## Channels and aliases

| Stage | Version | Shared Docker alias |
| --- | --- | --- |
| Alpha | `1.14.0-alpha.1` | `alpha` |
| Beta | `1.14.0-beta.1` | `beta` |
| RC | `1.14.0-rc.1` | `next` |
| Stable | `1.14.0` | `latest`, only if newest stable |

Repeated stages increment their serial. Alpha to beta to RC to stable is forward movement; going backward within the same version core is rejected. After stable, preparation starts the next patch. An RC-to-stable promotion creates a new version, never moves an existing tag.

Older stable lines use `lts-X.Y`. Older prereleases use aliases such as `next-X.Y` or `beta-X.Y` when a newer version exists in the same channel. No old line may move a shared alias backward. PyPI uses PEP 440 versions and has no dist-tags; Helm publishes exact chart versions, not aliases.

For a stable bug: merge the main fix, merge its backport, prepare a patch RC if requested, then prepare stable when approved. For a beta bug: merge the main fix and backport, then prepare another beta. Do not skip straight to stable merely because checks pass.

## Review the preparation PR

The tool creates `prepare/vX.Y.Z[-channel.N]` targeting `release/X.Y`, never `main`. Inspect:

- `.release.toml`: branch, version, channel, previous reachable tag, and `source_sha` matching the preparation commit's only parent.
- Only the tool's `RELEASE_FILES` changed: package versions, Python lockfiles, changelog, manifest, and release notes.
- Intended backports are merged, notes and comparison links are accurate, migrations are documented, and changelog history is preserved.
- Required checks and reviews pass. Rebase-merge only after explicit approval.

If the destination advances, regenerate preparation from its new tip. Simply rebasing stale metadata is not sufficient. Preserve existing worktrees and branches for inspection before any explicitly authorized cleanup.

For release-process changes, run the focused tests first, then repository checks as warranted:

```bash
uv run pytest tests/test_release.py tests/test_helm_oci_release.py tests/release -q
make lint
make test
```

For an actual release, require the destination PR's CI and reviews. Never infer readiness from tests run on a different branch or from sandbox success.

## Publication and verification

Merging the preparation PR triggers `release.yml`. It validates the exact commit, builds artifacts, waits for approval, signs the tag with the release-branch identity, publishes registries, verifies public artifacts, and promotes eligible stable releases. Cutting a line or merging an ordinary backport does not publish.

Inspect the run associated with the exact release commit:

```bash
gh run list --repo AgenticResourceRegistry/AgenticResourceRegistry --workflow release.yml --branch release/1.14 --limit 5
gh release view v1.14.0 --repo AgenticResourceRegistry/AgenticResourceRegistry
```

Follow the [verification guide](../../../docs/security/release-verification.md) for downloaded checksums, provenance, and the signed tag. Download into a fresh directory outside the checkout. Verify the expected Python, npm, Docker, and Helm versions, and smoke-test the downloaded or installed artifact when execution is authorized. Never substitute a source-only test for an artifact check.

Check promotion eligibility and the actual shared aliases after publication. For the newest stable release, report the deployment job too; for maintenance or prerelease versions, confirm they did not replace the newer stable release. Production registry credentials and deployments remain unverified by a dummy repository test.

## Recovery

- **Preparation failure:** inspect the preserved `.worktrees/release-vX.Y.Z[-channel.N]`. Do not blindly rerun or delete it.
- **Backport conflict:** inspect `.worktrees/backport-X.Y-PR`, resolve the exact sequence with review, run relevant tests, and continue the cherry-pick. Never silently skip a commit. Inspect remote state before pushing or creating a PR after an interrupted operation.
- **Publication failure:** inspect the failed job and existing tag, assets, and registry versions before requesting a retry. Never overwrite a published version or tag. A tag must identify the validated commit and verify with its exact branch signing identity.
- **Manual recovery:** select the same `release/X.Y` branch for `release.yml` and supply the full validated release commit SHA as `target`. Obtain approval before dispatching. Never retry on `main` or substitute a newer branch tip.
- **Newer version already released:** do not force an old preparation through progression checks or move aliases manually. Report the state and prepare an approved correction as a new version.

## Completion report

Report the branch, version/channel, main and backport PRs, preparation PR, exact release commit/tag, workflow and release URLs, actual registry aliases, checks and artifact verification performed, and promotion/deployment outcomes where applicable. State anything skipped, blocked, or unverified. Do not call a preparation PR a published release.
