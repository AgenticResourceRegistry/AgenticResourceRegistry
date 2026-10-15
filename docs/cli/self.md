<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# `observal self`

Inspect or change the installed CLI version.

All four workflows support `--output table|json`. JSON upgrade, downgrade, and rollback require `--force` and never prompt.

## Commands

| Command | Purpose |
| --- | --- |
| `upgrade` | Install the latest or a specified newer CLI release |
| `downgrade` | List releases or install an older release |
| `rollback` | Restore the standalone binary saved before the last version change |
| `status` | Show current version, install method, and update availability |

The destructive uninstall workflow has been removed.

## Status

```bash
observal self status --output json
```

JSON returns:

```json
{
  "current_version": "2.4.0",
  "install_method": "uv_tool",
  "path": "/home/user/.local/bin/observal",
  "writable": true,
  "managed_by": "uv",
  "github_available": true,
  "latest_version": "2.5.0",
  "update_available": true
}
```

The command always checks GitHub. An unreachable GitHub service remains a successful local status result with `github_available: false`, `latest_version: null`, and `update_available: null`.

## Upgrade

Install the latest stable release:

```bash
observal self upgrade --force --output json
```

Install a specific release:

```bash
observal self upgrade --version 2.5.0 --force --output json
```

Include prereleases when resolving the latest version:

```bash
observal self upgrade --pre --force --output json
```

A completed JSON result contains the prior version, target version, install method, and executable path. When the current version already matches the target, status is `up_to_date` and no installation occurs.

The command rejects invalid versions and targets older than the current version. System-package installations must be upgraded through their package manager.

## Downgrade

List available releases:

```bash
observal self downgrade --list --output json
```

JSON returns `current_version` and release `items`, each with a `current` boolean.

Install an older release:

```bash
observal self downgrade --version 2.4.0 --force --output json
```

`--list` and `--version` are mutually exclusive. The target must be older than the current version and at least the CLI version floor. System-package installations must use their package manager.

Releases before 1.10.4 are pinned by disabling their legacy automatic-update setting. The JSON result reports this as `automatic_updates_disabled`.

## Rollback

```bash
observal self rollback --force --output json
```

Rollback is available only for standalone binary installations with `~/.observal/bin/observal.prev`. It acquires the same version-change lock as upgrade and downgrade, copies the backup to a temporary file, restores executable permissions, and atomically replaces the current binary.

Package-managed installations must install their previous version through the package manager.

## Installation safety

Upgrade and downgrade:

1. Acquire a process lock so version changes cannot overlap.
2. Use the detected installation method.
3. Verify standalone binary downloads against the published SHA-256 checksum.
4. Replace standalone binaries atomically and retain the previous binary for rollback.
5. Execute the installed binary and verify that it reports the requested version.

Human mode may explicitly confirm an unsigned binary. JSON mode never prompts and refuses standalone installation when the release has no published checksum.

## Exit codes

| Code | Meaning |
| --- | --- |
| 5 | Rollback backup not found |
| 6 | Package-managed install or another version change holds the lock |
| 7 | Invalid version, direction, conflicting mode, or missing `--force` in JSON mode |
| 9 | GitHub, download, installer, checksum, verification, or filesystem failure |

## Related

* [`observal server`](server.md): manage server stack versions separately
* [Upgrades](../self-hosting/upgrades.md): server and CLI upgrade procedures
* [Environment variables](../reference/environment-variables.md): update-check settings
