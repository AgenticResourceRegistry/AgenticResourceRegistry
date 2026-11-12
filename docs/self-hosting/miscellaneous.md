---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Miscellaneous Settings"
---

Settings that control platform-wide behavior, harness restrictions, and display preferences.

## Appearance

Controls the product name and images shown in the web UI.

| Field | Effect |
|-------|--------|
| Icon | Small logo used in navigation and compact surfaces |
| Wordmark | Optional full logo image that replaces text branding |
| App name | Text fallback used when no wordmark is configured |

**Image formats:** PNG, SVG, ICO, JPEG, and WEBP. Keep files under 2 MB. Transparent images work best across light and dark themes.

**When to set:** Self-hosted deployments that need company branding in the admin UI.

## Harness Allowlist

Choose which harnesses the server returns to web installation controls through
`/api/v1/config/harnesses`. Store the comma-separated IDs in
`misc.harness_allowlist`.

**Affects:** Harness choices in install dropdowns, component install controls and
agent builder controls that consume this endpoint. Agent compatibility declarations
and CLI/API installation validation are separate from this UI selection setting.

| Value | Effect |
|-------|--------|
| _(empty)_ (default) | Return all registered harnesses |
| `cursor,claude-code,pi` | Offer Cursor, Claude Code and Pi |
| `kiro,cursor` | Offer Kiro and Cursor |

**Format:** Comma-separated canonical registry identifiers. Valid identifiers:
`cursor`, `claude-code`, `kiro`, `pi`, `copilot`, `copilot-cli`, `codex`, `opencode`,
`antigravity`, `goose`.

Use exact IDs: this setting does not normalize underscores. Unknown IDs are
ignored; when no valid IDs remain, the endpoint returns all registered harnesses.
Registered capabilities and native compatibility are described in
[supported coding tools](/integrations).

**When to set:** Your deployment standardizes on specific harnesses and wants
installation controls focused on those tools.

**CLI behavior:** `observal agent pull` requires an explicit `--harness`.
The allowlist controls the metadata endpoint, not an installation authorization
boundary.

## Default Harness

Set `misc.default_harness` to pre-select a harness in web installation controls.
The endpoint returns it only when it is a canonical ID in the available list.

| Value | Effect |
|-------|--------|
| _(empty)_ (default) | No configured default; controls choose an available fallback |
| `claude-code` | Pre-select Claude Code when it is available |
| `pi` | Pre-select Pi when it is available |

**Affects:** Web controls using the configured default and the install commands
they display. The CLI still requires an explicit `--harness`. An unknown or
excluded default is returned as unset, so use an ID included in the available list.

## Git Mirror Path

Directory used for cloned repository mirrors during component analysis and source discovery.

| Value | Effect |
|-------|--------|
| _(empty)_ (default) | Use the system temporary directory |
| `/data/git-mirrors` | Store mirrors on a persistent volume |

**When to set:** Multi-instance deployments where repeated clone work should be shared, or production deployments where temporary storage is small.

## Registered Agents Only

Limits telemetry to agents that are registered in the Agentic Resource Registry registry.

| Value | Effect |
|-------|--------|
| `false` (default) | Accept telemetry from any agent, registered or not |
| `true` | Only registered agents are attributed. Unregistered agent activity may be missing from session views |

**Status:** This control is unstable. Enable it only after confirming all team agents are registered and harness patching is current.

**When to enable:** Deployments that want stricter control over which agents produce telemetry, for compliance or cost control.

## Trace Privacy

Restricts trace visibility by user.

| Value | Effect |
|-------|--------|
| `false` (default) | Admins can inspect traces across the deployment according to their role |
| `true` | Users and admins see only their own traces. Super admins retain full visibility |

**When to enable:** Deployments that want trace viewers scoped to each user's own activity while preserving super-admin access for incident response.
