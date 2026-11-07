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

After inspecting **Files changed**, return to **Conversation** to find the **Publication gate**. It shows required approvals and blocking checks. Pinned components may need publication first. Resolve actual blockers rather than weakening policy to make a first install succeed.

<Frame>
  <img src="/img/docs-renewal/review-files-desktop.jpg" alt="Files changed for a synthetic first-reviewer agent, showing its agent configuration, empty component list, and prompt for inspection." />
</Frame>

This local evaluation example has no third-party components. A real submission may have additional executable files and pinned dependencies to inspect.

## Approve in the web UI

1. Select **Review changes**.
2. Choose **Approve** after inspecting the current revision.
3. Add a concise review summary and select **Submit review**.
4. Read the updated publication gate and review state.

An approval counts toward the gate. It does not necessarily publish the release: policy may require more approvals, passing checks, or an explicit publication action. A new submitted revision can make an older approval outdated.

## Publish when the gate permits it

If policy did not publish automatically, return to **Conversation**. In the publication gate, an authorized reviewer selects **Publish vVERSION** and confirms the action. The UI should then show **Published vVERSION** and state that the release is available in the registry.

<Frame>
  <img src="/img/docs-renewal/review-published-desktop.jpg" alt="A synthetic agent review after publication, with the Published v1.0.0 confirmation and approval information." />
</Frame>

| State | Meaning for this workflow |
| --- | --- |
| Pending/open review | Submitted, not yet published for the approved install path. |
| Approved review | Approval recorded; publication may still be outstanding. |
| Published review | Release made available through the registry's publication workflow. |

Do not rely only on an “Approved” reviewer badge. Check the publication result and then return to the installing user's account.

## CLI alternative

Replace `REVIEW_NUMBER` with the actual review number:

```bash
observal review show REVIEW_NUMBER --output json
observal review files REVIEW_NUMBER --output json
observal review gate REVIEW_NUMBER --output json
observal review approve REVIEW_NUMBER --output json
observal review publish REVIEW_NUMBER --output json
```

These commands are under `observal review`, not `observal admin review`. Read the files/diff before approving. Use `observal review --help` for per-file diffs, comments, and requested changes.

## Confirm ordinary-user access

As the intended installing user:

```bash
observal agent list
```

The published agent should be visible if that account has the necessary access. Publication does not give every user permission to a private teamspace resource. Continue by [inspecting and pulling the release](/getting-started/quickstart).

If approval is outdated, required checks fail, or the publication button remains unavailable, inspect the gate and resolve its stated reason. Do not use an override merely to complete the guide.
