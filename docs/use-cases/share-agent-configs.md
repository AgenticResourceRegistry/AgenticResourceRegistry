---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Share agent configurations"
description: "Share an exact release or expiring manifest without copying local configuration or credentials."
---

Share the registry identity and selected release—not your local coding-tool files. Teammates authenticate to the same registry, inspect the release, and install compatible resources using their own credentials.

## Before you start

Choose a published agent/version the recipient may access. Team-private agents require membership; a link does not grant it. Match the declared harness support and actual component capabilities to the recipient's tool.

## Share the qualified name

Send the actual `namespace/slug`, exact version, intended task, and harnesses you have checked. For example, `platform-tools/team-reviewer` and `1.0.0` are clearer than “use reviewer.” These examples are placeholders for your returned identity/release:

```bash
observal agent show platform-tools/team-reviewer --output json
```

The default table omits full instructions/components. Follow [Inspect an agent](/guides/inspect-an-agent) for the exact version detail, lock, and component content; current listing data alone is not the selected release.

## Preview and pull

In the recipient's target project, use the same explicit version and integrity policy for both steps:

```bash
observal agent pull platform-tools/team-reviewer --harness claude-code --scope project --version AGENT_VERSION --strict --dry-run
observal agent pull platform-tools/team-reviewer --harness claude-code --scope project --version AGENT_VERSION --strict
```

Replace `AGENT_VERSION` with the reviewed version. Inspect planned files, component pins, required values, setup commands, and warnings before applying. Enter required secrets interactively; non-interactive workflows must supply approved secure inputs and stop if values are missing. A preview is not proof that all apply-time filesystem/setup checks will pass.

Activate using the exact generated local name in the [target integration guide](/integrations/index). Formats, scope options, resource support, and activation are harness-specific; Copilot CLI, for example, rejects an explicit `--scope` option. Native activation is a separate checkpoint, not an effect inferred from installed files.

## Create an expiring share link

Use a share manifest when you want to select one or more **already installed project agents**. First inspect candidates from the repository:

```bash
observal share candidates --dir ./service --output json
observal share create --dir ./service --agent platform-tools/team-reviewer --expires-days 7 --output json
```

Replace the directory and identity with actual candidates. `--agent` filters by identity, not version: if that identity has multiple installed versions across the repository, it selects each matching pair. Inspect the candidates first; use interactive `observal share` to select the intended pair, or send the qualified name and exact version directly. Do not fabricate/edit tracking entries to force a selection.

Candidate discovery reads project-agent entries in the active registry's local installation lockfile, across harnesses. A registry listing or `observal.lock` alone is not a candidate; user-scope installs are excluded. The result contains an opaque token, URL, expiry, and item count. Treat token/URL/JSON as sensitive, and keep the optional share title free of private repository details.

The manifest pins each selected agent version. It does not upload generated configuration, credentials, Git remotes, or automatically discovered repository paths. It does not move its entries to a newly published release.

## Open a share without installing

The recipient signs in to the same deployment and inspects the link:

```bash
observal share open SHARE_LINK_OR_TOKEN --no-pull --output json
```

Share URLs are CLI inputs, not currently a web share page. Full URLs must match a configured API/web origin. If the deployment's frontend URL is misconfigured, ask the operator to correct it; a bare token supplied by the trusted sender for this same deployment is also accepted. The CLI fetches through its configured registry, not an arbitrary supplied host.

Check returned versions, support, and `unavailable_count`. Inaccessible resources are counted without revealing their identities. The manifest's creator/title may still be visible; do not use titles to reveal private information. Membership, approval state, expiry, and revocation are checked on access.

JSON `share open` is inspection-only, even without `--no-pull`. For a controlled first install, inspect each release and use the explicit preview/pull sequence above with the manifest's version. Human-mode `share open` can prompt and delegate pulls, but it has no `--strict`, `--scope`, or preview passthrough; do not mistake that shortcut for the reviewed strict plan. A multi-agent apply may partially succeed.

## Share versions, not secrets

Commit the reviewed project `observal.lock` when teammates should retain the same agent release. Each teammate still needs registry access, harness/provider authentication, and required component values. Never commit authentication files, generated credentials, or someone else's local installation lockfile.

New component releases do not intentionally move an existing agent's pins; an author must release a new consuming agent. Review an upgrade deliberately rather than treating a notification or share link as permission to adopt “latest.” A pull may configure managed capture/delegation entries as well as explicit components: inspect them and their data implications.

## If a teammate cannot install it

Check registry connection, exact identity/version, team membership/publication, harness capabilities, required values, and setup failures. For `Server component pin disagrees with the install lock.`, stop and use the [known server limitation](/guides/version-components#known-install-response-limitation). Removing strict mode does not bypass that check; a share link is not a workaround.

A missing candidate needs a successful project install in this registry section, not a fabricated lock entry. A revoked/expired share needs a newly authorized link. The creator or an administrator can [revoke a link](/cli/share#revoke); this stops future opens, not installations already made or credentials already disclosed.

## Next

[Manage team access](/use-cases/teamspaces), [fork for another workflow](/use-cases/fork-and-customize), or [review update notices in Inbox](/guides/manage-inbox).
