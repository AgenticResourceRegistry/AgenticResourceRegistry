---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Teamspaces"
---

A teamspace is a shared registry namespace with a unique handle such as `platform-tools`. Team handles and usernames cannot collide.

## Roles

Every member has one role:

| Role | Can |
| --- | --- |
| `owner` | Manage members, invites, visibility, and registry content |
| `reviewer` | Approve or reject the teamspace's pending agents and components |
| `member` | View the roster and publish into the teamspace |

Global admins can manage any teamspace. A teamspace always keeps at least one owner.

## Creating a teamspace

Any signed-in user can create a teamspace under **Registry, Teamspaces**. Private teamspaces are ready immediately. A public teamspace reserves its handle but stays private and locked until a global reviewer or above approves it in the Review queue. A global reviewer may approve their own request.

Changing an existing private teamspace to public follows the same review flow. A rejected request keeps the teamspace private and can be submitted again. Personal teamspaces always remain private.

## Publishing and review

Private teamspaces can publish only team-private agents and components. Teamspace submissions never auto-approve.

After a teamspace is approved for public visibility, its owners and team reviewers may approve or reject all content in that namespace, including public content and their own submissions. Global reviewers continue to review public content across the registry. Public submissions notify both groups, while team-private submissions notify only that teamspace's owners and reviewers. Global admins may still review all content.

A public teamspace cannot become private while it owns public agents, components, or component sources. Restrict or remove those items first.

## Membership

Owners and global admins can add users and assign roles. Public teamspaces accept join requests. Private teamspaces use expiring invite links, and their owners decide the resulting requests. Members can leave unless they are the last owner.
