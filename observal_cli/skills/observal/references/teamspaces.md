<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Teamspace workflows

## Contents

- Discover and create
- Visibility review
- Join requests
- Members
- Private invitations
- Destructive operations

Use team UUIDs or handles returned by JSON. Never select a teamspace or request by table position.

## Discover and create

```bash
observal team list --output json
observal team list --all --output json
observal team show platform-tools --output json
observal team claim-personal --output json
observal team create 'Platform Tools' --handle platform-tools --visibility private --output json
```

`claim-personal` is idempotent. Public creation enters review and can return `visibility: private` with `visibility_request_status: pending`. Report both fields.

## Visibility review

Owners request a visibility change:

```bash
observal team visibility set platform-tools public --output json
observal team visibility set platform-tools private --output json
```

Reviewers and admins decide public visibility:

```bash
observal team visibility list-requests --output json
observal team visibility approve platform-tools --output json
observal team visibility reject platform-tools --reason 'Add a public description' --output json
```

Approval can revoke private invitation links. Verify the returned visibility and review status. Rejection keeps the team private and can include a reason.

## Join requests

A user requests access and inspects their own status:

```bash
observal team request join platform-tools --message 'I maintain deployments' --output json
observal team request mine platform-tools --output json
observal team request withdraw platform-tools --yes --output json
```

Withdrawal automatically finds the caller's sole pending request. JSON withdrawal requires `--yes`.

Owners and admins review requests:

```bash
observal team request list platform-tools --status pending --output json
observal team request approve platform-tools @alice --output json
observal team request reject platform-tools bob@example.com --reason 'Use the SRE teamspace' --output json
```

Approving grants member role. Approve and reject match an exact email or case-insensitive username. Verify the returned request status and resulting membership when approval matters.

## Members

```bash
observal team members list platform-tools --output json
observal team members add platform-tools alice@example.com --role reviewer --output json
observal team members add platform-tools @bob --role owner --output json
observal team members remove platform-tools @bob --yes --output json
```

Roles are `member`, `reviewer`, and `owner`. Adding an existing member updates the role. The last owner cannot leave or be removed.

## Private invitations

Owners create and inspect invitation links:

```bash
observal team invite create platform-tools --name onboarding --expires-days 30 --max-uses 20 --output json
observal team invite list platform-tools --output json
observal team invite requests platform-tools INVITE_UUID --output json
```

Creation returns a one-time token and URL. Treat the entire response as secret. Do not put the token in logs or final prose.

Recipients preview a token and request access:

```bash
observal team invite preview INVITE_TOKEN --output json
observal team invite request INVITE_TOKEN --message 'I am joining the deployment rotation' --output json
```

Preview does not mutate state. Request creates a pending join request and does not grant membership. An owner must approve it.

## Destructive operations

```bash
observal team invite revoke platform-tools INVITE_UUID --yes --output json
observal team invite delete platform-tools INVITE_UUID --yes --output json
observal team leave platform-tools --yes --output json
observal team delete platform-tools --yes --output json
```

Revoke preserves invitation audit history. Delete succeeds only for an unused invitation without request history. Team deletion is permanent. Verify state after every destructive operation.
