---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal team"
---

Create and govern teamspaces, members, visibility reviews, join requests, and private-team invitations.

Every leaf command supports `--output table|json`. JSON success output contains no Rich text or prompts. JSON failures leave stdout empty and write one categorized error object to stderr.

## Commands

| Command | Purpose |
| --- | --- |
| `list` | List the signed-in user's teamspaces |
| `show` | Show a teamspace and its members |
| `create` | Create a teamspace |
| `claim-personal` | Claim or return the caller's personal teamspace |
| `delete` | Permanently delete a teamspace |
| `leave` | Leave a teamspace |
| `visibility set` | Change visibility or request public review |
| `visibility list-requests` | List pending public visibility requests |
| `visibility approve` | Approve pending public visibility |
| `visibility reject` | Reject pending public visibility |
| `request join` | Request membership |
| `request list` | List a teamspace's join requests |
| `request mine` | View the caller's request history and status |
| `request withdraw` | Withdraw the caller's pending request |
| `request approve` | Approve a pending request |
| `request reject` | Reject a pending request |
| `members list` | List members |
| `members add` | Add a member or update a role |
| `members remove` | Remove a member |
| `invite create` | Create a private-team invitation |
| `invite list` | List invitations |
| `invite revoke` | Revoke an invitation |
| `invite preview` | Preview an invitation token |
| `invite request` | Use a token to request access |
| `invite delete` | Delete an unused invitation |
| `invite requests` | List requests associated with an invitation |

Team references may be UUIDs, handles, or `@handle`. Unknown teamspaces use not-found exit code 5.

## List, show, and claim

```bash
observal team list --output json
observal team list --all --output json
observal team show platform-tools --output json
observal team claim-personal --output json
```

`list` returns the standard `items`, `total`, `page`, and `page_size` envelope. The default includes teamspaces where the user is a member. `--all` requests all teamspaces visible to the caller. Empty results use `items: []` and `page_size: 0`.

`show` returns a combined result:

```json
{
  "team": {
    "id": "11111111-1111-1111-1111-111111111111",
    "name": "Platform Tools",
    "handle": "platform-tools",
    "role": "owner"
  },
  "members": []
}
```

`claim-personal` is idempotent. It creates or returns the caller's one private personal teamspace and returns the direct Team object.

## Create and visibility

```bash
observal team create 'Platform Tools' \
  --handle platform-tools \
  --description 'Internal tooling' \
  --visibility private \
  --output json

observal team visibility set platform-tools public --output json
```

Visibility is `public` or `private`. Creating a public teamspace or setting a private teamspace to public submits a review request. Until approval, the response reports `visibility: "private"` and `visibility_request_status: "pending"`.

Create and visibility JSON return the direct Team object.

Reviewers and deployment admins manage pending public visibility requests:

```bash
observal team visibility list-requests --output json
observal team visibility approve platform-tools --output json
observal team visibility reject platform-tools \
  --reason 'Add a public description' \
  --output json
```

The list returns the standard list envelope. Approve and reject return the direct Team object. Approval makes the teamspace public and revokes its private invitation links. A rejection reason is optional and accepts up to 500 characters.

## Delete and leave

```bash
observal team delete platform-tools --yes --output json
observal team leave platform-tools --yes --output json
```

Delete is permanent. Leave removes only the caller's membership. The last owner cannot leave. Human mode prompts unless `--yes` is supplied. JSON mode never prompts and requires `--yes`.

Both endpoints currently return an empty JSON object on success.

## Join requests

Request access to a visible teamspace:

```bash
observal team request join platform-tools \
  --message 'I maintain deployments' \
  --output json
```

The message is optional and limited to 500 characters. JSON returns the created join request.

View your own status or withdraw the sole pending request:

```bash
observal team request mine platform-tools --output json
observal team request withdraw platform-tools --yes --output json
```

`request mine` returns the standard list envelope with requests ordered newest first. `request withdraw` finds the caller's pending request and marks it cancelled. Human mode prompts unless `--yes` is supplied. JSON mode requires `--yes` and returns an empty object.

Owners and deployment admins can list and decide requests:

```bash
observal team request list platform-tools --status pending --output json
observal team request approve platform-tools @alice --output json
observal team request reject platform-tools bob@example.com \
  --reason 'Use the SRE teamspace' \
  --output json
```

Valid status filters are `pending`, `approved`, `rejected`, and `cancelled`. Approve and reject select a pending request by exact email or case-insensitive username. A missing pending request uses not-found exit code 5.

The list returns the standard list envelope. Join, approve, and reject return the direct join-request object.

## Members

```bash
observal team members list platform-tools --output json
observal team members add platform-tools alice@example.com --role reviewer --output json
observal team members add platform-tools @bob --role owner --output json
observal team members remove platform-tools @bob --yes --output json
```

Roles are `member`, `reviewer`, and `owner`. Adding an existing member updates the role. The last owner cannot be removed.

Member list returns the standard list envelope. Add returns the saved member. Remove currently returns an empty object.

Human remove prompts unless `--yes` is supplied. JSON remove requires `--yes`.

## Private-team invitations

Create and list invitations:

```bash
observal team invite create platform-tools \
  --name onboarding \
  --expires-days 30 \
  --max-uses 20 \
  --output json

observal team invite list platform-tools --output json
```

`--expires-days` accepts 1 through 365. `--max-uses` accepts 1 through 10,000 or may be omitted for no use limit. Invite names accept 1 through 100 characters.

Create returns the direct invitation object, including the one-time token and URL. Treat both as secrets. List returns the standard list envelope. States include active, expired, exhausted, and revoked.

A recipient can preview the token, then submit an owner-reviewed access request:

```bash
observal team invite preview INVITE_TOKEN --output json
observal team invite request INVITE_TOKEN \
  --message 'I am joining the deployment rotation' \
  --output json
```

`invite preview` does not mutate membership or request state. `invite request` does not grant membership. It creates a pending join request that an owner must approve. Neither command prints the token in human output or errors.

Owners and admins can inspect invitation usage:

```bash
observal team invite requests \
  platform-tools \
  550e8400-e29b-41d4-a716-446655440000 \
  --output json
```

The command returns the standard list envelope for requests associated with that invitation. Empty results use `items: []`.

Revoke an invitation while retaining its audit history:

```bash
observal team invite revoke \
  platform-tools \
  550e8400-e29b-41d4-a716-446655440000 \
  --yes \
  --output json
```

Delete an invitation only when it has no uses or request history:

```bash
observal team invite delete \
  platform-tools \
  550e8400-e29b-41d4-a716-446655440000 \
  --yes \
  --output json
```

The invite ID must be a UUID. Human mode prompts unless `--yes` is supplied. JSON mode requires `--yes`. Revoke returns the invitation object. Delete returns an empty object.

## Exit codes

| Code | Meaning |
| --- | --- |
| 3 | Authentication required or failed |
| 4 | Membership, owner, reviewer, or admin permission denied |
| 5 | Teamspace, invitation, member, or request not found |
| 6 | Handle, membership, owner, visibility, invitation, or request state conflict |
| 7 | Invalid visibility, role, status, UUID, text length, token, or missing JSON confirmation |
| 8 | Rate limit reached |
| 9 | Server unavailable |
| 10 | CLI and server version mismatch |

## Related

* [`observal inbox`](/cli/inbox): view request and visibility decisions
* [`observal agent`](/cli/agent): publish Agents to a teamspace
* [`observal registry`](/cli/registry): publish components to a teamspace
