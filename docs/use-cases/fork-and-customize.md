<!-- SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Fork and customize an agent or component

Fork an **approved release** when you want to build on a registry item without editing its owner's copy. A fork is a new listing with its own owner, namespace, releases, reviews, downloads, ratings, and telemetry. Agent forks retain **pinned references** to approved components; they do not fork those components.

## In the web UI

1. Open an approved agent or component and select **Fork**. Choose an approved base version, a new name, and your namespace or an eligible teamspace. A private team source can only be forked into the same private teamspace.
2. The new item opens as your **draft**. An agent opens in the Builder; a component opens in its draft editor. Edit and save it before submitting for review.
3. On the detail page or in the review queue, select **Diff vs upstream** to compare your version with the approved base. **Unchanged from upstream** means tracked content has not changed. A version number, validation state, or a secret environment/header value alone does not count as an install-content change.
4. Submit the draft through its normal review workflow. Approval makes a public fork appear in the source's **Forks** tab and count. Drafts and team-private forks never appear there.

If you later lose access to the source, provenance reads **Source unavailable**. The comparison will no longer be available; Observal does not expose a saved source name or base version as a fallback.

## From the CLI

```bash
observal agent fork acme/pr-reviewer --name my-reviewer --dir ./my-reviewer --output json
# Edit ./my-reviewer/observal-agent.yaml, then update the *fork*, not the source:
observal agent publish --update --dir ./my-reviewer
observal agent publish --submit <fork-id>

observal registry skill fork-listing acme/review-skill --name my-review-skill --output json
# Edit and submit the resulting skill draft through its normal registry workflow.
```

The `--dir` scaffold stores the fork's ID, so subsequent `publish --update` calls target that draft rather than creating another listing. If a name is already taken, choose another; the existing item is never overwritten.

For scripted integrations, use `POST /api/v1/agents/{id}/fork` or `POST /api/v1/{type}/{id}/fork` (`type` is `mcps`, `skills`, `hooks`, `prompts`, or `sandboxes`). `GET …/{id}/fork-diff?version=…` compares a visible fork version with its approved, currently accessible base. ARD search can filter currently **public and approved** direct forks using `query.filter["obs:forksOf"]` set to the upstream's `urn:air:` identifier; public results then include `obs:forkedFrom`. Private and unpublished lineage is never exposed through this facet.
