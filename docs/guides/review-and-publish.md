---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Review and publish a submission"
description: "Inspect a submitted revision, approve it, and confirm its release is published."
---

Review is the checkpoint between an author's submitted configuration and a release other users can install. Use this guide to inspect a submission and determine whether it is ready to publish.

## Before you start

You need an eligible reviewer account and access to the submission. Public reviews normally require a global reviewer or administrator; teamspace policy determines who can review team resources. The author may not be allowed to approve their own work.

For a local evaluation with default demo accounts, the ordinary user submits the agent and `reviewer@demo.example` reviews it. Use separate browser sessions or sign out before switching accounts. Demo credentials are for local evaluation only.

## Open the submitted revision

Follow the review URL returned after submission, or find it in the web UI's review queue. Confirm the resource, version, author, and revision; do not assume a fixed review number.

Use **Files changed** to inspect the instructions and configuration. Check component pins, executable hooks, MCP commands/endpoints, and any requested credentials or permissions. A change summary is not a substitute for reading the content.

After inspecting **Files changed**, return to **Conversation** to find the **Publication gate**. It shows required approvals and integrity checks. v1.16.0 has advisory edit-lock/thread/change-request conditions for human publication; later linked-request/withdrawal controls are not available in this release. Inspect the actual warnings and deliberately resolve conversations rather than assuming every displayed condition is an enforced block. Pinned components may need publication first. Resolve actual blockers rather than weakening policy to make a first install succeed.


## Approve in the web UI

<Warning>
  **The v1.16.0 web flow has the same inspected-revision race as the CLI.** An author update between inspection and approval can become the approved head, and policy can publish it automatically before a later check. Coordinate a pause on edits through publication and recheck the revision immediately before approving; this is not an atomic guard. Compare the recorded approval and current head with the inspected revision before manual publication. If this race is unacceptable, do not approve until the operator/reviewer provides an acceptable process. Use the [CLI detail checks](#cli-alternative) if the UI does not expose sufficient revision information. If a mismatch is already published, escalate rather than assuming approval dismissal or a post-check undoes it.
</Warning>

1. Select **Review changes**.
2. Choose **Approve** after inspecting the current revision.
3. Add a concise review summary and select **Submit review**.
4. Read the updated publication gate and review state.

An approval is recorded; whether it counts toward the gate depends on the effective policy, author relationship, and revision. It does not necessarily publish the release: policy may require more counted approvals, passing checks, or an explicit publication action. A new submitted revision can make an older approval outdated.

## Publish when the gate permits it

If policy did not publish automatically, return to **Conversation**. In the publication gate, an authorized reviewer selects **Publish vVERSION** and confirms the action. The v1.16.0 UI should then show the **Published** review state. Confirm the selected release in the registry rather than expecting the newer UI's exact confirmation sentence.


| State | Meaning for this workflow |
| --- | --- |
| Pending/open review | Submitted, not yet published for the approved install path. |
| Approved review | Approval recorded; publication may still be outstanding. |
| Published review | Release made available through the registry's publication workflow. |

Do not rely only on an “Approved” reviewer badge. Check the publication result and then return to the installing user's account.

## CLI alternative

<Warning>
  Reviewer login rewrites shared CLI connection/credential state (normally `~/.observal/config.json`) and may clear the previous hooks credential. `--no-setup` does not preserve that saved state. Use approved isolated CLI state when you must retain another account's automation credentials, or explicitly coordinate the account switch and restore the ordinary-user session and required hook setup afterward. Do not copy credential-bearing configs to an unprotected backup. A browser account switch does not switch or restore the CLI account.
</Warning>

Use the **same reviewer identity and registry** in the CLI as in the browser when checking a web review. `my_last_submission` is scoped to the current CLI caller, not whoever last reviewed in your browser. Confirm `observal auth status` first and verify its configured server URL is the API for the browser registry; matching account names alone do not confirm the deployment. Distinct API/frontend hosts are valid only with the operator-confirmed deployment mapping. Also verify `observal auth whoami`. If either the server or reviewer differs, sign in as the intended reviewer at the correct registry URL before inspecting approvals. In the review shell, restore the recorded evaluation port/origin before signing in. For the documented localhost layout, reconstruct `EVAL_URL="http://localhost:${LB_HOST_PORT:?Restore the recorded evaluation LB port}"` from the recorded port, compare it with the evaluation browser URL, then use `observal auth login --server "${EVAL_URL:?Restore and verify the evaluation origin}" --no-setup`. Never supply an unset/empty server value that can fall back to port 80. Then verify both status and whoami. Do not mistake missing or unrelated caller approval data for the browser reviewer's status. Before signing in, resolve conflicting approved `OBSERVAL_SERVER_URL`, `OBSERVAL_ACCESS_TOKEN`, `OBSERVAL_ACCESS_TOKEN_FILE`, `OBSERVAL_TOKEN`, or historical `OBSERVAL_API_KEY` overrides in the dedicated review shell without printing secrets. These can override saved registry/credential selection; login does not rewrite an external token file. After login, rerun **both** `observal auth status` and `observal auth whoami` before reading approval details. Do not clear another workflow's credentials indiscriminately.

Replace `REVIEW_NUMBER` with the actual review number:

```bash
observal review show REVIEW_NUMBER --output json
observal review files REVIEW_NUMBER --output json
observal review diff REVIEW_NUMBER --output json
observal review gate REVIEW_NUMBER --output json
```

Stop and inspect the returned content, revision, and gate. `review files` is only a file list.

<Warning>
  **v1.16.0 approval is not bound to your inspected revision.** The server records the current review head when `approve` executes; it can differ from the revision you read if the author submits another update. Coordinate a pause on edits, re-read the current revision immediately before approval, and fetch review details again and compare the recorded approval revision with your inspected one afterward. This coordination is not an enforced atomic guard. Effective policy can publish automatically as approval is recorded, before a post-check; if that race is unacceptable, do not approve until the operator/reviewer provides an acceptable process. A later comparison cannot prevent an already published wrong revision. Do not weaken policy or assume advisory edit locks solve this.
</Warning>

After confirming the unchanged revision under that process and deciding it is acceptable, record approval separately:

```bash
observal review approve REVIEW_NUMBER --output json
```

The v1.16.0 approval response contains only `id`, `state`, and `verdict`; it does not include a revision or gate. Fetch the details again:

```bash
observal review show REVIEW_NUMBER --output json
```

Require **both** `my_last_submission.revision_id` and the freshly fetched `head_revision_id` to equal the revision ID recorded during inspection. A matching approval alone is insufficient: policy may count a stale approval toward a newer head. Missing approval metadata is not proof of approval. Read the fetched `gate` and `state` too, and keep the coordinated pause on edits through publication. These comparisons remain best-effort checks, not an atomic publication guard.

If either revision differs, do not publish or treat old inspection as review of new content. Notify the author/operator and coordinate a process that prevents publication before an administrator or authorized team owner dismisses the incorrect approval using the `id` returned by `approve`. Do not blindly dismiss it: dismissal can make an earlier approval count again and trigger automatic publication. Dismissal is unavailable once the review is published. If already published, escalate for an approved corrective-release/access procedure; this guide does not supply a rollback or claim that a later check undoes publication. Re-inspect the intended revision under the agreed process before another approval.

The auto-publication race warning still applies. If the inspected revision, approval revision, and current head agree, publication remains outstanding, and the acceptable coordinated process is still in place, an authorized reviewer publishes separately:

```bash
observal review publish REVIEW_NUMBER --output json
```

These commands are under `observal review`, not `observal admin review`. `review files` lists changed files; inspect the `review diff` output for their content before approving. Use `observal review --help` for per-file diffs, comments, and requested changes.

## Confirm ordinary-user access

First restore/sign in as the intended ordinary installing user at this same verified registry, then rerun `observal auth status` and `observal auth whoami`. Re-establish any required hook setup through the approved workflow before continuing; do not run this check while still signed in as the reviewer.

As that verified ordinary user:

```bash
observal agent list
```

The published agent should be visible if that account has the necessary access. Publication does not give every user permission to a private teamspace resource. Continue by [inspecting and pulling the release](/getting-started/quickstart).

If approval is outdated, required checks fail, or the publication button remains unavailable, inspect the gate and resolve its stated reason. Do not use an override merely to complete the guide.
