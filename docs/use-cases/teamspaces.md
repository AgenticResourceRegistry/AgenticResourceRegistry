---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Work in a teamspace"
description: "Join a shared namespace, verify access, and publish through its review policy."
---

A teamspace gives a group a shared namespace and access boundary. For example, `platform-tools/team-reviewer` belongs to `platform-tools`, not the individual who submitted it. Team handles and usernames cannot collide.

## Before you start

[Sign in to the same registry](/getting-started/onboarding) as your teammates. You need an account before requesting membership. A teamspace invitation requests access; an agent share link selects releases. Neither replaces registry authentication.

## Creating a teamspace

Start private unless you intend to publish resources outside the team:

```bash
observal team create 'Platform Tools' --handle platform-tools --visibility private --output json
observal team show platform-tools --output json
```

The creator becomes an owner. Save the returned ID and handle; check `visibility` and your role, not just the display name. A private teamspace is not a public catalog. Creating one does not provision accounts, model credentials, or harness configuration.

## Membership

For a **public** teamspace, request membership and inspect your request:

```bash
observal team request join platform-tools --message 'I maintain the deployment tools' --output json
observal team request mine platform-tools --output json
```

For a **private** teamspace, ask an owner for an invitation. Owners can create a bounded invitation:

```bash
observal team invite create platform-tools --name onboarding --expires-days 7 --max-uses 5 --output json
```

The result includes a secret token and URL. Send it through an approved private channel, not a ticket, screenshot, repository, or public log. CLI token arguments can appear in shell history/process inspection; use the approved web flow or controlled secret input, and do not paste real tokens into examples.

A signed-in recipient previews the invitation, then requests access:

```bash
observal team invite preview INVITE_TOKEN --output json
observal team invite request INVITE_TOKEN --message 'Joining the deployment rotation' --output json
observal team invite preview INVITE_TOKEN --output json
```

Replace the token placeholder privately. Preview is read-only. Check `valid`, `invite_state`, `is_member`, and the returned `request.status`. A pending request is **not membership**. For private teams, `team request mine HANDLE` can remain not-found while you are outside the visibility boundary; use the invitation preview to inspect its request state, or ask the owner. Do not treat a hidden team as permission to override access checks.

An owner lists and decides requests:

```bash
observal team request list platform-tools --status pending --output json
observal team request approve platform-tools @REQUESTER_USERNAME --output json
observal team members list platform-tools --output json
```

Replace the requester with the exact returned username or email. After approval, the recipient checks `team show platform-tools --output json` for actual membership. Owners can also [add an existing account directly](/cli/team#members). Choose the least role needed.

## Roles

Teamspace roles and server roles are different:

| Team role | Intended responsibility |
| --- | --- |
| `member` | Access team resources and submit into the namespace |
| `reviewer` | Also review authorized team submissions |
| `owner` | Also manage membership, invitations, visibility, and team resources |

Deployment admins have broader authority. A global reviewer alone does not grant access to another team's private resources. The last owner cannot leave, be removed, or be demoted without retaining another owner.

### Offboarding

Removing membership restricts subsequent access to team-private resources according to current visibility and any other authorized roles. It does not erase already downloaded profiles/component files or previously disclosed content, and it does not revoke independently supplied MCP/provider credentials. Credential revocation or rotation and managed local cleanup are separate actions. Team membership is not a remote kill switch for an installed agent.

## Publishing and review

A private teamspace accepts `team` visibility, not public resources. For a minimal agent submission:

```bash
observal agent create --name team-reviewer --description 'A shared review starting point' --prompt 'Read the requested file and summarize questions before proposing changes. Do not edit files.' --harness claude-code --team platform-tools --visibility team --output json
```

Expect a qualified name such as `platform-tools/team-reviewer`, a version, and review information. This example is instructions, not a tested security boundary. Inspect the actual returned identity and candidate, then follow [review and publication](/guides/review-and-publish).

Team membership does not automatically approve a submission. Required approvals, self-approval counting, and automatic publication depend on the effective review policy; zero required approvals can allow automatic publication when enabled and the resource's other gates permit it. Team owners/reviewers can record a decision on their own submission, but whether it counts toward publication depends on that policy. Permission to approve is not proof of independent approval or a satisfied gate. Check `review gate REVIEW_NUMBER --output json` and the final publication state. Use a separate reviewer when your policy requires one.

## Public visibility is a separate review

Creating a public teamspace or requesting a private-to-public change reserves the handle but leaves the team private and locked pending visibility review. Do not try to publish through that lock. A global reviewer or administrator decides the request. Approval revokes private invitation links; review the access implications before requesting it.

```bash
observal team visibility set platform-tools public --output json
```

Do not run this merely to solve a teammate's access problem. Grant appropriate membership instead. A public team cannot become private while it owns public agents, components, or component sources. Personal teamspaces stay private and have stricter creator/owner restrictions.

## If something fails

| Symptom | Next step |
| --- | --- |
| Handle unavailable | Choose a unique namespace; user and team handles share the identity space. |
| Private team not found before joining | Check the invitation preview and owner approval, not only the handle. |
| Invite preview says invalid/expired/revoked/exhausted | Ask for a fresh authorized invitation. Preview may succeed as a command while reporting `valid: false`. |
| Publication locked pending visibility review | Wait for the visibility decision; do not bypass the gate. |
| Approval exists but publication is blocked | Inspect the effective review gate, threads, checks, and counted approvals. |
| Cannot remove/leave the last owner | Retain another authorized owner before changing membership. |

## Next

[Share an exact agent release](/use-cases/share-agent-configs), [work through Inbox](/guides/manage-inbox), or consult the [team command reference](/cli/team) for rejection, withdrawal, revocation, and role changes.
